"""Voice lifecycle regressions using fake processes; never invoke a speaker."""
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from src.voice_feedback import VoiceFeedback


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for fake voice worker")
        threading.Event().wait(.005)


class FakeProcess:
    def __init__(self, *, completed=False, cooperative=True, wait_error=False,
                 terminate_error=False, delay_wait=False):
        self.returncode = 0 if completed else None
        self.cooperative = cooperative
        self.wait_error = wait_error
        self.terminate_error = terminate_error
        self.delay_wait = delay_wait
        self.terminated = self.killed = self.reaped = False
        self.wait_timeouts = []
        self.lock = threading.Lock()

    def poll(self):
        with self.lock:
            return self.returncode

    def terminate(self):
        with self.lock:
            self.terminated = True
            if self.terminate_error:
                raise OSError("fake terminate failed")
            if self.cooperative:
                self.returncode = -15

    def kill(self):
        with self.lock:
            self.killed = True
            self.returncode = -9

    def wait(self, timeout):
        with self.lock:
            self.wait_timeouts.append(timeout)
            if self.wait_error:
                self.wait_error = False
                raise OSError("fake wait failed")
            code = self.returncode
            if code is not None:
                self.reaped = True
                return code
        if self.delay_wait:
            threading.Event().wait(timeout)
        raise subprocess.TimeoutExpired("fake-say", timeout)


class VoiceFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.voices = []
        self.patch = patch("src.voice_feedback.subprocess.Popen")
        self.popen = self.patch.start()
        self.popen.side_effect = lambda *args, **kwargs: FakeProcess(completed=True)
        self.alert = {"key": "pace_fast", "message": "조금 천천히 말하세요.",
                      "priority": 3, "shown_sec": 0}
        self.session = object()

    def tearDown(self):
        try:
            for voice in self.voices:
                voice.close()
        finally:
            self.patch.stop()

    def record(self, session, kind, alert, **fields):
        self.events.append((kind, alert["key"], fields))

    def voice(self, *, emit=None, valid=None, **kwargs):
        voice = VoiceFeedback(emit or self.record, valid or (lambda session, alert: True),
                              executable="/never-execute-real-speech", **kwargs)
        self.voices.append(voice)
        return voice

    def kinds(self):
        return [event[0] for event in self.events]

    def test_queue_announcement_completes_before_started_and_completion(self):
        entered, release = threading.Event(), threading.Event()
        accepted = []

        def emit(session, kind, alert, **fields):
            if kind == "voice_queued":
                entered.set()
                release.wait(2)
            self.record(session, kind, alert, **fields)

        voice = self.voice(emit=emit)
        producer = threading.Thread(target=lambda: accepted.append(voice.enqueue(self.session, self.alert)))
        producer.start()
        try:
            self.assertTrue(entered.wait(1))
            threading.Event().wait(.08)
            self.assertEqual(self.popen.call_count, 0)
            self.assertEqual(self.kinds(), [])
        finally:
            release.set()
            producer.join(1)
        wait_until(lambda: "voice_completed" in self.kinds())
        self.assertEqual(accepted, [True])
        self.assertEqual(self.kinds(), ["voice_queued", "voice_started", "voice_completed"])

    def test_callbacks_are_not_invoked_under_internal_mutexes(self):
        holder, acquired = {}, []

        def check_locks():
            voice = holder["voice"]
            for lock in (voice._state_lock, voice._cleanup_lock):
                got = lock.acquire(blocking=False)
                acquired.append(got)
                if got:
                    lock.release()

        def emit(session, kind, alert, **fields):
            check_locks()
            self.record(session, kind, alert, **fields)

        def valid(session, alert):
            check_locks()
            return True

        voice = self.voice(emit=emit, valid=valid)
        holder["voice"] = voice
        self.assertTrue(voice.enqueue(self.session, self.alert))
        wait_until(lambda: "voice_completed" in self.kinds())
        self.assertTrue(acquired)
        self.assertTrue(all(acquired))

    def test_enqueue_after_close_is_rejected_without_process(self):
        voice = self.voice()
        voice.close()
        self.assertFalse(voice.enqueue(self.session, self.alert))
        self.assertEqual(self.popen.call_count, 0)
        self.assertEqual(voice.queue.unfinished_tasks, 0)
        self.assertEqual(self.events[-1][0], "voice_cancelled")
        self.assertEqual(self.events[-1][2]["reason"], "shutdown")

    def test_unavailable_output_is_rejected(self):
        with patch("src.voice_feedback.shutil.which", return_value=None):
            voice = VoiceFeedback(self.record, lambda session, alert: True)
        self.voices.append(voice)
        self.assertFalse(voice.enqueue(self.session, self.alert))
        self.assertEqual(self.kinds(), ["voice_failed"])
        self.assertEqual(self.popen.call_count, 0)

    def test_full_queue_is_rejected_and_pending_items_are_cancelled_on_close(self):
        process = FakeProcess()
        self.popen.return_value = process
        self.popen.side_effect = None
        voice = self.voice()
        self.assertTrue(voice.enqueue(self.session, self.alert))
        wait_until(lambda: voice.process is process)
        for index in range(8):
            self.assertTrue(voice.enqueue(self.session, {**self.alert, "key": f"pending-{index}"}))
        self.assertFalse(voice.enqueue(self.session, {**self.alert, "key": "overflow"}))
        self.assertEqual(self.events[-1][2]["reason"], "queue_full")
        voice.close()
        self.assertTrue(process.terminated)
        self.assertTrue(process.reaped)
        self.assertFalse(voice.thread.is_alive())
        self.assertEqual(voice.queue.unfinished_tasks, 0)
        self.assertEqual(self.popen.call_count, 1)
        shutdown = [event for event in self.events if event[2].get("reason") == "shutdown"]
        self.assertEqual(len(shutdown), 9)

    def test_close_terminates_kills_and_reaps_uncooperative_child_within_budget(self):
        process = FakeProcess(cooperative=False, delay_wait=True)
        self.popen.side_effect = None
        self.popen.return_value = process
        voice = self.voice(close_timeout_sec=1.5)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: voice.process is process)
        began = time.monotonic()
        voice.close()
        self.assertLess(time.monotonic() - began, 1.5)
        self.assertTrue(process.terminated and process.killed and process.reaped)
        self.assertFalse(voice.thread.is_alive())
        self.assertEqual(voice.queue.unfinished_tasks, 0)
        self.assertNotIn("voice_failed", self.kinds())
        self.assertEqual(self.events[-1][2]["reason"], "shutdown")

    def test_close_reaping_before_worker_poll_is_reported_as_cancellation(self):
        process = FakeProcess()
        entered, release = threading.Event(), threading.Event()
        checks = []

        def valid(session, alert):
            checks.append(True)
            if len(checks) == 3:
                entered.set()
                release.wait(2)
            return True

        self.popen.side_effect = None
        self.popen.return_value = process
        voice = self.voice(valid=valid)
        voice.enqueue(self.session, self.alert)
        self.assertTrue(entered.wait(1))
        errors = []

        def closer():
            try:
                voice.close()
            except Exception as exc:
                errors.append(exc)

        closing = threading.Thread(target=closer)
        closing.start()
        try:
            wait_until(lambda: process.reaped)
        finally:
            release.set()
            closing.join(1)
        self.assertFalse(errors)
        self.assertNotIn("voice_failed", self.kinds())
        self.assertNotIn("voice_completed", self.kinds())
        self.assertEqual(self.events[-1][2]["reason"], "shutdown")

    def test_changed_configuration_during_started_callback_does_not_spawn(self):
        enabled = [True]

        def emit(session, kind, alert, **fields):
            self.record(session, kind, alert, **fields)
            if kind == "voice_started":
                enabled[0] = False

        voice = self.voice(emit=emit, valid=lambda session, alert: enabled[0])
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: voice.queue.unfinished_tasks == 0)
        self.assertEqual(self.popen.call_count, 0)
        self.assertEqual(self.events[-1][2]["reason"], "expired_or_changed")

    def test_configuration_changed_when_child_completes_is_cancelled(self):
        checks = []

        def valid(session, alert):
            checks.append(True)
            return len(checks) < 3

        voice = self.voice(valid=valid)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: voice.queue.unfinished_tasks == 0)
        self.assertEqual(self.popen.call_count, 1)
        self.assertNotIn("voice_completed", self.kinds())
        self.assertEqual(self.events[-1][2]["reason"], "expired_or_changed")

    def test_validity_exception_after_spawn_reaps_before_failure_and_worker_recovers(self):
        process = FakeProcess()
        self.popen.side_effect = [process, FakeProcess(completed=True)]
        checks = []
        reaped_at_failure = []

        def valid(session, alert):
            checks.append(alert["key"])
            if alert["key"] == self.alert["key"] and len(checks) == 3:
                raise ValueError("fake validity failure")
            return True

        def emit(session, kind, alert, **fields):
            if kind == "voice_failed":
                reaped_at_failure.append(process.reaped)
            self.record(session, kind, alert, **fields)

        voice = self.voice(emit=emit, valid=valid)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: "voice_failed" in self.kinds())
        self.assertTrue(process.terminated and process.reaped)
        self.assertEqual(reaped_at_failure, [True])
        self.assertIsNone(voice.process)
        self.assertTrue(voice.enqueue(self.session, {**self.alert, "key": "recovered"}))
        wait_until(lambda: "voice_completed" in self.kinds())
        self.assertTrue(voice.thread.is_alive())

    def test_spawn_failure_finishes_queue_item_and_worker_remains_available(self):
        self.popen.side_effect = [OSError("fake spawn failure"), FakeProcess(completed=True)]
        voice = self.voice()
        self.assertTrue(voice.enqueue(self.session, self.alert))
        wait_until(lambda: "voice_failed" in self.kinds())
        self.assertIsNone(voice.process)
        self.assertTrue(voice.enqueue(self.session, {**self.alert, "key": "retry"}))
        wait_until(lambda: "voice_completed" in self.kinds())
        self.assertEqual(voice.queue.unfinished_tasks, 0)

    def test_queued_callback_exception_cancels_item_without_spawn_or_deadlock(self):
        def emit(session, kind, alert, **fields):
            if kind == "voice_queued":
                raise ValueError("fake queued callback failure")
            self.record(session, kind, alert, **fields)

        voice = self.voice(emit=emit)
        self.assertTrue(voice.enqueue(self.session, self.alert))
        wait_until(lambda: voice.queue.unfinished_tasks == 0)
        self.assertEqual(self.popen.call_count, 0)
        self.assertEqual(self.events[-1][2]["reason"], "event_callback_failed")
        self.assertIn("fake queued callback failure", voice.last_callback_error)

    def test_started_callback_exception_does_not_spawn_or_end_worker(self):
        def emit(session, kind, alert, **fields):
            if kind == "voice_started":
                raise ValueError("fake started callback failure")
            self.record(session, kind, alert, **fields)

        voice = self.voice(emit=emit)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: voice.queue.unfinished_tasks == 0)
        self.assertEqual(self.popen.call_count, 0)
        self.assertTrue(voice.thread.is_alive())
        self.assertEqual(self.events[-1][2]["reason"], "event_callback_failed")

    def test_close_during_unannounced_enqueue_defers_cancel_event_until_queued(self):
        entered, release = threading.Event(), threading.Event()

        def emit(session, kind, alert, **fields):
            if kind == "voice_queued":
                entered.set()
                release.wait(2)
            self.record(session, kind, alert, **fields)

        voice = self.voice(emit=emit)
        producer = threading.Thread(target=lambda: voice.enqueue(self.session, self.alert))
        producer.start()
        try:
            self.assertTrue(entered.wait(1))
            voice.close()
            self.assertFalse(voice.thread.is_alive())
            self.assertEqual(self.kinds(), [])
            self.assertEqual(self.popen.call_count, 0)
        finally:
            release.set()
            producer.join(1)
        self.assertEqual(self.kinds(), ["voice_queued", "voice_cancelled"])
        self.assertEqual(self.events[-1][2]["reason"], "shutdown")
        self.assertEqual(voice.queue.unfinished_tasks, 0)

    def test_time_limit_reaps_process_and_cancels_instruction(self):
        process = FakeProcess(cooperative=False)
        self.popen.side_effect = None
        self.popen.return_value = process
        voice = self.voice(max_duration_sec=.02)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: "voice_cancelled" in self.kinds())
        self.assertTrue(process.terminated and process.killed and process.reaped)
        self.assertEqual(self.events[-1][2]["reason"], "timeout")

    def test_cleanup_wait_or_terminate_error_falls_back_to_kill_and_reap(self):
        for error in ("wait_error", "terminate_error"):
            with self.subTest(error=error):
                process = FakeProcess(cooperative=False, **{error: True})
                self.popen.side_effect = None
                self.popen.return_value = process
                voice = self.voice(max_duration_sec=.02)
                voice.enqueue(self.session, {**self.alert, "key": error})
                wait_until(lambda: voice.queue.unfinished_tasks == 0)
                self.assertTrue(process.killed and process.reaped)
                voice.close()

    def test_close_has_bounded_join_when_external_callback_is_blocked(self):
        entered, release = threading.Event(), threading.Event()

        def valid(session, alert):
            entered.set()
            release.wait(2)
            return True

        voice = self.voice(valid=valid, close_timeout_sec=.12)
        voice.enqueue(self.session, self.alert)
        self.assertTrue(entered.wait(1))
        began = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, "종료 제한 시간"):
                voice.close()
            self.assertLess(time.monotonic() - began, .4)
        finally:
            release.set()
            voice.thread.join(1)
        voice.close()
        self.assertEqual(self.popen.call_count, 0)

    def test_unreaped_child_is_preserved_and_prevents_next_queued_process(self):
        class UnreapableProcess(FakeProcess):
            unreapable = True

            def wait(self, timeout):
                if self.unreapable:
                    raise subprocess.TimeoutExpired("fake-unreapable-say", timeout)
                return super().wait(timeout)

        process = UnreapableProcess(cooperative=False)
        entered, release = threading.Event(), threading.Event()
        checks = []

        def valid(session, alert):
            checks.append(True)
            if len(checks) == 3:
                entered.set()
                release.wait(2)
                raise ValueError("fake validity exception with unreapable child")
            return True

        self.popen.side_effect = None
        self.popen.return_value = process
        voice = self.voice(valid=valid)
        voice.enqueue(self.session, self.alert)
        self.assertTrue(entered.wait(1))
        voice.enqueue(self.session, {**self.alert, "key": "must-not-start"})
        release.set()
        wait_until(lambda: not voice.thread.is_alive())
        self.assertTrue(voice.stop.is_set())
        self.assertIs(voice.process, process)
        self.assertEqual(self.popen.call_count, 1)
        self.assertIsNotNone(self.events[-1][2]["cleanup_error"])
        try:
            with self.assertRaisesRegex(RuntimeError, "프로세스를 종료하지 못했습니다"):
                voice.close()
            self.assertEqual(voice.queue.unfinished_tasks, 0)
            self.assertIs(voice.process, process)
            self.assertFalse(voice.enqueue(self.session, self.alert))
        finally:
            process.unreapable = False
            voice.close()
        self.assertTrue(process.reaped)
        self.assertIsNone(voice.process)

    def test_close_cleanup_lock_wait_uses_remaining_deadline(self):
        process = FakeProcess()
        self.popen.side_effect = None
        self.popen.return_value = process
        voice = self.voice(close_timeout_sec=.12)
        voice.enqueue(self.session, self.alert)
        wait_until(lambda: voice.process is process)
        voice._cleanup_lock.acquire()
        began = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, "프로세스를 종료하지 못했습니다"):
                voice.close()
            self.assertLess(time.monotonic() - began, .4)
        finally:
            voice._cleanup_lock.release()
            voice.thread.join(1)
        voice.close()
        self.assertTrue(process.terminated and process.reaped)
        self.assertFalse(voice.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
