"""独立 Token 浮窗；只读取用量，不打开账户库。Created by Jobs."""
from datetime import datetime
from dataclasses import replace
import hashlib
from pathlib import Path
import threading

from PySide6.QtCore import QLockFile, QPoint, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QVBoxLayout, QWidget)

from .appearance import apply_appearance
from .usage import UsageMonitor
from .labels import TitleIndex


def widget_identity(home):
    digest = hashlib.sha256(str(Path(home).expanduser().resolve()).encode()).hexdigest()[:20]
    return 'JobsCodexTokens-' + digest


class UsageWorker(QThread):
    snapshot_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.stop_requested = threading.Event()
        self.refresh_requested = threading.Event()

    def run(self):
        monitor = UsageMonitor(self.home, cancelled=self.stop_requested.is_set)
        titles = TitleIndex(self.home)
        while not self.stop_requested.is_set():
            try:
                snapshot = monitor.poll()
                turns = tuple(replace(turn, thread_title=titles.title(turn.session_id))
                              for turn in snapshot.turns)
                self.snapshot_ready.emit(replace(snapshot, turns=turns))
            except Exception as error:
                self.failed.emit(f'{type(error).__name__}：读取用量失败，可从菜单重新加载。')
            self.refresh_requested.wait(1.0)
            self.refresh_requested.clear()

    def stop(self):
        self.stop_requested.set()
        self.refresh_requested.set()


class DragHeader(QLabel):
    """拖动标题移动浮窗，数字区域仍可选择和复制。"""
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle and handle.startSystemMove():
                return
            self.offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, 'offset'):
            self.window().move(event.globalPosition().toPoint() - self.offset)
            event.accept()


def token_text(value):
    return '未知' if value is None else f'{value:,}'


def short_time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M:%S')
    except (ValueError, AttributeError):
        return value or '时间未知'


def turn_title(turn):
    return getattr(turn, 'thread_title', '') or f'对话 {turn.session_id[:8]}'


def turn_prompt(turn):
    return getattr(turn, 'user_prompt', '') or '这轮没有可读取的提问摘要'


