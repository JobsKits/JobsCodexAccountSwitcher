"""验证账户入口不会重新接入独立监控功能。Created by Jobs."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class AccountEntrypointTests(unittest.TestCase):
    project = Path(__file__).resolve().parents[1]

    def launch_isolated_application(self, arguments):
        from jobs_codex_account_switcher import app
        window = MagicMock()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(app, 'QApplication') as application, \
                    patch.object(app, 'QStandardPaths') as paths, \
                    patch.object(app, 'QLockFile') as lock, \
                    patch.object(app, 'Window', return_value=window):
                paths.writableLocation.return_value = temp
                lock.return_value.tryLock.return_value = True
                application.return_value.exec.return_value = 0
                self.assertEqual(app.main(arguments), 0)
        return window

    def test_explicit_home_applies_both_argument_forms(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / '所选 Codex 目录'
            for arguments in (['--codex-home', str(home)], ['--codex-home=' + str(home)]):
                with self.subTest(arguments=arguments):
                    window = self.launch_isolated_application(arguments)
                    window.home.setText.assert_called_once_with(str(home.absolute()))
            self.assertFalse(home.exists())

    def test_no_explicit_home_preserves_window_preferences(self):
        window = self.launch_isolated_application([])
        window.home.setText.assert_not_called()

    def run_entrypoint(self, arguments, *, stdlib_only=False, input_text=None):
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(self.project / 'src')
        environment['QT_QPA_PLATFORM'] = 'offscreen'
        return subprocess.run(
            [sys.executable, *(['-S'] if stdlib_only else []),
             str(self.project / 'scripts/launch.py'), *arguments],
            capture_output=True,
            text=True,
            input=input_text,
            env=environment,
            timeout=10,
            check=False,
        )

    def test_help_describes_only_account_switching(self):
        result = self.run_entrypoint(['--help'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--codex-home', result.stdout)
        self.assertIn('账户切换器', result.stdout)
        self.assertNotIn('Token', result.stdout)
        self.assertNotIn('--hook-', result.stdout)

    def test_monitor_arguments_rejected_without_writing_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for argument in ('--token-widget', '--install-token-hook', '--remove-token-hook'):
                with self.subTest(argument=argument):
                    result = self.run_entrypoint(['--codex-home', str(root), argument])
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('unrecognized arguments', result.stderr)
            self.assertEqual(list(root.iterdir()), [])

    def test_retired_hooks_acknowledge_without_qt_or_configuration_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for argument in ('--hook-launch', '--hook-report'):
                with self.subTest(argument=argument):
                    result = self.run_entrypoint(['--codex-home', str(root), argument],
                                                 stdlib_only=True, input_text='ignored non-JSON input')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, '')
                    if argument == '--hook-report':
                        message = json.loads(result.stdout)
                        self.assertEqual(set(message), {'systemMessage'})
                        self.assertIn('JobsCodexTokenMonitor', message['systemMessage'])
                        result.stdout.encode('ascii')
                    else:
                        self.assertEqual(result.stdout, '')
            self.assertEqual(list(root.iterdir()), [])

    def test_retired_hook_rejects_conflicting_or_monitor_options(self):
        for arguments in (['--hook-launch', '--hook-report'], ['--hook-report', '--token-widget']):
            with self.subTest(arguments=arguments):
                result = self.run_entrypoint(arguments, stdlib_only=True)
                self.assertEqual(result.returncode, 2, result.stderr)

    def load_launcher(self):
        spec = importlib.util.spec_from_file_location('account_launcher', self.project / 'scripts/launch.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_retired_stop_without_stdout_uses_ascii_descriptor_output(self):
        launcher = self.load_launcher()
        with patch.object(launcher.sys, 'stdout', None), patch.object(launcher.os, 'write') as write:
            self.assertEqual(launcher.main(['--hook-report']), 0)
        write.assert_called_once()
        descriptor, raw = write.call_args.args
        self.assertEqual(descriptor, 1)
        self.assertIn('JobsCodexTokenMonitor', json.loads(raw.decode('ascii'))['systemMessage'])

    def test_retired_stop_without_output_descriptor_still_succeeds(self):
        launcher = self.load_launcher()
        with patch.object(launcher.sys, 'stdout', None), \
                patch.object(launcher.os, 'write', side_effect=OSError('no inherited stdout')):
            self.assertEqual(launcher.main(['--hook-report']), 0)

    def test_retired_stop_with_closed_stdout_still_succeeds(self):
        launcher = self.load_launcher()
        output = io.StringIO()
        output.close()
        with patch.object(launcher.sys, 'stdout', output):
            self.assertEqual(launcher.main(['--hook-report']), 0)


if __name__ == '__main__':
    unittest.main()
