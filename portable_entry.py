"""Entry point for the standalone Windows application."""
from pathlib import Path
import sys
import json
import traceback


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--portable-self-test':
        report = Path(sys.argv[2])
        try:
            from portable_self_test import check
            result = check()
        except Exception:
            report.write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}), encoding='utf-8')
            raise SystemExit(1)
        report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    else:
        from app_container import main as run_app
        run_app()


if __name__ == '__main__':
    main()
