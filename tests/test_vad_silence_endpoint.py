"""Cut tails must complete during silence without waiting for another utterance."""
import unittest
import numpy as np
from src.config import VadConfig
from src.vad import VadProcessor


class Detector:
    def __init__(self, speaking=False):
        self.speaking = speaking
    def accept_waveform(self, samples):
        pass
    def is_speech_detected(self):
        return self.speaking
    def empty(self):
        return True


class SilenceEndpointTests(unittest.TestCase):
    def processor(self, speaking=False):
        vad = VadProcessor(VadConfig(hard_max_speech_duration=None))
        vad.vad = Detector(speaking)
        vad.carryover_samples = np.array([.1, .2, .3], dtype=np.float32)
        vad.carryover_start_sample = 100
        vad.awaiting_endpoint = True
        return vad

    def test_tail_released_once_after_sustained_silence_with_exact_samples(self):
        vad = self.processor()
        self.assertEqual(vad.process_chunk(np.zeros(4096, dtype=np.float32)), [])
        tail = vad.process_chunk(np.zeros(4096, dtype=np.float32))
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0].endpoint_reason, "silence")
        self.assertEqual((tail[0].start_sample, tail[0].end_sample), (100, 103))
        np.testing.assert_array_equal(tail[0].samples, np.array([.1, .2, .3], dtype=np.float32))
        self.assertEqual(vad.silence_watermark_sample, 8192)
        self.assertEqual(vad.process_chunk(np.zeros(16000, dtype=np.float32)), [])

    def test_continuous_speech_retains_tail_for_next_chunk(self):
        vad = self.processor(True)
        self.assertEqual(vad.process_chunk(np.zeros(16000, dtype=np.float32)), [])
        self.assertEqual(len(vad.carryover_samples), 3)
        self.assertEqual(vad.silence_watermark_sample, 0)
