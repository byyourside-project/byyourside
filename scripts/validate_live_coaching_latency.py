#!/usr/bin/env python3
"""Replay a saved transcript's arrival times through the local coach, silently.

Uses recorded text and timing, not a microphone or human speaking. Voice output
is replaced by a rejecting stub. Model preparation precedes the replay timer.
"""
import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ollama_coach import OllamaCoach
from src.presentation_server import PresentationApp


class SilentVoice:
    executable = None

    def __init__(self, *args):
        pass

    def enqueue(self, *args):
        raise AssertionError('This replay must never request voice output.')

    def close(self):
        pass


def first_confirmations(events):
    first = {}
    for event in events:
        if event['type'] == 'keypoint_judged' and event['status'] == 'explained':
            first.setdefault(event['keypoint_id'], event)
    return first


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--model', default='qwen3:8b')
    parser.add_argument('--output-dir', type=Path, default=Path('logs/live_coaching_latency'))
    args = parser.parse_args()
    source = json.loads(args.record.read_text())
    root = Path(__file__).resolve().parents[1]
    replay_files = ['src/ollama_coach.py', 'src/presentation.py', 'src/script_coaching.py',
                    'src/presentation_server.py', 'src/semantic_guards.py',
                    'scripts/validate_live_coaching_latency.py']
    def source_hashes():
        return {name:hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() for name in replay_files}
    replay_source_hashes = source_hashes()
    utterances = [e for e in source['events'] if e['type'] == 'utterance']
    assert utterances
    folder = args.output_dir / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    coach = OllamaCoach(args.model, timeout=6)
    coach.verify_model()
    with patch('src.presentation_server.VoiceFeedback', SilentVoice):
        app = PresentationApp(source['deck'], coach=coach, output_dir=str(folder))
        try:
            preparation_started = time.perf_counter()
            app.command('start', {'microphone': False, 'voice': False})
            preparation_sec = time.perf_counter() - preparation_started
            # Skip leading idle time only; subsequent arrivals retain the exact
            # recorded real-time gaps and original speech timestamps.
            with app.lock:
                app.session.origin -= utterances[0]['elapsed_sec']
            for original in utterances:
                wait_sec = max(0, original['elapsed_sec'] - app.session.elapsed())
                time.sleep(wait_sec)
                app.command('utterance', {k:original[k] for k in (
                    'segment_id','text','start_sec','end_sec','status','endpoint_reason') if k in original})
            deadline = time.perf_counter() + 20
            while app.jobs.unfinished_tasks and time.perf_counter() < deadline:
                time.sleep(.02)
            assert not app.jobs.unfinished_tasks, 'Replay coaching did not drain.'
            app.command('stop', {})
            while app.session.status != 'ended' and time.perf_counter() < deadline:
                time.sleep(.02)
            assert app.session.status == 'ended'
            replay = json.loads(Path(app.output_path).read_text())
            before = first_confirmations(source['events'])
            after = first_confirmations(replay['events'])
            point = source['deck']['script_plan']['units'][0]['keypoint_id']
            assert point in after, 'First script sentence was not confirmed.'
            # First-sentence timing is compared against its original valid
            # evidence end, rather than any later incorrect model citation.
            segments = {s['segment_id']:s for s in source['segments']}
            first_end = max(segments[sid]['end_sec'] for sid in before[point]['evidence_segment_ids'])
            comparison = {'keypoint_id':point, 'speech_end_sec':first_end,
                          'before_confirmed_sec':before[point]['elapsed_sec'],
                          'after_confirmed_sec':after[point]['elapsed_sec'],
                          'before_delay_sec':before[point]['elapsed_sec']-first_end,
                          'after_delay_sec':after[point]['elapsed_sec']-first_end,
                          'second_speech_start_sec':utterances[1]['start_sec'],
                          'confirmed_before_second_speech':after[point]['elapsed_sec'] < utterances[1]['start_sec']}
            errors = [e for e in replay['events'] if e['type']=='coaching_inference' and e['outcome']=='error']
            report = {'scope':'Real local-model text replay at recorded arrival gaps; no microphone or playback',
                      'model':args.model, 'prompt_version':coach.prompt_version,
                      'record_name':args.record.name,
                      'record_sha256':hashlib.sha256(args.record.read_bytes()).hexdigest(),
                      'replay_source_sha256':replay_source_hashes,
                      'replay_sources_unchanged':replay_source_hashes == source_hashes(),
                      'preparation_sec':preparation_sec, 'warm_up_metrics':coach.warm_up_metrics,
                      'speaker_playback':False, 'physical_microphone':False, 'system_volume_changed':False,
                      'comparison':comparison, 'states':replay['states'], 'errors':errors,
                      'coaching_inferences':[e for e in replay['events'] if e['type']=='coaching_inference'],
                      'replay_path':app.output_path,
                      'passed':comparison['confirmed_before_second_speech'] and not errors and
                               replay_source_hashes == source_hashes()}
            target = folder / 'validation.json'
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'report':str(target),'comparison':comparison,'passed':report['passed']},ensure_ascii=False))
            if not report['passed']:
                raise RuntimeError('First-sentence latency target or model error check failed; report retained.')
        finally:
            app.close()


if __name__ == '__main__':
    main()
