"""桌面程序发现与运行保护。Created by Jobs."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import psutil

from .core import SwitchError


def discover_apps():
    """按 Bundle ID 发现 macOS 官方应用；Windows 使用用户选择路径。"""
    found = []
    if sys.platform == 'darwin':
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            for app in root.glob('*.app'):
                try:
                    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
                    if info.get('CFBundleIdentifier') in ('com.openai.codex', 'com.openai.chat'):
                        found.append(app)
                except (OSError, ValueError):
                    pass
    elif sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
        for path in (base / 'Programs/Codex/Codex.exe', base / 'Programs/ChatGPT/ChatGPT.exe'):
            if path.is_file():
                found.append(path)
    return found


def require_stopped():
    """拒绝任何 Codex、ChatGPT、已知包装器进程，包括 CLI 和后端。"""
    names = ('codex', 'chatgpt', 'codexplusplus', 'codex++')
    try:
        for process in psutil.process_iter(['pid', 'name']):
            if process.info['pid'] == os.getpid():
                continue
            name = (process.info['name'] or '').lower()
            if name in names or name in ('codex.exe', 'chatgpt.exe', 'codexplusplus.exe', 'codex-code-mode-host', 'codex-code-mode-host.exe') or name.startswith('codex ('):
                raise SwitchError(f'检测到其它 Codex / CLI / IDE 后端仍在运行（PID {process.pid}：{process.info["name"]}）。请关闭该客户端后重试。')
    except (psutil.AccessDenied, psutil.Error):
        raise SwitchError('无法可靠检查进程，停止切换。') from None


def launch(path: Path):
    """只启动明确选择的 APP / EXE，不使用 shell 拼接。"""
    if not path.exists():
        raise SwitchError('请选择实际存在的桌面应用。')
    if sys.platform == 'darwin' and path.suffix == '.app':
        subprocess.run(['open', '-a', str(path)], check=True, timeout=15)
    elif sys.platform == 'win32' and path.suffix.lower() == '.exe':
        subprocess.Popen([str(path)], cwd=path.parent)
    else:
        raise SwitchError('macOS 请选择 .app，Windows 请选择 .exe。')


def app_processes(path: Path):
    """按所选程序路径及其子进程定位，不误关浏览器扩展或其它应用。"""
    root = str(path.resolve())
    found = {}
    for process in psutil.process_iter(['pid', 'exe', 'name']):
        if process.pid == os.getpid():
            continue
        executable = process.info['exe']
        if not executable:
            continue
        value = str(Path(executable).resolve())
        if sys.platform == 'win32':
            matches = value.casefold() == root.casefold()
        else:
            matches = value.startswith(root + os.sep) if path.suffix == '.app' else value == root
        if matches:
            found[process.pid] = process
            try:
                for child in process.children(recursive=True):
                    if child.pid != os.getpid():
                        found[child.pid] = child
            except psutil.NoSuchProcess:
                pass
    return list(found.values())


def close_app(path: Path):
    """先正常退出，等待后清理目标程序残留；不触碰无关 Codex 客户端。"""
    processes = app_processes(path)
    if not processes:
        return
    if sys.platform == 'darwin':
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
        bundle = info.get('CFBundleIdentifier')
        if not bundle:
            raise SwitchError('所选应用没有 Bundle ID，无法正常退出。')
        script = 'on run argv\n tell application id (item 1 of argv) to quit\nend run'
        try:
            subprocess.run(['osascript', '-e', script, bundle], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
    elif sys.platform == 'win32':
        env = dict(os.environ, JOBS_CLOSE_PIDS=','.join(str(p.pid) for p in processes))
        script = '$env:JOBS_CLOSE_PIDS.Split(",") | ForEach-Object { $p = Get-Process -Id ([int]$_) -ErrorAction SilentlyContinue; if ($p) { $null = $p.CloseMainWindow() } }'
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], env=env,
                           check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
    else:
        raise SwitchError('仅支持 Windows 和 macOS。')
    _, alive = psutil.wait_procs(processes, timeout=5)
    # 仅处理已定位的目标进程；psutil 会检查 PID 复用。
    for process in alive:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=2)
    if alive or app_processes(path):
        raise SwitchError('所选桌面应用未能完全退出，未切换登录文件。')
