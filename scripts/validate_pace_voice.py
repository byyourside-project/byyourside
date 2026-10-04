#!/usr/bin/env python3
"""Verify STT, local judgment and pace speech synthesis without audio playback.

Speech timestamps are controlled test inputs, not a human pacing measurement.
Every say invocation is redirected to a WAV file; this script never plays it.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.io import wavfile
from src.config import PipelineConfig
from src.ollama_coach import OllamaCoach
from src.pipeline import SpeechPipeline
from src.presentation_server import PresentationApp
from src.script_coaching import ScriptSession, prepare_script


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise RuntimeError('검증 경로의 처리 제한 시간을 초과했습니다.')


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3:8b')
    parser.add_argument('--output-dir', type=Path, default=Path('logs/pace_voice_validation'))
    args = parser.parse_args()
    folder = args.output_dir / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    sample = Path('models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav')
    events = []
    pipeline = SpeechPipeline(PipelineConfig(log_dir=str(folder / 'stt')), event_sink=events.append,
                              terminal_output=False)
    try:
        result = pipeline.run_wav_vad(str(sample))
        transcript = ' '.join(event['text'] for event in events if event['event_type'] == 'segment_result').strip()
        assert transcript and result.segment_count > 0
    finally:
        pipeline.close()
    coach = OllamaCoach(args.model, timeout=6)
    coach.verify_model()
    coach.warm_up()
    # Replay the numeric sentence that a live UI check exposed as a model
    # false negative. This path evaluates text only and has no voice worker.
    regression_clock = Clock()
    regression = ScriptSession(prepare_script(Path('examples/presentation_script.txt').read_text(), 600),
                               clock=regression_clock)
    exact_reading = []
    for number, line in enumerate(Path('examples/presentation_script.txt').read_text().splitlines()[:3], 1):
        regression_clock.value += 20
        job = regression.ingest({'segment_id': f'exact:{number}', 'text': line,
                                 'start_sec': number*20-4, 'end_sec': number*20,
                                 'status': 'OK', 'endpoint_reason': 'silence'})
        response = coach.evaluate(job)
        regression.apply(job, response)
        state = regression.states[f'script-{number}']
        assert state['status'] == 'explained', f'Exact script line {number} was not confirmed: {state}'
        exact_reading.append({'line': number, 'text': line, 'state': state,
                              'guard_decisions': coach.last_metrics.get('guard_decisions', []),
                              'exact_match_fallbacks': coach.last_metrics.get('exact_match_fallbacks', []),
                              'passed': True})
    deck = prepare_script('조금만 생각을 하면서 살면 훨씬 편할 거야.\n발표 시간을 관리합니다.', 60)
    planned_end = deck['script_plan']['units'][0]['planned_end_sec']
    cases = []
    native_popen = subprocess.Popen
    for direction, speech_end in [('fast', 20), ('slow', 100), ('on_plan', planned_end)]:
        destinations = []

        def file_only(argv, *positional, **kwargs):
            # Reject any unexpected subprocess rather than permit playback.
            assert Path(argv[0]).name == 'say' and argv[1:3] == ['-v', 'Yuna'] and len(argv) == 4
            target = folder / f'{direction}_{len(destinations)+1}.wav'
            destinations.append(target)
            return native_popen([argv[0], '-v', 'Yuna', '-o', str(target),
                                 '--file-format=WAVE', '--data-format=LEI16@22050', argv[3]],
                                *positional, **kwargs)

        with patch('src.voice_feedback.subprocess.Popen', side_effect=file_only):
            app = PresentationApp(deck, coach=coach, output_dir=str(folder / direction))
            try:
                app.command('start', {'microphone': False, 'voice': True, 'voice_scope': 'pace'})
                clock = Clock()
                with app.lock:
                    app.session.clock = clock
                    app.session.origin = clock.value
                    clock.value += speech_end + 2
                app.command('utterance', {'text': transcript, 'start_sec': speech_end-3,
                                          'end_sec': speech_end})
                wait_for(lambda: app.state()['session']['states']['script-1']['status'] == 'explained')
                with app.lock:
                    clock.value += 6
                wait_for(lambda: app.state()['session']['script_progress']['pace'] == direction)
                if direction in ('fast', 'slow'):
                    # Time-over screen alerts can reserve the shared five-second
                    # alert cooldown. Let that window pass while evidence is
                    # still fresh; a frozen test clock cannot expire it itself.
                    if not destinations:
                        with app.lock:
                            clock.value += 6
                    wait_for(lambda: app.state()['voice_last_event'] is not None and
                             app.state()['voice_last_event']['type'] == 'voice_completed')
                    assert len(destinations) == 1
                    rate, samples = wavfile.read(destinations[0])
                    samples = samples.astype(np.float32) / 32768
                    assert len(samples) > 0 and np.max(np.abs(samples)) > 0
                    synthesized = {'frames': len(samples), 'sample_rate': rate,
                                   'duration_sec': len(samples) / rate,
                                   'peak': float(np.max(np.abs(samples)))}
                else:
                    time.sleep(.2)
                    assert not destinations
                    synthesized = None
                progress = app.state()['session']['script_progress']
                voice_events = [event for event in app.session.events if event['type'].startswith('voice_')]
                cases.append({'direction': direction, 'speech_end_sec': speech_end,
                              'planned_end_sec': planned_end, 'progress': progress,
                              'voice_events': voice_events, 'synthesized': synthesized,
                              'output_files': [str(path) for path in destinations], 'passed': True})
                app.command('stop', {})
                wait_for(lambda: app.state()['session']['status'] == 'ended')
            finally:
                app.close()
    report = {'scope': 'Real WAV STT and installed local LLM with simulated pace timestamps; real Yuna WAV synthesis, no speaker playback',
              'model': args.model, 'model_info': coach.model_info, 'prompt_version': coach.prompt_version,
              'stt_transcript': transcript, 'stt_segments': result.segment_count,
              'exact_script_reading': exact_reading,
              'speaker_playback': False, 'system_volume_changed': False, 'physical_microphone': False,
              'human_hearing_verified': False, 'cases': cases, 'passed': all(case['passed'] for case in cases)}
    target = folder / 'validation.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'PASS: fast/slow/on_plan; no speaker playback; report: {target}')


if __name__ == '__main__':
    main()