class HistoryDialog(QDialog):
    """按标题和提问定位轮次，长历史在窗口内滚动。"""
    def __init__(self, turns, selected_key, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Codex · 对话用量记录')
        self.resize(640, 500)
        self.setMinimumSize(420, 320)
        self.turns = list(turns)
        self.selected_turn = None
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索对话标题或本轮提问')
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(6)
        layout.addWidget(self.list, 1)
        for turn in self.turns:
            item = QListWidgetItem(f'{turn_title(turn)}\n提问：{turn_prompt(turn)}\n'
                                   f'{short_time(turn.ended_at)} · ↑ {token_text(turn.input_tokens)}'
                                   f' / ↓ {token_text(turn.output_tokens)}')
            item.setData(Qt.ItemDataRole.UserRole, turn)
            item.setToolTip(f'对话：{turn.session_id}\n轮次：{turn.turn_id}\n{turn_prompt(turn)}')
            self.list.addItem(item)
            if (turn.session_id, turn.turn_id) == selected_key:
                self.list.setCurrentItem(item)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        self.empty_title = QLabel()
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_detail = QLabel()
        self.empty_detail.setWordWrap(True)
        self.empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_action = QPushButton()
        self.empty_action.clicked.connect(self.reload_or_clear)
        empty_layout.addStretch()
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_detail)
        empty_layout.addWidget(self.empty_action)
        empty_layout.addStretch()
        layout.addWidget(self.empty, 1)
        self.search.textChanged.connect(self.filter_turns)
        self.list.itemDoubleClicked.connect(lambda item: self.choose(item))
        choose = QPushButton('在浮窗显示所选轮次')
        choose.clicked.connect(lambda: self.choose(self.list.currentItem()))
        choose.setEnabled(bool(self.turns))
        layout.addWidget(choose)
        self.filter_turns('')

    def filter_turns(self, text):
        term = text.casefold().strip()
        visible = 0
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(term and term not in item.text().casefold()))
            visible += not item.isHidden()
        self.list.setVisible(bool(visible))
        self.empty.setVisible(not visible)
        self.empty_title.setText('没有匹配的对话轮次' if term else '等待第一轮对话完成')
        self.empty_detail.setText('换一个标题或提问关键词，或清空搜索查看所有记录。' if term
                                  else '完成的本地对话会自动出现在浮窗中，可重新加载后查看记录。')
        self.empty_action.setText('清空搜索' if term else '重新加载用量')

    def reload_or_clear(self):
        if self.search.text().strip():
            self.search.clear()
        else:
            if self.parent() and hasattr(self.parent(), 'refresh'):
                self.parent().refresh()
            self.reject()

    def choose(self, item):
        if item is not None and not item.isHidden():
            self.selected_turn = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class TokenWindow(QWidget):
    def __init__(self, home, settings=None, start_worker=True):
        super().__init__()
        self.home = Path(home).expanduser().resolve()
        self.settings = settings if settings is not None else QSettings('Jobs', 'CodexTokenWidget')
        self.turns = []
        self.selected_key = None
        self.worker = None
        self.setWindowTitle('Jobs · Codex Token')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.resize(390, 265)
        self.setMinimumSize(360, 250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = DragHeader('Codex · Token')
        title.setFont(QFont('', 12, QFont.Weight.DemiBold))
        header.addWidget(title, 1)
        self.menu_button = QPushButton('⋯')
        self.menu_button.setFixedWidth(30)
        self.menu_button.setAccessibleName('Token 控件菜单')
        self.menu_button.clicked.connect(self.show_menu)
        close = QPushButton('×')
        close.setFixedWidth(30)
        close.setAccessibleName('关闭 Token 控件')
        close.clicked.connect(self.close)
        header.addWidget(self.menu_button)
        header.addWidget(close)
        layout.addLayout(header)
        numbers = QHBoxLayout()
        self.input_value = self.add_counter(numbers, '↑ 上行 · 输入')
        self.output_value = self.add_counter(numbers, '↓ 下行 · 输出')
        layout.addLayout(numbers)
        self.conversation = QLabel('等待可识别的对话')
        self.conversation.setWordWrap(True)
        self.conversation.setTextFormat(Qt.TextFormat.PlainText)
        self.conversation.setFont(QFont('', 11, QFont.Weight.DemiBold))
        layout.addWidget(self.conversation)
        self.prompt = QLabel('对话结束后显示本轮提问摘要')
        self.prompt.setWordWrap(True)
        self.prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.prompt)
        self.details = QLabel('等待完成的对话轮次')
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.status = QLabel('正在接入 Codex 用量记录…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.theme = QComboBox(self)
        self.theme.addItems(['跟随系统', '白天', '黑夜'])
        self.theme.setCurrentIndex(int(self.settings.value('theme', 0)))
        self.theme.hide()
        self.theme.currentIndexChanged.connect(self.apply_theme)
        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.apply_theme()
        point = self.settings.value('position')
        if isinstance(point, QPoint):
            screens = QApplication.screens()
            if any(screen.availableGeometry().contains(point + QPoint(100, 30)) for screen in screens):
                self.move(point)
        else:
            screen = QApplication.primaryScreen()
            if screen:
                rect = screen.availableGeometry()
                self.move(rect.right() - self.width() - 28, rect.top() + 68)
        if start_worker:
            self.worker = UsageWorker(self.home, self)
            self.worker.snapshot_ready.connect(self.update_snapshot)
            self.worker.failed.connect(self.status.setText)
            self.worker.start()

    def add_counter(self, layout, label):
        column = QVBoxLayout()
        name = QLabel(label)
        value = QLabel('—')
        value.setFont(QFont('', 26, QFont.Weight.DemiBold))
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setToolTip('实际模型用量。输入包含历史上下文和缓存；输出可包含推理 Token。')
        column.addWidget(name)
        column.addWidget(value)
        layout.addLayout(column, 1)
        return value

    def apply_theme(self):
        mode = self.theme.currentIndex()
        self.settings.setValue('theme', mode)
        apply_appearance(mode)

    def update_snapshot(self, snapshot):
        previous = (self.turns[0].session_id, self.turns[0].turn_id) if self.turns else None
        self.turns = [turn for turn in snapshot.turns if not turn.is_subagent]
        latest = (self.turns[0].session_id, self.turns[0].turn_id) if self.turns else None
        if latest != previous:
            self.selected_key = latest
        chosen = next((turn for turn in self.turns
                       if (turn.session_id, turn.turn_id) == self.selected_key), None)
        if chosen is None and self.turns:
            chosen = self.turns[0]
            self.selected_key = (chosen.session_id, chosen.turn_id)
        if chosen:
            self.render_turn(chosen)
        else:
            self.input_value.setText('—')
            self.output_value.setText('—')
            self.conversation.setText('等待可识别的对话')
            self.prompt.setText('对话结束后显示本轮提问摘要')
            self.details.setText('暂无已结束的主对话 · 菜单可重新加载')
        if getattr(snapshot, 'loading', False):
            self.status.setText('正在加载近期记录 · 完成后持续监听')
        elif snapshot.errors:
            self.status.setText('部分记录读取失败 · 菜单可重新加载')
            self.status.setToolTip('\n'.join(snapshot.errors))
        else:
            self.status.setToolTip(str(self.home / 'sessions'))
            self.status.setText(f'正在监听 · {snapshot.active_count} 轮进行中' if snapshot.active_count
                                else '正在监听 · 对话结束后自动更新')

    def render_turn(self, turn):
        self.input_value.setText(token_text(turn.input_tokens))
        self.output_value.setText(token_text(turn.output_tokens))
        outcome = {'completed': '已完成', 'interrupted': '已中断', 'aborted': '已中断',
                   'unfinished': '结束未确认', 'failed': '失败'}.get(turn.status, turn.status)
        quality = '完整用量' if turn.complete else '部分 / 未知用量'
        title, prompt = turn_title(turn), turn_prompt(turn)
        self.conversation.setText(title[:44] + ('…' if len(title) > 44 else ''))
        self.conversation.setToolTip(title)
        self.prompt.setText('提问：' + prompt[:76] + ('…' if len(prompt) > 76 else ''))
        self.prompt.setToolTip(prompt)
        self.details.setText(f'{short_time(turn.ended_at)} · {outcome} · {quality}')
        self.details.setToolTip(f'对话：{turn.session_id}\n轮次：{turn.turn_id}\n'
                               f'缓存输入（已包含在上行）：{token_text(turn.cached_input_tokens)}\n'
                               f'推理输出（已包含在下行）：{token_text(turn.reasoning_output_tokens)}\n'
                               f'{turn.note}')

    def show_menu(self):
        menu = QMenu(self)
        menu.addAction('查看对话用量记录…', self.show_history)
        appearance = menu.addMenu('外观')
        for index, name in enumerate(['跟随系统', '白天', '黑夜']):
            action = appearance.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.theme.currentIndex() == index)
            action.triggered.connect(lambda checked=False, mode=index: self.theme.setCurrentIndex(mode))
        menu.addAction('重新加载用量', self.refresh)
        menu.addSeparator()
        menu.addAction('关闭悬浮控件', self.close)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def show_history(self):
        dialog = HistoryDialog(self.turns, self.selected_key, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_turn:
            self.select_turn(dialog.selected_turn)

    def select_turn(self, turn):
        self.selected_key = (turn.session_id, turn.turn_id)
        self.render_turn(turn)

    def refresh(self):
        if self.worker:
            self.worker.refresh_requested.set()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(5000):
                self.status.setText('正在结束读取，请稍候再关闭。')
                event.ignore()
                return
        self.settings.setValue('position', self.pos())
        event.accept()


def run_token_widget(app, home, data_dir):
    """每个数据目录只有一个浮窗，重复钩子只唤起现有控件。"""
    name = widget_identity(home)
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b'show')
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return 0
    lock = QLockFile(str(Path(data_dir) / (name + '.lock')))
    if not lock.tryLock(0):
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        return 1
    window = TokenWindow(home)

    def connected():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.readyRead.connect(lambda conn=connection: conn.readAll())
            connection.disconnected.connect(connection.deleteLater)
            window.show()
            window.raise_()
            window.refresh()

    server.newConnection.connect(connected)
    def shutdown():
        if window.worker:
            window.worker.stop()
            window.worker.wait(10000)

    app.aboutToQuit.connect(shutdown)
    window.show()
    result = app.exec()
    server.close()
    lock.unlock()
    return result

