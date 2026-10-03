"""双端本机打包，依赖确认在清理旧产物之前。Created by Jobs."""
from datetime import datetime
import importlib.util
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
OUTER = ROOT.parent
NAME = 'JobsCodexAccountSwitcher'


def run(command):
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace') as process:
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
    check = 'import PySide6, cryptography, psutil, PyInstaller; from PySide6.QtGui import QStyleHints; assert hasattr(QStyleHints,"setColorScheme")'
    healthy = python.exists() and subprocess.run([str(python), '-c', check], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not healthy:
        confirm('缺少或损坏构建依赖，将在工程 .venv 中安装依赖，不升级系统 Python')
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(env)
        run([str(python), '-m', 'pip', '--version'])
        run([str(python), '-m', 'pip', 'install', '-e', str(ROOT) + '[build]'])
        run([str(python), '-c', check])
    run([str(python), str(__file__), '--prepared'])


def build():
    from artifact_shortcuts import clear_shortcuts, publish_shortcuts
    run([sys.executable, '-c', 'import PySide6, cryptography, psutil, PyInstaller'])
    if sys.platform == 'win32' and not shutil.which('powershell.exe'):
        raise RuntimeError('缺少系统 PowerShell，不能创建快捷方式。')
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
               str(ROOT / 'scripts/launch.py')]
    if sys.platform == 'darwin':
        command.extend(['--osx-bundle-identifier', 'com.jobs.codexaccountswitcher'])
    run(command)
    artifact = output / (NAME + ('.app' if sys.platform == 'darwin' else '.exe'))
    if not artifact.exists():
        raise RuntimeError('构建产物不存在。')
    if sys.platform == 'darwin':
        stage = OUTER / 'work/dmg' / stamp
        stage.mkdir(parents=True)
        shutil.copytree(artifact, stage / artifact.name, symlinks=True)
        (stage / 'Applications').symlink_to('/Applications')
        package = output / (NAME + '.dmg')
        run(['hdiutil', 'create', '-volname', NAME, '-srcfolder', str(stage), '-format', 'UDZO', str(package)])
    else:
        package = Path(shutil.make_archive(str(output / NAME), 'zip', output, artifact.name))
    if not package.is_file():
        raise RuntimeError('分发包未生成。')
    publish_shortcuts(OUTER, [artifact, package])
    if sys.platform == 'darwin':
        run(['open', str(output)])
        run(['open', str(artifact)])
    else:
        subprocess.Popen(['explorer.exe', str(output)])
        subprocess.Popen([str(artifact)], cwd=output)
    print('生成成功：', output)


def main():
    print('Jobs Codex 账户切换器：本机打包 APP/DMG 或 EXE/ZIP。\n'
          '将清理本工具外层 dist 的全部旧包和产物快捷方式；不会操作 Codex 登录文件。\n'
          '缺失依赖回车安装、任意字符取消；不跨平台编译。\n'
          '产物：dist/YYYY.MM.DD HH-mm-ss；成功后发布快捷方式、打开位置并启动新软件。\n'
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
