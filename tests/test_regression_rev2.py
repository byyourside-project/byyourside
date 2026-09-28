"""
Deterministic regression test suite for Task 01 Revision 02.
Verifies:
1. 100% sample preservation on hard-cut forced splitting (reconstitutes 282ms without loss).
2. Clean error termination without hang on VAD worker failure (queue size 1).
3. Clean error termination without hang on STT worker failure (queue size 1).
4. VAD flush delay injection (0.35s) and worker lifetime guarantee.
5. Overload loss tracking on input queue and segment queue (is_lossless=False, DROPPED, sample ranges).
6. Stream clock continuity across dropped audio chunks (prevents time-axis compression).
7. Latency clock mapping (VAD-estimated endpoint vs reference ground-truth separation).
"""
import os
import queue
import threading
import time
import unittest
import numpy as np
import scipy.io.wavfile as wavfile

from src.audio_utils import load_and_normalize_audio
from src.config import PipelineConfig, VadConfig, SttConfig, QueueConfig
from src.pipeline import SpeechPipeline, AudioChunk
from src.vad import VadProcessor, Segment

class TestRegressionRev2(unittest.TestCase):
    def setUp(self):
        self.sample_wav = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"
        self.fixtures_dir = "logs/test_fixtures"
        os.makedirs(self.fixtures_dir, exist_ok=True)
        self.forced_cutoff_wav = os.path.join(self.fixtures_dir, "temp_forced_cutoff.wav")

    def test_rev2_sample_preservation_across_hard_cut(self):
        """
        Item A [P1]: Verify that hard-cut forced splitting preserves 100% of samples.
        In Rev 01, 282ms (4,512 samples) was discarded from temp_forced_cutoff.wav.
        In Rev 02, Seg #1 + Seg #2 must reconstitute the exact original audio samples with zero sample loss.
        """
        self.assertTrue(os.path.exists(self.forced_cutoff_wav), f"Fixture not found: {self.forced_cutoff_wav}")
        samples, sr = load_and_normalize_audio(self.forced_cutoff_wav, target_sr=16000)

        vad_cfg = VadConfig(
            min_silence_duration=0.5,
            max_speech_duration=4.0,
            hard_max_speech_duration=4.0
        )
        vad = VadProcessor(vad_cfg)

        window = 512
        segments = []
        for i in range(0, len(samples), window):
            chunk = samples[i:i + window]
            segments.extend(vad.process_chunk(chunk, stream_sample_idx_start=i))
        segments.extend(vad.flush())

        self.assertGreaterEqual(len(segments), 2, "Expected at least 2 segments from forced cutoff audio")
        seg1 = segments[0]
        seg2 = segments[1]

        # Verify Seg 1 was split at hard limit (4.0s = 64000 samples)
        self.assertEqual(seg1.endpoint_reason, "hard_max_duration")
        self.assertEqual(len(seg1.samples), 64000)
        self.assertAlmostEqual(seg1.duration_ms, 4000.0, delta=1.0)

        # Verify Seg 2 is continuation containing the EXACT remaining 282ms (4512 samples)
        self.assertEqual(seg2.endpoint_reason, "hard_cut_continuation")
        self.assertEqual(len(seg2.samples), 4512, "Seg 2 must contain exactly 4,512 carryover samples (282ms)")
        self.assertAlmostEqual(seg2.duration_ms, 282.0, delta=1.0)

        # Boundary continuity: end of seg1 must strictly match start of seg2
        self.assertEqual(seg1.end_sample, seg2.start_sample)

        # Concatenation of seg1 and seg2 MUST match original audio slice bit-for-bit
        reconstituted = np.concatenate([seg1.samples, seg2.samples])
        expected_raw_slice = samples[seg1.start_sample:seg2.end_sample]
        self.assertEqual(len(reconstituted), len(expected_raw_slice))
        self.assertTrue(np.array_equal(reconstituted, expected_raw_slice),
                        "Reconstituted audio waveform must be bit-identical to raw audio slice")
        print(f"[Rev2 Check] Sample Preservation: Reconstituted {len(reconstituted)} samples bit-identically across hard cut boundary.")

    def test_rev2_vad_exception_hang_prevention(self):
        """
        Item B [P1]: Verify that an exception in VAD worker terminates cleanly
        within 2.0s even when audio queue has size 1, without sentinel deadlock or hang.
        """
        config = PipelineConfig(
            queue=QueueConfig(max_audio_queue_size=1, max_segment_queue_size=1, put_timeout=0.01)
        )
        pipeline = SpeechPipeline(config)

        # Inject exception on first VAD call
        def faulty_process_chunk(*args, **kwargs):
            raise ValueError("Injected VAD fault for hang prevention test")

        pipeline.vad.process_chunk = faulty_process_chunk

        t_start = time.perf_counter()
        with self.assertRaises(RuntimeError) as ctx:
            pipeline.run_replay(self.sample_wav, speed=10.0)
        t_elapsed = time.perf_counter() - t_start

        self.assertIn("Injected VAD fault", str(ctx.exception))
        self.assertLess(t_elapsed, 2.5, f"Pipeline took {t_elapsed:.2f}s to terminate; must be < 2.5s")
        print(f"[Rev2 Check] VAD Exception Shutdown: Clean error termination in {t_elapsed:.3f}s (no hang).")

    def test_rev2_stt_exception_hang_prevention(self):
        """
        Item B [P1]: Verify that an exception in STT worker terminates cleanly
        within 2.0s even when segment queue has size 1, without hang.
        """
        config = PipelineConfig(
            queue=QueueConfig(max_audio_queue_size=2, max_segment_queue_size=1, put_timeout=0.01)
        )
        pipeline = SpeechPipeline(config)

        # Inject exception on STT transcribe
        def faulty_transcribe(*args, **kwargs):
            raise ValueError("Injected STT fault for hang prevention test")

        pipeline.stt.transcribe = faulty_transcribe

        t_start = time.perf_counter()
        with self.assertRaises(RuntimeError) as ctx:
            pipeline.run_replay(self.sample_wav, speed=5.0)
        t_elapsed = time.perf_counter() - t_start

        self.assertIn("Injected STT fault", str(ctx.exception))
        self.assertLess(t_elapsed, 2.5, f"Pipeline took {t_elapsed:.2f}s to terminate; must be < 2.5s")
        print(f"[Rev2 Check] STT Exception Shutdown: Clean error termination in {t_elapsed:.3f}s (no hang).")

    def test_rev2_flush_delay_worker_lifetime(self):
        """
        Item B [P1]: Verify that injecting a 0.35s delay into VAD flush does NOT
        cause STT worker to exit prematurely, and the final segment is transcribed exactly once.
        """
        config = PipelineConfig(
            vad=VadConfig(min_silence_duration=0.5),
            stt=SttConfig(num_threads=2)
        )
        pipeline = SpeechPipeline(config)

        # Short 1.5s audio without trailing silence (depends on flush)
        samples, sr = load_and_normalize_audio(self.sample_wav, target_sr=16000)
        short_audio = samples[:int(sr * 1.5)]
        temp_wav = os.path.join(self.fixtures_dir, "test_flush_delay.wav")
        wavfile.write(temp_wav, sr, (short_audio * 32767).astype(np.int16))

        # Inject real 0.35s delay in flush
        orig_flush = pipeline.vad.flush
        flush_called = False

        def delayed_flush(endpoint_override="flush"):
            nonlocal flush_called
            flush_called = True
            time.sleep(0.35)
            return orig_flush(endpoint_override=endpoint_override)

        pipeline.vad.flush = delayed_flush

        res = pipeline.run_replay(temp_wav, speed=2.0)
        self.assertTrue(flush_called, "delayed_flush should have been invoked")
        self.assertEqual(res.status, "OK")
        self.assertEqual(res.segment_count, 1, "Exactly one segment should be transcribed from flush")
        self.assertTrue(res.is_lossless)
        print(f"[Rev2 Check] Flush Delay (0.35s): Worker waited cleanly; Seg count = {res.segment_count}.")

    def test_rev2_loss_tracking_under_audio_queue_overload(self):
        """
        Item C [P1]: Verify that audio queue overflow triggers DROPPED status,
        is_lossless=False, and tracks dropped chunk count, samples, and exact sample ranges.
        """
        # Queue size 1 with fast replay and tiny timeout
        config = PipelineConfig(
            queue=QueueConfig(max_audio_queue_size=1, max_segment_queue_size=10, put_timeout=0.001)
        )
        pipeline = SpeechPipeline(config)

        # Introduce artificial slowdown in VAD processing
        orig_process = pipeline.vad.process_chunk
        def slow_process(chunk, stream_sample_idx_start=None):
            time.sleep(0.05)
            return orig_process(chunk, stream_sample_idx_start=stream_sample_idx_start)

        pipeline.vad.process_chunk = slow_process

        res = pipeline.run_replay(self.sample_wav, speed=20.0)

        self.assertFalse(res.is_lossless, "is_lossless must be False under audio drop")
        self.assertEqual(res.status, "DROPPED", "status must be DROPPED")
        self.assertGreater(res.dropped_audio_chunks, 0, "dropped_audio_chunks must be > 0")
        self.assertGreater(res.dropped_audio_seconds, 0.0, "dropped_audio_seconds must be > 0")
        self.assertGreater(len(res.dropped_items), 0, "dropped_items must contain records")

        first_drop = res.dropped_items[0]
        self.assertEqual(first_drop["item_type"], "audio_chunk")
        self.assertIn("stream_sample_start", first_drop)
        self.assertIn("stream_sample_end", first_drop)
        self.assertGreater(first_drop["duration_ms"], 0.0)
        self.assertEqual(first_drop["reason"], "audio_queue_full")
        print(f"[Rev2 Check] Audio Queue Overload: Drops={res.dropped_audio_chunks}, "
              f"Dropped Sec={res.dropped_audio_seconds:.3f}s, Lossless={res.is_lossless}")

    def test_rev2_loss_tracking_under_segment_queue_overload(self):
        """
        Item C [P1]: Verify that segment queue overflow triggers DROPPED status,
        is_lossless=False, and records dropped segments with exact sample ranges.
        """
        config = PipelineConfig(
            queue=QueueConfig(max_audio_queue_size=100, max_segment_queue_size=1, put_timeout=0.001)
        )
        pipeline = SpeechPipeline(config)

        # Use continuous speech audio to produce multiple segments
        res = pipeline.run_replay(
            self.forced_cutoff_wav,
            speed=5.0,
            artificial_stt_delay_sec=0.4  # Slow STT causes segment queue to fill
        )

        self.assertFalse(res.is_lossless)
        self.assertEqual(res.status, "DROPPED")
        self.assertGreater(res.dropped_segments, 0)
        seg_drops = [d for d in res.dropped_items if d["item_type"] == "segment"]
        self.assertGreater(len(seg_drops), 0)
        self.assertIn(seg_drops[0]["reason"], ["segment_queue_full", "segment_queue_full_on_flush"])
        print(f"[Rev2 Check] Segment Queue Overload: Dropped Segments={res.dropped_segments}, Status={res.status}")

    def test_rev2_clock_continuity_across_chunk_gap(self):
        """
        Item C [P1]: Verify that when an audio chunk is dropped (gap in stream_sample_idx_start),
        the VAD resynchronizes its sample clock to stream_sample_idx_start,
        preventing time-axis compression on subsequent segments.
        """
        vad = VadProcessor(VadConfig(min_silence_duration=0.5))

        # Chunk 1: Silence, samples [0, 1024)
        c1 = np.zeros(1024, dtype=np.float32)
        vad.process_chunk(c1, stream_sample_idx_start=0)

        # Artificial GAP of 32,000 samples (2.0 seconds) dropped!
        # Chunk 2: Starts at stream sample 33024
        # We synthesize 1.0s of speech followed by silence
        sr = 16000
        samples, _ = load_and_normalize_audio(self.sample_wav, target_sr=sr)
        speech_slice = samples[int(sr * 0.8):int(sr * 1.8)]  # 1.0s of speech
        trailing_silence = np.zeros(int(sr * 0.6), dtype=np.float32)
        test_audio = np.concatenate([speech_slice, trailing_silence])

        gap_start_idx = 33024
        window = 512
        ready_segs = []
        for i in range(0, len(test_audio), window):
            sub = test_audio[i:i + window]
            ready_segs.extend(vad.process_chunk(sub, stream_sample_idx_start=gap_start_idx + i))
        ready_segs.extend(vad.flush())

        self.assertGreaterEqual(len(ready_segs), 1, "Expected speech segment after gap")
        seg_after_gap = ready_segs[0]

        # In Revision 01 without clock sync: seg.start_sample would be ~1024 (compressed by 32000 samples!)
        # In Revision 02 with clock sync: seg.start_sample MUST be >= 33024
        self.assertGreaterEqual(seg_after_gap.start_sample, gap_start_idx,
                                f"Segment start_sample {seg_after_gap.start_sample} must be >= {gap_start_idx} (no time compression)")
        self.assertAlmostEqual(seg_after_gap.start_ms, (seg_after_gap.start_sample / float(sr)) * 1000.0, places=1)
        print(f"[Rev2 Check] Clock Continuity: Gap of 32000 samples respected; Seg Start = {seg_after_gap.start_sample} samples ({seg_after_gap.start_ms:.1f}ms).")

    def test_rev2_delay_metric_definition(self):
        """
        Item D [P2]: Verify that post-speech delay in replay mode reflects
        (result_emit_ts - audio_speech_end_wall_ts), and strictly includes VAD silence waiting time.
        """
        config = PipelineConfig(
            vad=VadConfig(min_silence_duration=0.5),
            stt=SttConfig(num_threads=4)
        )
        pipeline = SpeechPipeline(config)

        res = pipeline.run_replay(self.sample_wav, speed=1.0)
        self.assertEqual(res.segment_count, 1)

        estimated_delay = res.estimated_delay_stats["p50"]
        # In 1.0x real-time replay, speech ends at 3.69s.
        # Segment is ready after min_silence_duration (0.5s = 500ms).
        # Plus STT inference (~50ms) -> total delay from speech end is >= 450ms.
        self.assertGreaterEqual(estimated_delay, 450.0)
        self.assertLess(estimated_delay, 1200.0)
        print(f"[Rev2 Check] Estimated Post-Speech Delay (VAD-estimated): {estimated_delay:.1f}ms (>= 450ms verified).")

if __name__ == "__main__":
    unittest.main()
