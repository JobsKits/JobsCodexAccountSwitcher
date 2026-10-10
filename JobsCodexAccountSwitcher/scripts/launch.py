"""源码和打包应用共用入口。Created by Jobs."""
import argparse
import os
import sys
from pathlib import Path


def main():
    """SessionStart 只派生浮窗进程，让钩子不必初始化 Qt。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    if '--hook-launch' in sys.argv[1:] or '--hook-report' in sys.argv[1:]:
        parser = argparse.ArgumentParser(description='启动独立 Token 浮窗或报告本轮用量。')
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--hook-launch', action='store_true')
        mode.add_argument('--hook-report', action='store_true')
        parser.add_argument('--codex-home', type=Path)
        args = parser.parse_args()
        home = args.codex_home or Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        if args.hook_report:
            from jobs_codex_account_switcher.hook_report import report_from_stdin
            return report_from_stdin(home)
        from jobs_codex_account_switcher.token_hooks import launch_token_widget
        return 0 if launch_token_widget(home) else 1

    from jobs_codex_account_switcher.app import main as application_main
    return application_main()


if __name__ == '__main__':
    raise SystemExit(main())
