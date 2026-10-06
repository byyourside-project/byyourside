"""Actual model/event bridge and synthetic input cancellation; no physical mic."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from src.config import PipelineConfig
from src.pipeline import SpeechPipeline


SAMPLE = Path("models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav")


@unittest.skipUnless(SAMPLE.exists(), "local STT sample/model required")
class PresentationPipelineTests(unittest.TestCase):
    def test_actual_wav_transcription_is_published_to_consumer(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            pipeline = SpeechPipeline(PipelineConfig(log_dir=directory), event_sink=events.append, terminal_output=False)
            try:
                result = pipeline.run_wav_vad(str(SAMPLE))
                emitted = [e for e in events if e["event_type"] == "segment_result"]
                self.assertEqual(len(emitted), result.segment_count)
                self.assertIn("생각", emitted[0]["text"])
                self.assertGreater(emitted[0]["audio_end_ms"], emitted[0]["audio_start_ms"])
                self.assertEqual(events[-1]["event_type"], "run_summary")
            finally:
                pipeline.close()

    def test_stop_signal_exits_long_capture_and_drains_without_physical_mic(self):
        stop = threading.Event()
        events = []
        class SyntheticStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]
                self.blocksize = kwargs["blocksize"]
            def __enter__(self):
                self.callback(np.zeros((self.blocksize, 1), dtype=np.float32), self.blocksize, {}, None)
                stop.set()
                return self
            def __exit__(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory:
            pipeline = SpeechPipeline(PipelineConfig(log_dir=directory), event_sink=events.append, terminal_output=False)
            try:
                began = time.perf_counter()
                result = pipeline.run_mic(duration_seconds=600, stop_event=stop, stream_factory=SyntheticStream)
                self.assertLess(time.perf_counter() - began, 2)
                self.assertAlmostEqual(result.total_audio_seconds, .032, places=3)
                self.assertTrue(result.is_lossless)
                self.assertFalse(pipeline.has_running_workers())
                self.assertEqual(events[0]["event_type"], "run_start")
                self.assertEqual(events[-1]["event_type"], "run_summary")
            finally:
                pipeline.close()


if __name__ == "__main__":
    unittest.main()
