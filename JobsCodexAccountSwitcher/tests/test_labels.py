"""使用隔离标题索引与数据库；不访问真实聊天正文或账户文件。Created by Jobs."""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from jobs_codex_account_switcher import labels


def row(session_id='thread-one', title='当前工作', updated='2026-10-10T01:00:00Z'):
    return {'id': session_id, 'thread_name': title, 'updated_at': updated}


class TitleIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'codex'
        self.home.mkdir()
        self.index = self.home / 'session_index.jsonl'

    def write_index(self, *rows):
        self.index.write_bytes(b''.join(json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n'
                                       for value in rows))

    def create_database(self, title='数据库标题', *, name='state_5.sqlite', wal=False):
        path = self.home / name
        connection = sqlite3.connect(path)
        if wal:
            connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, first_user_message TEXT)')
        connection.execute('INSERT INTO threads VALUES (?, ?, ?)',
                           ('thread-one', title, '禁止读取的正文哨兵'))
        connection.commit()
        return path, connection

    def test_json_title_takes_precedence_without_opening_database(self):
        self.write_index(row(title='修复 Token 悬浮窗'))
        database, connection = self.create_database()
        connection.close()
        before = database.read_bytes()
        with patch.object(labels.sqlite3, 'connect', side_effect=AssertionError('不应打开数据库')):
            self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '修复 Token 悬浮窗')
        self.assertEqual(database.read_bytes(), before)

    def test_latest_rename_wins_even_when_rows_arrive_out_of_order(self):
        self.write_index(row(title='最新名字', updated='2026-10-10T10:00:00+08:00'),
                         row(title='旧名字', updated='2026-10-10T01:00:00Z'))
        self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '最新名字')

    def test_unchanged_index_is_not_read_again(self):
        self.write_index(row())
        index = labels.TitleIndex(self.home)
        self.assertEqual(index.title('thread-one'), '当前工作')
        with patch.object(labels.os, 'open', side_effect=AssertionError('未变化索引重复读取')):
            self.assertEqual(index.title('thread-one'), '当前工作')

    def test_changed_index_and_removal_refresh_cached_title(self):
        self.write_index(row())
        index = labels.TitleIndex(self.home)
        self.assertEqual(index.title('thread-one'), '当前工作')
        self.write_index(row(title='名字已经更新为更长文字'))
        self.assertEqual(index.title('thread-one'), '名字已经更新为更长文字')
        self.index.unlink()
        self.assertEqual(index.title('thread-one'), '')
        self.write_index(row(title='重新出现'))
        self.assertEqual(index.title('thread-one'), '重新出现')

    def test_bad_rows_do_not_expose_data_or_hide_other_valid_titles(self):
        self.index.write_bytes(b'{broken\n\xff\xfe\n' + json.dumps(row()).encode() + b'\n')
        index = labels.TitleIndex(self.home)
        self.assertEqual(index.title('thread-one'), '当前工作')
        for bad in ('', None, 123, 'a' * 257, 'bad\0id'):
            self.assertEqual(index.title(bad), '')
        self.assertEqual(index.title('missing'), '')

    def test_invalid_schema_is_not_treated_as_a_title(self):
        self.write_index({'id': 'thread-one', 'first_user_message': '不要把正文当标题'},
                         row('thread-two', title=123), row('thread-three', updated='invalid date'))
        index = labels.TitleIndex(self.home)
        for session_id in ('thread-one', 'thread-two', 'thread-three'):
            self.assertEqual(index.title(session_id), '')

    def test_empty_index_title_does_not_resurrect_older_database_title(self):
        self.write_index(row(title=''))
        _, connection = self.create_database()
        connection.close()
        with patch.object(labels.sqlite3, 'connect', side_effect=AssertionError('不应使用旧标题')):
            self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '')

    def test_tail_read_keeps_latest_title_and_is_bounded(self):
        self.write_index(row('very-old', title='过大旧行' * 1000), row(title='最新标题'))
        original_fdopen = labels.os.fdopen
        sizes = []

        class Reader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def seek(self, offset):
                return self.stream.seek(offset)

            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)

        with patch.object(labels.os, 'fdopen', side_effect=lambda *args: Reader(original_fdopen(*args))):
            index = labels.TitleIndex(self.home, max_bytes=256)
            self.assertEqual(index.title('thread-one'), '最新标题')
        self.assertEqual(sizes, [1, 256])
        self.assertEqual(index.title('very-old'), '')

    def test_tail_start_at_line_boundary_keeps_whole_last_line(self):
        self.write_index(row('old'), row(title='最近一行'))
        size = len(json.dumps(row(title='最近一行'), ensure_ascii=False).encode() + b'\n')
        self.assertEqual(labels.TitleIndex(self.home, max_bytes=size).title('thread-one'), '最近一行')

    def test_memory_and_title_lengths_are_bounded(self):
        self.write_index(row('old', updated='2026-10-10T00:00:00Z'),
                         row('new', title='x' * 4000, updated='2026-10-10T02:00:00Z'))
        index = labels.TitleIndex(self.home, max_titles=1)
        self.assertEqual(index.title('new'), 'x' * 512)
        self.assertEqual(index.title('old'), '')

    def test_database_fallback_uses_read_only_and_only_id_title(self):
        database, connection = self.create_database()
        connection.close()
        before = database.read_bytes(), database.stat().st_mtime_ns, set(self.home.iterdir())
        original_connect = sqlite3.connect
        calls, queries = [], []

        def connect(database_uri, **kwargs):
            calls.append((database_uri, kwargs))
            result = original_connect(database_uri, **kwargs)
            result.set_trace_callback(queries.append)
            return result

        with patch.object(labels.sqlite3, 'connect', side_effect=connect):
            self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '数据库标题')
        self.assertIn('?mode=ro', calls[0][0])
        self.assertTrue(calls[0][1]['uri'])
        self.assertEqual(calls[0][1]['timeout'], 0)
        self.assertIn('PRAGMA query_only=ON', queries)
        self.assertTrue(any('SELECT substr(title' in query for query in queries))
        self.assertFalse(any('first_user_message' in query for query in queries))
        self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns, set(self.home.iterdir())), before)

    def test_database_results_are_cached_and_refresh_after_update(self):
        database, connection = self.create_database()
        connection.close()
        index = labels.TitleIndex(self.home)
        original_connect = sqlite3.connect
        with patch.object(labels.sqlite3, 'connect', wraps=original_connect) as connect:
            self.assertEqual(index.title('thread-one'), '数据库标题')
            self.assertEqual(index.title('thread-one'), '数据库标题')
            self.assertEqual(connect.call_count, 1)
            with closing(original_connect(database)) as writer:
                writer.execute('UPDATE threads SET title=? WHERE id=?', ('更新数据库标题', 'thread-one'))
                writer.commit()
            self.assertEqual(index.title('thread-one'), '更新数据库标题')
            self.assertEqual(connect.call_count, 2)

    def test_sql_parameters_do_not_accept_injection(self):
        _, connection = self.create_database()
        connection.close()
        self.assertEqual(labels.TitleIndex(self.home).title("missing' OR 1=1 --"), '')

    def test_locked_database_returns_empty_without_logging_and_can_recover(self):
        database, connection = self.create_database()
        connection.execute('BEGIN EXCLUSIVE')
        index = labels.TitleIndex(self.home)
        output = io.StringIO()
        with patch('sys.stdout', output), patch('sys.stderr', output):
            self.assertEqual(index.title('thread-one'), '')
        self.assertEqual(output.getvalue(), '')
        connection.rollback()
        connection.close()
        self.assertEqual(index.title('thread-one'), '数据库标题')
        self.assertTrue(database.is_file())

    def test_active_wal_database_is_not_opened_or_modified(self):
        _, connection = self.create_database(wal=True)
        try:
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in self.home.iterdir()}
            with patch.object(labels.sqlite3, 'connect', side_effect=AssertionError('禁止打开活跃 WAL')):
                self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '')
            self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                              for path in self.home.iterdir()}, before)
        finally:
            connection.close()

    def test_checkpointed_wal_mode_does_not_create_missing_side_files(self):
        database, connection = self.create_database(wal=True)
        connection.close()
        self.assertEqual(database.read_bytes()[18:20], b'\x02\x02')
        self.assertEqual(set(self.home.iterdir()), {database})
        with patch.object(labels.sqlite3, 'connect', side_effect=AssertionError('可能创建 WAL 侧文件')):
            self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '')
        self.assertEqual(set(self.home.iterdir()), {database})

    def test_database_view_cannot_pull_first_user_message_as_a_title(self):
        database = self.home / 'state_5.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE private_messages(first_user_message TEXT)')
            connection.execute('INSERT INTO private_messages VALUES (?)', ('禁止读取的正文',))
            connection.execute("CREATE VIEW threads AS SELECT 'thread-one' AS id, first_user_message AS title FROM private_messages")
            connection.commit()
        self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '')

    def test_missing_or_bad_database_never_creates_files(self):
        index = labels.TitleIndex(self.home)
        self.assertEqual(index.title('thread-one'), '')
        self.assertEqual(list(self.home.iterdir()), [])
        database = self.home / 'state_5.sqlite'
        database.write_bytes(b'not a database')
        self.assertEqual(index.title('thread-one'), '')
        self.assertEqual(database.read_bytes(), b'not a database')
        self.assertEqual(set(self.home.iterdir()), {database})

    def test_symlink_index_and_database_are_not_read(self):
        target = self.home / 'auth.json'
        target.write_text(json.dumps(row(title='不应读取')), encoding='utf-8')
        try:
            self.index.symlink_to(target)
            (self.home / 'state_5.sqlite').symlink_to(target)
        except OSError:
            self.skipTest('平台未允许创建符号链接')
        with patch.object(labels.os, 'open', side_effect=AssertionError('不应读取账户文件')):
            self.assertEqual(labels.TitleIndex(self.home).title('thread-one'), '')


if __name__ == '__main__':
    unittest.main()
