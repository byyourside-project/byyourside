"""
Regression tests for Task 01 Revision 01 (R1 - R7).
Covers worker lifetime, lossless tracking, normalization, time-axis alignment, and forced splitting.
"""
import os
import queue
import time
import unittest
import numpy as np
import scipy.io.wavfile as wavfile

from src.audio_utils import load_and_normalize_audio
from src.config import PipelineConfig, VadConfig, SttConfig, QueueConfig
from src.pipeline import SpeechPipeline, Segment
from src.metrics import compute_cer, get_memory_stats

class TestRegressionR1ToR7(unittest.TestCase):
    def setUp(self):
        self.sample_wav = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"
        os.makedirs("logs/test_fixtures", exist_ok=True)

    def test_r4_stereo_pcm_normalization(self):
        """
        R4: Verify that stereo int16 audio (e.g. 16384) is normalized to float32 0.5,
        not left unnormalized as 16384.0. Also verify int32 and float.
        """
        temp_dir = "logs/test_fixtures"
        os.makedirs(temp_dir, exist_ok=True)

        # 1. Stereo int16
        stereo_int16 = np.full((16000, 2), 16384, dtype=np.int16)
        path_s16 = os.path.join(temp_dir, "test_stereo_int16.wav")
        wavfile.write(path_s16, 16000, stereo_int16)
        samples, sr = load_and_normalize_audio(path_s16, target_sr=16000)
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(samples.ndim, 1)
        self.assertAlmostEqual(float(np.max(samples)), 0.5, places=4)
        self.assertAlmostEqual(float(np.min(samples)), 0.5, places=4)

        # 2. Mono int32 (1073741824 = half of 2^31)
        mono_int32 = np.full((16000,), 1073741824, dtype=np.int32)
        path_m32 = os.path.join(temp_dir, "test_mono_int32.wav")
        wavfile.write(path_m32, 16000, mono_int32)
        samples_m32, _ = load_and_normalize_audio(path_m32, target_sr=16000)
        self.assertAlmostEqual(float(np.max(samples_m32)), 0.5, places=4)

        # 3. Stereo float32
        stereo_float = np.full((16000, 2), 0.75, dtype=np.float32)
        path_sf = os.path.join(temp_dir, "test_stereo_float.wav")
        wavfile.write(path_sf, 16000, stereo_float)
        samples_sf, _ = load_and_normalize_audio(path_sf, target_sr=16000)
        self.assertAlmostEqual(float(np.max(samples_sf)), 0.75, places=4)

    def test_r3_flush_delay_worker_lifetime(self):
        """
        R3: Verify that even if VAD flush has delayed segment emission (0.3s delay injected),
        the STT worker does NOT exit prematurely and processes the segment cleanly.
        """
        config = PipelineConfig(
            vad=VadConfig(min_silence_duration=0.5),
            stt=SttConfig(num_threads=2)
        )
        pipeline = SpeechPipeline(config)

        # Short 1.5s audio with no trailing silence
        samples, sr = load_and_normalize_audio(self.sample_wav, target_sr=16000)
        short_audio = samples[:int(sr * 1.5)]
        temp_wav = "logs/test_fixtures/test_flush_short.wav"
        wavfile.write(temp_wav, sr, (short_audio * 32767).astype(np.int16))

        orig_flush = pipeline.vad.flush
        def delayed_flush(endpoint_override="flush"):
            time.sleep(0.3)
            return orig_flush(endpoint_override=endpoint_override)

        pipeline.vad.flush = delayed_flush

        # Run replay: STT worker must process the flushed segment and not drop it
        res = pipeline.run_replay(temp_wav, speed=2.0)
        self.assertEqual(res.status, "OK")
        self.assertEqual(res.segment_count, 1)
        self.assertTrue(res.is_lossless)

    def test_r3_worker_exception_propagation(self):
        """
        R3: Verify that an exception inside STT worker is propagated and fails the run.
        """
        config = PipelineConfig()
        pipeline = SpeechPipeline(config)

        # Corrupt STT engine to raise an exception on transcribe
        def faulty_transcribe(*args, **kwargs):
            raise ValueError("Injected STT fault for R3 test")

        pipeline.stt.transcribe = faulty_transcribe

        with self.assertRaises(RuntimeError) as ctx:
            pipeline.run_replay(self.sample_wav, speed=2.0)
        self.assertIn("Injected STT fault", str(ctx.exception))

    def test_r2_loss_tracking_under_overload(self):
        """
        R2: Verify that when queue overflows under artificial delay,
        dropped chunks/segments are tracked and lossless status fails with exact sample ranges.
        """
        config = PipelineConfig(
            queue=QueueConfig(max_audio_queue_size=2, max_segment_queue_size=1, put_timeout=0.001)
        )
        pipeline = SpeechPipeline(config)

        # Inject slowdown in VAD chunk processing to cause audio queue overflow
        orig_process = pipeline.vad.process_chunk
        def slow_process(chunk, stream_sample_idx_start=None):
            time.sleep(0.02)
            return orig_process(chunk, stream_sample_idx_start=stream_sample_idx_start)
        pipeline.vad.process_chunk = slow_process

        res = pipeline.run_replay(
            self.sample_wav,
            speed=10.0,
            artificial_stt_delay_sec=0.1
        )

        self.assertFalse(res.is_lossless, "is_lossless must be False under overload")
        self.assertEqual(res.status, "DROPPED")
        self.assertTrue(res.dropped_audio_chunks > 0 or res.dropped_segments > 0)
        self.assertGreater(len(res.dropped_items), 0)

        self.assertIn("stream_sample_start", res.dropped_items[0])
        self.assertIn("stream_sample_end", res.dropped_items[0])

    def test_r1_post_speech_delay_includes_silence_waiting(self):
        """
        R1: Verify that in 1.0x replay, post-speech delay includes the min_silence waiting time (0.5s).
        Delay after speech must be at least 500ms (0.5s) from speech end to result emit.
        """
        config = PipelineConfig(
            vad=VadConfig(min_silence_duration=0.5),
            stt=SttConfig(num_threads=4)
        )
        pipeline = SpeechPipeline(config)

        # Use sample ko.wav where speech ends around 3.7s, followed by silence until 4.6s
        res = pipeline.run_replay(self.sample_wav, speed=1.0)
        self.assertEqual(res.segment_count, 1)

        # VAD requires at least 0.5s (500ms) silence to finalize segment.
        # Therefore, delay_after_speech_ms MUST be >= 450ms!
        delay_ms = res.estimated_delay_stats["p50"]
        print(f"[R1 Check] Replay Estimated Delay After Speech: {delay_ms:.1f}ms")
        self.assertGreaterEqual(delay_ms, 450.0)

    def test_r7_hard_max_duration_forced_splitting(self):
        """
        R7: Verify that continuous speech with zero pause is split within hard_max_duration (4.0s)
        and preserves sample continuity across the boundary without discarded audio.
        """
        sr, samples = 16000, load_and_normalize_audio(self.sample_wav, 16000)[0]
        # Speech unit without pauses
        speech_unit = samples[int(0.8 * sr):int(3.7 * sr)]
        # Concatenate 3 times with ZERO pause in between -> 8.7 seconds continuous speech
        cont_audio = np.concatenate([speech_unit, speech_unit, speech_unit])

        temp_wav = "logs/test_fixtures/test_cont_hardcut.wav"
        wavfile.write(temp_wav, sr, (cont_audio * 32767).astype(np.int16))

        config = PipelineConfig(
            vad=VadConfig(
                min_silence_duration=0.5,
                max_speech_duration=4.0,
                hard_max_speech_duration=4.0
            )
        )
        pipeline = SpeechPipeline(config)
        res = pipeline.run_wav_vad(temp_wav)

        print(f"[R7 Check] Continuous Audio ({len(cont_audio)/sr:.2f}s) -> Segments: {res.segment_count}")
        total_covered_ms = 0.0
        for seg in res.segments:
            print(f"  Seg #{seg['segment_id']}: Dur: {seg['duration_ms']:.1f}ms | Reason: {seg['endpoint_reason']}")
            # Every segment MUST be <= 4100ms (within 1 window tolerance)
            self.assertLessEqual(seg["duration_ms"], 4100.0)
            total_covered_ms += seg["duration_ms"]

        # Ensure total duration of segments accounts for speech without massive discard
        expected_speech_ms = (len(cont_audio) / float(sr)) * 1000.0
        self.assertGreater(total_covered_ms, expected_speech_ms * 0.95)


    def test_memory_stats_separation(self):
        """
        R6: Verify that current RSS and peak RSS are separately reported and positive.
        """
        mem = get_memory_stats()
        self.assertIn("current_rss_mb", mem)
        self.assertIn("peak_rss_mb", mem)
        self.assertGreater(mem["current_rss_mb"], 0.0)
        self.assertGreaterEqual(mem["peak_rss_mb"], mem["current_rss_mb"])

if __name__ == "__main__":
    unittest.main()
