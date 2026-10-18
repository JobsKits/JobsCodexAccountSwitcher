"""源码和打包应用共用入口。Created by Jobs."""
import argparse
import json
import os
import sys
from pathlib import Path


def acknowledge_retired_hook(report):
    """已加载的旧会话可完成退出；不再统计用量或启动监控。"""
    if report:
        message = ('Token 监控已拆分到 JobsCodexTokenMonitor；此旧钩子仅确认退出，不再统计。'
                   '请使用独立监控工具，并在 Codex 的 /hooks 中审阅新联动。')
        payload = json.dumps({'systemMessage': message}, ensure_ascii=True) + '\n'
        try:
            if sys.stdout is not None:
                sys.stdout.write(payload)
                sys.stdout.flush()
            else:
                os.write(1, payload.encode('ascii'))
        except (OSError, ValueError):
            pass
    return 0


def main(argv=None):
    """账户启动前只应答仍被旧会话缓存的退役钩子。"""
    arguments = sys.argv[1:] if argv is None else argv
    if '--hook-launch' in arguments or '--hook-report' in arguments:
        parser = argparse.ArgumentParser(description='应答已退役的旧会话联动；监控功能已拆分。')
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--hook-launch', action='store_true')
        mode.add_argument('--hook-report', action='store_true')
        parser.add_argument('--codex-home', type=Path)
        args = parser.parse_args(arguments)
        return acknowledge_retired_hook(args.hook_report)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from jobs_codex_account_switcher.app import main as application_main
    return application_main(arguments)


if __name__ == '__main__':
    raise SystemExit(main())
