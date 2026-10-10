"""双端本机打包，依赖确认在清理旧产物之前。Created by Jobs."""
from datetime import datetime
import logging
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTER = ROOT.parent
NAME = 'JobsCodexAccountSwitcher'
TOKEN_NAME = 'JobsCodexTokenWidget'


def run(command):
    with subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding='utf-8', errors='replace') as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            logging.info('%s', line.rstrip())
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, command)


def confirm(message):
    if not sys.stdin.isatty():
        raise RuntimeError('无交互输入，请在终端运行。')
    if input(message + '（直接回车继续；输入任意字符取消）：') != '':
        raise RuntimeError('已取消。')


def bootstrap():
    """只在依赖不健康时准备独立环境，检查安装结果再进入构建。"""
    if sys.platform not in ('darwin', 'win32') or sys.version_info < (3, 11):
        raise RuntimeError('请使用 macOS / Windows 和 Python 3.11+。')
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    check = ('import PySide6, cryptography, psutil, PyInstaller; '
             'from PySide6.QtNetwork import QLocalServer, QLocalSocket; '
             'from PySide6.QtGui import QStyleHints; assert hasattr(QStyleHints,"setColorScheme")')
    healthy = python.exists() and subprocess.run([str(python), '-c', check], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not healthy:
        confirm('缺少或损坏构建依赖，将在工程 .venv 中安装依赖，不升级系统 Python')
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(env)
        run([str(python), '-m', 'pip', '--version'])
        run([str(python), '-m', 'pip', 'install', '-e', str(ROOT) + '[build]'])
        run([str(python), '-c', check])
    run([str(python), str(__file__), '--prepared'])


def create_token_launcher(output, artifact):
    """为同包程序提供真实双击入口，参数直接进入独立 Token 模式。"""
    if sys.platform == 'darwin':
        launcher = output / (TOKEN_NAME + '.app')
        contents = launcher / 'Contents'
        executable = contents / 'MacOS' / TOKEN_NAME
        executable.parent.mkdir(parents=True)
        executable.write_text('''#!/bin/zsh
# 脚本自述：同包 Token 浮窗入口，直接运行相邻主 APP 的独立浮窗模式。
# 只读取本地用量日志；双击即启动，不操作账户库或 Codex 登录文件。
SCRIPT_FILE="${(%):-%x}"
# 打印启动入口职责，图形启动无需终端确认。
show_launcher_intro() {
    print -r -- 'Jobs Codex Token 浮窗：读取本地用量日志，关闭浮窗可结束运行。'
}
# 按启动器自身位置寻找相邻主应用，并完整传入用户参数。
launch_token_widget() {
    setopt NO_NOMATCH
    local bundle_parent="${SCRIPT_FILE:A:h:h:h:h}"
    local main_binary="${bundle_parent}/JobsCodexAccountSwitcher.app/Contents/MacOS/JobsCodexAccountSwitcher"
    if [[ ! -x "$main_binary" ]]; then
        /usr/bin/osascript -e 'display alert "Token 浮窗无法启动" message "请将 JobsCodexTokenWidget.app 与 JobsCodexAccountSwitcher.app 安装在同一目录。" as critical'
        exit 1
    fi
    exec "$main_binary" --token-widget "$@"
}
# 编排图形启动入口。
main() {
    show_launcher_intro # 说明只读用量入口的职责。
    launch_token_widget "$@" # 启动同包主程序的独立 Token 浮窗。
}
main "$@"
''', encoding='utf-8')
        executable.chmod(0o755)
        with (ROOT / 'pyproject.toml').open('rb') as config:
            version = tomllib.load(config)['project']['version']
        info = {
            'CFBundleName': TOKEN_NAME,
            'CFBundleDisplayName': 'Jobs Codex Token',
            'CFBundleExecutable': TOKEN_NAME,
            'CFBundleIdentifier': 'com.jobs.codexaccountswitcher.tokenwidget',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': version,
            'CFBundleVersion': version,
            'NSHighResolutionCapable': True,
            'LSUIElement': True,
        }
        with (contents / 'Info.plist').open('wb') as config:
            plistlib.dump(info, config)
        run(['zsh', '-n', str(executable)])
        return launcher

    launcher = output / (TOKEN_NAME + '.vbs')
    launcher.write_text('''Option Explicit
' Created by Jobs. Launch the adjacent packaged EXE in Token widget mode.
Dim shell, files, program, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
program = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "JobsCodexAccountSwitcher.exe")
If Not files.FileExists(program) Then
    MsgBox "Keep JobsCodexTokenWidget.vbs beside JobsCodexAccountSwitcher.exe.", 16, "Jobs Codex Token"
    WScript.Quit 1
End If
command = QuoteArgument(program) & " --token-widget"
For Each argument In WScript.Arguments
    command = command & " " & QuoteArgument(argument)
Next
shell.CurrentDirectory = files.GetParentFolderName(program)
shell.Run command, 0, False

' Quote arguments with the Windows backslash and quotation rules.
Function QuoteArgument(value)
    Dim result, slashes, index, character
    result = Chr(34)
    slashes = 0
    For index = 1 To Len(value)
        character = Mid(value, index, 1)
        If character = Chr(92) Then
            slashes = slashes + 1
        ElseIf character = Chr(34) Then
            result = result & String(slashes * 2 + 1, Chr(92)) & Chr(34)
            slashes = 0
        Else
            result = result & String(slashes, Chr(92)) & character
            slashes = 0
        End If
    Next
    QuoteArgument = result & String(slashes * 2, Chr(92)) & Chr(34)
End Function
''', encoding='ascii')
    return launcher


def publish_windows_token_shortcut(artifact):
    """外层 Token 快捷方式直接指向真实 EXE 并携带浮窗参数。"""
    script = r'''
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$path = Join-Path $env:JOBS_TOKEN_ROOT 'JobsCodexTokenWidget.lnk'
$dist = [IO.Path]::GetFullPath((Join-Path $env:JOBS_TOKEN_ROOT 'dist')).TrimEnd('\') + '\'
if (Test-Path -LiteralPath $path) {
    $old = $shell.CreateShortcut($path)
    if (-not $old.TargetPath.StartsWith($dist, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Token shortcut name occupied by an unrelated file: $path"
    }
}
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:JOBS_TOKEN_EXE
$link.Arguments = '--token-widget'
$link.WorkingDirectory = Split-Path -Parent $env:JOBS_TOKEN_EXE
$link.IconLocation = $env:JOBS_TOKEN_EXE + ',0'
$link.Save()
$saved = $shell.CreateShortcut($path)
if (-not (Test-Path -LiteralPath $path) -or $saved.TargetPath -ne $env:JOBS_TOKEN_EXE -or $saved.Arguments -ne '--token-widget') {
    throw "Token shortcut verification failed: $path"
}
'''
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                   env=dict(os.environ, JOBS_TOKEN_ROOT=str(OUTER), JOBS_TOKEN_EXE=str(artifact)),
                   check=True)


