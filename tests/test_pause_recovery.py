import threading
import unittest
import numpy as np
from types import SimpleNamespace
from src.pipeline import SpeechPipeline
from src.ollama_coach import OllamaCoach, joined_utterances
from src.presentation import PhraseCoach
from tests import test_presentation as fixtures

class PauseContextTests(unittest.TestCase):
    setUp = fixtures.PresentationTests.setUp
    feed = fixtures.PresentationTests.feed
    def test_pause_inside_sentence_includes_first_fragment_and_previous_sentence(self):
        self.feed('앞 문장입니다.',sid='previous')
        partial=self.feed('기기에서 음성을',start=5,end=6,sid='first')
        self.session.apply(partial,self.coach.evaluate(partial))
        complete=self.feed('인식합니다',start=6.8,end=8,sid='second')
        self.session.apply(complete,self.coach.evaluate(complete))
        self.assertEqual(self.session.states['p1']['status'],'explained')
        self.assertEqual([s['segment_id'] for s in complete['segments']],['previous','first','second'])
        groups=joined_utterances(complete['segments'])
        self.assertEqual(groups[-1]['numbers'],[2,3])
        self.assertIn('기기에서 음성을 인식합니다',groups[-1]['text'])

    def test_model_sees_joined_text_and_evidence_expands_to_both_fragments(self):
        import json
        coach = OllamaCoach('fixture')
        coach.model_info = {}
        requests = []
        response = {'done': True, 'message': {'content': json.dumps({'P1': {'s': 1, 'e': [1]}})}}
        def request(url, body):
            requests.append(json.loads(body))
            return response
        coach._request = request
        job = {'slide': {'title': '내용', 'keypoints': [{'keypoint_id': 'p', 'text': '로컬에서 실행합니다'}]},
               'segments': [{'segment_id': 'a', 'text': '로컬에서', 'start_sec': 0, 'end_sec': 1},
                            {'segment_id': 'b', 'text': '실행합니다', 'start_sec': 1.8, 'end_sec': 3}]}
        result = coach.evaluate(job)
        self.assertEqual(result['judgments'][0]['keypoint_id'], 'p')
        self.assertEqual(result['judgments'][0]['evidence_segment_ids'], ['a', 'b'])
        data = json.loads(requests[-1]['messages'][-1]['content'])
        self.assertEqual(data['keypoints'], {'P1': '로컬에서 실행합니다'})
        self.assertEqual(data['utterances'], [{'number': 1, 'text': '로컬에서 실행합니다'}])
        self.assertEqual(requests[-1]['format']['required'], ['P1'])
        self.assertEqual(requests[-1]['format']['properties']['P1']['properties']['e']['items']['enum'], [1])
        response['message']['content'] = json.dumps({'P1': {'s': 1, 'e': [2]}})
        with self.assertRaises(ValueError):
            coach.evaluate(job)

    def test_pending_cuts_and_context_have_bounded_recent_history(self):
        for i in range(20):
            self.feed('이어서 설명합니다',start=1+i*2,end=2+i*2,sid=str(i),endpoint='hard_max_duration')
        job=self.session.finalize_pending_through(50)[0]
        self.assertEqual(len(job['segments']),12)
        self.assertEqual(len(self.session.segments),20)
        self.assertTrue(any(e['type']=='context_trimmed' for e in self.session.events))

    def test_incomplete_followup_preserves_already_confirmed_content(self):
        complete = self.feed('기기에서 음성을 인식합니다', sid='complete')
        self.session.apply(complete, self.coach.evaluate(complete))
        self.assertEqual(self.session.states['p1']['status'], 'explained')
        partial = self.feed('이 기기에서', start=5, end=6, sid='partial')
        response = self.coach.evaluate(partial)
        for judgment in response['judgments']:
            if judgment['keypoint_id'] == 'p1':
                judgment.update(status='uncertain', reason='미완성', reason_code='incomplete_tail', evidence_segment_ids=['partial'])
        self.session.apply(partial, response)
        self.assertEqual(self.session.states['p1']['status'], 'explained')
        self.assertTrue(any(e['type'] == 'judgment_deferred' and e.get('reason') == 'incomplete_tail' for e in self.session.events))

class RecoveryTests(unittest.TestCase):
    def test_actual_isolated_stt_recovers_after_timeout_without_ending_capture(self):
        import tempfile
        from src.config import PipelineConfig
        from tests import test_regression_rev5 as live
        live.create_repro_fixture_if_needed()
        events=[]
        with tempfile.TemporaryDirectory() as directory:
            config=PipelineConfig(log_dir=directory)
            pipeline=SpeechPipeline(config,event_sink=events.append,terminal_output=False)
            try:
                pipeline.stt.inject_uncooperative_delay(3)
                result=pipeline.run_mic(duration_seconds=9,stream_factory=live.MockMicInputStream,recover_stt_timeouts=True)
                self.assertTrue(result.is_lossless)
                self.assertGreaterEqual(result.segment_count,1)
                self.assertEqual([e['stage'] for e in events if e['event_type']=='stt_recovery'],['retry','resumed'])
                self.assertTrue(any(e['event_type']=='segment_result' and e['status']=='OK' for e in events))
                self.assertFalse(pipeline.has_running_workers())
            finally:pipeline.close()

    def pipeline(self,outcomes):
        class STT:
            def transcribe(self,samples,sr,**kwargs):
                value=outcomes.pop(0)
                if isinstance(value,Exception): raise value
                return value
        pipe=SpeechPipeline.__new__(SpeechPipeline)
        pipe.stt=STT()
        return pipe
    def test_retry_uses_retained_samples_and_success_does_not_drop_segment(self):
        pipe=self.pipeline([TimeoutError('stall'),('전사',10)])
        events=[]
        logger=SimpleNamespace(log_event=lambda k,v:events.append((k,v)))
        seg=SimpleNamespace(samples=np.zeros(16,dtype=np.float32),segment_id=1)
        self.assertEqual(pipe._transcribe_live_segment(seg,logger,threading.Event(),2,True),('전사',10,'OK'))
        self.assertEqual([e[1]['stage'] for e in events],['retry','resumed'])
    def test_failed_retry_is_explicit_error_and_next_request_can_succeed(self):
        pipe=self.pipeline([TimeoutError('first'),TimeoutError('retry'),('next',10)])
        events=[]
        logger=SimpleNamespace(log_event=lambda k,v:events.append((k,v)))
        seg=SimpleNamespace(samples=np.zeros(16,dtype=np.float32),segment_id=1)
        self.assertEqual(pipe._transcribe_live_segment(seg,logger,threading.Event(),2,True)[2],'ERROR')
        self.assertEqual(pipe._transcribe_live_segment(seg,logger,threading.Event(),2,True)[0],'next')
        self.assertEqual(events[-1][1]['stage'],'failed_segment')
    def test_benchmark_fail_fast_and_cancellation_do_not_retry(self):
        for recover,cancel in ((False,False),(True,True)):
            pipe=self.pipeline([TimeoutError('stall')])
            stop=threading.Event()
            if cancel:stop.set()
            seg=SimpleNamespace(samples=np.zeros(16,dtype=np.float32),segment_id=1)
            with self.assertRaises(TimeoutError):pipe._transcribe_live_segment(seg,SimpleNamespace(log_event=lambda *a:None),stop,2,recover)
