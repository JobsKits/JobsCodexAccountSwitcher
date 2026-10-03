"""用虚拟凭据验证切换与故障边界，不接触真实账户。Created by Jobs."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jobs_codex_account_switcher.core import FileSwitcher, SwitchError, Vault, identity


def auth(account, refresh='r1'):
    claims = base64.urlsafe_b64encode(json.dumps({'iss': 'https://auth.openai.com',
                                                'sub': account, 'email': account + '@example.test'}).encode()).decode().rstrip('=')
    return json.dumps({'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None, 'tokens': {
        'id_token': 'header.' + claims + '.signature', 'access_token': 'fake-access',
        'refresh_token': refresh, 'account_id': account}}).encode()


class SwitchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'codex'
        self.home.mkdir()
        self.path = self.home / 'auth.json'
        self.a, self.b = auth('a'), auth('b')
        self.path.write_bytes(self.a)
        self.vault = Vault(self.root / 'accounts.enc', 'test-password-123456')
        self.engine = FileSwitcher(self.home, self.vault)
        self.ka = self.vault.capture('A', self.a)
        self.kb = self.vault.capture('B', self.b)

    def tearDown(self):
        self.temp.cleanup()

    def test_optional_password_plain_reopens_without_prompt(self):
        path = self.root / 'plain.enc'
        vault = Vault(path)
        vault.capture('A', self.a)
        self.assertFalse(vault.protected)
        self.assertFalse(Vault.needs_password(path))
        self.assertEqual(Vault(path).data, vault.data)
        envelope = json.loads(path.read_bytes())
        saved = envelope['data']['profiles'][identity(self.a)['key']]['auth']
        self.assertEqual(base64.b64decode(saved), self.a)

    def test_convert_both_modes_preserves_profiles_and_recovery(self):
        self.engine.switch(self.kb)
        before = json.loads(json.dumps(self.vault.data))
        self.vault.set_password(None)
        plain = Vault(self.vault.path)
        self.assertEqual(plain.data, before)
        plain.set_password('new-password-123456')
        self.assertTrue(Vault.needs_password(plain.path))
        protected = Vault(plain.path, 'new-password-123456')
        self.assertEqual(protected.data, before)

    def test_legacy_encrypted_requires_original_password(self):
        original = self.vault.path.read_bytes()
        with self.assertRaises(SwitchError):
            Vault(self.vault.path)
        self.assertEqual(self.vault.path.read_bytes(), original)
        self.assertTrue(Vault.needs_password(self.vault.path))

    def test_failed_conversion_preserves_original_protection(self):
        original = self.vault.path.read_bytes()
        with patch('jobs_codex_account_switcher.core.atomic_write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.vault.set_password(None)
        self.assertTrue(self.vault.protected)
        self.assertEqual(self.vault.path.read_bytes(), original)
        self.assertEqual(Vault(self.vault.path, 'test-password-123456').data, self.vault.data)

    def test_short_optional_password_rejected_without_write(self):
        vault = Vault(self.root / 'short.enc')
        vault.save()
        original = vault.path.read_bytes()
        with self.assertRaises(SwitchError):
            vault.set_password('short')
        self.assertFalse(vault.protected)
        self.assertEqual(vault.path.read_bytes(), original)

    def test_rename_preserves_credentials_and_persists(self):
        before = self.vault.data['profiles'][self.ka]['auth']
        self.vault.rename(self.ka, '新的备注')
        saved = Vault(self.vault.path, 'test-password-123456')
        self.assertEqual(saved.data['profiles'][self.ka]['label'], '新的备注')
        self.assertEqual(saved.data['profiles'][self.ka]['auth'], before)
        for label in ('', 'B', 'x' * 81):
            with self.assertRaises(SwitchError):
                self.vault.rename(self.ka, label)
        with patch('jobs_codex_account_switcher.core.atomic_write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.vault.rename(self.ka, '未保存')
        self.assertEqual(self.vault.data['profiles'][self.ka]['label'], '新的备注')

    def test_repeat_switch_preserves_refreshed_credentials(self):
        latest_a = auth('a', 'latest-a')
        self.path.write_bytes(latest_a)
        self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.b)
        latest_b = auth('b', 'latest-b')
        self.path.write_bytes(latest_b)
        self.engine.switch(self.ka)
        self.assertEqual(self.path.read_bytes(), latest_a)
        self.assertEqual(base64.b64decode(self.vault.data['profiles'][self.kb]['auth']), latest_b)
        self.assertIsNone(self.vault.data['recovery'])

    def test_legacy_recovery_does_not_block_switch_or_capture(self):
        self.vault.data['recovery'] = {'legacy': 'record'}
        self.vault.save()
        self.engine.capture('A')
        self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.b)

    def test_failed_auth_write_keeps_source(self):
        from jobs_codex_account_switcher.core import atomic_write
        def write(path, raw):
            if path == self.path:
                raise OSError('disk')
            atomic_write(path, raw)
        with patch('jobs_codex_account_switcher.core.atomic_write', side_effect=write):
            with self.assertRaises(OSError):
                self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.a)
        self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.b)

    def test_ciphertext_and_wrong_password(self):
        self.assertNotIn(b'fake-access', self.vault.path.read_bytes())
        with self.assertRaises(SwitchError):
            Vault(self.vault.path, 'wrong-password-123456')
        self.assertEqual(Vault(self.vault.path, 'test-password-123456').data, self.vault.data)



    def test_running_process_and_keyring_do_not_mutate(self):
        def guard():
            raise SwitchError('running')
        with self.assertRaises(SwitchError):
            FileSwitcher(self.home, self.vault, guard).switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.a)
        for mode in ('keyring', 'auto', 'ephemeral'):
            (self.home / 'config.toml').write_text(f'cli_auth_credentials_store = "{mode}"')
            with self.assertRaises(SwitchError):
                self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.a)

    def test_rejects_duplicate_name_and_unknown_source(self):
        with self.assertRaises(SwitchError):
            self.vault.capture('A', self.b)
        self.path.write_bytes(auth('c'))
        with self.assertRaises(SwitchError):
            self.engine.switch(self.kb)
        self.assertEqual(identity(self.path.read_bytes())['account_id'], 'c')

    def test_failure_before_write_retains_recovery(self):
        with patch('jobs_codex_account_switcher.core.atomic_write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.engine.switch(self.kb)
        self.assertEqual(self.path.read_bytes(), self.a)
        # 捕获最新源账户先失败，尚未修改登录文件。
        self.assertFalse((self.home / '.jobs-account-switch.lock').exists())


    def test_symlink_and_concurrent_lock_rejected(self):
        lock = self.home / '.jobs-account-switch.lock'
        lock.write_text('busy')
        with self.assertRaises(SwitchError):
            self.engine.switch(self.kb)
        lock.unlink()
        other = self.home / 'other.json'
        self.path.rename(other)
        try:
            self.path.symlink_to(other)
        except OSError:
            self.skipTest('当前 Windows 用户未开启符号链接权限')
        with self.assertRaises(SwitchError):
            self.engine.switch(self.kb)
        self.assertEqual(other.read_bytes(), self.a)





if __name__ == '__main__':
    unittest.main()
