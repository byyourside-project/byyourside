"""Run the local rehearsal review app."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.review_server import serve


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='곁 - 대본과 영상으로 돌아보는 발표 연습')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--models-dir', type=Path, default=ROOT / 'models')
    parser.add_argument('--open', action='store_true', help='실행 후 기본 브라우저 열기')
    args = parser.parse_args()
    try:
        serve(ROOT, args.port, args.models_dir, args.open)
    except OSError as exc:
        if exc.errno in (48, 98, 10048) or getattr(exc, 'winerror', None) == 10048:
            print(f'이미 이 주소를 사용하는 프로그램이 있습니다: http://127.0.0.1:{args.port}\n'
                  '기존 실행 창에서 Ctrl+C로 종료한 뒤 다시 실행하거나 --port 8766을 붙여 주세요.')
            raise SystemExit(1)
        raise
