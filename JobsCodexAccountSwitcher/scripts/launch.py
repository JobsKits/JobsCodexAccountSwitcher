"""源码和打包应用共用入口。Created by Jobs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jobs_codex_account_switcher.app import main
if __name__ == '__main__':
    raise SystemExit(main())
