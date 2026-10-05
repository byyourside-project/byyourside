#!/usr/bin/env python3
"""Replay saved content judgments through sentence timing; no audio or model.

Keeps the user's transcript and saved judgments. Only the timing calculation
and display policy are exercised with a controlled monotonic clock.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.script_coaching import ScriptSession


class ReplayClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


def replay_record(source):
    clock = ReplayClock()
    session = ScriptSession(source['deck'], clock=clock)
    jobs, judgments, checkpoints = {}, {}, []
    sample = 0.0
    samples = []
    for event in source['events']:
        at = event['elapsed_sec']
        while sample + .1 < at:
            sample += .1
            clock.value = session.origin + sample
            session.tick()
            progress = session.progress()
            samples.append({'elapsed_sec':sample, 'voice_pace':progress['pace'],
                            'display_pace':progress.get('display_pace', {}).get('pace', 'waiting')})
        clock.value = session.origin + at
        kind = event['type']
        if kind == 'utterance':
            job = session.ingest({key:copy.deepcopy(event[key]) for key in (
                'segment_id', 'text', 'start_sec', 'end_sec', 'status', 'endpoint_reason',
                'stt_inference_ms', 'estimated_feedback_delay_ms') if key in event})
            if job is not None:
                jobs[(job['version'], job['revision'])] = job
        elif kind == 'keypoint_judged':
            key = (event['version'], event.get('revision', 1))
            judgments.setdefault(key, {})[event['keypoint_id']] = {
                field:copy.deepcopy(event[field]) for field in (
                    'keypoint_id', 'status', 'reason', 'evidence_segment_ids', 'reason_code') if field in event}
        elif kind == 'coaching_inference':
            key = (event['version'], event['revision'])
            job = jobs[key]
            if event['outcome'] == 'error':
                session.fail_job(job, 'Recorded content judgment failure.')
            else:
                # State events omit unchanged confirmations. An unconfirmed
                # answer preserves those states under the normal apply policy.
                response = {'judgments':[judgments.get(key, {}).get(point['keypoint_id'], {
                    'keypoint_id':point['keypoint_id'], 'status':'unconfirmed',
                    'reason':'Saved replay contains no changed judgment for this point.',
                    'evidence_segment_ids':[]}) for point in job['slide']['keypoints']]}
                session.apply(job, response)
        elif kind == 'session_stopping':
            session.stop()
        elif kind == 'session_ended':
            session.finish()
        if kind in ('utterance', 'coaching_inference', 'session_stopping', 'session_ended'):
            checkpoints.append({'event':kind, 'elapsed_sec':at, 'progress':session.progress()})
    return session, checkpoints, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('logs/sentence_pacing_validation.json'))
    args = parser.parse_args()
    source = json.loads(args.record.read_text())
    session, checkpoints, samples = replay_record(source)
    expected = {key:(value['status'], value['evidence_segment_ids']) for key,value in source['states'].items()}
    actual = {key:(value['status'], value['evidence_segment_ids']) for key,value in session.states.items()}
    assert actual == expected, 'Content states/evidence changed during timing-only replay.'
    progress = session.progress()
    display = progress['display_pace']
    assert display['final'] and display['reliable']
    assert display['pace'] in ('fast', 'on_plan', 'slow')
    pending = [point for point in checkpoints if point['event']=='utterance' and
               point['progress']['display_pace'].get('latest_keypoint_id')]
    assert pending and all(point['progress']['display_pace']['pace'] != 'waiting' for point in pending), \
        'Ordinary processing unexpectedly hid the last completed-sentence pace.'
    report = {'scope':'Saved transcript and saved content-judgment replay; controlled clock; no model/audio',
              'record_name':args.record.name, 'record_sha256':hashlib.sha256(args.record.read_bytes()).hexdigest(),
              'model_called':False, 'physical_microphone':False, 'speaker_playback':False,
              'system_volume_changed':False, 'content_states_and_evidence_preserved':actual == expected,
              'script_units':progress['units'], 'final_display':display,
              'legacy_voice_pace':progress['pace'],
              'pending_checkpoints':[{'elapsed_sec':point['elapsed_sec'],
                                      'display_pace':point['progress']['display_pace']}
                                     for point in pending],
              'sample_counts':{name:{pace:sum(sample[name]==pace for sample in samples)
                                    for pace in ('fast','on_plan','slow','waiting')}
                               for name in ('voice_pace','display_pace')},
              'source_sha256':{name:hashlib.sha256(ROOT.joinpath(name).read_bytes()).hexdigest() for name in (
                  'src/script_coaching.py','src/presentation.py','scripts/validate_sentence_pacing.py')},
              'passed':True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'final_display':display,'passed':True},ensure_ascii=False))


if __name__ == '__main__':
    main()
