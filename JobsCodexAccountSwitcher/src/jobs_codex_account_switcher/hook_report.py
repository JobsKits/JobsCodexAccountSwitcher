"""Stop 钩子的同轮 Token 提示；失败只显示未知，不改写回复。Created by Jobs."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import sys

from . import usage


MAX_STDIN_BYTES = 128 * 1024
UNKNOWN_MESSAGE = '本轮 Token：待统计/未知，浮窗完成后更新。'


def _open_windows_standard_stream(*, reading: bool):
    """窗口版成品只复制继承的管道句柄，不分配控制台或关闭原句柄。"""
    if sys.platform != 'win32':
        raise OSError('standard stream unavailable')
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    source = kernel.GetStdHandle(((-10 if reading else -11) + 2 ** 32) % 2 ** 32)
    invalid = ctypes.c_void_p(-1).value
    if source is None or source in (0, -1, invalid):
        raise OSError('standard handle unavailable')
    process = kernel.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, source, process, ctypes.byref(duplicate), 0, False, 2):
        raise OSError('standard handle duplication failed')
    if duplicate.value is None or duplicate.value in (0, -1, invalid):
        raise OSError('duplicate handle unavailable')
    flags = ((os.O_RDONLY if reading else os.O_WRONLY) |
             getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0))
    try:
        descriptor = msvcrt.open_osfhandle(duplicate.value, flags)
    except Exception:
        kernel.CloseHandle(duplicate.value)
        raise
    try:
        return os.fdopen(descriptor, 'rb' if reading else 'wb')
    except Exception:
        # open_osfhandle 已接管复制句柄，只关闭它创建的描述符。
        os.close(descriptor)
        raise


def _unique_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError('duplicate key')
        document[key] = value
    return document


def _invalid_constant(value):
    raise ValueError('invalid JSON constant')


def _read_input(stream) -> dict:
    # 真实 stdin 读取字节流；多读一个字节仅用于判定是否超限。
    reader = getattr(stream, 'buffer', stream)
    raw = reader.read(MAX_STDIN_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if not isinstance(raw, bytes) or len(raw) > MAX_STDIN_BYTES:
        raise ValueError('input limit')
    document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_keys,
                          parse_constant=_invalid_constant)
    if not isinstance(document, dict):
        raise ValueError('object required')
    return document


def _identifier(value) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256 or
            any(character.isspace() or ord(character) < 32 for character in value)):
        raise ValueError('invalid identifier')
    return value


def _plain_path(path: Path, *, directory: bool):
    details = path.lstat()
    reparse = getattr(details, 'st_file_attributes', 0) & getattr(
        stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    if stat.S_ISLNK(details.st_mode) or reparse:
        raise ValueError('link rejected')
    if not (stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode)):
        raise ValueError('ordinary path required')


def _safe_transcript(home, value) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('invalid transcript')
    selected_home = Path(home).expanduser().absolute()
    _plain_path(selected_home, directory=True)
    resolved_home = selected_home.resolve(strict=True)
    sessions = resolved_home / 'sessions'
    _plain_path(sessions, directory=True)
    candidate = Path(value)
    if not candidate.is_absolute() or '..' in candidate.parts:
        raise ValueError('absolute transcript required')

    # 允许 macOS /var 之类位于所选目录外的系统路径别名，目录内的链接仍逐层拒绝。
    for base in (selected_home / 'sessions', sessions):
        try:
            relative = candidate.relative_to(base)
        except ValueError:
            continue
        if not relative.parts:
            raise ValueError('transcript file required')
        _plain_path(base, directory=True)
        cursor = base
        for index, part in enumerate(relative.parts):
            cursor /= part
            _plain_path(cursor, directory=index < len(relative.parts) - 1)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(sessions)
        return resolved_home, resolved
    raise ValueError('transcript outside sessions')


def _message(turn) -> str:
    if (turn is None or turn.complete is not True or
            type(turn.input_tokens) is not int or turn.input_tokens < 0 or
            type(turn.output_tokens) is not int or turn.output_tokens < 0):
        return UNKNOWN_MESSAGE
    agent = '子代理' if turn.is_subagent else '主代理'
    total = turn.input_tokens + turn.output_tokens
    return (f'本轮 Token（{agent}）：上行 {turn.input_tokens:,}，'
            f'下行 {turn.output_tokens:,}，合计 {total:,}。')


def report_from_stdin(home, stream=sys.stdin) -> int:
    """只输出官方 systemMessage JSON；日志未落盘或输入不安全时不中断轮次。"""
    message = UNKNOWN_MESSAGE
    input_unavailable = False
    try:
        with ExitStack() as owned:
            try:
                if stream is None:
                    try:
                        stream = owned.enter_context(_open_windows_standard_stream(reading=True))
                    except Exception:
                        input_unavailable = True
                        raise
                document = _read_input(stream)
                if document.get('hook_event_name') != 'Stop':
                    raise ValueError('Stop required')
                session_id = _identifier(document.get('session_id'))
                turn_id = _identifier(document.get('turn_id'))
                resolved_home, transcript = _safe_transcript(home, document.get('transcript_path'))
                turn = usage.read_turn_usage(resolved_home, transcript, session_id, turn_id)
                message = _message(turn)
            except Exception:
                # 不把聊天正文、文件路径或读取异常带入输出，也不触发继续执行。
                pass
            output = sys.stdout
            binary_output = output is None
            if binary_output:
                output = owned.enter_context(_open_windows_standard_stream(reading=False))
            # ASCII JSON 在 Windows 任意控制台编码中均能完整传递中文提示。
            document = json.dumps({'systemMessage': message}, ensure_ascii=True) + '\n'
            output.write(document.encode('ascii') if binary_output else document)
            output.flush()
    except Exception:
        # 未继承输出管道时不能伪装为已成功报告。
        return 1
    return 1 if input_unavailable else 0
