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

    def test_fast_slow_and_stale_evidence_suppress_pace(self):
        for elapsed, expected in ((15,'fast'),(100,'slow')):
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
                wait_for(lambda:app.session.status=='ended')
                saved=json.load(open(app.output_path))
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
