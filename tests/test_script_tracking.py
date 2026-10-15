import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.script_coaching import ScriptSession, prepare_script
from src.ollama_coach import OllamaCoach
from src.presentation_server import PresentationApp
from tests.test_presentation import FakeClock


TEXT = '첫 문장입니다.\n매출은 20퍼센트 증가했습니다.\n마지막 설명입니다.\n<<슬라이드 넘김>>\n다음은 실험 결과입니다.\n발표를 마칩니다.'


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.deck = prepare_script(TEXT, 120, slide_durations=[60, 60])
        self.session = ScriptSession(self.deck, clock=self.clock)

    def job(self, text='실제 발화입니다.', start=10, end=12):
        self.clock.value = self.session.origin + end + 2
        return self.session.ingest({'segment_id': f'u{len(self.session.segments)}', 'text': text,
                                    'start_sec': start, 'end_sec': end, 'endpoint_reason': 'silence'})

    def result(self, job, kid=None, phase='none', status='unconfirmed', extra=None):
        sid = job['segments'][-1]['segment_id']
        judgments = [{'keypoint_id': p['keypoint_id'], 'status': status if p['keypoint_id'] == kid else 'unconfirmed',
                      'reason': '통제된 모델 응답', 'evidence_segment_ids': [sid] if p['keypoint_id'] == kid and status != 'unconfirmed' else []}
                     for p in job['slide']['keypoints']]
        for judgment in judgments:
            if extra and judgment['keypoint_id'] in extra:
                judgment.update(status='explained', evidence_segment_ids=[sid])
        return {'judgments': judgments, 'position': {'keypoint_id': kid, 'phase': phase,
                                                     'evidence_segment_ids': [sid] if kid else []}}

    def apply(self, kid, start=10, end=12, phase='complete', status='explained', extra=None):
        job = self.job(start=start, end=end)
        self.session.apply(job, self.result(job, kid, phase, status, extra))
        return job

    def test_marker_is_metadata_and_custom_budgets_survive_round_trip(self):
        plan = self.deck['script_plan']
        self.assertEqual(len(plan['slides']), 2)
        self.assertEqual(len(plan['units']), 5)
        self.assertFalse(any('<<' in p['text'] for p in plan['units']))
        self.assertEqual(plan['units'][2]['planned_end_sec'], 60)
        self.assertEqual(plan['units'][-1]['planned_end_sec'], 120)
        self.assertEqual(self.session.plan, plan)
        for text in ('<<슬라이드 넘김>>첫 문장.', '첫 문장.<<슬라이드 넘김>>', 'a<<슬라이드 넘김>><<슬라이드 넘김>>b'):
            with self.assertRaises(ValueError): prepare_script(text, 60)
        for values in ([60], [60, 70], [0, 120], [True, 119], [float('nan'), 60]):
            with self.assertRaises(ValueError): prepare_script(TEXT, 120, slide_durations=values)
        self.assertEqual(len(prepare_script('a<< 슬라이드 넘김 >>b', 20)['script_plan']['slides']), 2)

    def test_default_window_is_current_plus_three_and_preserves_skipped_facts(self):
        job = self.job()
        self.assertEqual(job['script_position_ids'], ['script-1', 'script-2', 'script-3', 'script-4'])
        self.session.apply(job, self.result(job, 'script-3', 'partial', 'uncertain'))
        self.assertEqual(self.session.cursor, 2)
        self.assertEqual(self.session.cursor_phase, 'partial')
        self.assertEqual(self.session.states['script-1']['status'], 'unconfirmed')
        self.assertEqual(self.session.states['script-3']['status'], 'uncertain')
        self.assertEqual(self.session.progress()['fraction'], 0)
        following = self.job(start=20, end=22)
        self.assertEqual(following['script_position_ids'], ['script-3', 'script-4', 'script-5'])
        self.assertIn('script-1', [p['keypoint_id'] for p in following['slide']['keypoints']])

    def test_number_error_can_locate_sentence_without_confirming_it(self):
        job = self.job('매출은 30퍼센트 증가했습니다.')
        self.session.apply(job, self.result(job, 'script-2', 'complete', 'uncertain'))
        self.assertEqual(self.session.cursor, 1)
        self.assertEqual(self.session.states['script-2']['status'], 'uncertain')
        self.assertEqual(self.session.progress()['confirmed_units'], 0)
        self.assertTrue(self.session.slide_timing_snapshot()['reliable'])
        before = self.session.slide_timing_snapshot()['schedule_delta_sec']
        self.clock.advance(5)
        self.assertEqual(self.session.slide_timing_snapshot()['schedule_delta_sec'], before)

    def test_position_without_related_content_cannot_force_a_match(self):
        job = self.job('점심으로 무엇을 먹을까요?')
        self.session.apply(job, self.result(job, 'script-3', 'complete', 'unconfirmed'))
        self.assertEqual(self.session.cursor, 0)
        self.assertEqual(self.session.cursor_phase, 'waiting')
        self.assertEqual(self.session.position_misses, 1)

    def test_recovery_after_two_misses_stays_within_current_slide(self):
        text = '\n'.join(f'연구 {i}입니다.' for i in range(10)) + '\n<<슬라이드 넘김>>\n' + '\n'.join(f'실험 {i}입니다.' for i in range(8))
        self.session = ScriptSession(prepare_script(text, 120), clock=self.clock)
        for start in (1, 5):
            job = self.job(start=start, end=start+2)
            self.session.apply(job, self.result(job))
        job = self.job(start=9, end=11)
        self.assertTrue(job['script_recovery'])
        self.assertIn('script-10', job['script_position_ids'])
        self.assertNotIn('script-11', job['script_position_ids'])
        self.session.apply(job, self.result(job, 'script-10', 'complete', 'explained'))
        self.assertEqual(self.session.cursor, 9)
        self.assertEqual(self.session.position_misses, 0)

    def test_ambiguous_or_repeated_sentence_never_forces_a_jump(self):
        self.session = ScriptSession(prepare_script('같은 문장입니다.\n중간 설명입니다.\n같은 문장입니다.', 60), clock=self.clock)
        job = self.job()
        self.session.apply(job, self.result(job, 'script-3', 'complete', 'explained'))
        self.assertEqual(self.session.cursor, 0)
        job = self.job(start=20, end=22)
        self.session.apply(job, self.result(job, phase='ambiguous'))
        self.assertEqual(self.session.cursor, 0)
        self.assertEqual(self.session.position_misses, 2)

    def test_inflight_response_advances_tracking_but_cannot_overwrite_newer_position(self):
        old = self.job(start=1, end=3)
        new = self.job(start=5, end=7)
        self.session.apply(old, self.result(old, 'script-2', 'complete', 'explained'))
        self.assertEqual(self.session.cursor, 1)
        self.session.apply(new, self.result(new, 'script-3', 'complete', 'uncertain'))
        self.assertEqual(self.session.cursor, 2)
        self.session.apply(old, self.result(old, 'script-2', 'complete', 'explained'))
        self.assertEqual(self.session.cursor, 2)

    def test_position_requires_latest_real_evidence_and_valid_candidate_atomically(self):
        self.apply('script-1')
        job = self.job(start=20, end=22)
        response = self.result(job, 'script-3', 'complete', 'uncertain')
        response['position']['evidence_segment_ids'] = [job['segments'][0]['segment_id']]
        self.session.apply(job, response)
        self.assertEqual(self.session.cursor, 0)
        for invalid in ({'keypoint_id': 'script-5', 'phase': 'complete', 'evidence_segment_ids': ['u0']},
                        {'keypoint_id': 'script-1', 'phase': 'complete', 'evidence_segment_ids': ['fake']},
                        {'keypoint_id': 'script-1', 'phase': 'none', 'evidence_segment_ids': []}):
            job = self.job(start=30, end=32)
            response = self.result(job, 'script-1', 'complete', 'explained')
            response['position'] = invalid
            before = copy.deepcopy(self.session.states)
            with self.assertRaises(ValueError): self.session.apply(job, response)
            self.assertEqual(before, self.session.states)

    def test_manual_resync_invalidates_inflight_results_and_does_not_confirm_content(self):
        old = self.job()
        self.session.resync(4)
        self.session.apply(old, self.result(old, 'script-3', 'complete', 'explained'))
        self.assertEqual(self.session.cursor, 4)
        self.assertTrue(all(p['status'] == 'unconfirmed' for p in self.session.states.values()))
        self.assertFalse(self.session.job_is_current(old))
        self.assertFalse(self.session.slide_records[0]['reliable'])
        self.assertFalse(any(a['key'].startswith('slide_pace:') for a in self.session.alerts))
        self.session.resync(0)
        self.assertEqual(self.session.cursor, 0)
        self.assertEqual(self.session.script_slide_index, 0)
        for value in (-1, 5, True, None):
            with self.assertRaises(ValueError): self.session.resync(value)

    def test_queued_request_refreshes_candidates_without_mutating_deck(self):
        first = self.job(start=1, end=3)
        waiting = self.job(start=5, end=7)
        self.session.apply(first, self.result(first, 'script-3', 'complete', 'explained'))
        refreshed = self.session._tracking_job(waiting)
        self.assertEqual(refreshed['script_position_ids'], ['script-3', 'script-4', 'script-5'])
        refreshed['slide']['keypoints'][0]['text'] = '외부 어댑터가 바꾼 사본'
        self.assertEqual(self.session.deck['slides'][0]['keypoints'][0]['text'], '첫 문장입니다.')

    def test_slide_boundary_uses_audio_start_not_inference_time_and_survives_new_speech(self):
        self.apply('script-1', start=1, end=4)
        job = self.job(start=30, end=35)
        self.clock.advance(9)
        self.session.apply(job, self.result(job, 'script-4', 'partial', 'uncertain'))
        record = self.session.slide_records[0]
        self.assertEqual(record['actual_duration_sec'], 30)
        self.assertTrue(record['estimated'])
        self.assertTrue(record['reliable'])
        self.assertEqual(record['pace'], 'fast')
        self.assertEqual(len(record['unconfirmed_ids']), 2)
        alerts = [a for a in self.session.alerts if a['key'].startswith('slide_pace:')]
        self.assertEqual(len(alerts), 1)
        self.assertIn('30초 일찍', alerts[0]['message'])
        self.assertNotIn('천천히', alerts[0]['message'])
        self.apply('script-4', start=47, end=49)
        self.assertEqual(len(self.session.slide_records), 1)
        self.assertGreater(alerts[0]['expires_sec'], self.session.elapsed())
        self.assertFalse(any(a['key'].startswith('pace:') for a in self.session.alerts))

    def test_mixed_boundary_withholds_pace_instead_of_inventing_split_time(self):
        self.apply('script-4', start=30, end=35, extra={'script-3'})
        self.assertEqual(self.session.script_slide_index, 1)
        self.assertFalse(self.session.slide_records[0]['reliable'])
        self.assertFalse(any(a['key'].startswith('slide_pace:') for a in self.session.alerts))

    def test_manual_boundaries_include_pauses_and_last_slide_closes_at_stop(self):
        self.clock.advance(90)
        self.session.navigate(1)
        first = self.session.slide_records[0]
        self.assertEqual(first['actual_duration_sec'], 90)
        self.assertEqual(first['pace'], 'slow')
        self.assertFalse(first['estimated'])
        self.clock.advance(60)
        self.session.stop()
        self.clock.advance(10)  # Draining inference must not add ten seconds.
        self.session.finish()
        self.session.finish()
        last = self.session.slide_records[-1]
        self.assertEqual(last['actual_duration_sec'], 60)
        self.assertEqual(last['pace'], 'on_plan')
        self.assertEqual(len(self.session.slide_records), 2)
        with tempfile.TemporaryDirectory() as directory:
            exported = json.loads(Path(self.session.save(directory)).read_text())
            self.assertEqual(len(exported['script_progress']['slide_timing']['records']), 2)

    def test_short_single_slide_has_no_sentence_voice_and_gets_final_summary(self):
        self.session = ScriptSession(prepare_script('짧은 발표입니다.', 10), clock=self.clock)
        self.apply('script-1', start=1, end=2)
        self.clock.advance(5)
        self.session.tick()
        self.assertFalse(any(a['key'].startswith(('pace:', 'slide_pace:')) for a in self.session.alerts))
        self.session.stop()
        self.session.finish()
        self.assertEqual(len(self.session.slide_records), 1)

    def test_app_preserves_targets_and_exposes_resync_command(self):
        with tempfile.TemporaryDirectory() as directory:
            app = PresentationApp(self.deck, output_dir=directory)
            try:
                self.assertEqual(app.deck['script_slide_durations_sec'], [60, 60])
                app.command('start', {'voice': False})
                state = app.command('resync', {'index': 3})
                self.assertEqual(state['session']['script_progress']['tracking']['slide_index'], 1)
                self.assertEqual(state['session']['states']['script-4']['status'], 'unconfirmed')
            finally:
                app.close()