def build():
    from artifact_shortcuts import clear_shortcuts, publish_shortcuts
    run([sys.executable, '-c', 'import PySide6, cryptography, psutil, PyInstaller'])
    if sys.platform == 'win32' and not shutil.which('powershell.exe'):
        raise RuntimeError('缺少系统 PowerShell，不能创建快捷方式。')
    if sys.platform == 'win32' and not shutil.which('wscript.exe'):
        raise RuntimeError('缺少系统 Windows Script Host，不能运行独立 Token 入口。')
    if sys.platform == 'darwin' and not shutil.which('hdiutil'):
        raise RuntimeError('缺少系统 hdiutil，停止构建。')
    stamp = datetime.now().strftime('%Y.%m.%d %H-%M-%S')
    dist = OUTER / 'dist'
    if dist.is_symlink() or dist.resolve().parent != OUTER.resolve():
        raise RuntimeError('拒绝清理外部或符号链接 dist。')
    clear_shortcuts(OUTER)
    if dist.exists():
        shutil.rmtree(dist)
    output = dist / stamp
    mode = '--onedir' if sys.platform == 'darwin' else '--onefile'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', mode,
               '--name', NAME, '--paths', str(ROOT / 'src'), '--distpath', str(output),
               '--workpath', str(OUTER / 'work/build' / stamp), '--specpath', str(OUTER / 'work/spec'),
               '--hidden-import', 'jobs_codex_account_switcher.usage',
               '--hidden-import', 'jobs_codex_account_switcher.token_hooks',
               '--hidden-import', 'jobs_codex_account_switcher.token_widget',
               '--hidden-import', 'jobs_codex_account_switcher.labels',
               '--hidden-import', 'jobs_codex_account_switcher.hook_report',
               '--hidden-import', 'PySide6.QtNetwork',
               str(ROOT / 'scripts/launch.py')]
    if sys.platform == 'darwin':
        command.extend(['--osx-bundle-identifier', 'com.jobs.codexaccountswitcher'])
    run(command)
    artifact = output / (NAME + ('.app' if sys.platform == 'darwin' else '.exe'))
    executable = artifact / 'Contents/MacOS' / NAME if sys.platform == 'darwin' else artifact
    if not executable.is_file():
        raise RuntimeError('构建产物不存在。')
    token_launcher = create_token_launcher(output, artifact)
    if sys.platform == 'darwin':
        stage = OUTER / 'work/dmg' / stamp
        stage.mkdir(parents=True)
        shutil.copytree(artifact, stage / artifact.name, symlinks=True)
        shutil.copytree(token_launcher, stage / token_launcher.name, symlinks=True)
        (stage / 'Applications').symlink_to('/Applications')
        package = output / (NAME + '.dmg')
        run(['hdiutil', 'create', '-volname', NAME, '-srcfolder', str(stage), '-format', 'UDZO', str(package)])
    else:
        package = output / (NAME + '.zip')
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(artifact, artifact.name)
            archive.write(token_launcher, token_launcher.name)
    if not package.is_file():
        raise RuntimeError('分发包未生成。')
    if sys.platform == 'darwin':
        publish_shortcuts(OUTER, [artifact, token_launcher, package])
    else:
        publish_shortcuts(OUTER, [artifact, package])
        publish_windows_token_shortcut(artifact)
    if sys.platform == 'darwin':
        run(['open', str(output)])
        run(['open', str(token_launcher)])
    else:
        subprocess.Popen(['explorer.exe', str(output)])
        subprocess.Popen([str(artifact), '--token-widget'], cwd=output)
    print('生成成功：', output)


