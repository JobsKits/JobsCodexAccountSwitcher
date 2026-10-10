"""钩子配置与后台启动使用隔离目录和模拟进程，不修改真实 Codex 配置。"""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from jobs_codex_account_switcher import token_hooks


class TokenHookConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'codex'
        self.home.mkdir()
        self.path = self.home / 'hooks.json'
        self.options = dict(executable='/opt/Jobs Python/bin/python3',
                            launch_script='/opt/Jobs Tools/scripts/launch.py',
                            frozen=False, platform='darwin')

    def write(self, document):
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return self.path.read_bytes()

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_new_install_and_repeat_preserve_bytes(self):
        result = token_hooks.install_token_hook(self.home, **self.options)
        self.assertTrue(result.changed)
        self.assertTrue(result.requires_trust_review)
        self.assertIsNone(result.backup_path)
        groups = self.read()['hooks']['SessionStart']
        self.assertEqual(groups[0]['matcher'], token_hooks.HOOK_MATCHER)
        self.assertIn('--hook-launch', groups[0]['hooks'][0]['command'])
        self.assertEqual(groups[0]['hooks'][0]['timeout'], 5)
        raw = self.path.read_bytes()
        result = token_hooks.install_token_hook(self.home, **self.options)
        self.assertFalse(result.changed)
        self.assertIsNone(result.backup_path)
        self.assertEqual(self.path.read_bytes(), raw)
        status = token_hooks.token_hook_status(self.home)
        self.assertTrue(status.installed)
        self.assertEqual(status.handler_count, 2)
        self.assertIn('--hook-report', self.read()['hooks']['Stop'][0]['hooks'][0]['command'])
        self.assertEqual(status.trust_status, 'unknown')

    def test_install_preserves_others_and_backs_up_exact_bytes(self):
        document = {'description': '用户钩子', 'futureField': {'version': 2}, 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'custom': True, 'hooks': [
                {'type': 'command', 'command': 'echo custom', 'future': {'field': 1}}]}],
            'Stop': [{'hooks': [{'type': 'command', 'command': 'echo done'}]}]}}
        original = self.write(document)
        self.path.chmod(0o640)
        config = self.home / 'config.toml'
        trust = '[hooks.state."existing-handler"]\ntrusted_hash = "unchanged"\n'
        config.write_text(trust, encoding='utf-8')
        result = token_hooks.install_token_hook(self.home, **self.options)
        self.assertEqual(result.backup_path.read_bytes(), original)
        installed = self.read()
        self.assertEqual(installed['description'], document['description'])
        self.assertEqual(installed['futureField'], document['futureField'])
        self.assertEqual(installed['hooks']['Stop'][0], document['hooks']['Stop'][0])
        self.assertIn('--hook-report', installed['hooks']['Stop'][1]['hooks'][0]['command'])
        self.assertEqual(installed['hooks']['SessionStart'][0], document['hooks']['SessionStart'][0])
        self.assertEqual(config.read_text(encoding='utf-8'), trust)
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(result.backup_path.stat().st_mode & 0o777, 0o600)

    def test_upgrade_removes_old_owned_command_and_preserves_user_handler(self):
        token_hooks.install_token_hook(self.home, **self.options)
        document = self.read()
        group = document['hooks']['SessionStart'][0]
        group['label'] = '保留组元数据'
        user_handler = {'type': 'command', 'command': 'echo keep'}
        group['hooks'].append(user_handler)
        self.write(document)
        newer = dict(self.options, launch_script='/opt/New Tools/scripts/launch.py')
        result = token_hooks.install_token_hook(self.home, **newer)
        self.assertTrue(result.changed)
        groups = self.read()['hooks']['SessionStart']
        self.assertEqual(groups[0]['label'], '保留组元数据')
        self.assertEqual(groups[0]['hooks'], [user_handler])
        self.assertIn('/opt/New Tools/scripts/launch.py', groups[1]['hooks'][0]['command'])
        self.assertEqual(token_hooks.token_hook_status(self.home).handler_count, 2)
        self.assertFalse(token_hooks.install_token_hook(self.home, **newer).changed)

    def test_uninstall_only_removes_identified_handler(self):
        lookalike = {'type': 'command', 'command': 'echo --hook-launch',
                     'statusMessage': token_hooks.HOOK_STATUS_MESSAGE}
        original = {'metadata': {'keep': True}, 'hooks': {
            'SessionStart': [{'matcher': 'resume', 'hooks': [lookalike]}],
            'Stop': [{'hooks': [{'type': 'command', 'command': 'echo stop'}]}]}}
        self.write(original)
        token_hooks.install_token_hook(self.home, **self.options)
        installed = self.path.read_bytes()
        result = token_hooks.uninstall_token_hook(self.home)
        self.assertTrue(result.changed)
        self.assertFalse(result.requires_trust_review)
        self.assertEqual(result.backup_path.read_bytes(), installed)
        self.assertEqual(self.read(), original)
        self.assertFalse(token_hooks.token_hook_status(self.home).installed)
        self.assertFalse(token_hooks.uninstall_token_hook(self.home).changed)

    def test_uninstall_preserves_metadata_on_now_empty_group(self):
        token_hooks.install_token_hook(self.home, **self.options)
        document = self.read()
        document['hooks']['SessionStart'][0]['future'] = 'keep'
        self.write(document)
        token_hooks.uninstall_token_hook(self.home)
        self.assertEqual(self.read()['hooks']['SessionStart'], [
            {'matcher': token_hooks.HOOK_MATCHER, 'hooks': [], 'future': 'keep'}])

    def test_missing_uninstall_does_not_create_home(self):
        missing = self.home / 'missing'
        self.assertFalse(token_hooks.uninstall_token_hook(missing).changed)
        self.assertFalse(token_hooks.token_hook_status(missing).installed)
        self.assertFalse(missing.exists())

    def test_invalid_json_and_shape_never_write_or_back_up(self):
        invalids = ['{broken', '[]', '{"hooks": []}',
                    '{"hooks":{"SessionStart":{}}}',
                    '{"hooks":{"SessionStart":[{"hooks": "bad"}]}}',
                    '{"hooks":{},"hooks":{}}', '{"future":NaN}']
        for raw in invalids:
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                with self.assertRaises(token_hooks.TokenHookError):
                    token_hooks.install_token_hook(self.home, **self.options)
                with self.assertRaises(token_hooks.TokenHookError):
                    token_hooks.uninstall_token_hook(self.home)
                self.assertEqual(self.path.read_text(encoding='utf-8'), raw)
                self.assertEqual(list(self.home.glob('*.bak')), [])

    def test_symlink_file_and_codex_home_are_rejected(self):
        target = self.home / 'target.json'
        target.write_text('{}', encoding='utf-8')
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest('平台未允许创建符号链接')
        with self.assertRaises(token_hooks.TokenHookError):
            token_hooks.install_token_hook(self.home, **self.options)
        self.assertEqual(target.read_text(encoding='utf-8'), '{}')
        linked_home = self.home.parent / 'linked-home'
        linked_home.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(token_hooks.TokenHookError):
            token_hooks.uninstall_token_hook(linked_home)

    def test_failed_atomic_replace_keeps_original_and_backup(self):
        original = self.write({'description': 'original'})
        with patch.object(token_hooks.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(token_hooks.TokenHookError):
                token_hooks.install_token_hook(self.home, **self.options)
        self.assertEqual(self.path.read_bytes(), original)
        backups = list(self.home.glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(list(self.home.glob('.jobs-token-hooks-*')), [])

    def test_concurrent_edit_is_not_overwritten(self):
        original = self.write({'description': 'original'})
        replacement = b'{"description":"external editor"}'
        original_mkstemp = token_hooks.tempfile.mkstemp

        def concurrent_mkstemp(*args, **kwargs):
            self.path.write_bytes(replacement)
            return original_mkstemp(*args, **kwargs)

        with patch.object(token_hooks.tempfile, 'mkstemp', side_effect=concurrent_mkstemp):
            with self.assertRaisesRegex(token_hooks.TokenHookError, '其它程序修改'):
                token_hooks.install_token_hook(self.home, **self.options)
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertEqual(list(self.home.glob('*.bak'))[0].read_bytes(), original)

    def test_windows_install_is_recognized_and_removed(self):
        options = dict(self.options, platform='win32', frozen=True,
                       executable=r'C:\Jobs Tools\JobsCodexAccountSwitcher.exe')
        token_hooks.install_token_hook(self.home, **options)
        self.assertTrue(token_hooks.token_hook_status(self.home).installed)
        self.assertFalse(token_hooks.install_token_hook(self.home, **options).changed)
        self.assertTrue(token_hooks.uninstall_token_hook(self.home).changed)
        self.assertFalse(token_hooks.token_hook_status(self.home).installed)


class TokenHookLauncherTests(unittest.TestCase):
    def test_source_command_quotes_spaces_and_shell_metacharacters(self):
        home = Path('/example/Jobs "$HOME" $(echo no)')
        command = token_hooks.build_hook_command(
            home, executable="/Jobs Python's/bin/python3",
            launch_script='/Jobs Tools/$(echo no)/launch.py', frozen=False, platform='darwin')
        self.assertEqual(shlex.split(command, comments=True), [
            "/Jobs Python's/bin/python3", '/Jobs Tools/$(echo no)/launch.py',
            '--hook-launch', '--codex-home', str(home)])

    def test_windows_encoded_command_quotes_apostrophes_and_percent(self):
        command = token_hooks.build_hook_command(
            '/example/Jobs', executable=r"C:\Jobs' Tools\100%\widget.exe",
            frozen=True, platform='win32')
        script = base64.b64decode(command.split()[-1]).decode('utf-16-le')
        self.assertIn("& 'C:\\Jobs'' Tools\\100%\\widget.exe' '--hook-launch'", script)
        self.assertIn(token_hooks.HOOK_MARKER, script)
        self.assertTrue(command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand '))

    def test_frozen_widget_command_has_no_python_or_launch_script(self):
        command = token_hooks.build_token_widget_command(
            '/example/Jobs', executable='/Applications/Jobs.app/Contents/MacOS/Jobs', frozen=True)
        self.assertEqual(command, ['/Applications/Jobs.app/Contents/MacOS/Jobs',
                                   '--token-widget', '--codex-home', '/example/Jobs'])

    def test_launcher_detaches_and_never_waits_for_gui(self):
        process = Mock()
        with patch.object(token_hooks.subprocess, 'Popen', return_value=process) as popen:
            result = token_hooks.launch_token_widget(
                '/example/Jobs', executable='/python', launch_script='/launch.py',
                frozen=False, platform='darwin')
        self.assertTrue(result)
        args, options = popen.call_args
        self.assertEqual(args[0], ['/python', '/launch.py', '--token-widget',
                                   '--codex-home', '/example/Jobs'])
        self.assertTrue(options['start_new_session'])
        self.assertTrue(options['close_fds'])
        self.assertEqual(options['env']['CODEX_HOME'], '/example/Jobs')
        for key in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[key], subprocess.DEVNULL)
        process.wait.assert_not_called()
        process.communicate.assert_not_called()

    def test_windows_frozen_launcher_resets_packager_environment(self):
        inherited = {'QT_PLUGIN_PATH': '/old-package/Qt/plugins',
                     'QT_QPA_PLATFORM_PLUGIN_PATH': '/old-package/platforms',
                     'QT_QPA_PLATFORM': 'offscreen'}
        with patch.dict(os.environ, inherited), patch.object(token_hooks.subprocess, 'Popen') as popen:
            token_hooks.launch_token_widget('/example/Jobs', executable='widget.exe',
                                            frozen=True, platform='win32')
        options = popen.call_args.kwargs
        self.assertEqual(options['creationflags'], 0x00000008 | 0x00000200)
        self.assertNotIn('start_new_session', options)
        self.assertEqual(options['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertNotIn('QT_PLUGIN_PATH', options['env'])
        self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH', options['env'])
        self.assertEqual(options['env']['QT_QPA_PLATFORM'], 'offscreen')

    def test_source_launcher_removes_inherited_package_metadata(self):
        inherited = {'QT_PLUGIN_PATH': '/old-package/Qt/plugins',
                     'QT_QPA_PLATFORM_PLUGIN_PATH': '/old-package/platforms',
                     'QT_QPA_PLATFORM': 'offscreen',
                     '_PYI_APPLICATION_HOME_DIR': '/old-package',
                     '_PYI_PARENT_PROCESS_LEVEL': '1',
                     '_PYI_ARCHIVE_FILE': '/old-package/program'}
        with patch.dict(os.environ, inherited), patch.object(token_hooks.subprocess, 'Popen') as popen:
            token_hooks.launch_token_widget('/example/Jobs', executable='/python',
                                            launch_script='/launch.py', frozen=False)
        env = popen.call_args.kwargs['env']
        self.assertNotIn('QT_PLUGIN_PATH', env)
        self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH', env)
        self.assertFalse(any(key.startswith('_PYI_') for key in env))
        self.assertEqual(env['QT_QPA_PLATFORM'], 'offscreen')

    def test_launch_error_is_readable(self):
        with patch.object(token_hooks.subprocess, 'Popen', side_effect=OSError('missing executable')):
            with self.assertRaisesRegex(token_hooks.TokenHookError, '无法启动'):
                token_hooks.launch_token_widget('/example/Jobs', frozen=True)

    def test_default_home_follows_environment(self):
        with patch.dict(os.environ, CODEX_HOME='/configured/codex'):
            self.assertEqual(token_hooks.resolve_codex_home(), Path('/configured/codex'))

    def test_newline_and_nul_launch_arguments_are_rejected(self):
        for argument in ('/bad\nprogram', '/bad\0program'):
            with self.subTest(argument=argument):
                with self.assertRaises(token_hooks.TokenHookError):
                    token_hooks.build_hook_command('/example/Jobs', executable=argument, frozen=True)


if __name__ == '__main__':
    unittest.main()
