"""Voice policy integration without sending audio to an output device."""
import tempfile
import unittest
from unittest.mock import patch

from src.presentation_server import PresentationApp
from src.script_coaching import prepare_script
from tests.test_presentation import DECK, FakeClock
from tests.test_presentation_server import wait_for


class RecordingVoice:
    def __init__(self, emit, valid):
        self.emit, self.valid = emit, valid
        self.executable = '/fake/say'
        self.sent = []
        self.accept = True

    def enqueue(self, session, alert):
        if self.accept:
            self.sent.append((session, dict(alert)))
            self.emit(session, 'voice_queued', alert)
        return self.accept

    def close(self):
        pass


class PaceVoicePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch('src.presentation_server.VoiceFeedback', RecordingVoice)
        self.patch.start()
        self.app = PresentationApp(DECK, output_dir=self.temp.name)
        self.clock = FakeClock()

    def tearDown(self):
        self.app.close()
        self.patch.stop()
        self.temp.cleanup()

    def start(self, scope='pace', enabled=True):
        self.app.command('start', {'voice': enabled, 'voice_scope': scope})
        with self.app.lock:
            self.app.session.clock = self.clock
            self.app.session.origin = self.clock.value
            self.app.session.pace = 'fast'

    def alert(self, key):
        with self.app.lock:
            self.clock.advance(6)
            self.app.session.alert(key, key, ttl=120)
            return dict(self.app.session.alerts[-1])

    def test_default_scope_speaks_pace_but_retains_other_screen_alerts(self):
        self.assertEqual(self.app.state()['voice_scope'], 'pace')
        self.start()
        for key in ('total_remaining', 'script_missing:script-2', 'pace:fast:1'):
            self.alert(key)
        wait_for(lambda: bool(self.app.voice.sent))
        self.assertEqual([a['key'] for _, a in self.app.voice.sent], ['pace:fast:1'])
        self.assertEqual(len(self.app.session.snapshot()['alerts']), 3)
        configured = next(e for e in self.app.session.events if e['type'] == 'voice_configured')
        self.assertEqual(configured['scope'], 'pace')

    def test_all_scope_admits_other_alerts_and_scope_change_invalidates_old_queue(self):
        self.start('all')
        self.alert('script_missing:script-2')
        wait_for(lambda: bool(self.app.voice.sent))
        session, old = self.app.voice.sent[-1]
        self.assertTrue(self.app._voice_valid(session, old))
        self.app.command('voice', {'enabled': True, 'scope': 'pace'})
        self.assertFalse(self.app._voice_valid(session, old))
        self.assertEqual(self.app.state()['voice_scope'], 'pace')

    def test_off_then_on_does_not_revive_a_previously_queued_instruction(self):
        self.start()
        self.alert('pace:fast:1')
        wait_for(lambda: bool(self.app.voice.sent))
        session, old = self.app.voice.sent[-1]
        self.app.command('voice', {'enabled': False})
        self.app.command('voice', {'enabled': True})
        self.assertFalse(self.app._voice_valid(session, old))
        # Late cancellation remains recorded but cannot replace the new UI state.
        self.app._voice_event(session, 'voice_cancelled', old)
        self.assertIsNone(self.app.state()['voice_last_event'])
        self.assertEqual(len(self.app.voice.sent), 1)

    def test_direction_change_stop_and_ended_session_cancel_old_instruction(self):
        self.start()
        self.alert('pace:fast:1')
        wait_for(lambda: bool(self.app.voice.sent))
        session, old = self.app.voice.sent[-1]
        with self.app.lock:
            session.pace = 'slow'
            self.assertFalse(self.app._voice_valid(session, old))
            session.pace = 'fast'
            session.stop()
            self.assertFalse(self.app._voice_valid(session, old))
        wait_for(lambda: self.app.state()['session']['status'] == 'ended')
        self.assertFalse(self.app._voice_valid(session, old))

    def test_explicit_voice_test_requires_enabled_running_session(self):
        with self.assertRaises(ValueError):
            self.app.command('voice_test', {})
        self.start(enabled=False)
        with self.assertRaises(ValueError):
            self.app.command('voice_test', {})
        self.app.command('voice', {'enabled': True})
        self.app.command('voice_test', {})
        wait_for(lambda: bool(self.app.voice.sent))
        self.assertTrue(self.app.voice.sent[0][1]['key'].startswith('voice_test:'))
        self.app.command('stop', {})
        wait_for(lambda: self.app.state()['session']['status'] == 'ended')
        with self.assertRaises(ValueError):
            self.app.command('voice_test', {})

    def test_invalid_scope_is_atomic_and_unavailable_output_is_visible(self):
        for value in (None, [], True, 'other'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.app.command('voice', {'enabled': True, 'scope': value})
                self.assertFalse(self.app.voice_enabled)
                self.assertEqual(self.app.voice_scope, 'pace')
        with self.assertRaises(ValueError):
            self.app.command('start', {'voice': True, 'voice_scope': 'other'})
        self.assertIsNone(self.app.session)
        with self.assertRaises(ValueError):
            self.app.command('start', {'voice': True, 'device_id': 'invalid'})
        self.assertFalse(self.app.voice_enabled)
        self.app.voice.executable = None
        with self.assertRaisesRegex(ValueError, '사용할 수 없습니다'):
            self.app.command('voice', {'enabled': True})

    def test_rejected_enqueue_can_be_retried_without_marking_it_as_spoken(self):
        self.start()
        self.app.voice.accept = False
        self.alert('pace:fast:1')
        self.assertNotIn('pace:fast:1', self.app.voiced)
        self.app.voice.accept = True
        wait_for(lambda: bool(self.app.voice.sent))
        self.assertIn('pace:fast:1', self.app.voiced)

    def test_ended_final_review_can_never_start_voice_in_all_scope(self):
        self.start('all')
        self.app.command('stop', {})
        wait_for(lambda: self.app.state()['session']['status'] == 'ended')
        with self.app.lock:
            session = self.app.session
            session.alert('script_final_review', '확인되지 않은 구간', ttl=120)
            final = dict(session.alerts[-1])
            self.assertFalse(self.app._voice_valid(session, final))
        self.assertFalse(self.app.voice.sent)

    def test_direct_start_preserves_selected_scope_when_scope_is_omitted(self):
        self.app.command('voice', {'enabled': False, 'scope': 'all'})
        self.app.start(voice=True)
        self.assertTrue(self.app.voice_enabled)
        self.assertEqual(self.app.voice_scope, 'all')

    def test_real_script_progress_queues_pace_and_new_pending_speech_cancels_it(self):
        text = ('첫 문장은 목적을 설명합니다.\n두 번째 문장은 비용을 설명합니다.\n'
                '세 번째 문장은 일정을 설명합니다.\n마지막 문장은 결과를 설명합니다.')
        self.app.command('deck', prepare_script(text, 120))
        self.start()
        with self.app.lock:
            self.clock.advance(20)
            self.app.session.pace = 'waiting'
        self.app.command('utterance', {'text': text.splitlines()[0], 'start_sec': 18, 'end_sec': 19})
        wait_for(lambda: self.app.state()['session']['states']['script-1']['status'] == 'explained')
        with self.app.lock:
            self.clock.advance(6)
        wait_for(lambda: bool(self.app.voice.sent))
        session, old = self.app.voice.sent[-1]
        self.assertIn('빠릅니다', old['message'])
        self.assertTrue(self.app._voice_valid(session, old))
        self.app.command('utterance', {'text': '두 번째 문장은', 'start_sec': 25, 'end_sec': 26,
                                      'endpoint_reason': 'hard_max_duration'})
        self.assertTrue(session.pending)
        self.assertFalse(self.app._voice_valid(session, old))
        self.assertEqual(self.app.state()['session']['script_progress']['pace'], 'waiting')
