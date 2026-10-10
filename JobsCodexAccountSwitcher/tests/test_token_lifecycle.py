"""隔离验证钩子快返、独立控件、真实线程退出与跨进程单实例。Created by Jobs."""
import argparse
import builtins
from contextlib import ExitStack
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_FILE = Path(__file__).resolve()


def _clean_environment(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', QT_QPA_PLATFORM='offscreen',
               PYTHONPATH=str(PROJECT_ROOT / 'src'), CODEX_HOME=str(root / 'codex'))
    for key in tuple(env):
        if key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH') or key.startswith('_PYI_'):
            env.pop(key)
    return env


class _NoQtOrAccounts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'cryptography')) or fullname in (
                'jobs_codex_account_switcher.app', 'jobs_codex_account_switcher.core',
                'jobs_codex_account_switcher.platforms'):
            raise AssertionError('钩子快返路径不应导入 ' + fullname)


def _hook_helper(root, frozen):
    """新解释器中禁止 GUI/账户模块，验证真正的 launch.py 入口。"""
    guard = _NoQtOrAccounts()
    sys.meta_path.insert(0, guard)
    from jobs_codex_account_switcher import token_hooks
    spec = importlib.util.spec_from_file_location('lifecycle_launch', PROJECT_ROOT / 'scripts/launch.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    home = root / 'codex'
    argv = [str(PROJECT_ROOT / 'scripts/launch.py'), '--hook-launch', '--codex-home', str(home)]
    with patch.object(sys, 'argv', argv), patch.object(sys, 'frozen', frozen, create=True), \
            patch.object(token_hooks.subprocess, 'Popen') as spawn:
        result = launcher.main()
    command = spawn.call_args.args[0]
    expected = [sys.executable] if frozen else [sys.executable, str(PROJECT_ROOT / 'scripts/launch.py')]
    assert command == expected + ['--token-widget', '--codex-home', str(home)]
    options = spawn.call_args.kwargs
    assert all(options[key] == subprocess.DEVNULL for key in ('stdin', 'stdout', 'stderr'))
    assert not home.exists()
    assert not any(name.startswith('PySide6') for name in sys.modules)
    if frozen:
        assert options['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    return {'result': result, 'qt_imported': False, 'frozen': frozen}


def _forbid_accounts(filename):
    if isinstance(filename, (str, bytes, os.PathLike)):
        if Path(os.fsdecode(filename)).name in ('auth.json', 'accounts.enc'):
            raise AssertionError('Token 模式不应读写账户文件')


def _account_io_guard(stack):
    path_open, builtin_open, os_open = Path.open, builtins.open, os.open

    def guarded_path_open(path, *args, **kwargs):
        _forbid_accounts(path)
        return path_open(path, *args, **kwargs)

    def guarded_builtin_open(filename, *args, **kwargs):
        _forbid_accounts(filename)
        return builtin_open(filename, *args, **kwargs)

    def guarded_os_open(filename, *args, **kwargs):
        _forbid_accounts(filename)
        return os_open(filename, *args, **kwargs)

    stack.enter_context(patch.object(Path, 'open', guarded_path_open))
    stack.enter_context(patch.object(builtins, 'open', guarded_builtin_open))
    stack.enter_context(patch.object(os, 'open', guarded_os_open))


def _standalone_helper(root):
    from PySide6.QtCore import QSettings, QTimer
    from jobs_codex_account_switcher import app, token_widget

    home, data = root / 'codex', root / 'widget-data'
    (home / 'sessions').mkdir(parents=True)
    data.mkdir()
    auth, accounts = home / 'auth.json', data / 'accounts.enc'
    auth.write_bytes(b'isolated-auth-sentinel')
    accounts.write_bytes(b'isolated-account-sentinel')
    windows = []

    class IsolatedWindow(token_widget.TokenWindow):
        def __init__(self, codex_home):
            settings = QSettings(str(root / 'widget.ini'), QSettings.Format.IniFormat)
            super().__init__(codex_home, settings)
            self.saw_snapshot = False
            windows.append(self)
            self.worker.snapshot_ready.connect(self.received)
            self.worker.refresh_requested.set()
            QTimer.singleShot(2000, self.close)

        def received(self, snapshot):
            self.saw_snapshot = True
            QTimer.singleShot(0, self.close)

    forbidden = AssertionError('独立 Token 模式不应管理账户或关闭/重启 Codex')
    with ExitStack() as stack:
        _account_io_guard(stack)
        stack.enter_context(patch.object(app.QStandardPaths, 'writableLocation', return_value=str(data)))
        stack.enter_context(patch.object(token_widget, 'TokenWindow', IsolatedWindow))
        mocks = [stack.enter_context(patch.object(app, name, side_effect=forbidden))
                 for name in ('Vault', 'Window', 'close_app', 'launch', 'require_stopped', 'FileSwitcher')]
        result = app.main(['--token-widget', '--codex-home', str(home)])
        for mock in mocks:
            mock.assert_not_called()
    assert len(windows) == 1 and windows[0].saw_snapshot
    assert windows[0].worker.stop_requested.is_set()
    assert not windows[0].worker.isRunning()
    assert auth.read_bytes() == b'isolated-auth-sentinel'
    assert accounts.read_bytes() == b'isolated-account-sentinel'
    return {'result': result, 'worker_stopped': True, 'account_files_unchanged': True}


def _singleton_helper(root, secondary):
    from PySide6.QtCore import QCoreApplication, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from jobs_codex_account_switcher import token_widget

    app = QCoreApplication([]) if secondary else QApplication([])
    windows = []

    class IsolatedWindow(token_widget.TokenWindow):
        def __init__(self, home):
            if secondary:
                raise AssertionError('第二实例不应创建窗口')
            settings = QSettings(str(root / 'singleton.ini'), QSettings.Format.IniFormat)
            super().__init__(home, settings, start_worker=False)
            self.woken = False
            windows.append(self)
            QTimer.singleShot(2000, self.close)
            print('primary-ready', flush=True)

        def refresh(self):
            self.woken = True
            QTimer.singleShot(0, self.close)

    with patch.object(token_widget, 'TokenWindow', IsolatedWindow):
        result = token_widget.run_token_widget(app, root / 'codex', root)
    if secondary:
        assert not windows
    else:
        assert len(windows) == 1 and windows[0].woken
    assert not list(root.rglob('accounts.enc'))
    assert not (root / 'codex').exists()
    return {'result': result, 'window_count': len(windows), 'secondary': secondary}


class TokenLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = _clean_environment(self.root)

    def helper_command(self, role):
        return [sys.executable, str(HELPER_FILE), '--lifecycle-helper', role, '--root', str(self.root)]

    def run_helper(self, role, timeout=5):
        process = subprocess.run(self.helper_command(role), env=self.env, cwd=PROJECT_ROOT,
                                 capture_output=True, text=True, timeout=timeout)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_source_and_frozen_hooks_return_before_any_qt_or_account_import(self):
        self.assertEqual(self.run_helper('hooks'), {
            'source': {'result': 0, 'qt_imported': False, 'frozen': False},
            'frozen': {'result': 0, 'qt_imported': False, 'frozen': True}})

    def test_standalone_mode_does_not_touch_accounts_or_codex_and_stops_real_worker(self):
        self.assertEqual(self.run_helper('standalone'),
                         {'result': 0, 'worker_stopped': True, 'account_files_unchanged': True})

    def test_second_process_wakes_existing_widget_without_another_window(self):
        deadline = time.monotonic() + 5
        first = subprocess.Popen(self.helper_command('singleton-primary'), env=self.env,
                                 cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(first.stdout.readline().strip()), daemon=True).start()
        try:
            self.assertEqual(ready.get(timeout=3), 'primary-ready')
            self.assertEqual(self.run_helper('singleton-secondary', max(0.1, deadline - time.monotonic())),
                             {'result': 0, 'window_count': 0, 'secondary': True})
            output, errors = first.communicate(timeout=max(0.1, deadline - time.monotonic()))
            self.assertEqual(first.returncode, 0, errors)
            self.assertEqual(json.loads(output),
                             {'result': 0, 'window_count': 1, 'secondary': False})
        finally:
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=3)
            first.stdout.close()
            first.stderr.close()


def _run_helper():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lifecycle-helper', required=True,
                        choices=('hooks', 'standalone', 'singleton-primary', 'singleton-secondary'))
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    if args.lifecycle_helper == 'hooks':
        result = {'source': _hook_helper(args.root, False), 'frozen': _hook_helper(args.root, True)}
    elif args.lifecycle_helper == 'standalone':
        result = _standalone_helper(args.root)
    else:
        result = _singleton_helper(args.root, args.lifecycle_helper == 'singleton-secondary')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    if '--lifecycle-helper' in sys.argv[1:]:
        _run_helper()
    else:
        unittest.main()
