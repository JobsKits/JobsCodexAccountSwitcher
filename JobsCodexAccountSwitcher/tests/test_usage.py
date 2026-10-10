"""虚拟会话日志验证轮次归属、热接入与恢复；不访问真实凭据。Created by Jobs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jobs_codex_account_switcher.usage import UsageMonitor, read_turn_usage


def tokens(inputs, outputs, cached=0, reasoning=0):
    return {'input_tokens': inputs, 'output_tokens': outputs,
            'cached_input_tokens': cached, 'reasoning_output_tokens': reasoning,
            'total_tokens': inputs + outputs}


def event(kind, timestamp='2026-10-10T01:00:00Z', **fields):
    return {'timestamp': timestamp, 'type': 'event_msg', 'payload': {'type': kind, **fields}}


def count(total, last=None, timestamp='2026-10-10T01:00:01Z'):
    return event('token_count', timestamp, info={'total_token_usage': total,
                                               'last_token_usage': last or total})


def record(turn, total, *, thread='main', thread_total=None, response='response-1',
           usage=None, timestamp='2026-10-10T01:00:01Z'):
    return {'timestamp': timestamp, 'type': 'token_usage_record', 'payload': {
        'thread_id': thread, 'session_id': 'root-account-free-id', 'turn_id': turn,
        'root_turn_id': 'root-turn', 'response_id': response,
        'usage': usage or total, 'turn_token_usage': total,
        'thread_token_usage': thread_total}}


def user_item(*blocks, turn_id='', role='user'):
    payload = {'type': 'message', 'role': role, 'content': list(blocks)}
    if turn_id:
        payload['internal_chat_message_metadata_passthrough'] = {'turn_id': turn_id}
    return {'type': 'response_item', 'payload': payload}


def text_block(text, kind='input_text'):
    return {'type': kind, 'text': text}


def final_answer(turn_id='one'):
    return {'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'phase': 'final_answer',
        'internal_chat_message_metadata_passthrough': {'turn_id': turn_id},
        'content': [{'type': 'output_text', 'text': 'synthetic final answer'}]}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'codex'
        self.root = self.home / 'sessions' / '2026' / '10' / '10'
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def append(self, path, *rows):
        with path.open('ab') as stream:
            for row in rows:
                stream.write(json.dumps(row).encode() + b'\n')

    def session(self, name='main', *, parent=''):
        path = self.root / (name + '.jsonl')
        payload = {'id': name, 'source': 'vscode'}
        if parent:
            payload.update(parent_thread_id=parent,
                           source={'subagent': {'thread_spawn': {'parent_thread_id': parent}}})
        self.append(path, {'timestamp': '2026-10-10T01:00:00Z',
                           'type': 'session_meta', 'payload': payload})
        return path

    def monitor(self, **kwargs):
        return UsageMonitor(self.home, discovery_interval=0, **kwargs)

    def test_old_format_sums_all_calls_and_separates_turns(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    count(tokens(100, 10, 80, 3)),
                    count(tokens(250, 30, 200, 8), tokens(150, 20, 120, 5)),
                    event('task_complete', '2026-10-10T01:00:02Z', turn_id='one'),
                    event('task_started', '2026-10-10T01:00:03Z', turn_id='two'),
                    count(tokens(400, 50, 300, 13), tokens(150, 20, 100, 5)),
                    event('task_complete', '2026-10-10T01:00:04Z', turn_id='two'))
        recent, earlier = self.monitor().poll().turns
        self.assertEqual((recent.turn_id, recent.input_tokens, recent.output_tokens), ('two', 150, 20))
        self.assertEqual((earlier.input_tokens, earlier.output_tokens,
                          earlier.cached_input_tokens, earlier.reasoning_output_tokens), (250, 30, 200, 8))
        self.assertTrue(recent.complete and earlier.complete)

    def test_precise_records_and_compatibility_events_do_not_double_count(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    record('one', tokens(100, 10), thread_total=tokens(100, 10)),
                    count(tokens(100, 10)),
                    record('one', tokens(250, 30), thread_total=tokens(250, 30),
                           usage=tokens(150, 20), response='response-2'),
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (250, 30))
        self.assertTrue(turn.complete)

    def test_precise_records_without_thread_total_match_paired_count(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    record('one', tokens(100, 10)), count(tokens(100, 10)),
                    record('one', tokens(250, 30), usage=tokens(150, 20), response='r2'),
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (250, 30))
        self.assertTrue(turn.complete)

    def test_real_precise_and_legacy_thread_totals_have_different_baselines(self):
        path = self.session()
        self.append(path, count(tokens(23361847, 98541, 22873728, 29073)),
                    event('task_started', turn_id='one'),
                    record('one', tokens(4154513, 17751, 4075008, 4694),
                           thread_total=tokens(23972547, 105373, 23476992, 29073),
                           usage=tokens(190976, 191, 190080, 0)),
                    count(tokens(23552823, 98732, 23063808, 29073),
                          tokens(190976, 191, 190080, 0)),
                    record('one', tokens(4345692, 18843, 4265856, 5694), response='r2',
                           thread_total=tokens(24163726, 106465, 23667840, 30073),
                           usage=tokens(191179, 1092, 190848, 1000)),
                    count(tokens(23744002, 99824, 23254656, 30073),
                          tokens(191179, 1092, 190848, 1000)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (4345692, 18843))
        self.assertTrue(turn.complete)

    def test_missing_precise_record_for_later_call_marks_partial_until_corrected(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    record('one', tokens(100, 10), thread_total=tokens(100, 10)),
                    count(tokens(100, 10)), count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        partial = monitor.poll().turns[0]
        self.assertFalse(partial.complete)
        self.assertEqual(partial.input_tokens, 100)
        self.append(path, record('one', tokens(250, 30), response='r2',
                                 usage=tokens(150, 20), thread_total=tokens(250, 30)))
        self.assertTrue(monitor.poll().turns[0].complete)

    def test_delayed_compatibility_events_cannot_add_already_precise_calls_again(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    record('one', tokens(100, 10, 80, 3), response='r1',
                           thread_total=tokens(100, 10, 80, 3)),
                    record('one', tokens(250, 30, 200, 8), response='r2',
                           thread_total=tokens(250, 30, 200, 8), usage=tokens(150, 20, 120, 5)),
                    count(tokens(100, 10, 80, 3)),
                    count(tokens(250, 30, 200, 8), tokens(150, 20, 120, 5)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens,
                          turn.cached_input_tokens, turn.reasoning_output_tokens), (250, 30, 200, 8))
        self.assertFalse(turn.complete)
        self.assertIn('保留精确累计', turn.note)

    def test_replayed_finished_start_cannot_interrupt_current_round(self):
        path = self.session()
        old_start = event('task_started', turn_id='old')
        self.append(path, old_start, count(tokens(100, 10)),
                    event('task_complete', turn_id='old'),
                    event('task_started', '2026-10-10T01:00:02Z', turn_id='new'),
                    old_start, count(tokens(250, 30), tokens(150, 20)))
        monitor = self.monitor()
        snapshot = monitor.poll()
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual([turn.turn_id for turn in snapshot.turns], ['old'])
        self.append(path, event('task_complete', '2026-10-10T01:00:03Z', turn_id='new'))
        recent = monitor.poll().turns[0]
        self.assertEqual((recent.turn_id, recent.status, recent.input_tokens), ('new', 'completed', 150))
        self.assertTrue(recent.complete)

    def test_repeated_totals_response_records_and_endings_are_idempotent(self):
        path = self.session()
        usage = record('one', tokens(100, 10), thread_total=tokens(100, 10))
        repeated = count(tokens(100, 10))
        end = event('task_complete', turn_id='one')
        self.append(path, event('task_started', turn_id='one'), usage, usage,
                    repeated, repeated, end, end)
        monitor = self.monitor()
        for _ in range(3):
            snapshot = monitor.poll()
            self.assertEqual(len(snapshot.turns), 1)
            self.assertEqual(snapshot.turns[0].input_tokens, 100)

    def test_hot_attach_in_progress_preserves_whole_round(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)))
        monitor = self.monitor(include_history=False)
        snapshot = monitor.poll()
        self.assertEqual(snapshot.active_count, 1)
        self.assertFalse(snapshot.turns)
        self.append(path, count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='one'))
        turn = monitor.poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (250, 30))
        self.assertTrue(turn.complete)

    def test_initial_history_can_be_hidden_but_future_rounds_appear(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='old'), count(tokens(100, 10)),
                    event('task_complete', turn_id='old'))
        monitor = self.monitor(include_history=False, read_bytes=32)
        snapshot = monitor.poll()
        while snapshot.loading:
            snapshot = monitor.poll()
        self.assertFalse(snapshot.turns)
        self.append(path, event('task_started', turn_id='new'),
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='new'))
        snapshot = monitor.poll()
        while snapshot.loading:
            snapshot = monitor.poll()
        self.assertEqual([turn.turn_id for turn in snapshot.turns], ['new'])

    def test_partial_line_waits_for_newline_and_does_not_parse_half_json(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)))
        raw = json.dumps(event('task_complete', turn_id='one')).encode()
        with path.open('ab') as stream:
            stream.write(raw[:len(raw) // 2])
        monitor = self.monitor()
        snapshot = monitor.poll()
        self.assertFalse(snapshot.errors)
        self.assertFalse(snapshot.turns)
        with path.open('ab') as stream:
            stream.write(raw[len(raw) // 2:] + b'\n')
        self.assertEqual(monitor.poll().turns[0].input_tokens, 100)

    def test_new_session_discovered_after_monitor_starts(self):
        monitor = self.monitor()
        self.assertEqual(monitor.poll().file_count, 0)
        path = self.session('later')
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        self.assertEqual(monitor.poll().turns[0].session_id, 'later')

    def test_compaction_model_changes_and_repeated_context_do_not_split_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    {'type': 'compacted', 'payload': {'message': 'synthetic private text'}},
                    {'type': 'turn_context', 'payload': {'turn_id': 'one', 'model': 'model-two'}},
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', turn_id='one'))
        snapshot = self.monitor().poll()
        self.assertEqual(len(snapshot.turns), 1)
        self.assertEqual(snapshot.turns[0].input_tokens, 250)
        self.assertEqual(snapshot.turns[0].model, 'model-two')
        self.assertTrue(snapshot.turns[0].complete)

    def test_counter_reset_retains_known_calls_but_marks_partial(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    count(tokens(50, 5)), count(tokens(120, 12), tokens(70, 7)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (220, 22))
        self.assertFalse(turn.complete)
        self.assertIn('重置', turn.note)

    def test_unknown_baseline_counts_only_confirmed_call(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='resumed'),
                    count(tokens(1000, 100), tokens(100, 10)),
                    count(tokens(1150, 120), tokens(150, 20)),
                    event('task_complete', turn_id='resumed'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual((turn.input_tokens, turn.output_tokens), (250, 30))
        self.assertFalse(turn.complete)

    def test_no_usage_is_unknown_instead_of_zero(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    event('token_count', info=None, rate_limits={}),
                    event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertIsNone(turn.input_tokens)
        self.assertIsNone(turn.output_tokens)
        self.assertFalse(turn.complete)

    def test_aborted_turn_is_terminal_and_next_turn_uses_correct_baseline(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('turn_aborted', '2026-10-10T01:00:02Z', turn_id='one', reason='interrupted'),
                    event('task_started', turn_id='two'),
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', '2026-10-10T01:00:03Z', turn_id='two'))
        turns = {turn.turn_id: turn for turn in self.monitor().poll().turns}
        self.assertEqual(turns['one'].status, 'aborted')
        self.assertEqual(turns['two'].input_tokens, 150)

    def test_missing_end_is_partial_when_next_round_begins(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_started', turn_id='two'))
        snapshot = self.monitor().poll()
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.turns[0].status, 'interrupted')
        self.assertFalse(snapshot.turns[0].complete)

    def test_restart_recovers_completed_history_and_stale_unfinished_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'),
                    event('task_started', turn_id='two'),
                    count(tokens(250, 30), tokens(150, 20)))
        self.monitor().poll()
        monitor = self.monitor(clock=lambda: path.stat().st_mtime + 100,
                               active_idle_seconds=10)
        snapshot = monitor.poll()
        turns = {turn.turn_id: turn for turn in snapshot.turns}
        self.assertEqual(turns['one'].input_tokens, 100)
        self.assertEqual(turns['two'].status, 'unfinished')
        self.assertFalse(turns['two'].complete)
        self.assertEqual(snapshot.active_count, 0)
        self.append(path, event('task_complete', turn_id='two'))
        self.assertEqual(next(turn for turn in monitor.poll().turns if turn.turn_id == 'two').status,
                         'completed')

    def test_concurrent_threads_and_subagents_are_isolated(self):
        parent = self.session()
        child = self.session('child', parent='main')
        other = self.session('other')
        for path, name, amount in ((parent, 'main', 100), (child, 'child', 500),
                                  (other, 'other', 900)):
            self.append(path, event('task_started', turn_id='same-turn-id'),
                        record('same-turn-id', tokens(amount, 10), thread=name,
                               thread_total=tokens(amount, 10)),
                        event('task_complete', turn_id='same-turn-id'))
        turns = {turn.session_id: turn for turn in self.monitor().poll().turns}
        self.assertEqual({key: turn.input_tokens for key, turn in turns.items()},
                         {'main': 100, 'child': 500, 'other': 900})
        self.assertTrue(turns['child'].is_subagent)
        self.assertEqual(turns['child'].parent_session_id, 'main')
        self.assertEqual(turns['child'].root_turn_id, 'root-turn')

    def test_subagent_activity_is_separate_from_user_activity(self):
        parent = self.session()
        child = self.session('child', parent='main')
        self.append(parent, event('task_started', turn_id='parent'))
        self.append(child, event('task_started', turn_id='child'))
        snapshot = self.monitor().poll()
        self.assertEqual((snapshot.active_count, snapshot.subagent_active_count), (1, 1))

    def test_rotation_replays_without_duplicate_turns_and_updates_new_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        self.assertEqual(len(monitor.poll().turns), 1)
        replacement = self.root / 'replacement.tmp'
        replacement.write_bytes(path.read_bytes())
        self.append(replacement, event('task_started', turn_id='two'),
                    count(tokens(250, 30), tokens(150, 20)),
                    event('task_complete', '2026-10-10T01:00:03Z', turn_id='two'))
        replacement.replace(path)
        turns = monitor.poll().turns
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].input_tokens, 150)

    def test_truncation_rebuilds_and_does_not_reuse_old_baseline(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(1000, 100)),
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        monitor.poll()
        path.write_bytes(b'')
        self.append(path, {'type': 'session_meta', 'payload': {'id': 'main'}},
                    event('task_started', turn_id='two'), count(tokens(100, 10)),
                    event('task_complete', turn_id='two'))
        turns = {turn.turn_id: turn for turn in monitor.poll().turns}
        self.assertEqual(turns['two'].input_tokens, 100)

    def test_late_precise_record_can_update_an_ended_unknown_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), event('task_complete', turn_id='one'))
        monitor = self.monitor()
        self.assertFalse(monitor.poll().turns[0].complete)
        self.append(path, record('one', tokens(100, 10), thread_total=tokens(100, 10)))
        turn = monitor.poll().turns[0]
        self.assertEqual(turn.input_tokens, 100)
        self.assertTrue(turn.complete)

    def test_truncated_initial_tail_recovers_from_precise_per_turn_usage(self):
        identifier = '12345678-1234-1234-1234-123456789abc'
        path = self.root / ('rollout-2026-10-10T01-00-00-' + identifier + '.jsonl')
        self.append(path, {'type': 'session_meta', 'payload': {'id': identifier}},
                    event('task_started', turn_id='one'),
                    {'type': 'response_item', 'payload': {'synthetic_private_text': 'a' * 1000}},
                    record('one', tokens(100, 10), thread=identifier,
                           thread_total=tokens(100, 10)))
        monitor = self.monitor(initial_bytes=800)
        self.assertEqual(monitor.poll().active_count, 1)
        self.append(path, event('task_complete', turn_id='one'))
        turn = monitor.poll().turns[0]
        self.assertEqual((turn.session_id, turn.input_tokens), (identifier, 100))
        self.assertTrue(turn.complete)

    def test_truncated_subagent_tail_retains_metadata_not_just_filename(self):
        path = self.session('child', parent='main')
        self.append(path, event('task_started', turn_id='one'),
                    {'type': 'response_item', 'payload': {'synthetic_private_text': 'a' * 1000}},
                    record('one', tokens(100, 10), thread='child', thread_total=tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        turn = self.monitor(initial_bytes=800).poll().turns[0]
        self.assertEqual((turn.session_id, turn.parent_session_id), ('child', 'main'))
        self.assertTrue(turn.is_subagent)
        self.assertTrue(turn.complete)

    def test_same_response_can_be_corrected_from_unknown_to_complete(self):
        path = self.session()
        incomplete = record('one', None, usage=tokens(100, 10), response='r1')
        self.append(path, event('task_started', turn_id='one'), incomplete,
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        self.assertFalse(monitor.poll().turns[0].complete)
        self.append(path, record('one', tokens(150, 15), response='r1',
                                 thread_total=tokens(150, 15)))
        corrected = monitor.poll().turns[0]
        self.assertTrue(corrected.complete)
        self.assertEqual(corrected.input_tokens, 150)

    def test_replayed_earlier_precise_response_cannot_regress_round_total(self):
        path = self.session()
        first = record('one', tokens(100, 10), response='r1', thread_total=tokens(100, 10))
        self.append(path, event('task_started', turn_id='one'), first,
                    record('one', tokens(250, 30), response='r2',
                           thread_total=tokens(250, 30), usage=tokens(150, 20)),
                    first, event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].input_tokens, 250)

    def test_late_old_turn_record_does_not_corrupt_current_turn_baseline(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'),
                    event('task_started', turn_id='two'),
                    count(tokens(250, 30), tokens(150, 20)),
                    record('one', tokens(100, 10), thread_total=tokens(100, 10)),
                    count(tokens(400, 50), tokens(150, 20)),
                    event('task_complete', turn_id='two'))
        turns = {turn.turn_id: turn for turn in self.monitor().poll().turns}
        self.assertEqual(turns['two'].input_tokens, 300)
        self.assertTrue(turns['two'].complete)

    def test_poll_has_global_read_budget_and_resumes_without_dropping_lines(self):
        for name in ('one', 'two', 'three'):
            path = self.session(name)
            self.append(path, event('task_started', turn_id=name), count(tokens(100, 10)),
                        event('task_complete', turn_id=name))
        monitor = self.monitor(read_bytes=10000, poll_bytes=128)
        snapshot = monitor.poll()
        self.assertTrue(snapshot.loading)
        self.assertLessEqual(sum(reader.offset for reader in monitor._readers.values()), 128)
        for _ in range(100):
            if not snapshot.loading:
                break
            snapshot = monitor.poll()
        self.assertEqual(len(snapshot.turns), 3)
        self.assertFalse(snapshot.loading)

    def test_background_cancellation_does_not_consume_unread_events(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        stopped = [True]
        monitor = self.monitor(cancelled=lambda: stopped[0])
        self.assertFalse(monitor.poll().turns)
        stopped[0] = False
        self.assertEqual(monitor.poll().turns[0].input_tokens, 100)

    def test_index_rediscovers_old_resumed_session_among_bounded_readers(self):
        older = self.session('old')
        newer = self.session('new')
        self.append(newer, event('task_started', turn_id='new'), count(tokens(100, 10)),
                    event('task_complete', turn_id='new'))
        monitor = self.monitor(max_files=1)
        self.assertEqual(monitor.poll().turns[0].session_id, 'new')
        self.append(older, event('task_started', turn_id='old'), count(tokens(300, 30)),
                    event('task_complete', turn_id='old'))
        turns = {turn.session_id: turn for turn in monitor.poll().turns}
        self.assertEqual(turns['old'].input_tokens, 300)

    def test_unchanged_directory_inventory_does_not_relist_tree(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        monitor.poll()
        with patch.object(Path, 'iterdir', side_effect=AssertionError('unchanged directory relisted')):
            self.assertEqual(monitor.poll().turns[0].input_tokens, 100)

    def test_user_message_summary_collapses_whitespace_and_limits_preview(self):
        path = self.session()
        request = '  修复\n  轮次\t统计  ' + '需求' * 100
        self.append(path, event('task_started', turn_id='one'),
                    event('user_message', message=request), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        original = path.read_bytes()
        turn = self.monitor().poll().turns[0]
        self.assertEqual(len(turn.user_prompt), 160)
        self.assertTrue(turn.user_prompt.startswith('修复 轮次 统计 '))
        self.assertTrue(turn.user_prompt.endswith('…'))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(turn.thread_title, '')

    def test_response_item_uses_active_turn_and_ignores_injected_blocks(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('# AGENTS.md instructions\n<INSTRUCTIONS>synthetic rules</INSTRUCTIONS>'),
                              text_block('<environment_context>synthetic paths</environment_context>')),
                    user_item(text_block('  请修复统计窗口\n 并保留账户切换。', 'text')),
                    user_item(text_block('后续补充说明，不替换本轮第一请求。')),
                    count(tokens(100, 10)), event('task_complete', turn_id='one'))
        turn = self.monitor().poll().turns[0]
        self.assertEqual(turn.user_prompt, '请修复统计窗口 并保留账户切换。')
        self.assertNotIn('synthetic', turn.user_prompt)

    def test_combined_injection_and_actual_request_keep_only_actual_request(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('<environment_context>synthetic paths</environment_context>\n真正请求')),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '真正请求')

    def test_app_navigation_event_does_not_become_user_request(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>')),
                    user_item(text_block('实际的用户请求')), event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '实际的用户请求')

    def test_attachment_wrapper_summary_prefers_request_without_full_file_paths(self):
        path = self.session()
        wrapper = '# Files mentioned by the user:\n\n- /synthetic/private/full/path/report.pdf\n\n## My request:\n请分析附件中的统计差异。'
        self.append(path, event('task_started', turn_id='one'),
                    event('user_message', message=wrapper), event('task_complete', turn_id='one'))
        summary = self.monitor().poll().turns[0].user_prompt
        self.assertEqual(summary, '请分析附件中的统计差异。')
        self.assertNotIn('/synthetic/private', summary)

    def test_response_attachment_wrapper_and_wrapper_without_request_are_safe(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('# Files mentioned by the user:\n- /synthetic/private/report.pdf')),
                    user_item(text_block('# Files mentioned by the user:\n- /synthetic/private/report.pdf\n## My request:\n修复统计')),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '修复统计')

    def test_normal_user_text_without_attachment_marker_is_not_removed(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('请检查普通请求中的 README.md 内容。')),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '请检查普通请求中的 README.md 内容。')

    def test_late_explicit_user_message_updates_old_turn_without_touching_current(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='old'),
                    event('task_complete', '2026-10-10T01:00:01Z', turn_id='old'),
                    event('task_started', '2026-10-10T01:00:02Z', turn_id='new'),
                    event('user_message', message='当前请求', turn_id='new'))
        monitor = self.monitor()
        self.assertEqual(monitor.poll().turns[0].user_prompt, '')
        self.append(path, event('user_message', message='迟到的旧请求', turn_id='old'),
                    event('task_complete', '2026-10-10T01:00:03Z', turn_id='new'))
        turns = {turn.turn_id: turn for turn in monitor.poll().turns}
        self.assertEqual(turns['old'].user_prompt, '迟到的旧请求')
        self.assertEqual(turns['new'].user_prompt, '当前请求')

    def test_nested_response_turn_metadata_wins_over_current_active_round(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='old'),
                    event('task_complete', turn_id='old'),
                    event('task_started', turn_id='new'),
                    user_item(text_block('旧轮请求'), turn_id='old'),
                    user_item(text_block('新轮请求'), turn_id='new'),
                    event('task_complete', turn_id='new'))
        turns = {turn.turn_id: turn for turn in self.monitor().poll().turns}
        self.assertEqual(turns['old'].user_prompt, '旧轮请求')
        self.assertEqual(turns['new'].user_prompt, '新轮请求')

    def test_user_message_is_preferred_to_response_fallback_and_can_precede_start(self):
        path = self.session()
        self.append(path, user_item(text_block('回退摘要'), turn_id='one'),
                    event('user_message', message='正式请求', turn_id='one'),
                    event('task_started', turn_id='one'),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '正式请求')

    def test_missing_user_text_stays_empty_and_never_uses_assistant_or_tool_body(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item(text_block('synthetic assistant body'), role='assistant'),
                    {'type': 'response_item', 'payload': {'type': 'function_call_output',
                                                        'output': 'synthetic tool body'}},
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '')

    def test_image_only_request_has_clear_preview_but_text_with_image_keeps_request(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item({'type': 'input_image', 'image_url': 'synthetic image'}),
                    event('task_complete', '2026-10-10T01:00:01Z', turn_id='one'),
                    event('task_started', turn_id='two'),
                    user_item({'type': 'input_image', 'image_url': 'synthetic image'},
                              text_block('看图修复标题显示')),
                    event('task_complete', '2026-10-10T01:00:02Z', turn_id='two'))
        turns = {turn.turn_id: turn for turn in self.monitor().poll().turns}
        self.assertEqual(turns['one'].user_prompt, '图片或附件消息（无文字请求）')
        self.assertEqual(turns['two'].user_prompt, '看图修复标题显示')

    def test_attachment_placeholder_can_be_replaced_by_later_actual_text(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    user_item({'type': 'input_image', 'image_url': 'synthetic image'}),
                    user_item(text_block('后到的真实文字请求')),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '后到的真实文字请求')

    def test_subagent_messages_do_not_become_user_requests(self):
        path = self.session('child', parent='main')
        self.append(path, event('task_started', turn_id='one'),
                    event('user_message', message='synthetic delegated task', turn_id='one'),
                    user_item(text_block('synthetic delegated task'), turn_id='one'),
                    event('task_complete', turn_id='one'))
        self.assertEqual(self.monitor().poll().turns[0].user_prompt, '')

    def test_stop_reader_accepts_precise_final_usage_before_task_complete(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer(),
                    record('one', tokens(250, 30), thread_total=tokens(250, 30)))
        turn = read_turn_usage(self.home, path, 'main', 'one')
        self.assertIsNotNone(turn)
        self.assertTrue(turn.complete)
        self.assertEqual((turn.status, turn.input_tokens, turn.output_tokens), ('active', 250, 30))

    def test_stop_reader_rejects_precise_usage_before_final_marker(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), record('one', tokens(100, 10)),
                    final_answer())
        turn = read_turn_usage(self.home, path, 'main', 'one')
        self.assertEqual(turn.input_tokens, 100)
        self.assertFalse(turn.complete)

    def test_stop_reader_does_not_use_legacy_usage_as_final_confirmation(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer(),
                    count(tokens(100, 10)), event('task_complete', turn_id='one'))
        turn = read_turn_usage(self.home, path, 'main', 'one')
        self.assertEqual(turn.input_tokens, 100)
        self.assertFalse(turn.complete)

    def test_stop_reader_requires_final_marker_even_if_task_is_complete(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), record('one', tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        self.assertFalse(read_turn_usage(self.home, path, 'main', 'one').complete)

    def test_stop_reader_final_marker_metadata_does_not_leak_to_other_active_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='old'),
                    event('task_complete', turn_id='old'),
                    event('task_started', turn_id='new'), final_answer('old'),
                    record('new', tokens(100, 10)))
        self.assertFalse(read_turn_usage(self.home, path, 'main', 'new').complete)

    def test_stop_reader_event_final_marker_can_use_active_turn(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'),
                    event('agent_message', phase='final_answer', message='synthetic final answer'),
                    record('one', tokens(100, 10)))
        self.assertTrue(read_turn_usage(self.home, path, 'main', 'one').complete)

    def test_stop_reader_duplicate_old_response_does_not_confirm_final_answer_cost(self):
        path = self.session()
        old_record = record('one', tokens(100, 10), response='old-response')
        self.append(path, event('task_started', turn_id='one'), old_record, final_answer(), old_record)
        self.assertFalse(read_turn_usage(self.home, path, 'main', 'one').complete)

    def test_stop_reader_latest_final_answer_requires_a_new_precise_record(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer(),
                    record('one', tokens(100, 10), response='first-final'), final_answer())
        self.assertFalse(read_turn_usage(self.home, path, 'main', 'one').complete)

    def test_stop_reader_unfinished_usage_line_is_pending_instead_of_complete(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer())
        with path.open('ab') as stream:
            stream.write(json.dumps(record('one', tokens(100, 10))).encode())
        turn = read_turn_usage(self.home, path, 'main', 'one')
        self.assertFalse(turn.complete)
        self.assertIsNone(turn.input_tokens)

    def test_stop_reader_missing_or_mismatched_identity_is_unknown(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer(),
                    record('one', tokens(100, 10)))
        self.assertIsNone(read_turn_usage(self.home, path, 'wrong-session', 'one'))
        self.assertIsNone(read_turn_usage(self.home, path, 'main', 'wrong-turn'))
        self.assertIsNone(read_turn_usage(self.home, path, 'main', ''))
        self.assertIsNone(read_turn_usage(self.home, object(), 'main', 'one'))

    def test_stop_reader_rejects_outside_paths_directories_and_symlinks(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), final_answer(),
                    record('one', tokens(100, 10)))
        outside = self.home / 'outside.jsonl'
        outside.write_bytes(path.read_bytes())
        directory = self.root / 'directory.jsonl'
        directory.mkdir()
        link = self.root / 'linked.jsonl'
        link.symlink_to(path)
        parent_link = self.root / 'linked-directory'
        parent_link.symlink_to(self.root, target_is_directory=True)
        for candidate in (outside, directory, link, parent_link / path.name):
            self.assertIsNone(read_turn_usage(self.home, candidate, 'main', 'one'))
        self.assertIsNone(read_turn_usage(self.home, self.home / 'auth.json', 'main', 'one'))

    def test_stop_reader_tail_scan_is_bounded_and_recovers_identity_from_header(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'))
        filler = json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'output': 'x' * 65536}}).encode() + b'\n'
        with path.open('ab') as stream:
            for _ in range(270):
                stream.write(filler)
        self.append(path, final_answer(), record('one', tokens(100, 10)))
        import os
        original_fdopen = os.fdopen
        received = [0]

        class CountedStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def seek(self, offset):
                return self.stream.seek(offset)

            def readline(self, limit):
                raw = self.stream.readline(limit)
                received[0] += len(raw)
                return raw

        with patch('jobs_codex_account_switcher.usage.os.fdopen',
                   side_effect=lambda *args: CountedStream(original_fdopen(*args))):
            turn = read_turn_usage(self.home, path, 'main', 'one')
        self.assertEqual(turn.input_tokens, 100)
        self.assertTrue(turn.complete)
        self.assertLessEqual(received[0], 16 * 1024 * 1024 + 65536)

    def test_malformed_line_is_generic_and_marks_fallback_partial(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'))
        with path.open('ab') as stream:
            stream.write(b'{"synthetic_secret": invalid}\n')
        self.append(path, count(tokens(100, 10)), event('task_complete', turn_id='one'))
        snapshot = self.monitor().poll()
        self.assertFalse(snapshot.turns[0].complete)
        self.assertTrue(snapshot.errors)
        self.assertNotIn('synthetic_secret', str(snapshot))

    def test_poll_without_append_does_not_read_log_text_again(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        monitor = self.monitor()
        monitor.poll()
        with patch.object(Path, 'open', side_effect=AssertionError('unchanged text reread')):
            self.assertEqual(monitor.poll().turns[0].input_tokens, 100)

    def test_log_failures_retry_and_never_expose_body_or_read_auth(self):
        path = self.session()
        self.append(path, event('task_started', turn_id='one'), count(tokens(100, 10)),
                    event('task_complete', turn_id='one'))
        (self.home / 'auth.json').write_text('never read this synthetic credential')
        monitor = self.monitor()
        original_open = Path.open

        def guarded_open(target, *args, **kwargs):
            self.assertNotEqual(target.name, 'auth.json')
            if target == path:
                raise PermissionError('synthetic private OS text')
            return original_open(target, *args, **kwargs)

        with patch.object(Path, 'open', guarded_open):
            snapshot = monitor.poll()
            self.assertTrue(snapshot.errors)
            self.assertNotIn('synthetic private', str(snapshot))
        self.assertEqual(monitor.poll().turns[0].input_tokens, 100)


if __name__ == '__main__':
    unittest.main()
