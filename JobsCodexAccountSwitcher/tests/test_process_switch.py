"""自动退出流程只使用模拟进程，不关闭开发宿主。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jobs_codex_account_switcher import platforms
from jobs_codex_account_switcher.app import SwitchWorker
from jobs_codex_account_switcher.core import SwitchError


class ProcessSwitchTests(unittest.TestCase):
    def test_unrelated_names_do_not_block(self):
        processes = [SimpleNamespace(pid=123, info={'pid': 123, 'name': name})
                     for name in ('ChatGPT for Chrome', 'Codex Computer Use')]
        with patch.object(platforms.psutil, 'process_iter', return_value=processes):
            platforms.require_stopped()

    def test_cli_blocks_with_pid(self):
        process = SimpleNamespace(pid=123, info={'pid': 123, 'name': 'codex'})
        with patch.object(platforms.psutil, 'process_iter', return_value=[process]):
            with self.assertRaisesRegex(SwitchError, 'PID 123'):
                platforms.require_stopped()

    def test_worker_order(self):
        order = []
        engine = Mock()
        engine.switch.side_effect = lambda key: order.append('switch')
        worker = SwitchWorker(engine, 'target', Path('/Applications/ChatGPT.app'), None)
        with patch('jobs_codex_account_switcher.app.close_app', side_effect=lambda path: order.append('close')), patch('jobs_codex_account_switcher.app.launch', side_effect=lambda path: order.append('launch')):
            worker.run()
        self.assertEqual(order, ['close', 'switch', 'launch'])

    def test_failed_close_never_writes_or_launches(self):
        engine = Mock()
        worker = SwitchWorker(engine, 'target', Path('/Applications/ChatGPT.app'), None)
        with patch('jobs_codex_account_switcher.app.close_app', side_effect=SwitchError('still running')), patch('jobs_codex_account_switcher.app.launch') as launch:
            worker.run()
        engine.switch.assert_not_called()
        launch.assert_not_called()
