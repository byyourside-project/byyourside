#!/usr/bin/env python3
"""Replay text through the real local model; never capture or play audio."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.ollama_coach import OllamaCoach
from src.script_coaching import ScriptSession, prepare_script


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='qwen3:8b')
    parser.add_argument('--output', default='logs/script_tracking_validation.json')
    args = parser.parse_args()
    coach = OllamaCoach(args.model, timeout=6)
    coach.verify_model()
    coach.warm_up()
    text = ('발표자의 시간과 핵심 내용 설명을 돕습니다.\n음성 인식은 로컬에서 실행합니다.\n'
            '발화를 최대 4초 구간으로 분할합니다.\n<<슬라이드 넘김>>\n'
            '불확실한 내용은 판단을 보류합니다.\n실제 발표 음성으로 정확도와 지연을 검증합니다.\n보드 성능은 별도로 측정합니다.')
    cases = [('first', '발표자의 시간과 핵심 내용 설명을 돕습니다.', 'script-1', 'complete'),
             ('skip', '발화를 최대 4초 구간으로 분할합니다.', 'script-3', 'complete'),
             ('number_error', '발화를 최대 3초 구간으로 분할합니다.', 'script-3', 'complete'),
             ('partial', '음성 인식은 로컬에서', 'script-2', 'partial'),
             ('unrelated', '점심으로 무엇을 먹을까요?', None, None),
             ('slide_transition', '불확실한 내용은 판단을 보류합니다.', 'script-4', 'complete')]
    results = []
    for name, utterance, expected, phase in cases:
        now = [0.0]
        session = ScriptSession(prepare_script(text, 120), clock=lambda: now[0])
        now[0] = 25
        job = session.ingest({'segment_id': name, 'text': utterance, 'start_sec': 18, 'end_sec': 22, 'endpoint_reason': 'silence'})
        row = {'case': name, 'input': utterance, 'expected_id': expected, 'expected_phase': phase}
        try:
            response = coach.evaluate(job)
            session.apply(job, response)
            position = response['position']
            passed = position['keypoint_id'] == expected and (phase is None or position['phase'] == phase)
            if name == 'number_error':
                passed &= session.states['script-3']['status'] != 'explained'
            row.update(passed=passed, response=response, metrics=coach.last_metrics,
                       tracking=session.tracking_snapshot(), slides=session.slide_timing_snapshot())
        except Exception as exc:
            row.update(passed=False, error=str(exc))
        results.append(row)
        print(f"{name}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'scope': 'local model text replay; no microphone or listening test',
                                 'model': coach.model_info, 'prompt_version': coach.prompt_version,
                                 'results': results}, ensure_ascii=False, indent=2))
    print(output)
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
