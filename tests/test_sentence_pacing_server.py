"""User-facing timing contract through app commands; fake coach/clock/voice."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from src.presentation_server import PresentationApp, make_server
from src.script_coaching import ScriptSession, prepare_script
from tests.test_presentation import FakeClock
from tests.test_presentation_server import wait_for


class SilentVoice:
    executable = None

    def __init__(self, *args):
        pass

    def enqueue(self, *args):
        raise AssertionError('These tests must never request speaker output.')

    def close(self):
        pass


class GatedCompletionCoach:
    name = 'saved_completion_fixture'

    def __init__(self):
        self.entered = [threading.Event(), threading.Event()]
        self.release = [threading.Event(), threading.Event()]
        self.calls = 0

    def evaluate(self, job):
        index = self.calls
        self.calls += 1
        if index < 2:
            self.entered[index].set()
            if not self.release[index].wait(3):
                raise TimeoutError('Fixture completion was not released.')
        seen = {segment['segment_id'] for segment in job['segments']}
        return {'judgments':[{'keypoint_id':point['keypoint_id'],
                             'status':'explained' if f'u{i}' in seen else 'unconfirmed',
                             'reason':'Saved model completion; sentence-matching policy is unchanged.',
                             'evidence_segment_ids':[f'u{i}'] if f'u{i}' in seen else []}
                            for i,point in enumerate(job['slide']['keypoints'], 1)]}


class SentencePacingAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.coach = GatedCompletionCoach()
        deck = prepare_script('기기 안에서 음성 인식을 실행합니다.\n이번 실험 결과를 표와 그래프로 설명합니다.',60)
        with patch('src.presentation_server.VoiceFeedback', SilentVoice):
            self.app = PresentationApp(deck, coach=self.coach, output_dir=self.temp.name)
        with patch('src.presentation_server.ScriptSession', side_effect=lambda value:ScriptSession(value, clock=self.clock)):
            self.app.command('start', {'microphone':False, 'voice':False})
        self.server = make_server(self.app, 0)
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        for gate in self.coach.release:
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.http_thread.join(2)
        self.app.close()
        self.temp.cleanup()

    def utterance(self, number, text, start, end, arrival):
        with self.app.lock:
            self.clock.value = self.app.session.origin + arrival
            self.app.command('utterance', {'segment_id':f'u{number}','text':text,
                                           'start_sec':start,'end_sec':end})

    def test_first_completion_is_immediate_and_visible_during_next_sentence_processing(self):
        # The coach deliberately accepts partial text, matching the user's
        # requested policy. Timing must never make that approval stricter.
        self.utterance(1, '기기 안에서 음성 인식', 1, 4, 5)
        self.assertTrue(self.coach.entered[0].wait(1))
        self.utterance(2, '이번 실험 결과', 6, 9, 10)
        self.coach.release[0].set()
        self.assertTrue(self.coach.entered[1].wait(1))
        with urlopen(self.url + '/api/state', timeout=2) as response:
            state = json.load(response)
        progress = state['session']['script_progress']
        display = progress['display_pace']
        self.assertEqual(state['session']['states']['script-1']['status'], 'explained')
        self.assertEqual(display['pace'], 'fast')
        self.assertTrue(display['reliable'])
        self.assertTrue(display['pending'])
        self.assertAlmostEqual(display['measured_elapsed_sec'],4)
        unit = progress['units'][0]
        self.assertAlmostEqual(unit['completed_at_sec'] - unit['processing_delay_sec'],4)
        self.assertEqual(progress['pace'], 'waiting')  # Voice still waits safely.
        self.assertFalse(state['voice_enabled'])

    def test_ended_export_retains_completion_timing_and_final_visual_pace(self):
        self.utterance(1, '기기 안에서 음성 인식', 1, 4, 5)
        self.assertTrue(self.coach.entered[0].wait(1))
        self.coach.release[0].set()
        wait_for(lambda:self.app.state()['session']['states']['script-1']['status']=='explained')
        self.utterance(2, '이번 실험 결과', 6, 9, 10)
        self.assertTrue(self.coach.entered[1].wait(1))
        self.coach.release[1].set()
        wait_for(lambda:self.app.state()['session']['states']['script-2']['status']=='explained')
        with self.app.lock:
            self.clock.value = self.app.session.origin + 60
        self.app.command('stop', {})
        wait_for(lambda:self.app.state()['session']['status']=='ended')
        with urlopen(self.url + '/api/export', timeout=2) as response:
            export = json.load(response)
        display = export['script_progress']['display_pace']
        self.assertTrue(display['final'])
        self.assertEqual(display['pace'], 'fast')
        self.assertAlmostEqual(display['measured_elapsed_sec'],9)
        self.assertEqual(export['script_progress']['pace'],'waiting')
        self.assertTrue(all(unit['planned_duration_sec'] > 0 for unit in export['script_progress']['units']))


if __name__ == '__main__':
    unittest.main()