class ModelPositionTests(unittest.TestCase):
    def test_model_receives_all_four_candidates_and_returns_position_separate_from_facts(self):
        clock = FakeClock()
        session = ScriptSession(prepare_script(TEXT, 120), clock=clock)
        clock.advance(15)
        job = session.ingest({'segment_id': 'u1', 'text': '매출은 30퍼센트 증가했습니다.', 'start_sec': 10, 'end_sec': 12})
        coach = OllamaCoach('fixture')
        coach.model_info = {'name': 'fixture'}
        captured = []
        def request(url, body):
            payload = json.loads(body)
            captured.append(payload)
            reply = {f'P{i}': {'s': 0 if i == 2 else -1, 'e': [1] if i == 2 else []} for i in range(1, 5)}
            reply['position'] = {'id': 'P2', 'phase': 'complete', 'e': [1]}
            return {'done': True, 'message': {'content': json.dumps(reply)}}
        coach._request = request
        response = coach.evaluate(job)
        session.apply(job, response)
        self.assertEqual(session.cursor, 1)
        self.assertEqual(session.states['script-2']['status'], 'uncertain')
        payload = json.loads(captured[0]['messages'][1]['content'])
        self.assertEqual(payload['position_candidates'], ['P1', 'P2', 'P3', 'P4'])
        self.assertIn('position', captured[0]['format']['required'])


if __name__ == '__main__':
    unittest.main()
