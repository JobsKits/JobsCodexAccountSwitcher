"""仅发布当前工具 dist 内已验证产物的第一层入口。Created by Jobs."""
import json
import os
from pathlib import Path
import subprocess
import sys


def checked_dist(root):
    """拒绝快捷方式根目录或 dist 被符号链接改向。"""
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise RuntimeError(f'交付目录必须是真实目录：{root}')
    dist = root / 'dist'
    if dist.is_symlink() or dist.resolve().parent != root:
        raise RuntimeError(f'拒绝处理符号链接或外部 dist：{dist}')
    return root, dist


def windows_shortcuts(root, artifacts, clear=False):
    """使用普通用户可用的系统 COM 创建、校验或移除 .lnk。"""
    script = r'''
$ErrorActionPreference = 'Stop'
$root = $env:JOBS_ACCOUNT_SHORTCUT_ROOT
$dist = [IO.Path]::GetFullPath((Join-Path $root 'dist')).TrimEnd('\') + '\'
$shell = New-Object -ComObject WScript.Shell
function Test-OwnedShortcut($path) {
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a reparse-point shortcut: $path"
    }
    $link = $shell.CreateShortcut($path)
    return $link.TargetPath.StartsWith($dist, [StringComparison]::OrdinalIgnoreCase)
}
if ($env:JOBS_ACCOUNT_SHORTCUT_CLEAR -eq '1') {
    Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File | ForEach-Object {
        if (Test-OwnedShortcut $_.FullName) { Remove-Item -LiteralPath $_.FullName }
    }
} else {
    $artifacts = @(ConvertFrom-Json $env:JOBS_ACCOUNT_SHORTCUT_ARTIFACTS)
    foreach ($artifact in $artifacts) {
        $path = Join-Path $root ((Split-Path -Leaf $artifact) + '.lnk')
        if ((Test-Path -LiteralPath $path) -and -not (Test-OwnedShortcut $path)) {
            throw "An unrelated file occupies the shortcut name: $path"
        }
    }
    foreach ($artifact in $artifacts) {
        $path = Join-Path $root ((Split-Path -Leaf $artifact) + '.lnk')
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $artifact
        $link.WorkingDirectory = Split-Path -Parent $artifact
        if ([IO.Path]::GetExtension($artifact) -eq '.exe') { $link.IconLocation = $artifact + ',0' }
        $link.Save()
        $saved = $shell.CreateShortcut($path)
        if (-not (Test-Path -LiteralPath $path) -or $saved.TargetPath -ne $artifact -or
            $saved.WorkingDirectory -ne (Split-Path -Parent $artifact)) {
            throw "Shortcut verification failed: $path"
        }
    }
}
'''
    env = dict(os.environ, JOBS_ACCOUNT_SHORTCUT_ROOT=str(root),
               JOBS_ACCOUNT_SHORTCUT_ARTIFACTS=json.dumps([str(path) for path in artifacts]),
               JOBS_ACCOUNT_SHORTCUT_CLEAR='1' if clear else '0')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env=env, check=True)


def owned_link(shortcut, dist):
    """包括悬空链接在内，只识别目标仍属于本工具 dist 的旧入口。"""
    return shortcut.is_symlink() and shortcut.resolve().is_relative_to(dist.resolve())


def clear_shortcuts(root):
    """只移除指向当前 dist 的旧入口，保留真实文件及其它链接。"""
    root, dist = checked_dist(root)
    if sys.platform == 'win32':
        windows_shortcuts(root, [], clear=True)
        return
    for shortcut in root.iterdir():
        if shortcut.suffix in ('.app', '.dmg', '.zip') and owned_link(shortcut, dist):
            shortcut.unlink()


def publish_shortcuts(root, artifacts):
    """所有产物及入口名称通过预检后，再创建相对链接或 .lnk。"""
    root, dist = checked_dist(root)
    artifacts = [Path(path).absolute() for path in artifacts]
    for artifact in artifacts:
        if artifact.is_symlink() or not artifact.exists() or not artifact.resolve().is_relative_to(dist):
            raise RuntimeError(f'产物缺失、为符号链接或不属于当前 dist：{artifact}')
        for parent in artifact.parents:
            if parent == dist:
                break
            if parent.is_symlink():
                raise RuntimeError(f'产物路径含符号链接：{parent}')
    if sys.platform == 'win32':
        windows_shortcuts(root, artifacts)
        return
    for artifact in artifacts:
        shortcut = root / artifact.name
        if shortcut.is_symlink():
            if not owned_link(shortcut, dist):
                raise RuntimeError(f'入口名称被其它链接占用：{shortcut}')
        elif shortcut.exists():
            raise RuntimeError(f'入口名称被真实文件占用：{shortcut}')
    for artifact in artifacts:
        shortcut = root / artifact.name
        if shortcut.is_symlink():
            shortcut.unlink()
        shortcut.symlink_to(artifact.relative_to(root), target_is_directory=artifact.is_dir())
        if shortcut.resolve() != artifact.resolve() or not shortcut.exists():
            raise RuntimeError(f'入口校验失败：{shortcut}')
