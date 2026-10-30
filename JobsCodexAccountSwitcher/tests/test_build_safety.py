"""构建中的删除边界、依赖取消和入口发布保护。Created by Jobs."""
import importlib.util
import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load_script(name):
    """按文件位置加载构建辅助模块，测试不需要 Qt 或 PyInstaller。"""
    spec = importlib.util.spec_from_file_location('account_' + name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script('build')
shortcuts = load_script('artifact_shortcuts')


class BuildSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.outer = self.base / 'JobsCodexAccountSwitcher.py'
        self.root = self.outer / 'JobsCodexAccountSwitcher'
        self.root.mkdir(parents=True)
        self.root_patch = patch.object(build, 'ROOT', self.root)
        self.outer_patch = patch.object(build, 'OUTER', self.outer)
        self.root_patch.start()
        self.outer_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.outer_patch.stop()
        self.temp.cleanup()

    def test_only_fixed_dist_can_be_deleted(self):
        outside = self.base / 'keep'
        outside.mkdir()
        sentinel = outside / 'important.txt'
        sentinel.write_text('keep', encoding='utf-8')
        for unsafe in ('', self.outer, self.root, outside, self.outer / 'work'):
            with self.subTest(path=unsafe), self.assertRaises(RuntimeError):
                build.clean_dist(unsafe)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')

    def test_clone_directory_can_have_any_name_including_spaces_and_chinese(self):
        for name in ('JobsCodexAccountSwitcher', '我的监控 仓库'):
            with self.subTest(name=name):
                outer = self.base / name
                root = outer / 'JobsCodexAccountSwitcher'
                root.mkdir(parents=True)
                with patch.object(build, 'ROOT', root), patch.object(build, 'OUTER', outer):
                    dist = outer / 'dist'
                    dist.mkdir()
                    (dist / 'old.app').write_text('old', encoding='utf-8')
                    self.assertEqual(build.fixed_directory('dist'), dist)
                    self.assertEqual(build.fixed_directory('work'), outer / 'work')
                    build.clean_dist(dist)
                    self.assertFalse(dist.exists())
                    self.assertTrue(root.is_dir())

    def test_actual_script_location_anchors_output_after_renaming_clone(self):
        outer = self.base / 'Gitee 监控'
        script = outer / 'JobsCodexAccountSwitcher/scripts/build.py'
        script.parent.mkdir(parents=True)
        script.write_text(Path(build.__file__).read_text(encoding='utf-8'), encoding='utf-8')
        spec = importlib.util.spec_from_file_location('renamed_clone_build', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.OUTER, outer)
        self.assertEqual(module.fixed_directory('dist'), outer / 'dist')

    def test_layout_and_filesystem_root_are_rejected(self):
        with patch.object(build, 'OUTER', self.base):
            with self.assertRaises(RuntimeError):
                build.fixed_directory('dist')
        with patch.object(build, 'OUTER', Path('/')), patch.object(build, 'ROOT', Path('/JobsCodexAccountSwitcher')):
            with self.assertRaises(RuntimeError):
                build.fixed_directory('dist')
        with self.assertRaises(RuntimeError):
            build.fixed_directory('src')

    @unittest.skipIf(sys.platform == 'win32', 'Windows symlink privileges vary.')
    def test_linked_source_directory_is_rejected(self):
        outside = self.base / 'external-source'
        outside.mkdir()
        self.root.rmdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            build.fixed_directory('dist')

    @unittest.skipIf(sys.platform == 'win32', 'Windows symlink privileges vary.')
    def test_linked_dist_is_rejected_without_touching_target(self):
        outside = self.base / 'keep'
        outside.mkdir()
        (outside / 'important').write_text('keep', encoding='utf-8')
        (self.outer / 'dist').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            build.clean_dist(self.outer / 'dist')
        self.assertTrue((outside / 'important').is_file())
        with self.assertRaises(RuntimeError):
            shortcuts.clear_shortcuts(self.outer)

    @unittest.skipIf(sys.platform == 'win32', 'Windows symlink privileges vary.')
    def test_removing_dist_does_not_follow_internal_links(self):
        outside = self.base / 'keep'
        outside.mkdir()
        sentinel = outside / 'important'
        sentinel.write_text('keep', encoding='utf-8')
        dist = self.outer / 'dist'
        dist.mkdir()
        (dist / 'linked-app-framework').symlink_to(outside, target_is_directory=True)
        build.clean_dist(dist)
        self.assertFalse(dist.exists())
        self.assertTrue(sentinel.is_file())

    @unittest.skipIf(sys.platform == 'win32', 'Windows symlink privileges vary.')
    def test_work_symlink_is_rejected(self):
        outside = self.base / 'keep'
        outside.mkdir()
        (self.outer / 'work').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            build.fixed_directory('work')

    def test_cleanup_failure_stops(self):
        dist = self.outer / 'dist'
        dist.mkdir()
        with patch.object(build.shutil, 'rmtree', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                build.clean_dist(dist)
        self.assertTrue(dist.is_dir())

    def test_confirmation_rejects_space_eof_and_noninteractive(self):
        with patch.object(build.sys.stdin, 'isatty', return_value=True):
            with patch('builtins.input', return_value=' '), self.assertRaises(RuntimeError):
                build.confirm('test')
            with patch('builtins.input', side_effect=EOFError), self.assertRaises(RuntimeError):
                build.confirm('test')
        with patch.object(build.sys.stdin, 'isatty', return_value=False):
            with patch('builtins.input') as input_mock, self.assertRaises(RuntimeError):
                build.confirm('test')
            input_mock.assert_not_called()

    def test_current_healthy_interpreter_is_reused_without_writes(self):
        with redirect_stdout(io.StringIO()), patch.object(build, 'missing_requirements', return_value=[]):
            with patch.object(build.venv, 'EnvBuilder') as environment, patch.object(build, 'run') as run:
                self.assertEqual(build.bootstrap(), Path(sys.executable))
                environment.assert_not_called()
                run.assert_not_called()
                self.assertFalse((self.root / '.venv').exists())

    def test_windows_gbk_redirect_is_reconfigured_before_unicode_build_output(self):
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding='gbk', errors='strict')
        try:
            with patch.object(build.sys, 'platform', 'win32'), patch.object(build.sys, 'stdout', output):
                build.configure_output()
                with patch.object(build, 'missing_requirements', return_value=[]):
                    self.assertEqual(build.bootstrap(), Path(sys.executable))
            output.flush()
            self.assertIn('✔ 复用当前解释器', buffer.getvalue().decode('utf-8'))
        finally:
            output.close()

    def test_main_prepares_output_before_help_without_build_mutation(self):
        with redirect_stdout(io.StringIO()), patch.object(build.sys, 'argv', ['build.py', '--help']):
            with patch.object(build, 'configure_output') as configure, patch.object(build, 'build') as actual_build:
                with self.assertRaises(SystemExit) as raised:
                    build.main()
                self.assertEqual(raised.exception.code, 0)
                configure.assert_called_once()
                actual_build.assert_not_called()

    def test_child_python_output_is_utf8_for_redirected_build_logs(self):
        with patch.dict(build.os.environ, {'PYTHONIOENCODING': 'gbk', 'PYTHONUTF8': '0'}):
            child = build.child_environment()
            self.assertEqual(child['PYTHONIOENCODING'], 'utf-8')
            self.assertEqual(child['PYTHONUTF8'], '1')
            self.assertEqual(build.os.environ['PYTHONIOENCODING'], 'gbk')

    def test_dependency_cancellation_precedes_environment_creation(self):
        with patch.object(build, 'missing_requirements', return_value=['PySide6>=6.8,<7']):
            with patch.object(build, 'confirm', side_effect=RuntimeError('cancelled')):
                with patch.object(build.venv, 'EnvBuilder') as environment, patch.object(build, 'run') as run:
                    with self.assertRaises(RuntimeError):
                        build.bootstrap()
                    environment.assert_not_called()
                    run.assert_not_called()
        self.assertFalse((self.outer / 'dist').exists())

    def test_main_missing_dependencies_never_clear_old_dist_before_confirmation(self):
        dist = self.outer / 'dist'
        dist.mkdir()
        sentinel = dist / 'keep.dmg'
        sentinel.write_text('old', encoding='utf-8')
        with patch.object(build.sys, 'argv', ['build.py']), patch.object(build, 'show_intro'):
            with patch.object(build, 'confirm', side_effect=[None, RuntimeError('cancelled')]):
                with patch.object(build.logging, 'basicConfig'), patch.object(build, 'check_platform'):
                    with patch.object(build, 'missing_requirements', return_value=['PySide6>=6.8,<7']):
                        with patch.object(build, 'build') as actual_build, self.assertRaises(RuntimeError):
                            build.main()
                        actual_build.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'old')

    def test_unsupported_platform_is_rejected_before_build(self):
        with patch.object(build.sys, 'platform', 'linux'), self.assertRaisesRegex(RuntimeError, 'macOS 或 Windows'):
            build.check_platform()

    def test_pyinstaller_minimum_matches_project(self):
        requirement, minimum, _ = build.REQUIREMENTS['PyInstaller']
        self.assertEqual(requirement, 'PyInstaller>=6.10,<7')
        self.assertEqual(minimum, (6, 10))

    def test_direct_old_python_reports_version_requirement_before_tomllib_import(self):
        source = Path(build.__file__).read_text(encoding='utf-8')
        command = 'import sys; sys.version_info = (3, 9); exec(' + repr(source) + ')'
        result = subprocess.run([sys.executable, '-c', command], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('需要 Python 3.11+', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('tomllib', result.stderr)

    def test_dependency_install_failure_stops_before_recheck(self):
        python = self.root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        python.parent.mkdir(parents=True)
        python.touch()
        with patch.object(build, 'missing_requirements', return_value=['PySide6>=6.8,<7']) as probe:
            with patch.object(build, 'confirm'), patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                with patch.object(build, 'run', side_effect=[None, subprocess.CalledProcessError(1, 'pip')]):
                    with self.assertRaises(subprocess.CalledProcessError):
                        build.bootstrap()
        self.assertEqual(probe.call_count, 3)
        self.assertFalse((self.outer / 'dist').exists())

    def test_dependency_postinstall_failure_is_blocking(self):
        python = self.root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        python.parent.mkdir(parents=True)
        python.touch()
        with patch.object(build, 'missing_requirements', return_value=['PySide6>=6.8,<7']) as probe:
            with patch.object(build, 'confirm'), patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                with patch.object(build, 'run'), self.assertRaisesRegex(RuntimeError, '安装后依赖仍不可用'):
                    build.bootstrap()
        self.assertEqual(probe.call_count, 4)
        self.assertFalse((self.outer / 'dist').exists())

    @unittest.skipIf(sys.platform == 'win32', 'macOS relative-link behavior.')
    def test_relative_shortcuts_and_selective_cleanup(self):
        dist = self.outer / 'dist' / '2026.10.15 12-30-00'
        dist.mkdir(parents=True)
        app = dist / 'JobsCodexAccountSwitcher.app'
        app.mkdir()
        package = dist / 'JobsCodexAccountSwitcher.dmg'
        package.write_text('dmg', encoding='utf-8')
        unrelated = self.outer / 'Unrelated.app'
        unrelated.symlink_to(self.base / 'elsewhere', target_is_directory=True)
        real = self.outer / 'Keep.dmg'
        real.write_text('keep', encoding='utf-8')
        shortcuts.publish_shortcuts(self.outer, [app, package])
        entry = self.outer / app.name
        self.assertFalse(entry.readlink().is_absolute())
        self.assertEqual(entry.resolve(), app)
        shortcuts.clear_shortcuts(self.outer)
        self.assertFalse(entry.is_symlink())
        self.assertTrue(unrelated.is_symlink())
        self.assertEqual(real.read_text(encoding='utf-8'), 'keep')

    @unittest.skipIf(sys.platform == 'win32', 'macOS relative-link behavior.')
    def test_publication_preflights_all_name_collisions(self):
        dist = self.outer / 'dist' / '2026.10.15 12-30-00'
        dist.mkdir(parents=True)
        app = dist / 'JobsCodexAccountSwitcher.app'
        app.mkdir()
        package = dist / 'JobsCodexAccountSwitcher.dmg'
        package.write_text('new', encoding='utf-8')
        occupied = self.outer / package.name
        occupied.write_text('do not overwrite', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            shortcuts.publish_shortcuts(self.outer, [app, package])
        self.assertFalse((self.outer / app.name).is_symlink())
        self.assertEqual(occupied.read_text(encoding='utf-8'), 'do not overwrite')

    def test_windows_zip_contains_complete_onedir_runtime(self):
        output = self.outer / 'dist' / '2026.10.15 12-30-00'
        app_dir = output / 'JobsCodexAccountSwitcher'
        runtime = app_dir / '_internal'
        runtime.mkdir(parents=True)
        artifact = app_dir / 'JobsCodexAccountSwitcher.exe'
        artifact.write_bytes(b'MZtest')
        (runtime / 'runtime.dll').write_bytes(b'test')
        with patch.object(build.sys, 'platform', 'win32'):
            package = build.package_application(output, artifact, self.outer / 'work')
        with build.zipfile.ZipFile(package) as archive:
            self.assertIn('JobsCodexAccountSwitcher/JobsCodexAccountSwitcher.exe', archive.namelist())
            self.assertIn('JobsCodexAccountSwitcher/_internal/runtime.dll', archive.namelist())


    def test_prepared_mode_requires_healthy_dependencies_without_install_or_cleanup(self):
        dist = self.outer / 'dist'
        dist.mkdir()
        sentinel = dist / 'keep.dmg'
        sentinel.write_text('old', encoding='utf-8')
        with patch.object(build.sys, 'argv', ['build.py', '--prepared']):
            with patch.object(build, 'show_intro'), patch.object(build, 'confirm') as confirm:
                with patch.object(build.logging, 'basicConfig'), patch.object(build, 'check_platform'):
                    with patch.object(build, 'missing_requirements', return_value=['PySide6>=6.8,<7']):
                        with patch.object(build, 'bootstrap') as bootstrap, patch.object(build, 'build') as actual:
                            with self.assertRaisesRegex(RuntimeError, '--prepared 禁止安装'):
                                build.main()
                            bootstrap.assert_not_called()
                            actual.assert_not_called()
                            confirm.assert_called_once()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'old')

    def test_prepared_mode_reuses_current_interpreter_after_confirmation(self):
        with patch.object(build.sys, 'argv', ['build.py', '--prepared']):
            with patch.object(build, 'show_intro'), patch.object(build, 'confirm') as confirm:
                with patch.object(build.logging, 'basicConfig'), patch.object(build, 'check_platform'):
                    with patch.object(build, 'missing_requirements', return_value=[]):
                        with patch.object(build, 'bootstrap') as bootstrap, patch.object(build, 'build') as actual:
                            self.assertEqual(build.main(), 0)
                            bootstrap.assert_not_called()
                            actual.assert_called_once_with(Path(sys.executable))
                            confirm.assert_called_once()

    def test_fixed_dist_non_directory_is_rejected(self):
        target = self.outer / 'dist'
        target.write_text('keep', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            build.clean_dist(target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep')

    def test_windows_launch_has_no_monitor_or_account_switch_mode_argument(self):
        output = self.outer / 'dist' / '2026.10.18 12-30-00'
        artifact = output / build.NAME / (build.NAME + '.exe')
        with patch.object(build.sys, 'platform', 'win32'), patch.object(build.subprocess, 'Popen') as process:
            build.reveal_and_launch(output, artifact)
        self.assertEqual(process.call_args_list[0].args[0], ['explorer.exe', str(output)])
        self.assertEqual(process.call_args_list[1].args[0], [str(artifact)])
        self.assertEqual(process.call_args_list[1].kwargs['cwd'], artifact.parent)

    def test_spec_embeds_local_icon_and_stable_identity(self):
        icon = self.outer / 'icon.png'
        icon.write_bytes(b'local icon')
        work = self.outer / 'work'
        work.mkdir()
        with patch.object(build.sys, 'platform', 'darwin'):
            spec = build.write_spec(work, '0.3.0')
        text = spec.read_text(encoding='utf-8')
        tree = ast.parse(text)
        analysis = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'Analysis')
        self.assertEqual(ast.literal_eval(analysis.args[0]), [str(self.root / 'scripts/launch.py')])
        datas = next(keyword.value for keyword in analysis.keywords if keyword.arg == 'datas')
        self.assertEqual(ast.literal_eval(datas), [(str(icon), 'assets')])
        self.assertIn(build.NAME + '.app', text)
        self.assertNotIn('create_token_launcher', text)



    def test_child_environment_removes_host_qt_and_frozen_app_paths(self):
        incoming = {'QT_PLUGIN_PATH': '/old/app/plugins',
                    'QT_QPA_PLATFORM_PLUGIN_PATH': '/old/app/platforms',
                    '_PYI_APPLICATION_HOME_DIR': '/old/app', '_PYI_PARENT_PROCESS_LEVEL': '1',
                    'JOBS_KEEP_SETTING': 'unchanged'}
        with patch.dict(build.os.environ, incoming, clear=True):
            child = build.child_environment()
            self.assertNotIn('QT_PLUGIN_PATH', child)
            self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH', child)
            self.assertFalse(any(key.startswith('_PYI_') for key in child))
            self.assertEqual(child['JOBS_KEEP_SETTING'], 'unchanged')
            self.assertEqual(build.os.environ['QT_PLUGIN_PATH'], '/old/app/plugins')


if __name__ == '__main__':
    unittest.main()
