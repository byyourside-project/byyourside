import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from src.script_coaching import prepare_script, ScriptSession
from src.presentation import PhraseCoach
from src.presentation_server import PresentationApp
from src.voice_feedback import VoiceFeedback
from tests.test_presentation import FakeClock
from tests.test_presentation_server import wait_for

TEXT = '첫 문장은 목적을 설명합니다.\n두 번째 문장은 비용을 설명합니다.\n세 번째 문장은 일정을 설명합니다.\n마지막 문장은 결과를 설명합니다.'

class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.deck = prepare_script(TEXT, 120)
        self.session = ScriptSession(self.deck, clock=self.clock)
        self.coach = PhraseCoach()

    def feed(self, text, elapsed=20, sid='s'):
        self.clock.value = self.session.origin + elapsed
        job = self.session.ingest({'segment_id':sid,'text':text,'start_sec':elapsed-2,'end_sec':elapsed-1,'endpoint_reason':'silence'})
        self.session.apply(job,self.coach.evaluate(job))

    def test_plan_preserves_text_and_weighted_time(self):
        self.assertEqual(self.deck['script_text'],TEXT)
        self.assertEqual(len(self.deck['script_plan']['units']),4)
        self.assertAlmostEqual(self.deck['script_plan']['units'][-1]['planned_end_sec'],120)
        self.assertAlmostEqual(sum(p['planned_end_sec']-p['planned_start_sec'] for p in self.deck['script_plan']['units']),120)
        for text in ('', 'x'*20001, '\n'.join(['문장']*51)):
            with self.assertRaises(ValueError): prepare_script(text,120)
        for duration in (0,True,float('nan')):
            with self.assertRaises(ValueError): prepare_script(TEXT,duration)

    def test_repeat_does_not_inflate_progress_and_routing_does_not_mutate_deck(self):
        self.feed('첫 문장은 목적을 설명합니다.')
        first=self.session.progress()['confirmed_units']
        self.feed('첫 문장은 목적을 설명합니다.',25,'repeat')
        self.assertEqual(self.session.progress()['confirmed_units'],first)
        self.assertEqual(len(self.session.deck['slides'][0]['keypoints']),4)

    def test_gap_waits_for_anchor_and_retracts_after_late_explanation(self):
        self.feed('첫 문장은 목적을 설명합니다.')
        self.feed('세 번째 문장은 일정을 설명합니다.',30,'third')
        self.assertEqual(self.session.missing_ids,[])
        self.feed('마지막 문장은 결과를 설명합니다.',40,'last')
        self.assertEqual(self.session.missing_ids,['script-2'])
        self.assertTrue(any(a['key']=='script_missing:script-2' for a in self.session.alerts))
        self.feed('두 번째 문장은 비용을 설명합니다.',45,'late')
        self.assertEqual(self.session.missing_ids,[])
        self.assertTrue(any(e['type']=='alert_retracted' for e in self.session.events))

    def test_lost_input_withholds_gap_claim_but_later_explanation_still_confirms(self):
        self.feed('첫 문장은 목적을 설명합니다.')
        message = '음성 입력 손실 감지: dropped_chunks=1'
        self.session.issue(message)
        self.feed('세 번째 문장은 일정을 설명합니다.',30,'third')
        self.feed('마지막 문장은 결과를 설명합니다.',40,'last')
        self.assertEqual(self.session.missing_ids, [])
        self.assertEqual(self.session.states['script-2']['status'], 'uncertain')
        self.assertFalse(any(a['key'].startswith('script_missing:') for a in self.session.snapshot()['alerts']))
        self.assertEqual(sum(s['status'] == 'explained' for s in self.session.states.values()), 3)
        # A quality issue remains in the report without preventing new reliable
        # content evidence from completing a previously unresolved section.
        self.feed('두 번째 문장은 비용을 설명합니다.',45,'late')
        self.assertEqual(self.session.states['script-2']['status'], 'explained')
        self.assertEqual(self.session.progress()['fraction'], 1)
        self.assertEqual(self.session.issues, [message])

    def test_new_capture_stt_or_model_issue_immediately_retracts_existing_gap_alert(self):
        for message in ('음성 입력 손실 감지: dropped_chunks=1',
                        'STT 재시도 후에도 전사하지 못한 구간이 있습니다.',
                        '내용 판단 오류: model timeout'):
            with self.subTest(message=message):
                self.setUp()
                self.feed('첫 문장은 목적을 설명합니다.')
                self.feed('세 번째 문장은 일정을 설명합니다.',30,'third')
                self.feed('마지막 문장은 결과를 설명합니다.',40,'last')
                self.assertEqual(self.session.missing_ids, ['script-2'])
                confirmed = {kid: json.dumps(state) for kid,state in self.session.states.items()
                             if state['status'] == 'explained'}
                self.session.issue(message)
                self.assertEqual(self.session.missing_ids, [])
                self.assertEqual(self.session.states['script-2']['status'], 'uncertain')
                self.assertFalse(any(a['key'].startswith('script_missing:') for a in self.session.snapshot()['alerts']))
                self.assertTrue(any(e['type'] == 'alert_retracted' for e in self.session.events))
                for kid,state in confirmed.items():
                    self.assertEqual(json.dumps(self.session.states[kid]), state)
                self.assertIn(message, self.session.issues)

    def test_fast_slow_and_stale_evidence_suppress_pace(self):
        for elapsed, expected in ((16,'fast'),(100,'slow')):
            self.setUp()
            self.feed('첫 문장은 목적을 설명합니다.',elapsed)
            self.session.tick()
            self.clock.advance(6)
            self.session.tick()
            self.assertEqual(self.session.pace,expected)
            self.assertTrue(any(a['key'].startswith('pace:') for a in self.session.alerts))
            self.clock.advance(20)
            self.session.tick()
            self.assertEqual(self.session.pace,'waiting')

    def active_pace_alerts(self):
        return [a for a in self.session.snapshot()['alerts'] if a['key'].startswith('pace:')]

    def confirm_at(self, text, end, *, applied_at=None, sid=None, response=None):
        self.clock.value = self.session.origin + (applied_at if applied_at is not None else end + 1)
        job = self.session.ingest({'segment_id':sid or f'speech-{end}', 'text':text,
                                   'start_sec':end-2, 'end_sec':end, 'endpoint_reason':'silence'})
        self.session.apply(job, response or self.coach.evaluate(job))
        return job

    def test_model_delay_is_excluded_from_measured_pace(self):
        # This unit is allocated about 28 seconds. Speaking it in 28 seconds
        # stays on plan, even though recognition/analysis takes 12 seconds.
        planned = self.deck['script_plan']['units'][0]['planned_end_sec']
        self.confirm_at('첫 문장은 목적을 설명합니다.', planned, applied_at=planned+12)
        progress = self.session.progress()
        self.assertAlmostEqual(progress['ratio'], 1)
        self.assertAlmostEqual(progress['measured_elapsed_sec'], planned)
        self.assertAlmostEqual(progress['estimated_total_sec'], 120)
        self.assertAlmostEqual(progress['processing_delay_sec'], 12)
        self.assertTrue(progress['reliable'])
        self.assertEqual(self.session.pace_candidate, 'on_plan')
        self.assertFalse(self.active_pace_alerts())
        self.clock.advance(2)
        self.assertAlmostEqual(self.session.progress()['processing_delay_sec'], 12)
        self.assertAlmostEqual(self.session.progress()['evidence_age_sec'], 14)

    def test_minimum_evidence_is_based_on_audio_time_and_confirmed_script_amount(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 14, applied_at=20)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.active_pace_alerts())
        self.session = ScriptSession(prepare_script('처음.\n'+'나머지 설명을 자세히 이어갑니다.'*8, 120), clock=self.clock)
        self.confirm_at('처음.', 20)
        self.clock.advance(5)
        self.session.tick()
        self.assertLess(self.session.progress()['fraction'], .1)
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.active_pace_alerts())

    def test_exact_twenty_five_percent_boundaries_are_on_plan(self):
        for ratio in (.75, 1.25):
            with self.subTest(ratio=ratio):
                self.setUp()
                planned = self.deck['script_plan']['units'][0]['planned_end_sec']
                self.confirm_at('첫 문장은 목적을 설명합니다.', planned/ratio)
                self.clock.advance(5)
                self.session.tick()
                self.assertEqual(self.session.pace, 'on_plan')
                self.assertFalse(self.active_pace_alerts())

    def test_delayed_uncertain_capture_permanently_withholds_pace_even_after_new_progress(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertTrue(self.active_pace_alerts())
        # A delayed error for older audio must not disappear just because newer
        # speech has a later timestamp and the first unit is already confirmed.
        self.session.ingest({'segment_id':'lost-old', 'text':'', 'start_sec':1, 'end_sec':2, 'status':'ERROR'})
        self.assertFalse(self.active_pace_alerts())
        self.confirm_at('두 번째 문장은 비용을 설명합니다.', 22)
        self.clock.advance(5)
        self.session.tick()
        self.assertFalse(self.session.progress()['reliable'])
        self.assertIn('오류', self.session.progress()['pace_reason'])
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.active_pace_alerts())

    def test_delayed_old_audio_is_not_treated_as_slow_or_recent(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 20, applied_at=70)
        self.session.tick()
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.session.progress()['reliable'])
        self.assertFalse(self.active_pace_alerts())
        self.assertAlmostEqual(self.session.progress()['measured_elapsed_sec'], 20)

    def test_short_consecutive_model_waits_preserve_direction_candidate(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        initial_since = self.session.pace_since
        self.clock.advance(2)
        job = self.session.ingest({'segment_id':'second-pending', 'text':'두 번째 문장은 비용을 설명합니다.',
                                   'start_sec':17, 'end_sec':18, 'endpoint_reason':'silence'})
        self.session.tick()
        self.assertEqual(self.session.pace, 'waiting')
        self.assertEqual(self.session.pace_candidate, 'fast')
        self.assertEqual(self.session.pace_since, initial_since)
        self.assertFalse(self.active_pace_alerts())
        self.clock.advance(4)
        self.session.tick()
        self.assertEqual(self.session.pace_candidate, 'fast')
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.pace, 'fast')
        self.assertEqual(len(self.active_pace_alerts()), 1)

    def test_pending_speech_immediately_retracts_previous_pace_alert(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'fast')
        self.assertEqual(len(self.active_pace_alerts()), 1)
        self.session.ingest({'segment_id':'pending-cut', 'text':'두 번째 문장은',
                             'start_sec':20, 'end_sec':21, 'endpoint_reason':'hard_max_duration'})
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.active_pace_alerts())
        self.assertEqual(self.session.pace_candidate, 'fast')
        self.assertTrue(any(e['type']=='alert_retracted' and e['reason']=='new_speech_pending'
                            for e in self.session.events))

    def test_repeated_old_content_does_not_refresh_measurement_or_create_slow_notice(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        units = self.session.progress()['confirmed_units']
        self.confirm_at('첫 문장은 목적을 설명합니다.', 100, sid='repeat-later')
        self.clock.advance(6)
        self.session.tick()
        self.assertEqual(self.session.progress()['confirmed_units'], units)
        self.assertEqual(self.session.progress()['measured_elapsed_sec'], 16)
        self.assertFalse(self.session.progress()['reliable'])
        self.assertEqual(self.session.pace, 'waiting')
        self.assertIsNone(self.session.pace_candidate)
        self.assertFalse(self.active_pace_alerts())

    def test_uncertain_new_speech_resets_direction_and_retracts_old_notice(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(len(self.active_pace_alerts()), 1)
        self.clock.advance(1)
        job = self.session.ingest({'segment_id':'uncertain-new', 'text':'두 번째 문장은 비용이 달라집니다.',
                                   'start_sec':21, 'end_sec':22, 'endpoint_reason':'silence'})
        response = self.coach.evaluate(job)
        for judgment in response['judgments']:
            if judgment['keypoint_id'] == 'script-2':
                judgment.update(status='uncertain', reason='다른 비용 설명', evidence_segment_ids=['uncertain-new'])
        self.session.apply(job, response)
        self.assertEqual(self.session.pace, 'waiting')
        self.assertIsNone(self.session.pace_candidate)
        self.assertFalse(self.session.progress()['reliable'])
        self.assertFalse(self.active_pace_alerts())

    def test_input_stt_and_model_errors_reset_pace_immediately(self):
        for message in ('음성 입력 손실 감지: dropped_chunks=1', 'STT 변환 오류', '내용 판단 오류'):
            with self.subTest(message=message):
                self.setUp()
                self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
                self.clock.advance(5)
                self.session.tick()
                self.assertEqual(self.session.pace, 'fast')
                self.session.issue(message)
                self.assertEqual(self.session.pace, 'waiting')
                self.assertIsNone(self.session.pace_candidate)
                self.assertFalse(self.active_pace_alerts())
                self.assertFalse(self.session.progress()['reliable'])

    def test_opposite_direction_replaces_candidate_without_stale_notice(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'fast')
        self.confirm_at('두 번째 문장은 비용을 설명합니다.', 85)
        self.assertEqual(self.session.pace, 'waiting')
        self.assertEqual(self.session.pace_candidate, 'slow')
        self.assertFalse(self.active_pace_alerts())
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'slow')
        self.assertEqual([a['key'].split(':')[1] for a in self.active_pace_alerts()], ['slow'])

    def test_on_plan_cancels_fast_notice_and_has_no_voice_alert(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'fast')
        planned = self.deck['script_plan']['units'][1]['planned_end_sec']
        self.confirm_at('두 번째 문장은 비용을 설명합니다.', planned)
        self.assertEqual(self.session.pace_candidate, 'on_plan')
        self.assertFalse(self.active_pace_alerts())
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.pace, 'on_plan')
        self.assertFalse(self.active_pace_alerts())

    def test_repeat_notice_interval_uses_last_notice_not_clock_buckets(self):
        # Crossing a 30-second wall-clock boundary must not repeat an alert
        # that was spoken one second before that boundary.
        self.session = ScriptSession(prepare_script(TEXT, 600), clock=self.clock)
        self.confirm_at('첫 문장은 목적을 설명합니다.', 22)
        self.clock.advance(6)
        self.session.tick()  # notice at 29
        notices = [e for e in self.session.events if e['type']=='alert_shown' and e['key'].startswith('pace:')]
        self.assertEqual(len(notices), 1)
        self.confirm_at('두 번째 문장은 비용을 설명합니다.', 29, applied_at=30)
        self.session.tick()
        notices = [e for e in self.session.events if e['type']=='alert_shown' and e['key'].startswith('pace:')]
        self.assertEqual(len(notices), 1)
        self.confirm_at('세 번째 문장은 일정을 설명합니다.', 57, applied_at=58)
        self.clock.advance(5)
        self.session.tick()
        notices = [e for e in self.session.events if e['type']=='alert_shown' and e['key'].startswith('pace:')]
        self.assertEqual(len(notices), 2)
        self.assertGreaterEqual(notices[1]['shown_sec']-notices[0]['shown_sec'], 30)

    def test_passed_missing_section_does_not_trigger_pace(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.confirm_at('세 번째 문장은 일정을 설명합니다.', 18)
        self.confirm_at('마지막 문장은 결과를 설명합니다.', 20)
        self.clock.advance(5)
        self.session.tick()
        self.assertEqual(self.session.missing_ids, ['script-2'])
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.session.progress()['reliable'])
        self.assertFalse(self.active_pace_alerts())

    def test_stop_retracts_pace_instruction(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.assertTrue(self.active_pace_alerts())
        self.session.stop()
        self.assertEqual(self.session.pace, 'waiting')
        self.assertFalse(self.active_pace_alerts())

    def test_empty_stt_tail_preserves_current_pace_and_timing(self):
        self.confirm_at('첫 문장은 목적을 설명합니다.', 16)
        self.clock.advance(5)
        self.session.tick()
        self.session.ingest({'segment_id':'empty-end', 'text':'', 'start_sec':20, 'end_sec':21, 'status':'OK'})
        self.assertEqual(self.session.pace, 'fast')
        self.assertEqual(self.session.progress()['measured_elapsed_sec'], 16)
        self.assertTrue(self.active_pace_alerts())

    def test_empty_tail_does_not_damage_confirmed_or_pending_content(self):
        self.feed('첫 문장은 목적을 설명합니다.')
        before=json.dumps(self.session.states)
        self.session.ingest({'segment_id':'empty','text':'','start_sec':18,'end_sec':18.022,'status':'OK'})
        self.assertEqual(json.dumps(self.session.states),before)

    def test_jump_candidate_is_included_without_shrinking_full_script(self):
        deck=prepare_script('\n'.join(f'항목 {i}의 연구 결과를 설명합니다.' for i in range(30)),120)
        session=ScriptSession(deck,clock=self.clock)
        self.clock.advance(20)
        job=session.ingest({'segment_id':'jump','text':'항목 29의 연구 결과를 설명합니다.','start_sec':16,'end_sec':19})
        self.assertIn('script-30',[p['keypoint_id'] for p in job['slide']['keypoints']])
        self.assertLessEqual(len(job['slide']['keypoints']),9)
        self.assertEqual(len(session.deck['slides'][0]['keypoints']),30)

    def test_uncertain_passed_section_suppresses_speed_judgment(self):
        self.feed('마지막 문장은 결과를 설명합니다.')
        self.session.states['script-1']['status']='uncertain'
        self.assertFalse(self.session.progress()['reliable'])

    def test_http_application_preparation_export_and_voice_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            app=PresentationApp(self.deck,output_dir=directory)
            try:
                state=app.command('script',{'text':TEXT,'duration_sec':120})
                self.assertIn('script_plan',state['deck'])
                app.command('start',{'voice':False})
                self.assertIsInstance(app.session,ScriptSession)
                with self.assertRaises(ValueError): app.command('script',{'text':TEXT,'duration_sec':120})
                app.command('voice',{'enabled':True})
                self.assertTrue(app.state()['voice_enabled'])
                app.command('voice',{'enabled':False})
                app.command('stop',{})
                wait_for(lambda:app.state()['session']['status']=='ended')
                saved=json.loads(Path(app.output_path).read_text(encoding='utf-8'))
                self.assertIn('script_progress',saved)
            finally: app.close()

class VoiceTests(unittest.TestCase):
    def test_active_voice_is_terminated_when_instruction_is_no_longer_valid(self):
        events=[]
        valid=[True]
        class Process:
            terminated=False
            def poll(self): return None
            def terminate(self): self.terminated=True
            def wait(self,timeout): return 0
        process=Process()
        class Session: session_id='test'
        with patch('src.voice_feedback.subprocess.Popen',return_value=process):
            voice=VoiceFeedback(lambda s,k,a,**f:events.append(k),lambda s,a:valid[0],executable='/usr/bin/say')
            try:
                voice.enqueue(Session(),{'key':'a','message':'안내','priority':2,'shown_sec':0})
                wait_for(lambda:'voice_started' in events)
                valid[0]=False
                wait_for(lambda:'voice_cancelled' in events)
                self.assertTrue(process.terminated)
            finally: voice.close()

    def test_queue_records_success_without_blocking_and_stale_is_cancelled(self):
        events=[]
        class Process:
            def poll(self): return 0
            def wait(self,timeout): return 0
        class Session:
            session_id='test'
        alert={'key':'a','message':'안내','priority':2,'shown_sec':0}
        with patch('src.voice_feedback.subprocess.Popen',return_value=Process()) as popen:
            voice=VoiceFeedback(lambda s,k,a,**f:events.append(k),lambda s,a:a['key']=='a',executable='/usr/bin/say')
            try:
                voice.enqueue(Session(),alert)
                wait_for(lambda:'voice_completed' in events)
                voice.enqueue(Session(),{**alert,'key':'stale'})
                wait_for(lambda:'voice_cancelled' in events)
                self.assertEqual(popen.call_count,1)
                self.assertIn('voice_started',events)
            finally: voice.close()
