import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src import microphone


class FakeAudio:
    """A native backend that rejects refreshes while a stream is open."""

    PortAudioError = microphone.sd.PortAudioError

    def __init__(self):
        self.default = SimpleNamespace(device=(0, 1))
        self.devices = [self.device("Selected mic"), self.device("Speaker", channels=0),
                        self.device("Other mic", rate=44100)]
        self.snapshots = []
        self.failures = []
        self.settings_error = None
        self.events = []
        self.streams = []
        self.active = set()
        self.emit_samples = None
        self.refresh_error = None

    @staticmethod
    def device(name, rate=48000, channels=1):
        return {"name": name, "max_input_channels": channels, "default_samplerate": rate}

    def _terminate(self):
        assert microphone._lock.locked(), "PortAudio refresh must hold the capture/probe lock"
        assert not self.active, "PortAudio refresh must not terminate an open stream"
        self.events.append("terminate")
        if self.refresh_error:
            raise self.refresh_error

    def _initialize(self):
        assert microphone._lock.locked()
        assert not self.active
        self.events.append("initialize")

    def query_devices(self):
        self.events.append("devices")
        if self.snapshots:
            self.devices = self.snapshots.pop(0)
        return self.devices

    def check_input_settings(self, **kwargs):
        self.events.append(("settings", kwargs))
        if self.settings_error:
            raise self.settings_error

    def InputStream(self, **kwargs):
        failure = self.failures.pop(0) if self.failures else None
        if failure and failure[0] == "construct":
            self.events.append("construct_failed")
            raise failure[1]
        backend = self

        class Stream:
            def __init__(self):
                self.closed = False
                self.close_count = 0
                self.settings = kwargs
                backend.events.append("construct")

            def start(self):
                assert microphone._lock.locked()
                backend.events.append("start")
                if failure:
                    raise failure[1]
                backend.active.add(self)
                if backend.emit_samples is not None:
                    kwargs["callback"](backend.emit_samples, len(backend.emit_samples), None, None)

            def close(self):
                assert microphone._lock.locked(), "Stream must close before releasing the lock"
                self.closed = True
                self.close_count += 1
                backend.active.discard(self)
                backend.events.append("close")

        stream = Stream()
        self.streams.append(stream)
        return stream


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        self.audio = FakeAudio()
        self.patches = [patch.object(microphone, "sd", self.audio),
                        patch.object(microphone, "_lock", threading.Lock()),
                        patch.object(microphone, "_cached_devices", [])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def stream(self, **changes):
        settings = {"device": 0, "samplerate": 48000, "channels": 1,
                    "dtype": "float32", "blocksize": 1536, "callback": lambda *args: None,
                    "expected_name": "Selected mic"}
        return microphone.open_input_stream(**{**settings, **changes})

    def test_native_44100_blocksize_has_exact_resampling_and_vad_windows(self):
        self.assertEqual(microphone.native_blocksize(44100), 7056)
        for rate in (16000, 22050, 44100, 48000, 96000):
            blocksize = microphone.native_blocksize(rate)
            self.assertEqual(blocksize * 16000 % rate, 0)
            self.assertEqual((blocksize * 16000 // rate) % 512, 0)
        device = microphone.resolve_input(2)
        self.assertEqual(device["sample_rate"], 44100)
        self.assertEqual(device["blocksize"], 7056)
        self.assertEqual(self.audio.events[-1][1]["samplerate"], 44100)
        self.assertFalse(microphone._lock.locked())

    def test_invalid_selection_is_rejected_before_native_refresh(self):
        for invalid in (-1, True, 1.0, "0"):
            with self.assertRaises(ValueError):
                microphone.resolve_input(invalid)
        self.assertEqual(self.audio.events, [])
        for missing in (1, 99):
            with self.assertRaisesRegex(microphone.MicrophoneError, "찾지 못했습니다"):
                microphone.resolve_input(missing)
        self.assertFalse(microphone._lock.locked())
        self.assertFalse(self.audio.streams)

    def test_missing_default_does_not_fallback_to_another_input(self):
        self.audio.default.device = (-1, 1)
        with self.assertRaisesRegex(microphone.MicrophoneError, "찾지 못했습니다"):
            microphone.resolve_input()
        self.assertFalse(self.audio.streams)

    def test_settings_or_refresh_failure_releases_lock(self):
        self.audio.settings_error = self.audio.PortAudioError("Unsupported rate", -9997)
        with self.assertRaisesRegex(microphone.MicrophoneError, "Unsupported rate"):
            microphone.resolve_input(0)
        self.assertFalse(microphone._lock.locked())
        self.audio.settings_error = None
        self.audio.refresh_error = RuntimeError("native refresh failed")
        self.assertIn("native refresh failed", microphone.input_devices()["error"])
        self.assertFalse(microphone._lock.locked())
        self.audio.refresh_error = None
        self.assertEqual(microphone.resolve_input(0)["id"], 0)

    def test_picker_uses_copied_cache_without_refresh_during_capture(self):
        with self.stream():
            refreshes = self.audio.events.count("terminate")
            result = microphone.input_devices()
            self.assertEqual(self.audio.events.count("terminate"), refreshes)
            self.assertEqual(result["default_device_id"], 0)
            self.assertEqual([d["id"] for d in result["devices"]], [0, 2])
            result["devices"][0]["name"] = "Changed by UI"
            self.assertEqual(microphone.input_devices()["devices"][0]["name"], "Selected mic")
            with self.assertRaisesRegex(microphone.MicrophoneError, "사용 중"):
                microphone.resolve_input(0)
            with self.assertRaisesRegex(microphone.MicrophoneError, "사용 중"):
                with self.stream():
                    self.fail("A second stream must not start")
            self.assertEqual(self.audio.events.count("terminate"), refreshes)
        self.assertEqual(self.audio.streams[0].close_count, 1)
        self.assertFalse(microphone._lock.locked())

    def test_start_failure_closes_before_refresh_and_retries_same_device_once(self):
        self.audio.failures = [("start", self.audio.PortAudioError("Internal PortAudio error", -9986)), None]
        with self.stream() as stream:
            self.assertIs(stream, self.audio.streams[1])
            self.assertTrue(self.audio.streams[0].closed)
            self.assertEqual([s.settings["device"] for s in self.audio.streams], [0, 0])
        self.assertEqual([s.close_count for s in self.audio.streams], [1, 1])
        self.assertEqual(self.audio.events.count("terminate"), 2)
        first_close = self.audio.events.index("close")
        second_refresh = self.audio.events.index("terminate", 1)
        self.assertLess(first_close, second_refresh)
        self.assertFalse(microphone._lock.locked())

    def test_repeated_native_start_failure_is_bounded_and_unlocks(self):
        self.audio.failures = [("start", self.audio.PortAudioError("Internal error", -9986))] * 2
        with self.assertRaisesRegex(microphone.MicrophoneError, "Internal error"):
            with self.stream():
                self.fail("Two failed attempts must not yield a stream")
        self.assertEqual(self.audio.events.count("start"), 2)
        self.assertEqual([s.close_count for s in self.audio.streams], [1, 1])
        self.assertFalse(microphone._lock.locked())

    def test_constructor_failure_can_retry_without_uncreated_stream_close(self):
        self.audio.failures = [("construct", self.audio.PortAudioError("Host API error", -9999)), None]
        with self.stream():
            self.assertEqual(len(self.audio.streams), 1)
        self.assertEqual(self.audio.events.count("terminate"), 2)
        self.assertEqual(self.audio.streams[0].close_count, 1)
        self.assertFalse(microphone._lock.locked())

    def test_nontransient_start_failure_is_not_retried(self):
        self.audio.failures = [("start", self.audio.PortAudioError("Invalid sample rate", -9997))]
        with self.assertRaisesRegex(microphone.MicrophoneError, "Invalid sample rate"):
            with self.stream():
                self.fail("Unsupported input settings must not yield a stream")
        self.assertEqual(self.audio.events.count("terminate"), 1)
        self.assertEqual(self.audio.streams[0].close_count, 1)
        self.assertFalse(microphone._lock.locked())

    def test_recording_error_is_not_a_startup_retry(self):
        with self.assertRaises(self.audio.PortAudioError):
            with self.stream():
                raise self.audio.PortAudioError("Device disappeared during capture", -9986)
        self.assertEqual(self.audio.events.count("terminate"), 1)
        self.assertEqual(self.audio.streams[0].close_count, 1)
        self.assertFalse(microphone._lock.locked())

    def test_reallocated_selected_id_is_rejected_before_open(self):
        self.audio.devices[0] = self.audio.device("Replacement mic")
        with self.assertRaisesRegex(microphone.MicrophoneError, "바뀌었습니다"):
            with self.stream():
                self.fail("Selected name must remain stable")
        self.assertFalse(self.audio.streams)
        self.assertFalse(microphone._lock.locked())

    def test_selected_id_disappearing_during_retry_does_not_fallback(self):
        first = list(self.audio.devices)
        disappeared = [self.audio.device("Speaker", channels=0), first[1], first[2]]
        self.audio.snapshots = [first, disappeared]
        self.audio.failures = [("start", self.audio.PortAudioError("Internal error", -9986))]
        with self.assertRaisesRegex(microphone.MicrophoneError, "찾지 못했습니다"):
            with self.stream():
                self.fail("Disappeared selection must not switch to the other mic")
        self.assertEqual(len(self.audio.streams), 1)
        self.assertTrue(self.audio.streams[0].closed)
        self.assertFalse(microphone._lock.locked())

    def test_reallocated_selected_id_during_retry_does_not_open_replacement(self):
        first = list(self.audio.devices)
        replaced = [self.audio.device("Replacement mic"), first[1], first[2]]
        self.audio.snapshots = [first, replaced]
        self.audio.failures = [("start", self.audio.PortAudioError("Internal error", -9986))]
        with self.assertRaisesRegex(microphone.MicrophoneError, "바뀌었습니다"):
            with self.stream():
                self.fail("Retry must keep the original selection")
        self.assertEqual(len(self.audio.streams), 1)
        self.assertTrue(self.audio.streams[0].closed)
        self.assertFalse(microphone._lock.locked())

    def test_opened_input_without_frames_is_not_reported_as_working(self):
        with patch.object(microphone.time, "sleep"):
            with self.assertRaisesRegex(microphone.MicrophoneError, "입력 프레임을 받지 못했습니다"):
                microphone.test_input(0)
        self.assertEqual(self.audio.streams[0].close_count, 1)
        self.assertFalse(microphone._lock.locked())

    def test_input_probe_reports_actual_callback_peak_and_silence_separately(self):
        for samples, expected in ((np.full((16, 1), .5), -6.0), (np.zeros((16, 1)), -120.0)):
            self.audio.emit_samples = samples
            with patch.object(microphone.time, "sleep"):
                result = microphone.test_input(0)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["callbacks"], 1)
            self.assertEqual(result["peak_dbfs"], expected)
            self.assertTrue(self.audio.streams[-1].closed)
        self.assertFalse(microphone._lock.locked())

    def test_probe_rejects_device_reallocated_between_resolve_and_capture(self):
        first = list(self.audio.devices)
        replaced = [self.audio.device("Replacement mic"), first[1], first[2]]
        self.audio.snapshots = [first, replaced]
        self.audio.emit_samples = np.ones((16, 1))
        with patch.object(microphone.time, "sleep"):
            with self.assertRaisesRegex(microphone.MicrophoneError, "바뀌었습니다"):
                microphone.test_input(0)
        self.assertFalse(self.audio.streams)
        self.assertFalse(microphone._lock.locked())


if __name__ == "__main__":
    unittest.main()
