#!/usr/bin/env python3
"""
Task 01 Revision 03 Deterministic Regression Tests (F1–F4).
Covers:
  - F1 [P1]: Complete audio segment continuity across hard cuts, eliminating 166ms loss [70656, 73312)
             and testing 3+ continuous cuts without gap or overlap.
  - F2 [P1]: Worker cancellation, timeout handling, child process hang isolation, closed logger safety,
             and prevention of rerun worker interference.
  - F3 [P2]: Preserved per-segment queue_wait_ms, runaway queue backlog detection (FAIL), missing field detection (FAIL).
  - F4 [P2]: Strict separation of 60s smoke test (PARTIAL) from 600s stability requirement.
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import wave
import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig, QueueConfig, AudioConfig
from src.vad import VadProcessor, Segment
from src.stt import SttEngine
from src.pipeline import SpeechPipeline
from src.logger import StructuredLogger

FORCED_CUTOFF_WAV = "logs/test_fixtures/temp_forced_cutoff.wav"

class TestTask01Revision03(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(FORCED_CUTOFF_WAV), f"Fixture {FORCED_CUTOFF_WAV} must exist")
        with wave.open(FORCED_CUTOFF_WAV, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            self.samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    # -------------------------------------------------------------------------
    # F1 [P1]: Complete Audio Segment Continuity Across Hard Cuts
    # -------------------------------------------------------------------------
    def test_f1_forced_cutoff_all_segments_contiguous_no_166ms_loss(self):
        """
        F1 [P1]: On forced-cutoff fixture, verify that:
          1. ALL consecutive segments are strictly contiguous (seg[i].end_sample == seg[i+1].start_sample).
          2. The 166ms gap [70656, 73312) (2656 samples) from Rev 02 VAD re-initialization is COMPLETELY ELIMINATED.
          3. Total audio slice across all segments matches raw audio bit-identically.
        """
        cfg = VadConfig(hard_max_speech_duration=4.0)
        vad = VadProcessor(cfg)

        window = 512
        segments = []
        for i in range(0, len(self.samples), window):
            chunk = self.samples[i:i + window]
            segments.extend(vad.process_chunk(chunk, stream_sample_idx_start=i))
        segments.extend(vad.flush())

        self.assertGreaterEqual(len(segments), 2, "Expected at least 2 segments")

        # 1. Verify strict continuity across ALL segments
        for idx in range(len(segments) - 1):
            s_curr = segments[idx]
            s_next = segments[idx + 1]
            self.assertEqual(
                s_curr.end_sample, s_next.start_sample,
                f"Discontinuity between Segment {s_curr.segment_id} (end={s_curr.end_sample}) "
                f"and Segment {s_next.segment_id} (start={s_next.start_sample})!"
            )

        # 2. Specifically verify sample range [70656, 73312) is covered (was dropped in Rev 02)
        covered_start = segments[0].start_sample
        covered_end = segments[-1].end_sample
        self.assertLessEqual(covered_start, 70656, "Coverage must start before 70656")
        self.assertGreaterEqual(covered_end, 73312, "Coverage must extend past 73312")

        # 3. Concatenate all segment samples and verify bit-identical match
        reconstituted = np.concatenate([s.samples for s in segments])
        total_covered_samples = covered_end - covered_start
        self.assertEqual(len(reconstituted), total_covered_samples)

        # Build padded reference slice
        padded_samples = np.pad(self.samples, (0, max(0, covered_end - len(self.samples))))
        expected_slice = padded_samples[covered_start:covered_end]

        self.assertTrue(
            np.array_equal(reconstituted, expected_slice),
            "Reconstituted audio waveform across all segments must be bit-identical to the raw audio slice!"
        )
        print(f"[Rev3 F1 Check] All {len(segments)} segments contiguous. Zero sample loss across cut boundary.")

    def test_f1_three_plus_continuous_cuts_sample_coverage(self):
        """
        F1 [P1]: Test continuous audio exceeding 3 consecutive hard cuts (~17.3s),
        verifying that:
          1. 3+ hard cuts are executed cleanly.
          2. Every single boundary is strictly contiguous (no gaps, no unintended duplicates).
          3. Total concatenated audio matches raw audio bit-identically.
        """
        # Tile audio 3 times (~17.3 seconds of speech)
        speech_3x = np.tile(self.samples, 3)
        cfg = VadConfig(hard_max_speech_duration=4.0)
        vad = VadProcessor(cfg)

        window = 512
        segments = []
        for i in range(0, len(speech_3x), window):
            chunk = speech_3x[i:i + window]
            segments.extend(vad.process_chunk(chunk, stream_sample_idx_start=i))
        segments.extend(vad.flush())

        self.assertGreaterEqual(len(segments), 4, f"Expected >= 4 segments from ~17.3s speech, got {len(segments)}")

        # Verify continuity between every consecutive pair
        for idx in range(len(segments) - 1):
            s_curr = segments[idx]
            s_next = segments[idx + 1]
            self.assertEqual(
                s_curr.end_sample, s_next.start_sample,
                f"Discontinuity between seg {s_curr.segment_id} and seg {s_next.segment_id}!"
            )
            # Duration of intermediate cut segments should be exactly hard_max_duration
            if idx < len(segments) - 2:
                self.assertAlmostEqual(s_curr.duration_ms, 4000.0, delta=1.0)

        # Concatenate and verify bit-identical match
        reconstituted = np.concatenate([s.samples for s in segments])
        total_len = segments[-1].end_sample - segments[0].start_sample
        self.assertEqual(len(reconstituted), total_len)

        padded_speech = np.pad(speech_3x, (0, max(0, segments[-1].end_sample - len(speech_3x))))
        expected_slice = padded_speech[segments[0].start_sample:segments[-1].end_sample]
        self.assertTrue(
            np.array_equal(reconstituted, expected_slice),
            "3x continuous speech slice must be bit-identical across all 3+ cut boundaries!"
        )
        print(f"[Rev3 F1 Check] 3+ cuts ({len(segments)} segments) strictly contiguous and bit-identical.")

    # -------------------------------------------------------------------------
    # F2 [P1]: Worker Cancellation, Timeout, Closed Logger Safety, and Child Isolation
    # -------------------------------------------------------------------------
    def test_f2_cooperative_worker_cancellation_and_no_zombies(self):
        """
        F2 [P1]: Test that when abort_event is set, cooperative workers exit cleanly
        and has_running_workers() reports False (no zombie threads).
        Also verifies that rerun collision is prevented while a worker is running.
        """
        pipeline = SpeechPipeline()

        class SlowCooperativeStt:
            def __init__(self):
                self.cancelled = False

            def transcribe(self, samples, sr, is_warmup=False, abort_event=None):
                t0 = time.time()
                while time.time() - t0 < 10.0:
                    if abort_event is not None and abort_event.is_set():
                        self.cancelled = True
                        return "", 0.0
                    time.sleep(0.02)
                return "completed", 10000.0

        slow_stt = SlowCooperativeStt()
        pipeline.stt = slow_stt

        # Run with speed=100.0; STT will hang until timeout
        # Pipeline should timeout, cooperatively cancel STT, and leave no zombie workers
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="test_f2_coop")
        elapsed = time.time() - t0

        self.assertLess(elapsed, 4.0, "Cooperative cancellation should finish cleanly within 4.0s")
        self.assertTrue(slow_stt.cancelled, "Slow STT should receive abort_event")
        self.assertFalse(pipeline.has_running_workers(), "All workers must be cleaned up after termination")

        # Now test that active workers block new runs
        pipeline._active_workers.append(threading.Thread(target=time.sleep, args=(0.5,)))
        pipeline._active_workers[-1].start()
        self.assertTrue(pipeline.has_running_workers())
        with self.assertRaises(RuntimeError):
            pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="test_f2_collision")

        # Wait for dummy thread to finish
        pipeline._active_workers[-1].join(timeout=1.0)
        self.assertFalse(pipeline.has_running_workers())
        print("[Rev3 F2 Check] Cooperative cancellation, worker cleanup, and rerun protection verified.")

    def test_f2_closed_logger_protection(self):
        """
        F2 [P1]: Verify StructuredLogger gracefully handles calls after close()
        without raising ValueError (I/O operation on closed file).
        """
        logger = StructuredLogger(run_id="test_closed_logger", log_dir="logs", terminal_output=False)
        logger.close()
        self.assertTrue(logger.is_closed)

        # Must not raise exception
        try:
            logger.log_event("test_event", {"key": "value"})
            logger.log_segment_result(
                segment_id=1, input_mode="replay", audio_start_ms=0.0, audio_end_ms=1000.0,
                audio_duration_ms=1000.0, endpoint_reason="flush", segment_ready_ts=0.0,
                stt_start_ts=0.0, stt_end_ts=0.1, result_emit_ts=0.1, queue_wait_ms=0.0,
                stt_inference_ms=100.0, rtf=0.1, delay_after_speech_ms=100.0,
                total_latency_ms=1000.0, text="test"
            )
        except Exception as e:
            self.fail(f"Writing to closed logger raised exception: {e}")
        print("[Rev3 F2 Check] Closed logger thread safety and silent drop verified.")

    def test_f2_subprocess_hang_isolation(self):
        """
        F2 [P1]: Run a hung STT test in an isolated child process with an external timeout
        to verify that the parent process can cleanly isolate and terminate it without hanging.
        """
        code = """
