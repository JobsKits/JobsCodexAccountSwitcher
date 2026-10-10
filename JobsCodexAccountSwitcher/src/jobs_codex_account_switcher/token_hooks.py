"""Token 悬浮控件的用户级 SessionStart 钩子；不修改信任状态。Created by Jobs."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import uuid


HOOK_MARKER = 'jobs-codex-token-widget-hook:v1'
HOOK_STATUS_MESSAGE = 'JobsCodexAccountSwitcher Token 悬浮控件'
HOOK_REPORT_STATUS_MESSAGE = 'JobsCodexAccountSwitcher 本轮 Token 统计'
HOOK_MATCHER = '^(startup|resume)$'
HOOK_REVIEW_MESSAGE = '首次安装或启动命令变更后，请在 Codex CLI 的 /hooks 中审阅并信任此钩子。'


class TokenHookError(RuntimeError):
    """可直接展示的钩子配置错误。"""


@dataclass(frozen=True)
class HookChangeResult:
    changed: bool
    hooks_path: Path
    backup_path: Path | None = None
    requires_trust_review: bool = False


@dataclass(frozen=True)
class HookStatus:
    installed: bool
    hooks_path: Path
    handler_count: int = 0
    # 信任由 Codex 审阅和持久化；本工具只知道配置是否存在。
    trust_status: str = 'unknown'


def resolve_codex_home(codex_home: Path | str | None = None) -> Path:
    value = codex_home if codex_home is not None else os.environ.get('CODEX_HOME')
    return Path(value or Path.home() / '.codex').expanduser().absolute()


def _argument(value: Path | str) -> str:
    text = str(value)
    if not text or any(char in text for char in ('\0', '\r', '\n')):
        raise TokenHookError('启动参数不能为空或包含换行。')
    return text


def _launch_prefix(*, executable=None, launch_script=None, frozen=None) -> list[str]:
    is_frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    program = _argument(executable or sys.executable)
    if is_frozen:
        return [program]
    script = launch_script or Path(__file__).resolve().parents[2] / 'scripts/launch.py'
    return [program, _argument(script)]


def build_token_widget_command(codex_home=None, *, executable=None,
                               launch_script=None, frozen=None) -> list[str]:
    """源码与成品使用相同的独立控件入口；Popen 直接传参数列表。"""
    return _launch_prefix(executable=executable, launch_script=launch_script, frozen=frozen) + [
        '--token-widget', '--codex-home', _argument(resolve_codex_home(codex_home))]


def build_hook_command(codex_home=None, *, executable=None, launch_script=None,
                       frozen=None, platform=None, report=False) -> str:
    """钩子只执行轻量后台启动器，GUI 生命周期不占用 SessionStart。"""
    command = _launch_prefix(executable=executable, launch_script=launch_script, frozen=frozen) + [
        '--hook-report' if report else '--hook-launch',
        '--codex-home', _argument(resolve_codex_home(codex_home))]
    if (platform or sys.platform) == 'win32':
        # EncodedCommand 避免外层 cmd/PowerShell 二次解释路径中的 %、$ 与引号。
        quoted = ['\'' + item.replace('\'', '\'\'') + '\'' for item in command]
        script = '& ' + ' '.join(quoted) + '\nexit $LASTEXITCODE\n# ' + HOOK_MARKER
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
    return shlex.join(command) + ' # ' + HOOK_MARKER


def launch_token_widget(codex_home=None, *, executable=None, launch_script=None,
                        frozen=None, platform=None) -> bool:
    """脱离钩子进程启动；控件入口负责按 CODEX_HOME 保证单实例。"""
    home = resolve_codex_home(codex_home)
    command = build_token_widget_command(home, executable=executable,
                                         launch_script=launch_script, frozen=frozen)
    env = dict(os.environ, CODEX_HOME=str(home))
    is_frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    # 不沿用其它 Qt 程序的插件目录，避免新进程混载两套 Qt。
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        env.pop(key, None)
    if is_frozen:
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    else:
        for key in tuple(env):
            if key.startswith('_PYI_'):
                env.pop(key)
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True, env=env)
    if (platform or sys.platform) == 'win32':
        options['creationflags'] = (getattr(subprocess, 'DETACHED_PROCESS', 0x00000008) |
                                    getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
    else:
        options['start_new_session'] = True
    try:
        subprocess.Popen(command, **options)
    except OSError as error:
        raise TokenHookError(f'无法启动 Token 悬浮控件：{error}') from error
    return True


def _check_path(home: Path, path: Path):
    if home.is_symlink() or path.is_symlink():
        raise TokenHookError('拒绝修改符号链接形式的 Codex 目录或 hooks.json。')
    if home.exists() and not home.is_dir():
        raise TokenHookError('CODEX_HOME 必须是目录。')
    if path.exists() and not path.is_file():
        raise TokenHookError('hooks.json 必须是普通文件。')


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('invalid JSON constant')


def _read_hooks(home: Path) -> tuple[Path, bytes | None, dict]:
    path = home / 'hooks.json'
    _check_path(home, path)
    if not path.exists():
        return path, None, {}
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise TokenHookError('hooks.json 必须是普通文件。')
            raw = stream.read()
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_invalid_constant)
        if not isinstance(document, dict):
            raise ValueError('object required')
        events = document.get('hooks', {})
        if not isinstance(events, dict):
            raise ValueError('hooks object required')
        for groups in events.values():
            if not isinstance(groups, list):
                raise ValueError('matcher array required')
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                    raise ValueError('handler array required')
                if not all(isinstance(handler, dict) for handler in group['hooks']):
                    raise ValueError('handler object required')
    except (OSError, UnicodeError, ValueError) as error:
        raise TokenHookError('hooks.json 无法安全读取或不是有效的钩子 JSON，未修改。') from error
    return path, raw, document


def _command_text(command) -> str:
    if not isinstance(command, str):
        return ''
    prefix = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand '
    if command.startswith(prefix):
        try:
            return base64.b64decode(command[len(prefix):], validate=True).decode('utf-16-le')
        except (ValueError, UnicodeError):
            return ''
    return command


def _owned_handler(handler: dict) -> bool:
    text = _command_text(handler.get('command'))
    owned_mode = ((handler.get('statusMessage') == HOOK_STATUS_MESSAGE and '--hook-launch' in text)
                  or (handler.get('statusMessage') == HOOK_REPORT_STATUS_MESSAGE and '--hook-report' in text))
    return handler.get('type') == 'command' and owned_mode and HOOK_MARKER in text


def _remove_owned(document: dict) -> int:
    removed = 0
    events = document.get('hooks', {})
    for event, groups in list(events.items()):
        remaining_groups = []
        for group in groups:
            remaining = [handler for handler in group['hooks'] if not _owned_handler(handler)]
            count = len(group['hooks']) - len(remaining)
            removed += count
            if count:
                group['hooks'] = remaining
            # 只删除本工具创建的空组；有其它元数据的组原样保留。
            created_group = (group.get('matcher') == HOOK_MATCHER or
                             event == 'Stop' and 'matcher' not in group)
            if count and not remaining and set(group) <= {'matcher', 'hooks'} and created_group:
                continue
            remaining_groups.append(group)
        if remaining_groups:
            events[event] = remaining_groups
        elif groups:
            del events[event]
    return removed


def _replace_hooks(home: Path, path: Path, original: bytes | None,
                   document: dict) -> Path | None:
    _check_path(home, path)
    home.mkdir(parents=True, exist_ok=True)
    backup = None
    if original is not None:
        stamp = datetime.now().strftime('%Y.%m.%d %H-%M-%S')
        backup = home / f'hooks.json.jobs-token-widget.{stamp}.{uuid.uuid4().hex[:8]}.bak'
        with backup.open('xb') as stream:
            os.chmod(backup, 0o600)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    fd, name = tempfile.mkstemp(prefix='.jobs-token-hooks-', dir=home)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        mode = stat.S_IMODE(path.stat().st_mode) if original is not None else 0o600
        os.chmod(name, mode)
        _check_path(home, path)
        current = path.read_bytes() if path.exists() else None
        if current != original:
            raise TokenHookError('hooks.json 已被其它程序修改，请重新读取后再操作。')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return backup


def token_hook_status(codex_home=None) -> HookStatus:
    home = resolve_codex_home(codex_home)
    path, _, document = _read_hooks(home)
    count = sum(_owned_handler(handler) for groups in document.get('hooks', {}).values()
                for group in groups for handler in group['hooks'])
    return HookStatus(bool(count), path, count)


def install_token_hook(codex_home=None, *, executable=None, launch_script=None,
                       frozen=None, platform=None) -> HookChangeResult:
    """合并用户级钩子并备份；Codex 的 config.toml 和 trust hash 不在写入范围。"""
    home = resolve_codex_home(codex_home)
    path, original, document = _read_hooks(home)
    command = build_hook_command(home, executable=executable, launch_script=launch_script,
                                 frozen=frozen, platform=platform)
    handler = {'type': 'command', 'command': command, 'timeout': 5,
               'statusMessage': HOOK_STATUS_MESSAGE}
    group = {'matcher': HOOK_MATCHER, 'hooks': [handler]}
    report_command = build_hook_command(home, executable=executable, launch_script=launch_script,
                                        frozen=frozen, platform=platform, report=True)
    report_group = {'hooks': [{'type': 'command', 'command': report_command, 'timeout': 5,
                              'statusMessage': HOOK_REPORT_STATUS_MESSAGE}]}
    before = json.dumps(document, sort_keys=True)
    _remove_owned(document)
    document.setdefault('hooks', {}).setdefault('SessionStart', []).append(group)
    document['hooks'].setdefault('Stop', []).append(report_group)
    if json.dumps(document, sort_keys=True) == before:
        return HookChangeResult(False, path, requires_trust_review=True)
    try:
        backup = _replace_hooks(home, path, original, document)
    except OSError as error:
        raise TokenHookError(f'保存 Token 钩子失败，原配置保留：{error}') from error
    return HookChangeResult(True, path, backup, requires_trust_review=True)


def uninstall_token_hook(codex_home=None) -> HookChangeResult:
    """只移除带本工具标记的 handler，保留其它事件、命令和 JSON 元数据。"""
    home = resolve_codex_home(codex_home)
    path, original, document = _read_hooks(home)
    if not _remove_owned(document):
        return HookChangeResult(False, path)
    try:
        backup = _replace_hooks(home, path, original, document)
    except OSError as error:
        raise TokenHookError(f'移除 Token 钩子失败，原配置保留：{error}') from error
    return HookChangeResult(True, path, backup)
