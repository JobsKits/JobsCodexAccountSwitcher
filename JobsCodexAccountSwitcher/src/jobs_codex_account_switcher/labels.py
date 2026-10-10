"""只读会话标题索引；不读取消息正文、凭据或账户库。Created by Jobs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time


def _signature(path):
    try:
        if path.is_symlink():
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return None
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size
    except OSError:
        return None


def _title(value):
    if not isinstance(value, str):
        return ''
    return ' '.join(value[:2048].split())[:512]


def _updated_at(value):
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


class TitleIndex:
    """以文件变化缓存标题；大 JSONL 只读尾部，数据库只查询 id/title。"""
    def __init__(self, home, *, max_bytes=2 * 1024 * 1024, max_titles=2000,
                 max_databases=4, query_seconds=0.05):
        self.home = Path(home).expanduser().absolute()
        self.max_bytes = max(1, int(max_bytes))
        self.max_titles = max(1, int(max_titles))
        self.max_databases = max(1, int(max_databases))
        self.query_seconds = max(0.001, float(query_seconds))
        self._index_signature = object()
        self._titles = {}
        self._database_inventory = object()
        self._database_paths = []
        self._database_signature = None
        self._database_titles = {}
        self._lock = threading.RLock()

    def _refresh_index(self):
        path = self.home / 'session_index.jsonl'
        signature = _signature(path)
        if signature == self._index_signature:
            return
        self._index_signature = signature
        self._titles = {}
        if signature is None:
            return
        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(path, flags), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    return
                offset = max(0, info.st_size - self.max_bytes)
                stream.seek(max(0, offset - 1))
                boundary = not offset or stream.read(1) == b'\n'
                raw = stream.read(self.max_bytes)
            if offset and not boundary:
                # 尾读起点可能位于 UTF-8/JSON 行中间，只接受后续完整行。
                _, separator, raw = raw.partition(b'\n')
                if not separator:
                    return
            titles = {}
            for line in raw.splitlines():
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                session_id = row.get('id')
                updated = _updated_at(row.get('updated_at'))
                if (not isinstance(session_id, str) or not session_id or len(session_id) > 256 or
                        not isinstance(row.get('thread_name'), str) or updated is None):
                    continue
                previous = titles.get(session_id)
                if previous is None or updated >= previous[0]:
                    titles[session_id] = updated, _title(row['thread_name'])
            newest = sorted(titles.items(), key=lambda item: item[1][0], reverse=True)[:self.max_titles]
            self._titles = {key: value[1] for key, value in newest}
        except (OSError, ValueError, UnicodeError):
            return

    def _databases(self):
        try:
            inventory = self.home.stat().st_mtime_ns
            if inventory != self._database_inventory:
                candidates = [(signature[2], str(path), path)
                              for path in self.home.glob('state_*.sqlite')
                              if (signature := _signature(path)) is not None]
                candidates.sort(reverse=True)
                self._database_paths = [item[2] for item in candidates[:self.max_databases]]
                self._database_inventory = inventory
        except OSError:
            self._database_paths = []
        result = []
        for path in self._database_paths:
            signature = _signature(path)
            if signature is not None:
                wal, journal = Path(str(path) + '-wal'), Path(str(path) + '-journal')
                sidecars = tuple(('unsafe',) if item.is_symlink() else _signature(item)
                                 for item in (wal, journal))
                result.append((path, signature, *sidecars))
        return result

    @staticmethod
    def _authorize(action, first, second, database, source):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if first == 'threads' and second in ('id', 'title') else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second == 'substr':
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == 'query_only':
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _query_title(self, path, session_id):
        connection = None
        try:
            # WAL 模式即使暂时没有侧文件，SQLite 也可能创建它们；先读固定头识别。
            with path.open('rb') as stream:
                header = stream.read(100)
            if not header.startswith(b'SQLite format 3\0') or len(header) < 100 or header[18:20] != b'\x01\x01':
                return '', False
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)
            connection.set_authorizer(self._authorize)
            connection.execute('PRAGMA query_only=ON')
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024)
            deadline = time.monotonic() + self.query_seconds
            connection.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            row = connection.execute('SELECT substr(title, 1, 512) FROM threads WHERE id=? LIMIT 1',
                                     (session_id,)).fetchone()
            return _title(row[0]) if row else '', True
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return '', False
        finally:
            if connection is not None:
                connection.close()

    def _database_title(self, session_id):
        databases = self._databases()
        signature = tuple(databases)
        if signature != self._database_signature:
            self._database_signature = signature
            self._database_titles = {}
        if session_id in self._database_titles:
            return self._database_titles[session_id]
        successful = not databases
        title = ''
        for path, _, wal, journal in databases:
            # 活跃 WAL 的只读连接仍可能维护 -shm；不触碰该类文件。
            if wal is not None or journal is not None:
                continue
            found, ok = self._query_title(path, session_id)
            successful = successful or ok
            if found:
                title = found
                break
        if successful:
            if len(self._database_titles) >= self.max_titles:
                self._database_titles.pop(next(iter(self._database_titles)))
            self._database_titles[session_id] = title
        return title

    def title(self, session_id) -> str:
        if (not isinstance(session_id, str) or not session_id or len(session_id) > 256 or
                '\0' in session_id or self.home.is_symlink()):
            return ''
        with self._lock:
            self._refresh_index()
            if session_id in self._titles:
                return self._titles[session_id]
            return self._database_title(session_id)
