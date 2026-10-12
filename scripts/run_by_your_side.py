"""Start both stages; opening a screen never starts the microphone or voice."""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.app_suite import create_servers
from src.ollama_coach import OllamaCoach


def main():
    parser = argparse.ArgumentParser(description='곁 - 발표 전·중·후 통합 실행')
    parser.add_argument('--review-port', type=int, default=8765)
    parser.add_argument('--live-port', type=int, default=8766)
    parser.add_argument('--open', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--phrase-coach', action='store_true', help='대본 등록 표현 비교 (기본값)')
    mode.add_argument('--ollama-model', help='이미 설치된 로컬 의미 모델 이름')
    parser.add_argument('--coach-timeout', type=float, default=6.0)
    args = parser.parse_args()
    coach = None
    try:
        if args.ollama_model:
            coach = OllamaCoach(args.ollama_model, timeout=args.coach_timeout)
            coach.verify_model()
        live, review_server, live_server = create_servers(ROOT, args.review_port, args.live_port, coach)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'실행하지 못했습니다: {exc}\n기존 실행 창을 종료하거나 포트·로컬 모델 설정을 확인해 주세요.', flush=True)
        return 1
    threads = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval': .2}, daemon=True)
               for s in (review_server, live_server)]
    for thread in threads:
        thread.start()
    print(f'발표 전: {live.review_url}\n발표 중: http://127.0.0.1:{live_server.server_port}\n발표 후: http://127.0.0.1:{live_server.server_port}/after', flush=True)
    print('코칭: ' + (f'Ollama {args.ollama_model}' if coach else '등록 표현 비교 (바꿔 말하기 의미 판단 제한)'), flush=True)
    print('종료: Ctrl+C. 마이크와 음성 안내는 화면에서 직접 시작합니다.', flush=True)
    if args.open:
        webbrowser.open(live.review_url)
    try:
        while all(t.is_alive() for t in threads):
            threads[0].join(timeout=.5)
    except KeyboardInterrupt:
        pass
    finally:
        for server in (review_server, live_server):
            server.shutdown()
            server.server_close()
        live.close()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
