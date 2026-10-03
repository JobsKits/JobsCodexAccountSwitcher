"""跨平台账户管理窗口。Created by Jobs."""
import os
from pathlib import Path
import sys
import shutil
import tempfile

from PySide6.QtCore import QLockFile, QSettings, QStandardPaths, Qt, QTimer, QProcess, QProcessEnvironment, QThread, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QInputDialog, QMenu, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget, QHBoxLayout)

from .core import FileSwitcher, Vault, identity
from .platforms import discover_apps, launch, require_stopped, close_app


class SwitchWorker(QThread):
    completed = Signal(str)

    def __init__(self, engine, key, program, parent):
        super().__init__(parent)
        self.engine, self.key, self.program = engine, key, program

    def run(self):
        from .core import SwitchError
        try:
            close_app(self.program)
            self.engine.switch(self.key)
            launch(self.program)
            self.completed.emit('')
        except SwitchError as error:
            self.completed.emit(str(error))
        except Exception as error:
            self.completed.emit(f'{type(error).__name__}：自动切换未完成，请检查所选应用和当前账户。')


class Window(QWidget):
    def __init__(self, data_dir: Path, settings=None):
        super().__init__()
        self.data_dir = data_dir
        self.vault = None
        self.login_process = None
        self.switch_worker = None
        self.login_temp = None
        self.settings = settings if settings is not None else QSettings('Jobs', 'CodexAccountSwitcher')
        self.setWindowTitle('Jobs · Codex 账户切换器')
        self.resize(820, 610)
        self.setMinimumSize(650, 520)
        layout = QVBoxLayout(self)
        intro = QLabel('双击账户，自动关闭所选桌面应用、切换账户并重启。\n'
                       '首次添加仍需正常授权；切换后需在桌面应用核验账户。')
        intro.setWordWrap(True)
        heading = QHBoxLayout()
        heading.addWidget(intro, 1)
        self.appearance_slot = QHBoxLayout()
        heading.addLayout(self.appearance_slot)
        layout.addLayout(heading)
        form = QFormLayout()
        self.home = QLineEdit(self.settings.value('codex_home', os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
        form.addRow('Codex 数据目录', self.home)
        self.program = QLineEdit(self.settings.value('program', ''))
        if not self.program.text():
            apps = discover_apps()
            if apps:
                self.program.setText(str(apps[0]))
        program_row = QHBoxLayout()
        program_row.addWidget(self.program)
        choose = QPushButton('选择桌面应用')
        choose.clicked.connect(self.choose_program)
        program_row.addWidget(choose)
        form.addRow('重启的应用', program_row)
        candidate = shutil.which('codex') or ''
        if sys.platform == 'darwin':
            resources = Path(self.program.text()) / 'Contents/Resources'
            for relative in ('codex-cli/bin/codex', 'codex-cli/CodexCLI.app/Contents/MacOS/codex', 'codex-cli', 'codex'):
                bundled = resources / relative
                if bundled.is_file():
                    candidate = str(bundled)
                    break
        saved_cli = self.settings.value('cli', candidate)
        self.cli = QLineEdit(saved_cli if saved_cli and Path(saved_cli).is_file() else candidate)
        cli_row = QHBoxLayout()
        cli_row.addWidget(self.cli)
        cli_choose = QPushButton('选择 CLI 程序')
        cli_choose.clicked.connect(self.choose_cli)
        cli_row.addWidget(cli_choose)
        form.addRow('添加账户用 CLI', cli_row)
        self.theme = QComboBox()
        self.theme.addItems(['跟随系统', '白天', '黑夜'])
        self.theme.setCurrentIndex(int(self.settings.value('theme', 0)))
        self.theme.currentIndexChanged.connect(self.apply_theme)
        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.theme.setToolTip('外观：跟随系统 / 白天 / 黑夜')
        self.theme.setAccessibleName('外观')
        self.theme.setFixedWidth(120)
        self.appearance_slot.addWidget(self.theme, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(form)
        self.password_protection = QCheckBox('启用口令保护（可选；关闭后账户库为本地明文）')
        self.password_protection.setEnabled(False)
        self.password_protection.toggled.connect(self.change_protection)
        layout.addWidget(self.password_protection)
        unlock = QPushButton('打开 / 解锁账户库')
        unlock.clicked.connect(lambda: self.execute(self.unlock))
        layout.addWidget(unlock)
        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.account_menu)
        self.list.itemDoubleClicked.connect(lambda item: self.execute(lambda: self.switch_item(item)))
        self.list.setToolTip("双击账户切换；右键账户可切换或更改备注，右键空白处刷新缓存。")
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.list, 1)
        self.buttons = []
        for text, action in [('添加账户（独立浏览器授权）', self.enroll), ('保存当前登录账户', self.capture),
                             ('删除选中账户的本地保存', self.delete)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, fn=action: self.execute(fn))
            button.setEnabled(False)
            layout.addWidget(button)
            self.buttons.append(button)
        self.status = QLabel('默认免口令使用；已加密的旧账户库需要原口令解锁。')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.apply_theme()
        self.execute(self.open_without_password)

    def execute(self, action):
        """不展示底层异常正文，避免第三方异常包含凭据。"""
        from .core import SwitchError
        try:
            action()
        except SwitchError as error:
            QMessageBox.warning(self, '操作未完成', str(error))
        except Exception as error:
            QMessageBox.warning(self, '操作未完成', f'{type(error).__name__}：操作失败，未输出凭据。请检查当前登录账户，必要时完整退出 Codex 后选择原账户切换。')

    def engine(self):
        if not self.vault:
            from .core import SwitchError
            raise SwitchError('请先解锁账户库。')
        home = Path(self.home.text()).expanduser().absolute()
        self.settings.setValue('codex_home', str(home))
        self.settings.setValue('program', self.program.text())
        return FileSwitcher(home, self.vault, require_stopped)

    def selected(self):
        from .core import SwitchError
        item = self.list.currentItem()
        if not item:
            raise SwitchError('请选择账户。')
        return item.data(Qt.ItemDataRole.UserRole)

    def open_without_password(self):
        path = self.data_dir / 'accounts.enc'
        if Vault.needs_password(path):
            self.status.setText('该账户库已启用口令保护，请先解锁；解锁后可关闭口令保护。')
            return
        vault = Vault(path)
        if not path.exists():
            vault.save()
        self.activate_vault(vault)

    def activate_vault(self, vault):
        self.vault = vault
        for button in self.buttons:
            button.setEnabled(True)
        self.password_protection.setEnabled(True)
        self.sync_protection()
        self.refresh()

    def sync_protection(self):
        self.password_protection.blockSignals(True)
        self.password_protection.setChecked(bool(self.vault and self.vault.protected))
        self.password_protection.blockSignals(False)

    def unlock(self):
        if self.login_process:
            from .core import SwitchError
            raise SwitchError('先完成或取消正在进行的授权。')
        path = self.data_dir / 'accounts.enc'
        password = None
        if Vault.needs_password(path):
            password, ok = QInputDialog.getText(self, '解锁账户库', '输入原口令：', QLineEdit.EchoMode.Password)
            if not ok:
                return
        self.activate_vault(Vault(path, password))

    def change_protection(self, enabled):
        def change():
            from .core import SwitchError
            if self.login_process:
                raise SwitchError('先完成或取消正在进行的授权。')
            if not self.vault:
                raise SwitchError('先打开或解锁账户库。')
            password = None
            if enabled:
                password, ok = QInputDialog.getText(self, '启用口令保护', '设置口令（至少 12 字符）：', QLineEdit.EchoMode.Password)
                if not ok:
                    return
                again, ok = QInputDialog.getText(self, '确认口令', '再次输入：', QLineEdit.EchoMode.Password)
                if not ok:
                    return
                if password != again:
                    raise SwitchError('两次口令不一致。')
                if len(password) < 12:
                    raise SwitchError('启用口令保护时，口令至少 12 个字符。')
            self.vault.set_password(password)
            self.refresh()
        try:
            self.execute(change)
        finally:
            self.sync_protection()

    def refresh(self):
        self.list.clear()
        current = None
        home = Path(self.home.text()).expanduser()
        try:
            current = identity((home / 'auth.json').read_bytes())
        except Exception:
            pass
        if self.vault:
            for key, profile in self.vault.data['profiles'].items():
                suffix = ' · 当前缓存' if current and current['key'] == key else ''
                item = QListWidgetItem(f"{profile['label']} · {profile['email']}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.list.addItem(item)
            self.status.setText('账户库已打开。保存前请退出 Codex；双击切换会自动关闭并重启所选桌面应用。' +
                                '\n“当前缓存”仅代表本地文件身份，不代表桌面授权成功。' +
                                ('\n口令保护已开启。' if self.vault.protected else '\n口令保护未开启：账户库以明文保存。'))
        else:
            self.status.setText('账户库尚未解锁。检测到登录缓存。' if current else '账户库尚未解锁；未检测到受支持的登录缓存。')

    def choose_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 codex CLI / codex-cli 可执行程序')
        if path:
            self.cli.setText(path)

    def enroll(self):
        """用独立 CODEX_HOME 授权，不登出正在使用的桌面账户。"""
        from .core import SwitchError
        if self.login_process:
            raise SwitchError('已有浏览器授权进行中。')
        cli = Path(self.cli.text()).expanduser()
        if not cli.is_file() or (sys.platform == 'win32' and cli.suffix.lower() != '.exe'):
            raise SwitchError('请选择原生 Codex CLI 可执行程序；Windows 需要 .exe，不能选择 npm 的 .cmd。')
        label, ok = QInputDialog.getText(self, '添加账户', '账户备注')
        if not ok or not label.strip():
            return
        self.settings.setValue('cli', str(cli))
        self.login_temp = tempfile.TemporaryDirectory(prefix='enroll-', dir=self.data_dir)
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('CODEX_HOME', self.login_temp.name)
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'):
            env.remove(key)
        process.setProcessEnvironment(env)
        process.setProgram(str(cli))
        process.setArguments(['login', '-c', 'cli_auth_credentials_store="file"'])
        process.readyReadStandardOutput.connect(lambda: process.readAllStandardOutput())
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, state: self.finish_enrollment(label, code))
        process.errorOccurred.connect(lambda _: self.finish_enrollment(label, -1)
                                     if process.state() == QProcess.ProcessState.NotRunning else None)
        self.login_process = process
        self.status.setText('浏览器授权进行中，请选择正确账户；关闭窗口可取消。授权输出不记录到日志。')
        for button in self.buttons:
            button.setEnabled(False)
        process.start()

    def finish_enrollment(self, label, code):
        if not self.login_process:
            return
        process = self.login_process
        self.login_process = None
        def finish():
            from .core import SwitchError
            try:
                if code != 0:
                    raise SwitchError('浏览器授权未完成或 CLI 启动失败；当前桌面登录文件未修改。')
                raw = (Path(self.login_temp.name) / 'auth.json').read_bytes()
                self.vault.capture(label, raw)
            finally:
                self.login_temp.cleanup()
                self.login_temp = None
                process.deleteLater()
                for button in self.buttons:
                    button.setEnabled(True)
                self.refresh()
        self.execute(finish)

    def closeEvent(self, event):
        if self.switch_worker:
            event.ignore()
            return
        if self.login_process:
            if QMessageBox.question(self, '取消授权', '浏览器授权仍在进行，取消本次添加并关闭工具？') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.login_process.kill()
            self.login_process.waitForFinished(3000)
            if self.login_temp:
                self.login_temp.cleanup()
        event.accept()

    def account_menu(self, position):
        """右键命中的账户作为操作对象，空白处不显示菜单。"""
        item = self.list.itemAt(position)
        if self.login_process:
            return
        if item is None:
            menu = QMenu(self)
            action = menu.addAction('刷新缓存状态')
            action.triggered.connect(lambda checked=False: self.execute(self.refresh))
            menu.exec(self.list.viewport().mapToGlobal(position))
            return
        if self.vault is None:
            return
        self.list.setCurrentItem(item)
        key = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        switch_action = menu.addAction('切换账户并启动 Codex')
        switch_action.triggered.connect(lambda checked=False: self.execute(lambda: self.switch_item(item)))
        action = menu.addAction('更改备注')
        action.triggered.connect(lambda checked=False: self.execute(lambda: self.rename_account(key)))
        menu.addSeparator()
        refresh_action = menu.addAction('刷新缓存状态')
        refresh_action.triggered.connect(lambda checked=False: self.execute(self.refresh))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def switch_item(self, item):
        """双击和右键使用同一账户目标及原有切换确认。"""
        from .core import SwitchError
        if self.login_process:
            raise SwitchError('先完成或取消正在进行的授权。')
        if not self.vault or item.listWidget() is not self.list:
            raise SwitchError('请选择已打开账户库中的账户。')
        self.list.setCurrentItem(item)
        self.switch()

    def rename_account(self, key):
        from .core import SwitchError
        if self.login_process:
            raise SwitchError('先完成或取消正在进行的授权。')
        if not self.vault or key not in self.vault.data['profiles']:
            raise SwitchError('目标账户不存在或账户库未解锁。')
        old = self.vault.data['profiles'][key]['label']
        label, ok = QInputDialog.getText(self, '更改备注', '账户备注', text=old)
        if not ok:
            return
        self.vault.rename(key, label)
        self.refresh()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentItem(item)
                break

    def capture(self):
        label, ok = QInputDialog.getText(self, '保存当前账户', '账户备注')
        if ok:
            self.engine().capture(label)
            self.refresh()

    def switch(self):
        from .core import SwitchError
        if self.switch_worker:
            raise SwitchError('账户切换正在进行。')
        key = self.selected()
        program = Path(self.program.text()).expanduser()
        if not program.exists() or program.suffix.lower() not in ('.app', '.exe'):
            raise SwitchError('先选择有效的桌面应用路径。')
        engine = self.engine()
        engine.check_storage()
        who = identity(engine.auth.read_bytes())
        if who['key'] == key:
            raise SwitchError('当前缓存已经是该账户，无需切换。')
        if who['key'] not in self.vault.data['profiles']:
            raise SwitchError('请先保存当前账户。')
        if QMessageBox.question(self, '切换账户', '将自动关闭所选 ChatGPT / Codex、保存最新登录态、切换账户并重启。\n正在运行的任务会被中断；应用无法正常退出时会清理其残留进程。继续？') != QMessageBox.StandardButton.Yes:
            return
        worker = SwitchWorker(engine, key, program, self)
        self.switch_worker = worker
        self.setEnabled(False)
        self.status.setText('正在关闭桌面应用、切换账户并重启，请稍候…')
        def complete(error):
            self.setEnabled(True)
            self.refresh()
            if error:
                QMessageBox.warning(self, '切换未完成', error)
        worker.completed.connect(complete)
        worker.finished.connect(self.switch_finished)
        worker.start()

    def switch_finished(self):
        worker = self.switch_worker
        self.switch_worker = None
        if worker:
            worker.deleteLater()

    def delete(self):
        from .core import SwitchError
        key = self.selected()
        if QMessageBox.question(self, '删除保存', '只删除本工具账户库内的保存，不登出或撤销账户。继续？') == QMessageBox.StandardButton.Yes:
            del self.vault.data['profiles'][key]
            self.vault.save()
            self.refresh()

    def choose_program(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Codex 桌面应用', '/Applications' if sys.platform == 'darwin' else '',
                                             '应用 (*.app)' if sys.platform == 'darwin' else '应用 (*.exe)')
        if path:
            self.program.setText(path)

    def apply_theme(self):
        """Fusion 与统一 Palette 覆盖原生弹窗、列表、禁用态及选择颜色。"""
        mode = self.theme.currentIndex()
        self.settings.setValue('theme', mode)
        dark = mode == 2 or (mode == 0 and QApplication.instance().styleHints().colorScheme() == Qt.ColorScheme.Dark)
        palette = QPalette()
        roles = QPalette.ColorRole
        colors = {roles.Window: '#202124' if dark else '#f5f6f8',
                  roles.WindowText: '#f1f3f4' if dark else '#202124',
                  roles.Base: '#292b2f' if dark else '#ffffff',
                  roles.AlternateBase: '#34373d' if dark else '#eef1f5',
                  roles.Text: '#f1f3f4' if dark else '#202124',
                  roles.Button: '#34373d' if dark else '#e7eaf0',
                  roles.ButtonText: '#f1f3f4' if dark else '#202124',
                  roles.Highlight: '#3977bc', roles.HighlightedText: '#ffffff',
                  roles.ToolTipBase: '#292b2f' if dark else '#ffffff',
                  roles.ToolTipText: '#f1f3f4' if dark else '#202124',
                  roles.PlaceholderText: '#aab0b8' if dark else '#626975',
                  roles.Light: '#565b65' if dark else '#ffffff',
                  roles.Mid: '#41454d' if dark else '#bac1cc',
                  roles.Dark: '#151619' if dark else '#8b929e',
                  roles.Shadow: '#111111' if dark else '#646a74',
                  roles.Link: '#91c3ff' if dark else '#155ca8'}
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        for role in (roles.Text, roles.WindowText, roles.ButtonText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#969aa4' if dark else '#727987'))
        QApplication.instance().setPalette(palette)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setOrganizationName('Jobs')
    app.setApplicationName('CodexAccountSwitcher')
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / 'instance.lock'))
    if not lock.tryLock(0):
        QMessageBox.warning(None, '已有实例', '账户切换器已经运行。')
        return 1
    window = Window(data_dir)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