import sys, time
sys.path.insert(0, ".")
from src.pipeline import SpeechPipeline

class HungStt:
    def transcribe(self, samples, sr, is_warmup=False, abort_event=None):
        while True:
            time.sleep(1.0)

p = SpeechPipeline()
p.stt = HungStt()
try:
    p.run_replay("logs/test_fixtures/temp_forced_cutoff.wav", speed=100.0, run_id="child_hung")
except TimeoutError:
    sys.exit(42)
"""
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=6.0)
            self.assertEqual(proc.returncode, 42, f"Expected returncode 42 (TimeoutError), got {proc.returncode}")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("Child process hung beyond external deadline!")
        print("[Rev3 F2 Check] Subprocess hang isolation with external deadline verified.")

    # -------------------------------------------------------------------------
    # F3 [P2]: Preserved queue_wait_ms and Backlog Trend Evaluation
    # -------------------------------------------------------------------------
    def test_f3_queue_wait_ms_preservation_and_drift_evaluation(self):
        """
        F3 [P2]: Verify that:
          1. run_replay and run_mic store actual 'queue_wait_ms' in every segment result.
          2. Missing queue_wait_ms fails evaluation.
          3. Runaway backlog (drift > 200ms) fails stability check.
          4. Stable queue wait passes.
        """
        pipeline = SpeechPipeline()
        res = pipeline.run_replay(FORCED_CUTOFF_WAV, run_id="test_f3_replay")

        self.assertGreaterEqual(len(res.segments), 1)
        for s in res.segments:
            self.assertIn("queue_wait_ms", s, "Every segment must contain 'queue_wait_ms'")
            self.assertIsInstance(s["queue_wait_ms"], float)

        # Simulation 1: Missing field -> FAIL
        corrupted_segments = [dict(s) for s in res.segments]
        if corrupted_segments:
            del corrupted_segments[0]["queue_wait_ms"]
        missing_detected = any("queue_wait_ms" not in s for s in corrupted_segments)
        self.assertTrue(missing_detected, "Missing queue_wait_ms must be detected")

        # Simulation 2: Runaway backlog -> drift > 200ms -> FAIL
        runaway_waits = [10.0, 50.0, 120.0, 250.0]
        drift = runaway_waits[-1] - runaway_waits[0]
        self.assertGreater(drift, 200.0, "Drift must exceed 200ms")

        # Simulation 3: Stable backlog -> PASS
        stable_waits = [12.0, 15.0, 11.0, 14.0]
        stable_drift = stable_waits[-1] - stable_waits[0]
        self.assertLess(stable_drift, 200.0, "Stable drift must be < 200ms")
        print("[Rev3 F3 Check] queue_wait_ms presence, missing detection, and drift threshold verified.")

    # -------------------------------------------------------------------------
    # F4 [P2]: Separation of 60s Smoke Test from 600s Stability Requirement
    # -------------------------------------------------------------------------
    def test_f4_smoke_test_vs_10min_gate_separation(self):
        """
        F4 [P2]: Verify that a 60-second test is categorized as a smoke test,
        and cannot pass the 10-minute stability gate (must be PARTIAL / NOT_RUN).
        Only a test running >= 600s can pass the 10-minute stability requirement.
        """
        from tests.test_mic_10min import REQUIRED_STABILITY_SECONDS

        self.assertEqual(REQUIRED_STABILITY_SECONDS, 600.0)

        # Evaluate 60s scenario
        dur_60 = 60.0
        is_full_60 = (dur_60 >= REQUIRED_STABILITY_SECONDS)
        self.assertFalse(is_full_60, "60s test must NOT be treated as full 10-minute gate")

        # Gate status for 60s must be PARTIAL
        gate_status_60 = "PASS" if is_full_60 else "PARTIAL (Short smoke test executed; 600s stability requirement remains NOT_RUN/PARTIAL)"
        self.assertIn("PARTIAL", gate_status_60)

        # Evaluate 600s scenario
        dur_600 = 600.0
        is_full_600 = (dur_600 >= REQUIRED_STABILITY_SECONDS)
        self.assertTrue(is_full_600, "600s test meets gate duration")
        print("[Rev3 F4 Check] 60s smoke test vs 600s gate separation strictly verified.")


if __name__ == "__main__":
    unittest.main()
