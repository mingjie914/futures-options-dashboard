"""Default to the unified terminal; standalone is an explicit development mode."""
from pathlib import Path
import argparse
import os
import runpy
import sys

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standalone', action='store_true', help='仅开发调试：启动8766独立期货页面')
    args = parser.parse_args()
    if not args.standalone:
        unified = Path(__file__).resolve().parent.parent
        launcher = unified / 'start.py'
        python = unified / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not launcher.is_file() or not python.is_file():
            raise SystemExit('未找到统一终端。请先准备仓库根目录的.venv；独立调试请显式使用 --standalone。')
        os.chdir(unified)
        sys.argv = [str(launcher), '--module', 'futures']
        runpy.run_path(str(launcher), run_name='__main__')
        raise SystemExit(0)
    import uvicorn
    root = Path(__file__).resolve().parent
    os.chdir(root)
    if not (root / 'frontend/dist/client/index.html').is_file():
        raise SystemExit('Build frontend first: cd frontend && pnpm build')
    print('Local workbench: http://127.0.0.1:8766 (Ctrl+C to stop)')
    uvicorn.run('backend.api:app', host='127.0.0.1', port=8766)
