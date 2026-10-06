"""Server-level first-sentence visibility under continuing speech; no audio."""
import tempfile
import threading
import unittest

from src.presentation import PhraseCoach
from src.presentation_server import PresentationApp
from src.script_coaching import prepare_script
from tests.test_presentation_server import wait_for


class BlockingCoach(PhraseCoach):
    def __init__(self):
        self.entered = [threading.Event(), threading.Event()]
        self.release = [threading.Event(), threading.Event()]
        self.calls = []

    def evaluate(self, job):
        index = len(self.calls)
        self.calls.append(job['revision'])
        if index < 2:
            self.entered[index].set()
            if not self.release[index].wait(3):
                raise TimeoutError('test coach was not released')
        return super().evaluate(job)


class ContinuingSpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.coach = BlockingCoach()
        self.lines = ['기기에서 음성을 인식합니다.', '발표 내용을 정리합니다.', '발표를 마무리합니다.']
        self.app = PresentationApp(prepare_script('\n'.join(self.lines),90), coach=self.coach,
                                   output_dir=self.temp.name)
        self.app.command('start', {'microphone':False, 'voice':False})
        with self.app.lock:
            self.app.session.origin -= 10

    def tearDown(self):
        for event in self.coach.release:
            event.set()
        self.app.close()
        self.temp.cleanup()

    def say(self, number):
        self.app.command('utterance', {'text':self.lines[number-1], 'segment_id':f's{number}',
                                       'start_sec':number*2-1,'end_sec':number*2})

    def test_inflight_first_result_is_visible_while_second_request_remains_pending(self):
        self.say(1)
        self.assertTrue(self.coach.entered[0].wait(1))
        self.say(2)
        self.coach.release[0].set()
        self.assertTrue(self.coach.entered[1].wait(1))
        snapshot = self.app.state()['session']
        self.assertEqual(snapshot['states']['script-1']['status'], 'explained')
        self.assertEqual(snapshot['states']['script-2']['status'], 'unconfirmed')
        self.assertEqual(snapshot['judgment_status'], 'evaluating')
        self.assertEqual(snapshot['script_progress']['measured_elapsed_sec'], 2)
        self.assertFalse(snapshot['script_progress']['reliable'])
        event = next(e for e in self.app.session.events if e['type']=='coaching_inference')
        self.assertEqual(event['result_scope'], 'earlier_revision')
        self.assertIn('script-1', event['applied_keypoint_ids'])
        self.assertEqual(event['confirmed_keypoint_ids'], ['script-1'])
        self.coach.release[1].set()
        wait_for(lambda:self.app.state()['session']['states']['script-2']['status']=='explained')

    def test_queued_obsolete_snapshots_are_skipped_without_discarding_inflight_positive(self):
        self.say(1)
        self.assertTrue(self.coach.entered[0].wait(1))
        self.say(2)
        self.say(3)
        self.coach.release[0].set()
        self.assertTrue(self.coach.entered[1].wait(1))
        self.assertEqual(self.coach.calls, [1,3])
        snapshot = self.app.state()['session']
        self.assertEqual(snapshot['states']['script-1']['status'], 'explained')
        self.assertEqual(snapshot['judgment_status'], 'evaluating')
        self.assertTrue(any(e['type']=='judgment_discarded' and e['revision']==2 for e in self.app.session.events))
        self.coach.release[1].set()
        wait_for(lambda:self.app.state()['session']['states']['script-3']['status']=='explained')


if __name__=='__main__':
    unittest.main()
