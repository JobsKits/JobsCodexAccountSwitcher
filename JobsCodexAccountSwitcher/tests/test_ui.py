"""离屏验证外观和窗口，不写真实账户与偏好。Created by Jobs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
import sys
import time
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from jobs_codex_account_switcher.app import Window


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.temp.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_three_modes_persistence_and_disabled_roles(self):
        window = Window(Path(self.temp.name), QSettings(str(Path(self.temp.name) / 'ui.ini'), QSettings.Format.IniFormat))
        for mode in (1, 2, 0):
            window.theme.setCurrentIndex(mode)
            self.app.processEvents()
            palette = self.app.palette()
            self.assertNotEqual(palette.color(QPalette.ColorRole.Base), palette.color(QPalette.ColorRole.Text))
            self.assertNotEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text),
                                palette.color(QPalette.ColorRole.Base))
        window.theme.setCurrentIndex(2)
        window.close()
        second = Window(Path(self.temp.name), QSettings(str(Path(self.temp.name) / 'ui.ini'), QSettings.Format.IniFormat))
        self.assertEqual(second.theme.currentIndex(), 2)
        second.close()

    @unittest.skipIf(sys.platform == 'win32', 'Windows 原生 QProcess 授权需在 Windows 本机验收')
    def test_isolated_enrollment_saves_and_cleans_temp(self):
        from test_core import auth
        from jobs_codex_account_switcher.core import Vault
        root = Path(self.temp.name)
        cli = root / 'fake-codex'
        raw = auth('new-account')
        cli.write_text('#!' + sys.executable + '\nimport os,pathlib\npathlib.Path(os.environ["CODEX_HOME"],"auth.json").write_bytes(' + repr(raw) + ')\n')
        cli.chmod(0o700)
        window = Window(root, QSettings(str(root / 'enrollment-ui.ini'), QSettings.Format.IniFormat))
        window.vault = Vault(root / 'enrollment.enc', 'enrollment-pass-123456')
        window.cli.setText(str(cli))
        with patch('jobs_codex_account_switcher.app.QInputDialog.getText', return_value=('新增账户', True)):
            window.enroll()
        deadline = time.monotonic() + 5
        while window.login_process and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(window.login_process)
        self.assertIsNone(window.login_temp)
        self.assertEqual(len(window.vault.data['profiles']), 1)
        self.assertFalse(list(root.glob('enroll-*')))
        window.close()

    def test_no_password_default_and_enable_disable(self):
        from jobs_codex_account_switcher.core import Vault
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat)
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText') as prompt:
                window = Window(root, settings)
                prompt.assert_not_called()
            self.assertIsNotNone(window.vault)
            self.assertFalse(window.password_protection.isChecked())
            self.assertTrue(all(button.isEnabled() for button in window.buttons))
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText', side_effect=[('ui-password-123456', True), ('ui-password-123456', True)]):
                window.password_protection.setChecked(True)
            self.assertTrue(window.vault.protected)
            window.close()
            locked = Window(root, settings)
            self.assertIsNone(locked.vault)
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText', return_value=('ui-password-123456', True)):
                locked.unlock()
            self.assertTrue(locked.password_protection.isChecked())
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText') as prompt:
                locked.password_protection.setChecked(False)
                prompt.assert_not_called()
            self.assertFalse(Vault.needs_password(root / 'accounts.enc'))
            locked.close()
            reopened = Window(root, settings)
            self.assertFalse(reopened.vault.protected)
            reopened.close()

    def test_cancel_enabling_restores_checkbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            window = Window(root, QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat))
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText', return_value=('', False)):
                window.password_protection.setChecked(True)
            self.assertFalse(window.password_protection.isChecked())
            self.assertFalse(window.vault.protected)
            window.close()

    def test_rename_updates_selected_account_and_cancel_keeps_label(self):
        from test_core import auth
        from PySide6.QtCore import Qt
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            window = Window(root, QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat))
            key = window.vault.capture('原备注', auth('rename-test'))
            window.refresh()
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText', return_value=('新备注', True)):
                window.rename_account(key)
            self.assertEqual(window.list.currentItem().data(Qt.ItemDataRole.UserRole), key)
            self.assertTrue(window.list.currentItem().text().startswith('新备注'))
            with patch('jobs_codex_account_switcher.app.QInputDialog.getText', return_value=('取消备注', False)):
                window.rename_account(key)
            self.assertEqual(window.vault.data['profiles'][key]['label'], '新备注')
            window.close()

    def test_double_click_and_context_menu_switch_target(self):
        from test_core import auth
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMenu, QPushButton
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            window = Window(root, QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat))
            a = window.vault.capture('A', auth('switch-a'))
            b = window.vault.capture('B', auth('switch-b'))
            window.refresh()
            window.show()
            self.app.processEvents()
            first, second = window.list.item(0), window.list.item(1)
            window.list.setCurrentItem(first)
            with patch.object(window, 'switch') as switch:
                window.list.itemDoubleClicked.emit(second)
                switch.assert_called_once()
            self.assertEqual(window.selected(), b)
            window.list.setCurrentItem(first)
            def choose_switch(menu, point):
                self.assertEqual([a.text() for a in menu.actions()], ['切换账户并启动 Codex', '更改备注', '', '刷新缓存状态'])
                menu.actions()[0].trigger()
            menu = QMenu(window)
            with patch('jobs_codex_account_switcher.app.QMenu', return_value=menu), patch.object(menu, 'exec', lambda point: choose_switch(menu, point)), patch.object(window, 'switch') as switch:
                window.account_menu(window.list.visualItemRect(second).center())
                switch.assert_called_once()
            self.assertEqual(window.selected(), b)
            self.assertNotIn('切换选中账户并启动 Codex', [button.text() for button in window.findChildren(QPushButton)])
            window.close()

    def test_blank_menu_refresh_and_many_accounts_scroll(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QMenu, QPushButton
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            window = Window(root, QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat))
            window.show()
            self.app.processEvents()
            menu = QMenu(window)
            def choose(point):
                self.assertEqual([action.text() for action in menu.actions()], ['刷新缓存状态'])
                menu.actions()[0].trigger()
            with patch('jobs_codex_account_switcher.app.QMenu', return_value=menu), patch.object(menu, 'exec', choose), patch.object(window, 'refresh') as refresh:
                window.account_menu(QPoint(10, 10))
                refresh.assert_called_once()
            window.list.addItems([f'测试账户 {i}' for i in range(100)])
            self.app.processEvents()
            bar = window.list.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            window.list.scrollToBottom()
            self.assertEqual(bar.value(), bar.maximum())
            window.list.scrollToTop()
            self.assertEqual(bar.value(), 0)
            buttons = [button.text() for button in window.findChildren(QPushButton)]
            for removed in ('回滚上次切换', '已在桌面核验成功，完成切换', '刷新缓存状态'):
                self.assertNotIn(removed, buttons)
            window.close()

    def test_visual_preview(self):
        window = Window(Path(self.temp.name), QSettings(str(Path(self.temp.name) / 'ui.ini'), QSettings.Format.IniFormat))
        window.show()
        for mode, name in ((1, 'light'), (2, 'dark')):
            window.theme.setCurrentIndex(mode)
            self.app.processEvents()
            target = os.environ.get('JOBS_UI_PREVIEW')
            if target:
                window.grab().save(str(Path(target) / (name + '.png')))
        window.close()


if __name__ == '__main__':
    unittest.main()
