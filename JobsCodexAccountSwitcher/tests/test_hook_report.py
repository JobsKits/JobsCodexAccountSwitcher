"""Stop 同轮提示及输入、路径边界；只使用临时虚拟日志。Created by Jobs."""
from contextlib import redirect_stdout
import ctypes
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from jobs_codex_account_switcher import hook_report
from jobs_codex_account_switcher.usage import TurnUsage


class HookReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'codex'
        self.sessions = self.home / 'sessions'
        self.sessions.mkdir(parents=True)
        self.transcript = self.sessions / 'turn.jsonl'
        self.transcript.write_bytes(b'')
        self.request = {'hook_event_name': 'Stop', 'session_id': 'session-one',
                        'turn_id': 'turn-one', 'transcript_path': str(self.transcript),
                        'last_assistant_message': 'PRIVATE ASSISTANT BODY'}
        self.turn = TurnUsage('session-one', 'turn-one', 1234, 56, 1000, 21,
                              '', 'active', True)

    def tearDown(self):
        self.temp.cleanup()

    def report(self, request=None, *, stream=None, turn=None, error=None, home=None):
        if stream is None:
            stream = io.BytesIO(json.dumps(request or self.request).encode('utf-8'))
        output = io.StringIO()
        with patch.object(hook_report.usage, 'read_turn_usage',
                          return_value=self.turn if turn is None else turn,
                          side_effect=error) as read_usage, redirect_stdout(output):
            result = hook_report.report_from_stdin(home or self.home, stream)
        self.assertEqual(result, 0)
        text = output.getvalue()
        document = json.loads(text)
        self.assertEqual(set(document), {'systemMessage'})
        self.assertEqual(len(text.splitlines()), 1)
        self.assertNotIn('PRIVATE ASSISTANT BODY', text)
        self.assertNotIn(str(self.home), text)
        return document['systemMessage'], read_usage

    def assert_unknown(self, request=None, **kwargs):
        message, read_usage = self.report(request, **kwargs)
        self.assertEqual(message, hook_report.UNKNOWN_MESSAGE)
        return read_usage

    def test_complete_usage_includes_both_directions_without_double_counting_reasoning(self):
        message, read_usage = self.report()
        self.assertEqual(message, '本轮 Token（主代理）：上行 1,234，下行 56，合计 1,290。')
        read_usage.assert_called_once_with(self.home.resolve(), self.transcript.resolve(),
                                           'session-one', 'turn-one')

    def test_complete_zero_and_subagent_are_explicit(self):
        turn = replace(self.turn, input_tokens=0, output_tokens=0, is_subagent=True)
        message, _ = self.report(turn=turn)
        self.assertEqual(message, '本轮 Token（子代理）：上行 0，下行 0，合计 0。')

    def test_missing_or_partial_usage_is_unknown(self):
        self.assert_unknown(turn=replace(self.turn, complete=False))
        self.assert_unknown(turn=replace(self.turn, input_tokens=None))
        self.assert_unknown(turn=replace(self.turn, output_tokens=True))
        self.assert_unknown(turn=replace(self.turn, output_tokens=-1))
        self.assert_unknown(error=RuntimeError('PRIVATE ASSISTANT BODY ' + str(self.home)))
        output = io.StringIO()
        with patch.object(hook_report.usage, 'read_turn_usage', return_value=None), \
                redirect_stdout(output):
            self.assertEqual(hook_report.report_from_stdin(
                self.home, io.StringIO(json.dumps(self.request))), 0)
        self.assertEqual(json.loads(output.getvalue()), {'systemMessage': hook_report.UNKNOWN_MESSAGE})

    def test_invalid_event_ids_and_path_never_read_usage(self):
        for field, value in (('hook_event_name', 'SubagentStop'), ('session_id', ''),
                             ('session_id', 'x' * 257), ('turn_id', 'bad\nvalue'),
                             ('turn_id', 123), ('transcript_path', None),
                             ('transcript_path', 'sessions/turn.jsonl'),
                             ('transcript_path', str(self.home / 'auth.json')),
                             ('transcript_path', str(self.sessions)),
                             ('transcript_path', str(self.sessions / 'absent.jsonl'))):
            with self.subTest(field=field, value=value):
                read_usage = self.assert_unknown(dict(self.request, **{field: value}))
                read_usage.assert_not_called()

    def test_nested_regular_session_file_is_allowed(self):
        nested = self.sessions / '2026' / '10' / '10' / 'turn.jsonl'
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b'')
        _, read_usage = self.report(dict(self.request, transcript_path=str(nested)))
        self.assertEqual(read_usage.call_args.args[1], nested.resolve())

    def test_parent_traversal_and_prefix_neighbor_are_rejected(self):
        neighbor = self.home / 'sessions-copy'
        neighbor.mkdir()
        outside = neighbor / 'turn.jsonl'
        outside.write_bytes(b'PRIVATE ASSISTANT BODY')
        for path in (outside, self.sessions / '..' / 'sessions' / 'turn.jsonl'):
            with self.subTest(path=path):
                self.assert_unknown(dict(self.request, transcript_path=str(path))).assert_not_called()

    def test_symlink_file_and_nested_directory_are_rejected(self):
        outside = Path(self.temp.name) / 'private.jsonl'
        outside.write_bytes(b'PRIVATE ASSISTANT BODY')
        file_link = self.sessions / 'file-link.jsonl'
        dir_link = self.sessions / 'dir-link'
        try:
            file_link.symlink_to(outside)
            dir_link.symlink_to(outside.parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        for path in (file_link, dir_link / outside.name):
            with self.subTest(path=path):
                self.assert_unknown(dict(self.request, transcript_path=str(path))).assert_not_called()

    def test_sessions_symlink_is_rejected(self):
        elsewhere = Path(self.temp.name) / 'elsewhere'
        elsewhere.mkdir()
        target = elsewhere / 'turn.jsonl'
        target.write_bytes(b'')
        self.transcript.unlink()
        self.sessions.rmdir()
        try:
            self.sessions.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.assert_unknown().assert_not_called()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO unavailable')
    def test_fifo_is_rejected_without_opening_or_waiting(self):
        pipe = self.sessions / 'pipe.jsonl'
        os.mkfifo(pipe)
        self.assert_unknown(dict(self.request, transcript_path=str(pipe))).assert_not_called()

    def test_malformed_duplicate_or_nonobject_json_is_unknown(self):
        for raw in (b'', b'{', b'[]', b'null', b'{"session_id":"one","session_id":"two"}',
                    b'{"value":NaN}', b'\xff'):
            with self.subTest(raw=raw):
                self.assert_unknown(stream=io.BytesIO(raw)).assert_not_called()

    def test_input_limit_is_bytes_and_never_uses_unbounded_read(self):
        class BoundedInput(io.BytesIO):
            def read(self, size=-1):
                if size != hook_report.MAX_STDIN_BYTES + 1:
                    raise AssertionError('unexpected read size')
                return super().read(size)

        limit = hook_report.MAX_STDIN_BYTES
        self.assert_unknown(stream=BoundedInput(b' ' * (limit + 1))).assert_not_called()
        self.assert_unknown(stream=io.StringIO('界' * (limit // 3 + 1))).assert_not_called()
        raw = json.dumps(self.request).encode('utf-8')
        message, read_usage = self.report(stream=BoundedInput(raw + b' ' * (limit - len(raw))))
        self.assertIn('1,234', message)
        read_usage.assert_called_once()

    def test_real_text_stdin_uses_its_binary_buffer(self):
        raw = json.dumps(self.request).encode('utf-8')
        with io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8') as stream:
            message, read_usage = self.report(stream=stream)
        self.assertIn('1,234', message)
        read_usage.assert_called_once()

    def append(self, *rows):
        with self.transcript.open('ab') as stream:
            for row in rows:
                stream.write(json.dumps(row).encode('utf-8') + b'\n')

    def precise_record(self, inputs, outputs, response):
        counts = {'input_tokens': inputs, 'output_tokens': outputs,
                  'cached_input_tokens': 0, 'reasoning_output_tokens': 0}
        return {'type': 'token_usage_record', 'payload': {
            'session_id': 'session-one', 'thread_id': 'session-one', 'turn_id': 'turn-one',
            'response_id': response, 'turn_token_usage': counts, 'usage': counts}}

    def real_report(self, request=None):
        output = io.StringIO()
        stream = io.BytesIO(json.dumps(request or self.request).encode('utf-8'))
        with redirect_stdout(output):
            self.assertEqual(hook_report.report_from_stdin(self.home, stream), 0)
        document = json.loads(output.getvalue())
        self.assertEqual(set(document), {'systemMessage'})
        self.assertNotIn('PRIVATE ASSISTANT BODY', output.getvalue())
        return document['systemMessage']

    def test_real_parser_reports_final_usage_without_task_complete(self):
        self.append({'type': 'session_meta', 'payload': {'id': 'session-one'}},
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn-one'}},
                    self.precise_record(100, 10, 'prior-response'),
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                     'phase': 'final_answer', 'content': [{'type': 'output_text',
                                                          'text': 'PRIVATE ASSISTANT BODY'}]}})
        self.assertEqual(self.real_report(), hook_report.UNKNOWN_MESSAGE)
        self.append(self.precise_record(1234, 56, 'final-response'))
        self.assertEqual(self.real_report(),
                         '本轮 Token（主代理）：上行 1,234，下行 56，合计 1,290。')

    def test_real_parser_rejects_session_mismatch_and_partial_final_line(self):
        self.append({'type': 'session_meta', 'payload': {'id': 'session-one'}},
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn-one'}},
                    {'type': 'event_msg', 'payload': {'type': 'agent_message',
                                                    'phase': 'final_answer', 'turn_id': 'turn-one'}})
        with self.transcript.open('ab') as stream:
            stream.write(json.dumps(self.precise_record(1234, 56, 'final-response')).encode('utf-8'))
        self.assertEqual(self.real_report(), hook_report.UNKNOWN_MESSAGE)
        self.assertEqual(self.real_report(dict(self.request, session_id='another-session')),
                         hook_report.UNKNOWN_MESSAGE)


class WindowsHandleTests(unittest.TestCase):
    def kernel(self, handle=101, *, duplicate_ok=True):
        kernel = MagicMock()
        kernel.GetStdHandle.return_value = handle
        kernel.GetCurrentProcess.return_value = 201

        def duplicate(source_process, source, target_process, output, access, inherit, options):
            if duplicate_ok:
                output._obj.value = 301
            return duplicate_ok

        kernel.DuplicateHandle.side_effect = duplicate
        return kernel

    def open_stream(self, kernel, crt, *, reading):
        with patch.object(hook_report.sys, 'platform', 'win32'), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.dict('sys.modules', {'msvcrt': crt}):
            return hook_report._open_windows_standard_stream(reading=reading)

    def test_read_and_write_transfer_only_the_duplicated_handle(self):
        for reading in (True, False):
            with self.subTest(reading=reading), tempfile.TemporaryFile() as original:
                if reading:
                    original.write(b'input')
                    original.seek(0)
                descriptor = os.dup(original.fileno())
                kernel = self.kernel()
                crt = SimpleNamespace(open_osfhandle=MagicMock(return_value=descriptor))
                with self.open_stream(kernel, crt, reading=reading) as stream:
                    if reading:
                        self.assertEqual(stream.read(), b'input')
                    else:
                        stream.write(b'output')
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
                os.fstat(original.fileno())
                if not reading:
                    original.seek(0)
                    self.assertEqual(original.read(), b'output')
                self.assertEqual(crt.open_osfhandle.call_args.args[0], 301)
                self.assertEqual(crt.open_osfhandle.call_args.args[1] & os.O_WRONLY,
                                 0 if reading else os.O_WRONLY)
                kernel.CloseHandle.assert_not_called()
                self.assertEqual(kernel.GetStdHandle.call_args.args[0],
                                 4294967286 if reading else 4294967285)
                args = kernel.DuplicateHandle.call_args.args
                self.assertEqual((args[0], args[1], args[2], args[4:]),
                                 (201, 101, 201, (0, False, 2)))

    def test_missing_invalid_or_unduplicable_handle_does_not_close_the_source(self):
        for handle, duplicate_ok in ((None, True), (0, True), (-1, True),
                                     (ctypes.c_void_p(-1).value, True), (101, False)):
            with self.subTest(handle=handle, duplicate_ok=duplicate_ok):
                kernel = self.kernel(handle, duplicate_ok=duplicate_ok)
                crt = SimpleNamespace(open_osfhandle=MagicMock())
                with self.assertRaises(OSError):
                    self.open_stream(kernel, crt, reading=True)
                crt.open_osfhandle.assert_not_called()
                kernel.CloseHandle.assert_not_called()

    def test_crt_conversion_failure_closes_only_the_copy(self):
        kernel = self.kernel()
        crt = SimpleNamespace(open_osfhandle=MagicMock(side_effect=OSError('failed')))
        with self.assertRaises(OSError):
            self.open_stream(kernel, crt, reading=True)
        kernel.CloseHandle.assert_called_once_with(301)

    def test_fdopen_failure_closes_owned_descriptor_and_leaves_source_open(self):
        with tempfile.TemporaryFile() as original:
            descriptor = os.dup(original.fileno())
            kernel = self.kernel()
            crt = SimpleNamespace(open_osfhandle=MagicMock(return_value=descriptor))
            with patch.object(hook_report.os, 'fdopen', side_effect=OSError('failed')), \
                    self.assertRaises(OSError):
                self.open_stream(kernel, crt, reading=False)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            os.fstat(original.fileno())
            kernel.CloseHandle.assert_not_called()

    def test_report_uses_and_closes_fallback_streams_when_python_streams_are_none(self):
        class CapturedOutput(io.BytesIO):
            def close(self):
                self.captured = self.getvalue()
                super().close()

        stdin = io.BytesIO(b'{}')
        stdout = CapturedOutput()
        with patch.object(hook_report.sys, 'stdout', None), \
                patch.object(hook_report, '_open_windows_standard_stream',
                             side_effect=[stdin, stdout]) as opened:
            result = hook_report.report_from_stdin('/unused', None)
        self.assertEqual(result, 0)
        self.assertTrue(stdin.closed and stdout.closed)
        self.assertEqual(json.loads(stdout.captured), {'systemMessage': hook_report.UNKNOWN_MESSAGE})
        self.assertEqual([call.kwargs for call in opened.call_args_list],
                         [{'reading': True}, {'reading': False}])

    def test_missing_fallback_streams_report_failure_without_devnull(self):
        output = io.StringIO()
        with redirect_stdout(output), \
                patch.object(hook_report, '_open_windows_standard_stream', side_effect=OSError('failed')):
            self.assertEqual(hook_report.report_from_stdin('/unused', None), 1)
        self.assertEqual(json.loads(output.getvalue()), {'systemMessage': hook_report.UNKNOWN_MESSAGE})
        with patch.object(hook_report.sys, 'stdout', None), \
                patch.object(hook_report, '_open_windows_standard_stream', side_effect=OSError('failed')):
            self.assertEqual(hook_report.report_from_stdin('/unused', io.BytesIO(b'{}')), 1)


if __name__ == '__main__':
    unittest.main()
