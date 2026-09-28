#!/usr/bin/env python3
"""
Regression test suite for Task 01 Revision 06.
Focuses strictly on:
  1. Single consistent STT request deadline (F1):
     - Parent request override correctly forwarded to child worker via IPC.
     - Resolves conflict where child config had shorter default (e.g. 0.001s) causing premature failure.
     - Shorter override causes TimeoutError and reaps child process.
     - WAV direct/batch automatic budget calculation and recording.
     - Warm-up override budget forwarding, TimeoutError, and distinction between dummy infer_ms vs total reinit time.
     - Removal of broad TypeError catching around transcribe().
  2. Exact evidence matching and monotonic timing breakdown (F2):
     - Real monotonic timestamps for stream start, segment ready, STT request, timeout detection, child reap, and failure caught.
     - Non-inference roundtrip overhead (IPC + serialization + OS scheduling).
     - Distinct parent RSS and child process RSS reporting.
"""

import os
import shutil
import sys
import time
import unittest
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.audio_utils import load_and_normalize_audio
from src.config import PipelineConfig, SttConfig, VadConfig
from src.pipeline import SpeechPipeline
from src.stt import IsolatedSttEngine, SttEngine
from src.metrics import get_memory_stats

REPRO_FIXTURE_PATH = "logs/test_fixtures/repro_ko_plus_4s_silence.wav"
OFFICIAL_KO_WAV = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"


