#!/usr/bin/env python3
"""Launch the local presentation dashboard; microphone starts only from the UI."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.presentation import LocalHttpCoach
from src.presentation_server import PresentationApp, make_server


def main():
    parser = argparse.ArgumentParser(description="발표 중 코파일럿 로컬 화면")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--deck", type=Path, default=Path(__file__).resolve().parent.parent / "examples/presentation_deck.json")
    parser.add_argument("--coach-url", help="선택적 로컬 모델 어댑터 HTTP 주소")
    parser.add_argument("--output-dir", default="logs/presentation_sessions")
    args = parser.parse_args()
    app = PresentationApp(json.loads(args.deck.read_text(encoding="utf-8")),
                          coach=LocalHttpCoach(args.coach_url) if args.coach_url else None,
                          output_dir=args.output_dir)
    server = None
    try:
        server = make_server(app, args.port)
        print(f"발표 진행 화면: http://127.0.0.1:{server.server_port}", flush=True)
        print("화면에서 입력 방식을 선택하고 발표를 시작하세요. Ctrl+C로 서버를 종료합니다.", flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        app.close()


if __name__ == "__main__":
    main()
