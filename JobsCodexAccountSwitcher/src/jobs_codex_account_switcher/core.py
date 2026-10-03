"""加密账户库与可恢复文件切换；不修改 Codex 配置或程序。Created by Jobs."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
import uuid

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class SwitchError(RuntimeError):
    """可展示的错误，不包含凭据正文。"""


def identity(raw: bytes) -> dict:
    """解析缓存身份，仅用于本地匹配；JWT 未经服务器验签。"""
    try:
        auth = json.loads(raw)
        tokens = auth['tokens']
        if auth.get('auth_mode', 'chatgpt') != 'chatgpt' or auth.get('OPENAI_API_KEY'):
            raise ValueError()
        if not all(isinstance(tokens.get(k), str) and tokens[k] for k in
                   ('id_token', 'access_token', 'refresh_token', 'account_id')):
            raise ValueError()
        body = tokens['id_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        subject = claims['sub']
        issuer = claims['iss']
        if not isinstance(subject, str) or not isinstance(issuer, str):
            raise ValueError()
        key = hashlib.sha256(json.dumps([issuer, subject, tokens['account_id']]).encode()).hexdigest()
        return {'key': key, 'email': str(claims.get('email', '未提供邮箱')),
                'account_id': tokens['account_id']}
    except (ValueError, KeyError, TypeError, IndexError):
        raise SwitchError('不是受支持的 ChatGPT 登录文件；请在 Codex 完成正常登录。') from None


def atomic_write(path: Path, raw: bytes):
    """同目录原子替换，拒绝通过符号链接写到其它位置。"""
    if path.is_symlink() or path.parent.is_symlink():
        raise SwitchError('拒绝写入符号链接路径。')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.jobs-auth-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Vault:
    """可选口令保护；兼容原版加密库，免口令模式不做伪加密。"""
    def __init__(self, path: Path, password: str | None = None):
        self.path = path
        self.cipher = None
        self.salt = None
        if path.is_symlink():
            raise SwitchError('账户库不能是符号链接。')
        self.data = {'profiles': {}, 'recovery': None}
        if not path.exists():
            if password:
                self._configure_password(password)
            return
        try:
            envelope = json.loads(path.read_bytes())
            if envelope.get('version') == 2 and envelope.get('mode') == 'plain':
                self.data = envelope['data']
                if password:
                    raise SwitchError('免口令库请直接打开，再启用口令保护。')
            elif envelope.get('version') == 1:
                if not password:
                    raise SwitchError('该账户库已启用口令，请先使用原口令解锁。')
                salt = base64.b64decode(envelope['salt'], validate=True)
                if len(salt) != 16:
                    raise ValueError()
                self._configure_password(password, salt)
                self.data = json.loads(self.cipher.decrypt(envelope['payload'].encode()))
            else:
                raise ValueError()
            if not isinstance(self.data, dict) or not isinstance(self.data.get('profiles'), dict) or 'recovery' not in self.data:
                raise ValueError()
        except (ValueError, KeyError, InvalidToken, TypeError, AttributeError):
            raise SwitchError('口令不正确或账户库损坏；没有覆盖原文件。') from None

    @staticmethod
    def needs_password(path: Path):
        """只检查格式标记，不尝试解密或覆盖旧库。"""
        if path.is_symlink():
            raise SwitchError('账户库不能是符号链接。')
        if not path.exists():
            return False
        try:
            envelope = json.loads(path.read_bytes())
            if envelope.get('version') == 1:
                return True
            if envelope.get('version') == 2 and envelope.get('mode') == 'plain':
                return False
        except (ValueError, AttributeError):
            pass
        raise SwitchError('账户库格式不受支持；没有覆盖原文件。')

    @property
    def protected(self):
        return self.cipher is not None

    def _configure_password(self, password, salt=None):
        if salt is None and len(password) < 12:
            raise SwitchError('启用口令保护时，口令至少 12 个字符。')
        self.salt = salt if salt is not None else os.urandom(16)
        key = Scrypt(salt=self.salt, length=32, n=2**15, r=8, p=1).derive(password.encode())
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def set_password(self, password: str | None):
        """原子转换保存模式，失败时保留原文件和内存保护状态。"""
        old = self.cipher, self.salt
        try:
            if password:
                self._configure_password(password)
            else:
                self.cipher = self.salt = None
            self.save()
        except Exception:
            self.cipher, self.salt = old
            raise

    def save(self):
        """保护开启保存密文，关闭保存明文；仍使用原子写入及用户权限。"""
        if self.protected:
            payload = self.cipher.encrypt(json.dumps(self.data, ensure_ascii=False).encode()).decode()
            envelope = {'version': 1, 'salt': base64.b64encode(self.salt).decode(), 'payload': payload}
        else:
            envelope = {'version': 2, 'mode': 'plain', 'data': self.data}
        atomic_write(self.path, json.dumps(envelope, ensure_ascii=False).encode())

    def rename(self, key: str, label: str):
        """仅更新备注；保存失败恢复内存中的原备注。"""
        label = label.strip()
        profiles = self.data['profiles']
        if key not in profiles:
            raise SwitchError('目标账户不存在。')
        if not label or len(label) > 80:
            raise SwitchError('账户备注需要 1～80 个字符。')
        if any(item['label'] == label and other != key for other, item in profiles.items()):
            raise SwitchError('该备注已被另一账户使用。')
        old = profiles[key]['label']
        profiles[key]['label'] = label
        try:
            self.save()
        except Exception:
            profiles[key]['label'] = old
            raise

    def capture(self, label: str, raw: bytes):
        """同一身份更新最新 Token，不允许把另一身份覆盖到同名账户。"""
        label = label.strip()
        if not label or len(label) > 80:
            raise SwitchError('账户名称需要 1～80 个字符。')
        who = identity(raw)
        profiles = self.data['profiles']
        for key, item in profiles.items():
            if item['label'] == label and key != who['key']:
                raise SwitchError('该名称属于另一个账户，请使用不同名称。')
        profiles[who['key']] = dict(who, label=label, auth=base64.b64encode(raw).decode(),
                                   updated=datetime.now(timezone.utc).isoformat())
        self.save()
        return who['key']


class FileSwitcher:
    """只支持显式 file 存储；其它模式必须停止，避免覆盖无效缓存。"""
    def __init__(self, home: Path, vault: Vault, guard=lambda: None):
        self.home = home
        self.vault = vault
        self.guard = guard
        self.auth = home / 'auth.json'

    def check_storage(self, require_auth=True):
        if self.home.is_symlink() or self.auth.is_symlink():
            raise SwitchError('Codex 目录或 auth.json 是符号链接，停止处理。')
        try:
            config = self.home / 'config.toml'
            data = tomllib.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
        except (OSError, tomllib.TOMLDecodeError):
            raise SwitchError('无法读取 Codex 配置。') from None
        if data.get('cli_auth_credentials_store', 'file') != 'file':
            raise SwitchError('当前是 keyring / auto / ephemeral 存储，不支持文件切换；工具不自动改配置。')
        if require_auth and not self.auth.is_file():
            raise SwitchError('没有 auth.json；请先完成 Codex 登录并核实实际存储模式。')

    @contextmanager
    def locked(self, require_auth=True):
        self.check_storage(require_auth)
        path = self.home / '.jobs-account-switch.lock'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise SwitchError('另一切换操作正在进行；异常退出后需确认无工具运行再移除锁文件。') from None
        try:
            os.close(fd)
            self.guard()
            yield
        finally:
            path.unlink(missing_ok=True)

    def capture(self, label):
        with self.locked():
            return self.vault.capture(label, self.auth.read_bytes())

    def switch(self, key):
        """保存源账户最新凭据后原子切换，无人工核验状态门禁。"""
        with self.locked():
            current = self.auth.read_bytes()
            who = identity(current)
            profiles = self.vault.data['profiles']
            if who['key'] not in profiles:
                raise SwitchError('先保存当前账户，避免丢失当前登录态。')
            if key not in profiles:
                raise SwitchError('目标账户不存在。')
            if who['key'] == key:
                raise SwitchError('当前缓存已经是该账户，无需切换。')
            target = base64.b64decode(profiles[key]['auth'])
            if identity(target)['key'] != key:
                raise SwitchError('目标账户数据不一致，停止切换。')
            self.vault.capture(profiles[who['key']]['label'], current)
            self.guard()
            if self.auth.read_bytes() != current:
                raise SwitchError('登录文件发生并发变化，未覆盖。')
            atomic_write(self.auth, target)
            if identity(self.auth.read_bytes())['key'] != key:
                raise SwitchError('登录文件发生并发变化，请检查当前登录账户。')
