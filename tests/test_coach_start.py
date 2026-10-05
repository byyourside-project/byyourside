"""Start preparation tests: no model, microphone or speaker access."""
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.presentation import PhraseCoach
from src.presentation_server import PresentationApp
from tests.test_presentation import DECK


class PreparingCoach(PhraseCoach):
    name = 'preparation_fixture'

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = None
        self.calls = 0

    def prepare_for_start(self):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError('fixture preparation not released')
        if self.failure:
            raise self.failure
        return {'dummy': True, 'provider': self.name}


class CoachStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.coach = PreparingCoach()
        self.app = PresentationApp(DECK, coach=self.coach, output_dir=self.temp.name)
        self.threads = []

    def tearDown(self):
        self.coach.release.set()
        for thread in self.threads:
            thread.join(2)
        self.app.close()
        self.temp.cleanup()

    def start_in_thread(self, body=None):
        results, errors = [], []
        def run():
            try:
                results.append(self.app.command('start', body or {'microphone': False}))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.assertTrue(self.coach.entered.wait(1))
        return thread, results, errors

    def test_start_preparation_leaves_timer_and_audio_idle_without_holding_state_lock(self):
        thread, results, errors = self.start_in_thread()
        observed, read_done = [], threading.Event()
        def read():
            observed.append(self.app.state())
            read_done.set()
        reader = threading.Thread(target=read, daemon=True)
        self.threads.append(reader)
        reader.start()
        self.assertTrue(read_done.wait(.5), 'state must remain readable while prepare is blocked')
        state = observed[0]
        self.assertIsNone(state['session'])
        self.assertTrue(state['start_preparing'])
        self.assertEqual(state['coach_status']['status'], 'preparing')
        self.assertEqual(state['audio_status'], 'idle')
        self.coach.release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)
        state = results[0]
        self.assertFalse(state['start_preparing'])
        self.assertEqual(state['coach_status']['status'], 'ready')
        self.assertLess(state['session']['elapsed_sec'], 1)
        prepared = next(e for e in self.app.session.events if e['type']=='coach_prepared')
        self.assertTrue(prepared['before_timer'])
        self.assertTrue(prepared['preparation']['dummy'])

    def test_preparation_blocks_duplicate_start_and_setting_changes(self):
        thread, _, errors = self.start_in_thread()
        requests = [('start', {}), ('script', {'text':'새 대본입니다.', 'duration_sec':20}),
                    ('deck', DECK), ('voice', {'enabled':False}), ('microphone_test', {})]
        with patch('src.microphone.test_input') as probe:
            for action, body in requests:
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, '준비'):
                    self.app.command(action, body)
            probe.assert_not_called()
        self.assertEqual(self.coach.calls, 1)
        self.coach.release.set()
        thread.join(1)
        self.assertFalse(errors)

    def test_failed_preparation_never_starts_mic_timer_or_enables_voice_and_can_retry(self):
        self.coach.failure = RuntimeError('model is unavailable')
        self.coach.release.set()
        with patch.object(self.app.voice, 'executable', '/fake/say'), patch.object(self.app, '_launch_audio') as launch:
            with self.assertRaisesRegex(ValueError, '모델 준비 실패'):
                self.app.command('start', {'microphone':True, 'voice':True})
            launch.assert_not_called()
        state = self.app.state()
        self.assertIsNone(state['session'])
        self.assertFalse(state['voice_enabled'])
        self.assertFalse(state['start_preparing'])
        self.assertEqual(state['coach_status']['status'], 'error')
        self.assertEqual(state['audio_status'], 'idle')
        self.coach.failure = None
        state = self.app.command('start', {'microphone':False})
        self.assertEqual(state['session']['status'], 'running')
        self.assertEqual(state['coach_status']['status'], 'ready')

    def test_invalid_start_is_rejected_before_preparation_and_voice_changes(self):
        for body in ({'voice':True,'voice_scope':'invalid'}, {'device_id':'invalid'}, {'microphone':'true'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.app.command('start', body)
        self.assertEqual(self.coach.calls, 0)
        self.assertFalse(self.app.voice_enabled)
        self.assertIsNone(self.app.session)

    def test_shutdown_during_preparation_does_not_create_a_session_later(self):
        thread, results, errors = self.start_in_thread()
        self.app.close()
        self.coach.release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results)
        self.assertEqual(len(errors), 1)
        self.assertIn('서버 종료', str(errors[0]))
        self.assertIsNone(self.app.session)
        self.assertFalse(self.app.start_preparing)


if __name__ == '__main__':
    unittest.main()