def main():
    print('Jobs Codex 账户切换器与独立 Token 浮窗：本机打包 APP/DMG 或 EXE/ZIP。\n'
          '将清理本工具外层 dist 的全部旧包和产物快捷方式；不会操作 Codex 登录文件。\n'
          '缺失依赖回车安装、任意字符取消；不跨平台编译。\n'
          '产物：dist/YYYY.MM.DD HH-mm-ss；包含账户工具和只打开 Token 浮窗的独立入口。\n'
          'Mac 两 APP 安装在同一目录；Windows ZIP 保持 EXE 与 Token VBS 相邻。\n'
          '成功后发布快捷方式、打开位置并启动新 Token 浮窗；账户工具有独立入口。\n'
          '构建不会安装 Codex 钩子。\n'
          '日志：系统临时目录 JobsCodexAccountSwitcher-build.log。')
    confirm('已了解构建范围和旧产物清理')
    if '--prepared' in sys.argv:
        build()
    else:
        bootstrap()


if __name__ == '__main__':
    log = Path(tempfile.gettempdir()) / (NAME + '-build.log')
    logging.basicConfig(filename=log, level=logging.INFO)
    try:
        main()
    except Exception as error:
        logging.exception('构建失败')
        print(f'构建失败：{error}\n日志：{log}', file=sys.stderr)
        raise SystemExit(1)