class TestTask01Revision06(unittest.TestCase):
    def setUp(self):
        self.config = PipelineConfig(
            vad=VadConfig(
                min_silence_duration=0.5,
                max_speech_duration=4.0,
                hard_max_speech_duration=4.0
            ),
            stt=SttConfig(
                request_timeout_sec=2.0,
                warm_up_timeout_sec=10.0,
                num_threads=4
            ),
            worker_join_timeout_sec=2.0
        )
        self.pipeline = None

    def tearDown(self):
        if self.pipeline is not None:
            self.pipeline.close()
            self.pipeline = None

    def test_rev6_f1_budget_override_longer_than_short_default_succeeds(self):
        """
        Revision 06 F1 Core Test:
        SttConfig has request_timeout_sec=0.001 (1ms).
        Parent calls transcribe(..., timeout=5.0).
        Verifies:
          - Child process does NOT fail on its default 0.001s.
          - Child correctly applies the 5.0s override.
          - Normal transcription succeeds in ~50ms (< 5.0s).
          - Child process remains alive.
        """
        samples, sr = load_and_normalize_audio(OFFICIAL_KO_WAV, target_sr=16000)
        engine = IsolatedSttEngine(SttConfig(request_timeout_sec=0.001))
        try:
            t0 = time.perf_counter()
            text, infer_ms = engine.transcribe(samples, sr, timeout=5.0)
            elapsed = time.perf_counter() - t0

            self.assertTrue(engine.is_child_alive(), "Child process should remain alive on success")
            self.assertIn("생각", text)
            self.assertLess(elapsed, 5.0, f"Inference took {elapsed:.3f}s which is within 5.0s budget")
            print(f"\n[Rev6 F1 Check] Short default (0.001s) with 5.0s override SUCCEEDED: '{text}' ({infer_ms:.1f}ms, elapsed: {elapsed:.3f}s)")
        finally:
            engine.close()

    def test_rev6_f1_budget_override_shorter_than_long_default_fails_and_reaps(self):
        """
        Revision 06 F1 Reverse Test:
        SttConfig has request_timeout_sec=5.0.
        Parent calls transcribe(..., timeout=0.001) (1ms override).
        Verifies:
          - Explicit TimeoutError is raised (NOT RuntimeError).
          - Child process is reaped (is_child_alive() == False).
          - Same parent can rerun with timeout=5.0 and succeed.
        """
        samples, sr = load_and_normalize_audio(OFFICIAL_KO_WAV, target_sr=16000)
        engine = IsolatedSttEngine(SttConfig(request_timeout_sec=5.0))
        try:
            with self.assertRaises(TimeoutError) as ctx:
                engine.transcribe(samples, sr, timeout=0.001)

            self.assertIn("timed out after 0.00s deadline", str(ctx.exception))
            self.assertFalse(engine.is_child_alive(), "Child process must be reaped after TimeoutError")

            # Same parent process rerun with sufficient budget
            text, infer_ms = engine.transcribe(samples, sr, timeout=5.0)
            self.assertTrue(engine.is_child_alive(), "New child process should be alive after rerun")
            self.assertIn("생각", text)
            print(f"[Rev6 F1 Check] 0.001s override failed as TimeoutError, reaped child, and same-parent rerun succeeded: '{text}'")
        finally:
            engine.close()

    def test_rev6_f1_wav_direct_and_batch_budget_policy_and_recording(self):
        """
        Revision 06 F1 Test:
        Verifies that run_wav_direct_stt and run_wav_vad:
          1. Calculate budget: max(req_timeout, base + dur * rate).
          2. Explicitly record request_timeout_sec on segment results.
          3. Respect caller's explicit request_timeout override.
        """
        self.pipeline = SpeechPipeline(self.config)

        # 1. run_wav_direct_stt with auto budget
        res_direct = self.pipeline.run_wav_direct_stt(OFFICIAL_KO_WAV, run_id="rev6_wav_direct_auto")
        dur = res_direct.total_audio_seconds
        expected_auto_budget = max(
            self.config.stt.request_timeout_sec,
            self.config.stt.batch_timeout_base_sec + dur * self.config.stt.batch_timeout_per_second
        )
        self.assertAlmostEqual(res_direct.segments[0]["request_timeout_sec"], expected_auto_budget, places=2)

        # 2. run_wav_direct_stt with explicit override (3.5s)
        res_direct_override = self.pipeline.run_wav_direct_stt(
            OFFICIAL_KO_WAV, run_id="rev6_wav_direct_override", request_timeout=3.5
        )
        self.assertAlmostEqual(res_direct_override.segments[0]["request_timeout_sec"], 3.5, places=2)

        # 3. run_wav_vad with auto budget
        res_vad = self.pipeline.run_wav_vad(OFFICIAL_KO_WAV, run_id="rev6_wav_vad_auto")
        for seg in res_vad.segments:
            seg_dur = seg["duration_ms"] / 1000.0
            exp_seg_budget = max(
                self.config.stt.request_timeout_sec,
                self.config.stt.batch_timeout_base_sec + seg_dur * self.config.stt.batch_timeout_per_second
            )
            self.assertAlmostEqual(seg["request_timeout_sec"], exp_seg_budget, places=2)

        print(f"[Rev6 F1 Check] WAV direct auto budget: {expected_auto_budget:.2f}s | override: 3.50s | VAD seg budget: {res_vad.segments[0]['request_timeout_sec']:.2f}s")

    def test_rev6_f1_warm_up_override_budget_and_distinct_timings(self):
        """
        Revision 06 F1 Test:
        Verifies:
          1. warm_up(timeout=0.001) raises TimeoutError and reaps child process.
          2. Subsequent warm_up(timeout=10.0) cleanly restarts within same parent.
          3. Distinguishes dummy audio infer_ms (~18ms) from total respawn/initialization wall time (~800ms).
        """
        engine = IsolatedSttEngine(SttConfig(warm_up_timeout_sec=10.0))
        try:
            # 1. Short override -> TimeoutError & child reaped
            with self.assertRaises(TimeoutError) as ctx:
                engine.warm_up(duration_seconds=1.0, timeout=0.001)
            self.assertFalse(engine.is_child_alive(), "Child process must be reaped on warm_up timeout")

            # 2. Valid recovery -> measure total respawn time vs dummy infer time
            t_reinit_start = time.perf_counter()
            dummy_infer_ms = engine.warm_up(duration_seconds=1.0, timeout=10.0)
            total_reinit_sec = time.perf_counter() - t_reinit_start

            self.assertTrue(engine.is_child_alive(), "Child process must be alive after recovery warm_up")
            self.assertGreater(dummy_infer_ms, 5.0)
            self.assertLess(dummy_infer_ms, 150.0)
            self.assertGreater(total_reinit_sec, dummy_infer_ms / 1000.0,
                               "Total reinitialization time must include process spawn and model load")

            print(f"\n[Rev6 Warm-up Timings] Dummy Audio Infer Time: {dummy_infer_ms:.2f} ms | "
                  f"Total Respawn + Load + Warmup Wall Time: {total_reinit_sec * 1000.0:.2f} ms")
        finally:
            engine.close()

    def test_rev6_f2_real_monotonic_timing_breakdown(self):
        """
        Revision 06 F2 Core Test:
        Verifies timing breakdown using REAL monotonic clock timestamps on parent process:
          - t_stream_start: Monotonic timestamp of streaming onset
          - t_seg_ready: Monotonic timestamp when VAD completes audio collection & endpoint
          - t_stt_req: Monotonic timestamp when stt_worker pops segment
          - t_req_issued: Monotonic timestamp when IPC request sent to child pipe
          - t_timeout_detected: Monotonic timestamp when parent detects deadline expiry
          - t_reap_completed: Monotonic timestamp when SIGTERM/SIGKILL + join finished
          - t_fail_caught: Monotonic timestamp when caller catches TimeoutError
        Verifies:
          - Strict monotonic order:
            t_stream_start < t_seg_ready <= t_stt_req <= t_req_issued < t_timeout_detected <= t_reap_completed <= t_fail_caught
          - Total elapsed time is less than total audio duration (8.614s).
          - No constants or hardcoded subtraction residuals used.
        """
        self.pipeline = SpeechPipeline(self.config)
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t_caller_start = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(
                REPRO_FIXTURE_PATH,
                speed=1.0,
                run_id="rev6_real_monotonic_timing",
                request_timeout=2.0
            )
        t_caller_end = time.perf_counter()

        timing = self.pipeline.last_run_timing
        child_timing = timing.get("child_timing", {})

        t_stream_start = timing["stream_start_ts"]
        t_seg_ready = timing["first_segment_ready_ts"]
        t_stt_req = timing["first_stt_request_ts"]
        t_fail_caught = timing["failure_caught_ts"]

        t_req_issued = child_timing["t_req_issued"]
        t_timeout_detected = child_timing["t_timeout_detected"]
        t_reap_completed = child_timing["t_reap_completed"]

        # Verify strict monotonic sequence
        self.assertLess(t_stream_start, t_seg_ready)
        self.assertLessEqual(t_seg_ready, t_stt_req)
        self.assertLessEqual(t_stt_req, t_req_issued)
        self.assertLess(t_req_issued, t_timeout_detected)
        self.assertLessEqual(t_timeout_detected, t_reap_completed)
        self.assertLessEqual(t_reap_completed, t_fail_caught)

        # Real measured durations
        measured_audio_collection_sec = t_seg_ready - t_stream_start
        measured_request_to_timeout_sec = t_timeout_detected - t_req_issued
        measured_child_reap_sec = t_reap_completed - t_timeout_detected
        measured_total_elapsed_sec = t_fail_caught - t_stream_start

        # Audio collection should be ~4.13s (speech ends around 3.7s + 0.5s silence)
        self.assertGreater(measured_audio_collection_sec, 3.8)
        self.assertLess(measured_audio_collection_sec, 4.6)

        # Request to timeout should be ~2.00s (2.0s deadline)
        self.assertGreater(measured_request_to_timeout_sec, 1.95)
        self.assertLess(measured_request_to_timeout_sec, 2.20)

        # Cleanup should be under 0.5s
        self.assertLess(measured_child_reap_sec, 0.5)

        # Total elapsed should be well before EOF (8.614s)
        self.assertGreater(measured_total_elapsed_sec, 5.8)
        self.assertLess(measured_total_elapsed_sec, 7.5)

        print("\n[Rev6 Real Monotonic Timing Breakdown (All Measured from time.perf_counter)]")
        print(f"  t_stream_start:      {t_stream_start:.6f}")
        print(f"  t_seg_ready:         {t_seg_ready:.6f}  (+{t_seg_ready - t_stream_start:.3f}s: Audio Collection)")
        print(f"  t_req_issued:        {t_req_issued:.6f}  (+{t_req_issued - t_seg_ready:.3f}s: Queue dispatch)")
        print(f"  t_timeout_detected:  {t_timeout_detected:.6f}  (+{measured_request_to_timeout_sec:.3f}s: STT Deadline Expiry)")
        print(f"  t_reap_completed:    {t_reap_completed:.6f}  (+{measured_child_reap_sec:.3f}s: Child Signal & Join)")
        print(f"  t_fail_caught:       {t_fail_caught:.6f}  (+{t_fail_caught - t_reap_completed:.3f}s: Caller Return)")
        print(f"  Total Failure Time:  {measured_total_elapsed_sec:.3f}s  (BEFORE 8.614s Audio EOF)")

    def test_rev6_stt_call_adapter_no_broad_typeerror_swallowing(self):
        """
        Revision 06 F1 Test:
        Verifies that _call_stt_transcribe uses inspect.signature without catching TypeError.
        If an internal TypeError occurs, it is NOT swallowed and timeout is not silently stripped.
        """
        class BuggySttEngine:
            def transcribe(self, samples, sr, abort_event=None, timeout=None):
                raise TypeError("Internal bug: cannot unpack non-iterable object")

        pipeline = SpeechPipeline(self.config, stt=BuggySttEngine())
        dummy_audio = np.zeros(16000, dtype=np.float32)

        # Calling _call_stt_transcribe should directly raise TypeError without swallowing
        with self.assertRaises(TypeError) as ctx:
            pipeline._call_stt_transcribe(dummy_audio, 16000, timeout=2.0)
        self.assertIn("Internal bug", str(ctx.exception))
        print(f"[Rev6 F1 Check] _call_stt_transcribe preserved internal TypeError: {ctx.exception}")

    def test_rev6_non_inference_overhead_and_memory_separation(self):
        """
        Revision 06 F2 Test:
        Measures:
          - Child inference time (SenseVoice forward pass)
          - Parent roundtrip time (request send to result recv)
          - Non-inference roundtrip overhead (IPC serialization, pipe I/O, OS scheduling)
          - Separate Parent RSS vs Child process RSS.
        """
        samples, sr = load_and_normalize_audio(OFFICIAL_KO_WAV, target_sr=16000)
        engine = IsolatedSttEngine(self.config.stt)
        try:
            text, infer_ms = engine.transcribe(samples, sr, timeout=5.0)
            roundtrip_ms = engine.last_roundtrip_ms
            non_infer_overhead_ms = engine.last_ipc_overhead_ms
            child_pid = engine.child_pid

            mem = get_memory_stats(child_pid)
            parent_rss = mem["parent_rss_mb"]
            child_rss = mem["child_rss_mb"]
            combined_rss = mem["combined_rss_mb"]

            self.assertGreater(infer_ms, 10.0)
            self.assertGreater(roundtrip_ms, infer_ms)
            self.assertGreaterEqual(non_infer_overhead_ms, 0.0)
            self.assertGreater(child_rss, 500.0, "SenseVoice model should reside in child process (>500MB)")
            self.assertGreater(parent_rss, 50.0)

            print(f"\n[Rev6 Precision Measurements]")
            print(f"  Child Inference Time:         {infer_ms:.2f} ms")
            print(f"  Parent Roundtrip Time:        {roundtrip_ms:.2f} ms")
            print(f"  Non-Inference Overhead:       {non_infer_overhead_ms:.2f} ms (IPC pipe, serialization, OS scheduling)")
            print(f"  Parent Process RSS:           {parent_rss:.2f} MB")
            print(f"  Child Process RSS:            {child_rss:.2f} MB (SenseVoice ONNX model)")
            print(f"  Combined RSS:                 {combined_rss:.2f} MB (contains potential shared pages)")
        finally:
            engine.close()

    def test_rev6_eof_prevention_and_pid_preservation_regression(self):
        """
        Revision 06 Core Regression:
        Verifies that:
          1. 1x replay with 3.0s delay and 2.0s deadline fails BEFORE 8.61s EOF.
          2. Consecutive normal requests within budget reuse the exact same child PID.
        """
        self.pipeline = SpeechPipeline(self.config)

        # Consecutive normal requests -> PID preserved
        pid_before = self.pipeline.stt.child_pid
        res1 = self.pipeline.run_wav_direct_stt(OFFICIAL_KO_WAV, run_id="rev6_pid_reuse_1")
        pid_after_1 = self.pipeline.stt.child_pid
        self.assertEqual(pid_before, pid_after_1, "PID should be preserved across requests within budget")

        res2 = self.pipeline.run_wav_direct_stt(OFFICIAL_KO_WAV, run_id="rev6_pid_reuse_2")
        pid_after_2 = self.pipeline.stt.child_pid
        self.assertEqual(pid_after_1, pid_after_2, "PID should be preserved across consecutive normal requests")

        # Injected delay -> TimeoutError before EOF
        self.pipeline.stt.inject_uncooperative_delay(3.0)
        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(
                REPRO_FIXTURE_PATH,
                speed=1.0,
                run_id="rev6_eof_regression",
                request_timeout=2.0
            )
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 7.5, f"Must fail before 8.61s EOF, took {elapsed:.3f}s")
        print(f"[Rev6 Regression Check] PID preserved ({pid_before} == {pid_after_2}) and TimeoutError raised at {elapsed:.3f}s (before EOF).")


if __name__ == "__main__":
    unittest.main()
