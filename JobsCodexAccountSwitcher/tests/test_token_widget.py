"""隔离验证独立浮窗、轮次选择、未知值与三态主题。Created by Jobs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from jobs_codex_account_switcher.token_widget import HistoryDialog, TokenWindow, token_text, widget_identity


def turn(key='one', **values):
    data = dict(session_id='thread-12345', turn_id=key, input_tokens=3210,
                output_tokens=876, cached_input_tokens=2000,
                reasoning_output_tokens=400, ended_at='2026-10-10T04:23:00Z',
                status='completed', complete=True, is_subagent=False, note='')
    data.update(values)
    return SimpleNamespace(**data)


def snapshot(*turns, **values):
    return SimpleNamespace(turns=turns, active_count=values.get('active_count', 0),
                           errors=values.get('errors', ()), file_count=1)


class TokenWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / 'tokens.ini'), QSettings.Format.IniFormat)
        self.window = TokenWindow(self.root / 'codex', self.settings, start_worker=False)

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_unknown_is_not_zero_and_no_account_file(self):
        self.window.update_snapshot(snapshot(turn(input_tokens=None, output_tokens=None, complete=False)))
        self.assertEqual(self.window.input_value.text(), '未知')
        self.assertEqual(self.window.output_value.text(), '未知')
        self.assertIn('部分 / 未知', self.window.details.text())
        self.assertEqual(token_text(0), '0')
        self.assertFalse(list(self.root.rglob('accounts.enc')))

    def test_render_actual_counts_without_adding_cached_or_reasoning_again(self):
        self.window.update_snapshot(snapshot(turn()))
        self.assertEqual(self.window.input_value.text(), '3,210')
        self.assertEqual(self.window.output_value.text(), '876')
        self.assertIn('2,000', self.window.details.toolTip())
        self.assertIn('400', self.window.details.toolTip())
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_new_turn_selects_latest_and_history_selection_persists(self):
        a, b = turn('one'), turn('two', input_tokens=12)
        self.window.update_snapshot(snapshot(b, a))
        self.assertEqual(self.window.input_value.text(), '12')
        self.window.select_turn(a)
        self.window.update_snapshot(snapshot(b, a, active_count=1))
        self.assertEqual(self.window.input_value.text(), '3,210')
        self.assertIn('1 轮进行中', self.window.status.text())
        self.window.update_snapshot(snapshot(turn('three', input_tokens=15), b, a))
        self.assertEqual(self.window.input_value.text(), '15')

    def test_subagents_do_not_replace_main_turn(self):
        self.window.update_snapshot(snapshot(turn('agent', is_subagent=True, input_tokens=999), turn()))
        self.assertEqual(len(self.window.turns), 1)
        self.assertEqual(self.window.input_value.text(), '3,210')

    def test_empty_and_retry_are_visible(self):
        self.window.update_snapshot(snapshot())
        self.assertEqual(self.window.input_value.text(), '—')
        self.assertIn('重新加载', self.window.details.text())
        self.window.update_snapshot(snapshot(errors=('测试读取失败',)))
        self.assertIn('读取失败', self.window.status.text())
        self.assertEqual(self.window.status.toolTip(), '测试读取失败')

    def test_theme_reopen_and_system_changes(self):
        self.window.theme.setCurrentIndex(2)
        dark = self.app.palette().color(QPalette.ColorRole.Window)
        self.window.theme.setCurrentIndex(1)
        light = self.app.palette().color(QPalette.ColorRole.Window)
        self.assertNotEqual(dark, light)
        self.window.theme.setCurrentIndex(0)
        with patch.object(self.app.styleHints(), 'colorScheme', return_value=Qt.ColorScheme.Dark):
            self.window.apply_theme()
        self.assertEqual(dark, self.app.palette().color(QPalette.ColorRole.Window))
        self.window.theme.setCurrentIndex(2)
        self.window.close()
        reopened = TokenWindow(self.root / 'codex', self.settings, start_worker=False)
        self.assertEqual(reopened.theme.currentIndex(), 2)
        reopened.close()

    def test_home_identity_is_stable_and_independent(self):
        self.assertEqual(widget_identity(self.root), widget_identity(self.root / '.'))
        self.assertNotEqual(widget_identity(self.root), widget_identity(self.root / 'other'))

    def test_updated_same_turn_refreshes_numbers(self):
        self.window.update_snapshot(snapshot(turn(input_tokens=None, complete=False)))
        self.window.update_snapshot(snapshot(turn(input_tokens=9876)))
        self.assertEqual(self.window.input_value.text(), '9,876')
        self.assertIn('完整用量', self.window.details.text())

    def test_title_and_request_identify_round_without_short_id_lookup(self):
        self.window.update_snapshot(snapshot(turn(thread_title='账户工具升级', user_prompt='增加每轮 Token 统计')))
        self.assertEqual(self.window.conversation.text(), '账户工具升级')
        self.assertIn('增加每轮 Token 统计', self.window.prompt.text())
        self.assertEqual(self.window.prompt.textFormat(), Qt.TextFormat.PlainText)

    def test_history_search_and_selection_distinguish_rounds_in_same_chat(self):
        a = turn('one', thread_title='工具升级', user_prompt='打开 Token 浮窗')
        b = turn('two', thread_title='工具升级', user_prompt='在回复里显示统计')
        dialog = HistoryDialog([b, a], (a.session_id, a.turn_id), self.window)
        self.assertEqual(dialog.list.count(), 2)
        dialog.search.setText('回复')
        self.assertFalse(dialog.list.item(0).isHidden())
        self.assertTrue(dialog.list.item(1).isHidden())
        dialog.choose(dialog.list.item(0))
        self.assertEqual(dialog.selected_turn.turn_id, 'two')
        dialog.close()

    def test_history_empty_and_no_match_have_recovery_actions(self):
        dialog = HistoryDialog([turn(user_prompt='统计')], None, self.window)
        dialog.search.setText('不存在的请求')
        self.assertFalse(dialog.empty.isHidden())
        self.assertEqual(dialog.empty_action.text(), '清空搜索')
        dialog.reload_or_clear()
        self.assertTrue(dialog.empty.isHidden())
        dialog.close()
        empty = HistoryDialog([], None, self.window)
        self.assertEqual(empty.empty_action.text(), '重新加载用量')
        with patch.object(self.window, 'refresh') as refresh:
            empty.reload_or_clear()
            refresh.assert_called_once()
        empty.close()

