"""只读尾随 Codex 会话用量；保留轮次边界，不保存聊天正文。Created by Jobs."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import re
import stat as stat_types
import threading
import time
from typing import Callable
import uuid


_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens')
_MAX_LINE_BYTES = 4 * 1024 * 1024
_INJECTION_TAGS = ('environment_context', 'INSTRUCTIONS', 'app-context', 'skills_instructions',
                   'permissions instructions', 'collaboration_mode', 'multi_agent_role',
                   'external_codex_apps_open_page')
_INJECTION_BLOCK = re.compile(r'<(' + '|'.join(re.escape(tag) for tag in _INJECTION_TAGS) +
                              r')>.*?</\1>', re.DOTALL)


@dataclass(frozen=True)
class TurnUsage:
    session_id: str
    turn_id: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_output_tokens: int | None
    ended_at: str
    status: str
    complete: bool
    started_at: str = ''
    model: str = ''
    is_subagent: bool = False
    parent_session_id: str = ''
    root_turn_id: str = ''
    note: str = ''
    user_prompt: str = ''
    thread_title: str = ''


@dataclass(frozen=True)
class UsageSnapshot:
    turns: tuple[TurnUsage, ...] = ()
    active_count: int = 0
    errors: tuple[str, ...] = ()
    file_count: int = 0
    subagent_active_count: int = 0
    loading: bool = False


def _tokens(raw: object) -> tuple[int | None, ...] | None:
    if not isinstance(raw, dict):
        return None
    result = tuple(value if type(value) is int and value >= 0 else None
                   for value in (raw.get(name) for name in _FIELDS))
    return result if result[0] is not None and result[1] is not None else None


def _sum(left: tuple[int | None, ...], right: tuple[int | None, ...]):
    return tuple(a + b if a is not None and b is not None else None
                 for a, b in zip(left, right))


def _difference(current: tuple[int | None, ...], previous: tuple[int | None, ...]):
    result = tuple(a - b if a is not None and b is not None else None
                   for a, b in zip(current, previous))
    if any(value is not None and value < 0 for value in result):
        return None
    return result


def _text(value: object) -> str:
    return value if isinstance(value, str) else ''


def _user_text(raw: object) -> str:
    if not isinstance(raw, str):
        return ''
    text = raw.lstrip()
    request = re.search(r'(?m)^## My request:[ \t]*(?:\r?\n|$)', text)
    if request:
        text = text[request.end():].lstrip()
    elif text.startswith('# Files mentioned by the user:'):
        return ''
    agents = text.startswith('# AGENTS.md')
    injected = agents or any(text.startswith('<' + tag + '>') for tag in _INJECTION_TAGS)
    if injected:
        cleaned, removed = _INJECTION_BLOCK.subn('', text)
        if not removed:
            return ''
        if agents:
            cleaned = re.sub(r'^# AGENTS\.md[^\n]*(?:\n|$)', '', cleaned)
        text = cleaned
    return text


def _prompt_summary(content: object) -> tuple[str, bool]:
    parts = []
    media = False
    if isinstance(content, str):
        parts.append(_user_text(content))
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') in ('input_text', 'text'):
                parts.append(_user_text(block.get('text')))
            elif block.get('type') in ('input_image', 'image', 'input_file', 'file'):
                media = True
    summary = ' '.join(' '.join(parts).split())
    return (summary[:159] + '…' if len(summary) > 160 else summary), media


@dataclass
class _Turn:
    turn_id: str
    started_at: str = ''
    ended_at: str = ''
    root_turn_id: str = ''
    model: str = ''
    counts: tuple[int | None, ...] | None = None
    reliable: bool = False
    precise: bool = False
    status: str = 'active'
    note: str = ''
    publish: bool = True
    responses: set[str] = field(default_factory=set)
    user_prompt: str = ''
    prompt_source: int = 0
    prompt_is_media: bool = False
    final_answer_seen: bool = False
    final_usage_seen: bool = False


class _Session:
    def __init__(self, fallback_id: str):
        self.session_id = fallback_id
        self.is_subagent = False
        self.parent_session_id = ''
        self.model = ''
        self.total: tuple[int | None, ...] | None = None
        self.active: _Turn | None = None
        self.turns: dict[str, _Turn] = {}
        self.pending_usage: tuple[int | None, ...] | None = None
        self.pending_turn_id = ''
        self.last_at = ''
        self.damaged = False

    def uncertain(self, note: str):
        self.damaged = True
        if self.active:
            self.active.reliable = False
            self.active.note = note

    def _turn(self, turn_id: str, timestamp: str, *, began: bool = False) -> _Turn:
        turn = self.turns.get(turn_id)
        if turn is None:
            turn = _Turn(turn_id, started_at=timestamp if began else '')
            self.turns[turn_id] = turn
        if began and not turn.started_at:
            turn.started_at = timestamp
        if not turn.model:
            turn.model = self.model
        return turn

    def _capture_prompt(self, payload: dict, timestamp: str, *, canonical: bool) -> list[_Turn]:
        if self.is_subagent:
            return []
        turn_id = _text(payload.get('turn_id'))
        metadata = payload.get('internal_chat_message_metadata_passthrough')
        if not turn_id and isinstance(metadata, dict):
            turn_id = _text(metadata.get('turn_id'))
        turn = self._turn(turn_id, timestamp) if turn_id else self.active
        if turn is None:
            return []
        content = payload.get('message', payload.get('content')) if canonical else payload.get('content')
        summary, media = _prompt_summary(content)
        if canonical:
            media = media or any(bool(payload.get(name)) for name in ('images', 'local_images', 'attachments'))
        media_only = bool(media and not summary)
        if media_only:
            summary = '图片或附件消息（无文字请求）'
        if not summary:
            return []
        source = 2 if canonical else 1
        if turn.user_prompt and turn.prompt_source >= source and not (turn.prompt_is_media and not media_only):
            return []
        turn.user_prompt = summary
        turn.prompt_source = source
        turn.prompt_is_media = media_only
        return [turn] if turn.status != 'active' else []

    def consume(self, row: dict, history: bool, include_history: bool) -> list[_Turn]:
        timestamp = _text(row.get('timestamp'))
        self.last_at = timestamp or self.last_at
        payload = row.get('payload')
        if not isinstance(payload, dict):
            return []
        kind = _text(row.get('type'))
        if kind == 'event_msg':
            kind = _text(payload.get('type'))
        changed: list[_Turn] = []
        final_answer = payload.get('phase') == 'final_answer' and (
            kind == 'agent_message' and row.get('type') == 'event_msg' or
            kind == 'response_item' and payload.get('role') == 'assistant')
        if final_answer:
            turn_id = _text(payload.get('turn_id'))
            metadata = payload.get('internal_chat_message_metadata_passthrough')
            if not turn_id and isinstance(metadata, dict):
                turn_id = _text(metadata.get('turn_id'))
            turn = self._turn(turn_id, timestamp) if turn_id else self.active
            if turn is not None:
                turn.final_answer_seen = True
                turn.final_usage_seen = False
        elif kind == 'user_message' and row.get('type') == 'event_msg':
            changed.extend(self._capture_prompt(payload, timestamp, canonical=True))
        elif kind == 'response_item' and payload.get('role') == 'user':
            changed.extend(self._capture_prompt(payload, timestamp, canonical=False))
        elif kind == 'session_meta':
            self.session_id = _text(payload.get('id')) or self.session_id
            self.parent_session_id = _text(payload.get('parent_thread_id'))
            source = payload.get('source')
            self.is_subagent = bool(self.parent_session_id or
                                    isinstance(source, dict) and source.get('subagent'))
            if not self.parent_session_id and isinstance(source, dict):
                subagent = source.get('subagent')
                if isinstance(subagent, dict) and isinstance(subagent.get('thread_spawn'), dict):
                    self.parent_session_id = _text(subagent['thread_spawn'].get('parent_thread_id'))
        elif kind == 'turn_context':
            self.model = _text(payload.get('model')) or self.model
            if self.active is None and _text(payload.get('turn_id')):
                candidate = self._turn(payload['turn_id'], timestamp)
                if candidate.status == 'active':
                    self.active = candidate
            if self.active:
                self.active.model = self.model
        elif kind == 'task_started':
            turn_id = _text(payload.get('turn_id'))
            if not turn_id:
                self.uncertain('开始事件缺少轮次标识。')
                return changed
            existing = self.turns.get(turn_id)
            if existing is not None and existing.status != 'active':
                return changed
            if self.active and self.active.turn_id != turn_id:
                self.active.status = 'interrupted'
                self.active.ended_at = timestamp
                self.active.reliable = False
                self.active.note = '下一轮已开始，但上一轮缺少结束事件。'
                self.active.publish = include_history or not history
                changed.append(self.active)
            turn = self._turn(turn_id, timestamp, began=True)
            if turn.status != 'active':
                return changed
            turn.root_turn_id = _text(payload.get('root_turn_id')) or turn.root_turn_id
            if not turn.root_turn_id and isinstance(payload.get('turn_attribution'), dict):
                turn.root_turn_id = _text(payload['turn_attribution'].get('root_turn_id'))
            self.active = turn
            self.pending_usage = None
            self.pending_turn_id = ''
            self.damaged = False
        elif kind == 'token_usage_record':
            thread_id = _text(payload.get('thread_id'))
            if thread_id and thread_id != self.session_id:
                self.uncertain('用量事件线程标识不匹配。')
                return changed
            turn_id = _text(payload.get('turn_id'))
            if not turn_id:
                self.uncertain('用量事件缺少轮次标识。')
                return changed
            turn = self._turn(turn_id, timestamp)
            if self.active is None and turn.status == 'active':
                self.active = turn
            turn.root_turn_id = _text(payload.get('root_turn_id')) or turn.root_turn_id
            cumulative = _tokens(payload.get('turn_token_usage'))
            individual = _tokens(payload.get('usage'))
            response_id = _text(payload.get('response_id'))
            duplicate = bool(response_id and response_id in turn.responses)
            if duplicate:
                if cumulative is None:
                    return changed
                if turn.counts is not None and any(cumulative[index] < turn.counts[index]
                                                  for index in (0, 1)):
                    return changed
            if response_id:
                turn.responses.add(response_id)
            if cumulative is not None:
                # 本轮累计包含本轮此前调用，也能恢复尾读前未读到的调用。
                turn.counts = cumulative
                turn.reliable = True
                turn.precise = True
                turn.note = ''
                if turn.final_answer_seen and not duplicate:
                    turn.final_usage_seen = True
            elif individual is not None:
                if response_id:
                    turn.counts = individual if turn.counts is None else _sum(turn.counts, individual)
                turn.reliable = False
                turn.note = '仅发现单次调用用量，无法确认整轮是否完整。'
            # 新旧格式的线程累计口径可能不同，不能把 thread_token_usage 用作旧格式基线。
            if self.active is turn:
                self.pending_usage = individual
                self.pending_turn_id = turn_id
            if turn.status != 'active':
                changed.append(turn)
        elif kind == 'token_count':
            info = payload.get('info')
            if not isinstance(info, dict):
                return changed
            current = _tokens(info.get('total_token_usage'))
            last = _tokens(info.get('last_token_usage'))
            if current is None:
                if self.active and last is not None:
                    self.active.reliable = False
                    self.active.note = '仅有最后一次调用用量，无法确认整轮累计。'
                return changed
            previous = self.total
            self.total = current
            if self.active is None:
                return changed
            turn = self.active
            if turn.precise:
                if self.pending_turn_id == turn.turn_id:
                    self.pending_usage = None
                    self.pending_turn_id = ''
                    return changed
                if previous is None:
                    return changed
                delta = _difference(current, previous)
                if delta is None:
                    turn.reliable = False
                    turn.note = '精确记录之后兼容累计发生重置；整轮统计待确认。'
                elif delta[0] or delta[1]:
                    # 兼容事件可能滞后于精确累计；无法对齐时保留精确值，避免再次加同次调用。
                    turn.reliable = False
                    turn.note = '精确记录之后发现未对齐的兼容用量；保留精确累计，整轮统计待确认。'
                return changed
            before_reliable, before_note = turn.reliable, turn.note
            if previous is not None:
                delta = _difference(current, previous)
                if delta is None:
                    delta = last
                    turn.reliable = False
                    turn.note = '线程累计发生重置；这里只保留可确认的调用用量。'
                elif turn.counts is None:
                    turn.reliable = bool(turn.started_at and not self.damaged)
            elif last is not None:
                delta = last
                turn.reliable = bool(current == last and turn.started_at and not self.damaged)
                if not turn.reliable:
                    turn.note = '缺少轮次前累计基线；这里只保留可确认的调用用量。'
            else:
                delta = None
                turn.reliable = False
                turn.note = '缺少轮次前累计基线和单次调用用量。'
            if self.pending_usage is not None:
                # 某些版本同时发精确记录和兼容事件；同一次调用只记一次。
                if delta == self.pending_usage:
                    delta = None
                    turn.reliable, turn.note = before_reliable, before_note
                else:
                    turn.reliable = False
                    turn.note = '两种用量事件无法完全对齐。'
                self.pending_usage = None
                self.pending_turn_id = ''
            if delta is not None:
                turn.counts = delta if turn.counts is None else _sum(turn.counts, delta)
        elif kind in ('task_complete', 'turn_aborted'):
            turn_id = _text(payload.get('turn_id'))
            if not turn_id:
                turn_id = self.active.turn_id if self.active else ''
            if not turn_id:
                return changed
            turn = self._turn(turn_id, timestamp)
            turn.status = 'completed' if kind == 'task_complete' else 'aborted'
            turn.ended_at = timestamp
            turn.publish = include_history or not history
            if self.active is turn:
                self.active = None
            if turn.counts is None:
                turn.reliable = False
                turn.note = '结束事件已到达，尚未发现本轮可归属的用量。'
            changed.append(turn)
        return changed

    def result(self, turn: _Turn, *, unfinished: bool = False) -> TurnUsage:
        counts = turn.counts or (None, None, None, None)
        return TurnUsage(self.session_id, turn.turn_id, *counts,
                         turn.ended_at or self.last_at,
                         'unfinished' if unfinished else turn.status,
                         bool(turn.reliable and turn.counts is not None and not unfinished),
                         turn.started_at, turn.model, self.is_subagent,
                         self.parent_session_id, turn.root_turn_id,
                         '未发现结束事件，轮次可能仍在运行或已中断。' if unfinished else turn.note,
                         turn.user_prompt)


@dataclass
class _Reader:
    path: Path
    identity: tuple[int, int]
    session: _Session
    offset: int = 0
    pending: bytes = b''
    skipping_line: bool = False
    initial_end: int = 0
    mtime: float = 0
    size: int = 0


def _open_usage_transcript(home: Path, transcript_path: Path):
    """校验目录边界后打开固定文件句柄；支持的平台逐级拒绝符号链接。"""
    if not isinstance(home, (str, os.PathLike)) or not isinstance(transcript_path, (str, os.PathLike)):
        return None
    root = (Path(home).expanduser() / 'sessions').absolute()
    candidate = Path(transcript_path).expanduser()
    if not candidate.is_absolute() or candidate.suffix != '.jsonl':
        return None
    try:
        relative = candidate.relative_to(root)
        if not relative.parts or any(part in ('.', '..') for part in relative.parts):
            return None
        if root.is_symlink() or any((root.joinpath(*relative.parts[:index])).is_symlink()
                                    for index in range(1, len(relative.parts) + 1)):
            return None
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if not stat_types.S_ISREG(resolved.stat().st_mode):
            return None
        file_flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        file_flags |= getattr(os, 'O_NOFOLLOW', 0)
        descriptor = None
        directory_descriptor = None
        try:
            if os.open in os.supports_dir_fd and hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW'):
                directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory_descriptor = os.open(resolved_root, directory_flags)
                for part in relative.parts[:-1]:
                    child = os.open(part, directory_flags, dir_fd=directory_descriptor)
                    os.close(directory_descriptor)
                    directory_descriptor = child
                descriptor = os.open(relative.parts[-1], file_flags, dir_fd=directory_descriptor)
            else:
                descriptor = os.open(candidate, file_flags)
            opened = os.fstat(descriptor)
            if not stat_types.S_ISREG(opened.st_mode):
                return None
            current = candidate.stat()
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                return None
            if root.is_symlink() or any((root.joinpath(*relative.parts[:index])).is_symlink()
                                       for index in range(1, len(relative.parts) + 1)):
                return None
            candidate.resolve(strict=True).relative_to(resolved_root)
            stream = os.fdopen(descriptor, 'rb')
            descriptor = None
            return stream
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory_descriptor is not None:
                os.close(directory_descriptor)
    except (OSError, ValueError, RuntimeError):
        return None


def read_turn_usage(home: Path, transcript_path: Path, session_id: str,
                    turn_id: str) -> TurnUsage | None:
    """Stop 钩子的单次快照；最终答复之后的精确用量落盘才确认完整。

    只扫描给定日志末尾 16 MiB 和首行最多 64 KiB 元数据，不等待新事件。
    已发现轮次但最终答复用量尚未确认时，返回 complete=False；缺失或不安全返回 None。
    """
    if not isinstance(session_id, str) or not session_id or not isinstance(turn_id, str) or not turn_id:
        return None
    stream = _open_usage_transcript(home, transcript_path)
    if stream is None:
        return None
    try:
        with stream:
            size = os.fstat(stream.fileno()).st_size
            session = _Session(session_id)
            metadata = stream.readline(min(65537, size))
            if len(metadata) <= 65536 and metadata.endswith(b'\n'):
                try:
                    first = json.loads(metadata)
                except (ValueError, UnicodeDecodeError):
                    first = None
                if isinstance(first, dict) and first.get('type') == 'session_meta':
                    session.consume(first, True, True)
            if session.session_id != session_id:
                return None
            offset = max(0, size - 16 * 1024 * 1024)
            stream.seek(offset)
            remaining = size - offset
            skipping = bool(offset)
            while remaining > 0:
                raw = stream.readline(min(_MAX_LINE_BYTES + 1, remaining))
                if not raw:
                    break
                remaining -= len(raw)
                newline = raw.endswith(b'\n')
                if skipping:
                    skipping = not newline
                    continue
                if not newline:
                    if len(raw) > _MAX_LINE_BYTES:
                        session.uncertain('日志单行过大，最终用量尚未确认。')
                        skipping = True
                    continue
                if len(raw) > _MAX_LINE_BYTES:
                    session.uncertain('日志单行过大，最终用量尚未确认。')
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    session.uncertain('日志含无法解析的完整行，最终用量尚未确认。')
                    continue
                if isinstance(row, dict):
                    session.consume(row, True, True)
                if len(session.turns) > 256:
                    keep = {turn_id, session.active.turn_id if session.active else ''}
                    session.turns = {key: value for key, value in session.turns.items() if key in keep}
            if session.session_id != session_id:
                return None
            turn = session.turns.get(turn_id)
            if turn is None:
                return None
            result = session.result(turn)
            complete = bool(result.complete and turn.final_answer_seen and turn.final_usage_seen)
            return replace(result, complete=complete,
                           note=result.note if complete else result.note or '最终答复用量尚未确认。')
    except (OSError, ValueError, RuntimeError):
        return None


class UsageMonitor:
    """poll 可在后台调用；每次只读新增字节，定期重新发现活跃日志。

    首次读取最近 max_files 份日志；过大日志只读末尾 initial_bytes。
    include_history=False 会隐藏接入前已结束的轮次，但保留进行中轮次基线。
    未结束轮次超过 active_idle_seconds 不再计入 active_count，显示为未确认结束。
    进程重启通过日志重建，不写 Codex 文件、凭据或聊天副本。
    """
    def __init__(self, home: Path, *, include_history: bool = True,
                 max_files: int = 64, max_turns: int = 200,
                 discovery_interval: float = 10.0,
                 initial_bytes: int = 16 * 1024 * 1024,
                 read_bytes: int = 4 * 1024 * 1024,
                 poll_bytes: int = 8 * 1024 * 1024,
                 active_idle_seconds: float = 3600.0,
                 clock: Callable[[], float] = time.time,
                 cancelled: Callable[[], bool] | None = None):
        self.home = Path(home).expanduser()
        self.include_history = include_history
        self.max_files = max(1, max_files)
        self.max_turns = max(1, max_turns)
        self.discovery_interval = max(0.0, discovery_interval)
        self.initial_bytes = max(1, initial_bytes)
        self.read_bytes = max(1, read_bytes)
        self.poll_bytes = max(1, poll_bytes)
        self.active_idle_seconds = max(0.0, active_idle_seconds)
        self._clock = clock
        self._cancelled = cancelled or (lambda: False)
        self._last_discovery: float | None = None
        self._readers: dict[Path, _Reader] = {}
        self._directories: dict[Path, tuple[int, tuple[Path, ...], tuple[Path, ...]]] = {}
        self._completed: dict[tuple[str, str], TurnUsage] = {}
        self._errors: list[str] = []
        self._lock = threading.RLock()

    def _error(self, message: str):
        if message not in self._errors:
            self._errors.append(message)
            self._errors = self._errors[-8:]

    def _new_reader(self, path: Path, stat) -> _Reader:
        offset = max(0, stat.st_size - self.initial_bytes)
        try:
            fallback_id = str(uuid.UUID(path.stem[-36:]))
        except ValueError:
            fallback_id = path.stem
        session = _Session(fallback_id)
        reader = _Reader(path, (stat.st_dev, stat.st_ino), session,
                         offset=offset, skipping_line=bool(offset),
                         initial_end=stat.st_size, mtime=stat.st_mtime, size=stat.st_size)
        if offset:
            session.damaged = True
            try:
                with path.open('rb') as stream:
                    first = stream.readline(65537)
                if first.endswith(b'\n') and len(first) <= 65536:
                    metadata = json.loads(first)
                    if isinstance(metadata, dict) and metadata.get('type') == 'session_meta':
                        session.consume(metadata, True, False)
            except (OSError, ValueError, UnicodeDecodeError):
                pass
        return reader

    def _inventory(self, root: Path) -> list[Path]:
        """目录名称只在目录 mtime 改变时重读；缓存中的旧文件仍检查 mtime。"""
        stack = [root]
        visited = set()
        files = []
        while stack and not self._cancelled():
            directory = stack.pop()
            try:
                directory_stat = directory.stat()
                entry = self._directories.get(directory)
                if entry is None or entry[0] != directory_stat.st_mtime_ns:
                    child_directories = []
                    child_files = []
                    for path in directory.iterdir():
                        if path.is_symlink():
                            continue
                        path_stat = path.stat()
                        if stat_types.S_ISDIR(path_stat.st_mode):
                            child_directories.append(path)
                        elif stat_types.S_ISREG(path_stat.st_mode) and path.suffix == '.jsonl':
                            child_files.append(path)
                    entry = (directory_stat.st_mtime_ns, tuple(child_directories), tuple(child_files))
                    self._directories[directory] = entry
                visited.add(directory)
                stack.extend(entry[1])
                files.extend(entry[2])
            except OSError:
                self._error('部分会话目录暂时无法读取；下一次发现会重试。')
        if not self._cancelled():
            self._directories = {path: entry for path, entry in self._directories.items() if path in visited}
        return files

    def _discover(self, now: float):
        root = self.home / 'sessions'
        if not root.exists():
            self._error('未找到 Codex 会话目录；等待 Codex 生成本地日志。')
            return
        candidates = []
        try:
            for path in self._inventory(root):
                if self._cancelled():
                    return
                if path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat_types.S_ISREG(stat.st_mode):
                    candidates.append((stat.st_mtime, str(path), path, stat))
        except OSError:
            self._error('无法列出 Codex 会话目录；检查读取权限。')
            return
        candidates.sort(reverse=True)
        selected = candidates[:self.max_files]
        paths = {item[2] for item in selected}
        self._readers = {path: reader for path, reader in self._readers.items() if path in paths}
        for _, _, path, stat in selected:
            if path not in self._readers:
                self._readers[path] = self._new_reader(path, stat)
        self._last_discovery = now

    def _consume_line(self, reader: _Reader, raw: bytes, end_offset: int):
        if not raw.strip():
            return
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            reader.session.uncertain('本轮日志含无法解析的完整行。')
            self._error('会话日志含无法解析的完整行；相关轮次标记为部分。')
            return
        if not isinstance(row, dict):
            return
        changed = reader.session.consume(row, end_offset <= reader.initial_end,
                                         self.include_history)
        for turn in changed:
            if turn.publish:
                result = reader.session.result(turn)
                self._completed[(result.session_id, result.turn_id)] = result
        if len(reader.session.turns) > self.max_turns * 2:
            removable = [key for key, turn in reader.session.turns.items()
                         if turn is not reader.session.active]
            for key in removable[:len(reader.session.turns) - self.max_turns]:
                del reader.session.turns[key]

    def _read(self, reader: _Reader, budget: int) -> int:
        consumed = 0
        try:
            if reader.path.is_symlink():
                self._error('会话日志已变为符号链接；暂时停止读取该文件。')
                return consumed
            stat = reader.path.stat()
            if (stat.st_dev, stat.st_ino) != reader.identity or stat.st_size < reader.offset:
                # 原文件被替换或截短后重新解析；输出以线程和轮次键覆盖，避免重复。
                replacement = self._new_reader(reader.path, stat)
                replacement.initial_end = 0
                reader = replacement
                self._readers[reader.path] = reader
            reader.mtime, reader.size = stat.st_mtime, stat.st_size
            if stat.st_size <= reader.offset:
                return consumed
            with reader.path.open('rb') as stream:
                stream.seek(reader.offset)
                remaining = min(self.read_bytes, budget)
                while remaining > 0 and not self._cancelled():
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    chunk_start = reader.offset
                    reader.offset += len(chunk)
                    consumed += len(chunk)
                    remaining -= len(chunk)
                    data = reader.pending + chunk
                    data_start = chunk_start - len(reader.pending)
                    reader.pending = b''
                    position = 0
                    while True:
                        newline = data.find(b'\n', position)
                        if newline < 0:
                            remainder = data[position:]
                            if len(remainder) > _MAX_LINE_BYTES:
                                reader.skipping_line = True
                                reader.session.uncertain('日志单行过大，已跳过；本轮统计可能不完整。')
                                self._error('会话日志单行超过大小限制；相关轮次标记为部分。')
                            elif not reader.skipping_line:
                                reader.pending = remainder
                            break
                        raw = data[position:newline]
                        if reader.skipping_line:
                            reader.skipping_line = False
                        elif len(raw) <= _MAX_LINE_BYTES:
                            self._consume_line(reader, raw, data_start + newline + 1)
                        else:
                            reader.session.uncertain('日志单行过大，已跳过；本轮统计可能不完整。')
                            self._error('会话日志单行超过大小限制；相关轮次标记为部分。')
                        position = newline + 1
        except OSError:
            self._error('某份会话日志暂时无法读取；下一次轮询会重试。')
        return consumed

    def poll(self) -> UsageSnapshot:
        with self._lock:
            self._errors = []
            now = self._clock()
            if self._last_discovery is None or now - self._last_discovery >= self.discovery_interval:
                self._discover(now)
            budget = self.poll_bytes
            for reader in tuple(self._readers.values()):
                if budget <= 0 or self._cancelled():
                    break
                budget -= self._read(reader, budget)
            active = set()
            subagent_active = set()
            unfinished = {}
            for reader in self._readers.values():
                turn = reader.session.active
                if turn is None:
                    continue
                key = (reader.session.session_id, turn.turn_id)
                if now - reader.mtime <= self.active_idle_seconds:
                    (subagent_active if reader.session.is_subagent else active).add(key)
                elif self.include_history or reader.offset > reader.initial_end:
                    unfinished[key] = reader.session.result(turn, unfinished=True)
            merged = {**self._completed, **unfinished}
            turns = sorted(merged.values(), key=lambda item: (item.ended_at, item.session_id, item.turn_id),
                           reverse=True)[:self.max_turns]
            if len(self._completed) > self.max_turns * 2:
                keep = {(item.session_id, item.turn_id) for item in turns}
                self._completed = {key: value for key, value in self._completed.items() if key in keep}
            loading = any(reader.offset < reader.size for reader in self._readers.values())
            return UsageSnapshot(tuple(turns), len(active), tuple(self._errors), len(self._readers),
                                 len(subagent_active), loading)
