"""Run the local rehearsal review app."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.review_server import serve


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='곁 — 녹음으로 돌아보는 발표 연습')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--models-dir', type=Path, default=ROOT / 'models')
    args = parser.parse_args()
    serve(ROOT, args.port, args.models_dir)
