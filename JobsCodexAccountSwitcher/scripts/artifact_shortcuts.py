"""发布工程第一层的产物快捷方式。Created by Jobs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def windows_shortcuts(root, artifacts, clear=False):
    """使用系统 WScript 创建普通用户可用的 .lnk，不需要符号链接权限。"""
    script = r'''
$ErrorActionPreference = 'Stop'
$root = $env:JOBS_SHORTCUT_ROOT
$dist = [IO.Path]::GetFullPath((Join-Path $root 'dist')).TrimEnd('\') + '\'
$shell = New-Object -ComObject WScript.Shell
if ($env:JOBS_SHORTCUT_CLEAR -eq '1') {
    Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File | ForEach-Object {
        $link = $shell.CreateShortcut($_.FullName)
        if ($link.TargetPath.StartsWith($dist, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $_.FullName
        }
    }
} else {
    $artifacts = ConvertFrom-Json $env:JOBS_SHORTCUT_ARTIFACTS
    foreach ($artifact in $artifacts) {
        $path = Join-Path $root ((Split-Path -Leaf $artifact) + '.lnk')
        if (Test-Path -LiteralPath $path) {
            $old = $shell.CreateShortcut($path)
            if (-not $old.TargetPath.StartsWith($dist, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Shortcut name occupied by an unrelated file: $path"
            }
        }
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $artifact
        $link.WorkingDirectory = Split-Path -Parent $artifact
        if ([IO.Path]::GetExtension($artifact) -eq '.exe') { $link.IconLocation = $artifact + ',0' }
        $link.Save()
        if (-not (Test-Path -LiteralPath $path)) { throw "Shortcut creation failed: $path" }
    }
}
'''
    env = dict(os.environ, JOBS_SHORTCUT_ROOT=str(root),
               JOBS_SHORTCUT_ARTIFACTS=json.dumps([str(p) for p in artifacts]),
               JOBS_SHORTCUT_CLEAR='1' if clear else '0')
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], env=env, check=True)


def clear_shortcuts(root):
    """仅移除指向当前 dist 的旧产物入口，保留同名真实文件。"""
    root = Path(root).absolute()
    dist = root / 'dist'
    if dist.is_symlink():
        raise RuntimeError('拒绝处理符号链接 dist：' + str(dist))
    if sys.platform == 'win32':
        windows_shortcuts(root, [], clear=True)
        return
    for shortcut in root.iterdir():
        if shortcut.is_symlink() and shortcut.suffix in ('.app', '.dmg', '.zip'):
            target = (root / shortcut.readlink()).resolve()
            if target.is_relative_to(dist.resolve()):
                shortcut.unlink()


def publish_shortcuts(root, artifacts):
    """验证产物后在 dist 同层创建或更新入口，Mac 使用相对符号链接。"""
    root = Path(root).absolute()
    dist = root / 'dist'
    if dist.is_symlink():
        raise RuntimeError('拒绝处理符号链接 dist：' + str(dist))
    artifacts = [Path(p).absolute() for p in artifacts]
    for artifact in artifacts:
        if not artifact.exists() or not artifact.resolve().is_relative_to(dist.resolve()):
            raise RuntimeError('产物缺失或不属于当前 dist：' + str(artifact))
    if sys.platform == 'win32':
        windows_shortcuts(root, artifacts)
        return
    for artifact in artifacts:
        shortcut = root / artifact.name
        if shortcut.is_symlink():
            old = (root / shortcut.readlink()).resolve()
            if not old.is_relative_to(dist.resolve()):
                raise RuntimeError('快捷方式名称被其它入口占用：' + str(shortcut))
            shortcut.unlink()
        elif shortcut.exists():
            raise RuntimeError('快捷方式名称被真实文件占用：' + str(shortcut))
        shortcut.symlink_to(artifact.relative_to(root), target_is_directory=artifact.is_dir())
        if not shortcut.exists():
            raise RuntimeError('快捷方式创建失败：' + str(shortcut))


def main():
    """为外层打包脚本提供清理及发布入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--clear', action='store_true')
    parser.add_argument('artifacts', type=Path, nargs='*')
    args = parser.parse_args()
    if args.clear:
        clear_shortcuts(args.root)
    else:
        publish_shortcuts(args.root, args.artifacts)


if __name__ == '__main__':
    main()
