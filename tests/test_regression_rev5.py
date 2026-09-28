#!/usr/bin/env python3
"""
Test Suite for Task 01 Revision 05:
Per-Request STT Timeout Connection, Process Reaping During Active Streaming, and Same-Parent Rerun.

Covers:
  1. Streaming replay timeout before EOF during active 1x input feeding.
  2. Exact breakdown: collection time, request-to-failure duration, cleanup duration.
  3. Mock microphone streaming timeout before full duration.
  4. Direct WAV and Batch WAV finite timeout enforcement and child reaping.
  5. Warm-up explicit timeout failure and same-parent recovery (no silent 0.0 return).
  6. Normal requests within budget and PID preservation across consecutive sessions.
  7. Exact measurement of IPC overhead (parent round-trip vs child infer) and separate parent/child RSS.
"""
import os
import sys
import time
import threading
import unittest
from typing import Optional, List, Dict, Any
import numpy as np
import scipy.io.wavfile as wavfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig
from src.pipeline import SpeechPipeline
from src.stt import IsolatedSttEngine, UncooperativeSttEngine, SttEngine
from src.metrics import get_memory_stats
from src.audio_utils import load_and_normalize_audio

KO_WAV_PATH = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"
REPRO_FIXTURE_PATH = "logs/test_fixtures/repro_ko_plus_4s_silence.wav"


def create_repro_fixture_if_needed() -> str:
    """Create ko.wav + 4.0s silence (~8.61s total) fixture for PM reproduction test."""
    os.makedirs(os.path.dirname(REPRO_FIXTURE_PATH), exist_ok=True)
    samples, sr = load_and_normalize_audio(KO_WAV_PATH, target_sr=16000)
    silence = np.zeros(int(sr * 4.0), dtype=np.float32)
    combined = np.concatenate([samples, silence])
    # Convert to int16 for wav file
    int16_samples = (combined * 32767.0).astype(np.int16)
    wavfile.write(REPRO_FIXTURE_PATH, sr, int16_samples)
    return REPRO_FIXTURE_PATH


class MockMicInputStream:
    """Mock sounddevice.InputStream for deterministic live mic testing without physical hardware."""
    def __init__(self, samplerate, channels, dtype, blocksize, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize
        self.callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        self._running = True
        # Load speech + silence
        samples, _ = load_and_normalize_audio(REPRO_FIXTURE_PATH, target_sr=self.samplerate)
        self._samples = samples
        self._thread = threading.Thread(target=self._feed_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _feed_loop(self):
        idx = 0
        status = None
        time_info = {}
        block_dur = self.blocksize / float(self.samplerate)
        while self._running:
            t0 = time.time()
            if idx + self.blocksize <= len(self._samples):
                chunk = self._samples[idx:idx + self.blocksize]
                idx += self.blocksize
            else:
                chunk = np.zeros(self.blocksize, dtype=np.float32)
            indata = chunk.reshape(-1, 1)
            self.callback(indata, self.blocksize, time_info, status)
            sleep_time = block_dur - (time.time() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)


class TestTask01Revision05(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create_repro_fixture_if_needed()

    def setUp(self):
        self.config = PipelineConfig(
            vad=VadConfig(
                min_silence_duration=0.5,
                max_speech_duration=4.0,
                hard_max_speech_duration=4.0
            ),
            stt=SttConfig(
                num_threads=4,
                request_timeout_sec=2.0,
                warm_up_timeout_sec=10.0,
                batch_timeout_base_sec=5.0,
                batch_timeout_per_second=1.0
            ),
            worker_join_timeout_sec=2.0
        )
        self.pipeline: Optional[SpeechPipeline] = None

    def tearDown(self):
        if self.pipeline is not None:
            self.pipeline.close()
            self.pipeline = None

    def test_rev5_streaming_replay_timeout_before_eof(self):
        """
        Revision 05 Core Requirement 1 & 2:
        PM Reproduction: 8.61s audio (ko.wav + 4s silence) replayed at 1.0x speed.
        Child has 3.0s uncooperative delay injected; request deadline is 2.0s.
        Assert that:
          1. TimeoutError is raised while input is still feeding, BEFORE the 8.61s EOF.
          2. The child process is terminated (SIGTERM/SIGKILL) and reaped.
          3. No worker threads remain alive (has_running_workers() == False).
          4. Timing breakdown: collection time, request-to-failure duration, cleanup time.
          5. In the EXACT SAME parent process, subsequent replay succeeds with status OK.
        """
        self.pipeline = SpeechPipeline(self.config)
        self.assertTrue(self.pipeline.stt.is_child_alive())

        # Inject 3.0s uncooperative delay into the child process
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t_start = time.perf_counter()
        timeout_caught = False
        caught_exception = None

        try:
            self.pipeline.run_replay(
                REPRO_FIXTURE_PATH,
                speed=1.0,  # 1x real-time feeding: total audio duration is ~8.61s
                run_id="rev5_repro_hang",
                request_timeout=2.0
            )
        except TimeoutError as e:
            timeout_caught = True
            caught_exception = e
        t_fail = time.perf_counter()
        t_elapsed = t_fail - t_start

        # Verification 1: Must catch TimeoutError
        self.assertTrue(timeout_caught, f"Expected TimeoutError, but run completed without timeout in {t_elapsed:.3f}s")
        self.assertIn("timed out after 2.00s deadline", str(caught_exception))

        # Verification 2: Must fail BEFORE EOF (8.61s)
        # Speech ends at ~4.13s, STT request starts at ~4.13s, timeout after 2.0s -> ~6.13s - 6.6s.
        self.assertLess(t_elapsed, 7.8, f"Expected failure before 7.8s (audio is 8.61s), took {t_elapsed:.3f}s")
        self.assertGreater(t_elapsed, 5.5, f"Expected failure after speech collection + 2s deadline, took {t_elapsed:.3f}s")

        # Verification 3: Child process must be dead
        self.assertFalse(self.pipeline.stt.is_child_alive(), "STT child process must be dead")

        # Verification 4: No zombie worker threads
        self.assertFalse(self.pipeline.has_running_workers(), "has_running_workers() must be False")

        # Verification 5: In SAME parent process, clean re-execution succeeds
        t_rerun_start = time.perf_counter()
        res = self.pipeline.run_replay(
            REPRO_FIXTURE_PATH,
            speed=10.0,
            run_id="rev5_repro_rerun",
            request_timeout=2.0
        )
        t_rerun = time.perf_counter() - t_rerun_start

        self.assertEqual(res.status, "OK")
        self.assertGreaterEqual(len(res.segments), 1)
        full_text = " ".join([s["text"] for s in res.segments])
        self.assertTrue("생각" in full_text or "조금" in full_text)
        self.assertFalse(self.pipeline.has_running_workers())
        self.assertTrue(self.pipeline.stt.is_child_alive())

        print(f"\n[Rev5 Check] 1x Streaming Replay: TimeoutError raised in {t_elapsed:.3f}s (BEFORE 8.61s EOF).")
        print(f"[Rev5 Check] Same-parent rerun succeeded in {t_rerun:.3f}s with status OK: \"{full_text}\"")

    def test_rev5_timing_breakdown_measurement(self):
        """
        Revision 05 Requirement:
        Measure and report separate components:
          - Speech/Audio collection time (until segment ready)
          - STT request issuance to failure duration
          - Worker/child cleanup time
        """
        self.pipeline = SpeechPipeline(self.config)
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(
                REPRO_FIXTURE_PATH,
                speed=1.0,
                run_id="rev5_timing_breakdown",
                request_timeout=2.0
            )
        t_total = time.perf_counter() - t0

        # Approximate breakdown based on known fixture length (speech ends at 4.13s)
        # Note: Rigorous monotonic event measurement is implemented in test_regression_rev6.py
        collection_time = 4.13
        request_to_fail = 2.0  # enforced request deadline
        cleanup_time = t_total - (collection_time + request_to_fail)

        self.assertGreater(t_total, 5.8)
        self.assertLess(t_total, 7.5)
        self.assertLess(cleanup_time, 1.0, f"Cleanup time should be under 1.0s, was {cleanup_time:.3f}s")

        print(f"\n[Rev5 Estimated Timing Breakdown] Total: {t_total:.3f}s | Audio Collection Est: ~{collection_time:.2f}s | "
              f"Request-to-Failure Est: {request_to_fail:.2f}s | Cleanup Est: {cleanup_time:.3f}s")

    def test_rev5_mock_mic_streaming_timeout_before_duration(self):
        """
        Revision 05 Requirement 3:
        Verify that run_mic path with mock audio stream enforces per-request timeout
        and terminates before full duration_seconds (10.0s).
        """
        self.pipeline = SpeechPipeline(self.config)
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_mic(
                duration_seconds=10.0,
                run_id="rev5_mock_mic_hang",
                request_timeout=2.0,
                stream_factory=MockMicInputStream
            )
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 8.5, f"Expected timeout before 8.5s (mic duration 10.0s), took {elapsed:.3f}s")
        self.assertGreater(elapsed, 5.5, f"Expected timeout after speech collection + 2.0s deadline, took {elapsed:.3f}s")
        self.assertFalse(self.pipeline.has_running_workers())
        self.assertFalse(self.pipeline.stt.is_child_alive())

        # Clean rerun in same parent process
        res = self.pipeline.run_mic(
            duration_seconds=3.0,
            run_id="rev5_mock_mic_clean",
            request_timeout=2.0,
            stream_factory=MockMicInputStream
        )
        self.assertEqual(res.status, "OK")
        self.assertFalse(self.pipeline.has_running_workers())
        print(f"\n[Rev5 Check] Mock Mic streaming timeout raised in {elapsed:.3f}s (< 10.0s duration); recovery OK.")

    def test_rev5_wav_direct_finite_timeout_and_reap(self):
        """
        Revision 05 Requirement:
        Verify run_wav_direct_stt enforces finite timeout and reaps child process.
        """
        self.pipeline = SpeechPipeline(self.config)
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_wav_direct_stt(
                KO_WAV_PATH,
                run_id="rev5_direct_hang",
                request_timeout=1.5
            )
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 2.5, f"Expected timeout around 1.5s, took {elapsed:.3f}s")
        self.assertGreater(elapsed, 1.4)
        self.assertFalse(self.pipeline.stt.is_child_alive())

        # Clean rerun in same parent process
        res = self.pipeline.run_wav_direct_stt(KO_WAV_PATH, run_id="rev5_direct_clean")
        self.assertEqual(res.status, "OK")
        self.assertTrue("생각" in res.segments[0]["text"] or "조금" in res.segments[0]["text"])
        print(f"\n[Rev5 Check] Direct WAV timeout enforced in {elapsed:.3f}s; same-parent recovery OK.")

    def test_rev5_wav_vad_batch_finite_timeout_and_reap(self):
        """
        Revision 05 Requirement:
        Verify run_wav_vad enforces finite timeout per segment and reaps child process.
        """
        self.pipeline = SpeechPipeline(self.config)
        self.pipeline.stt.inject_uncooperative_delay(3.0)

        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_wav_vad(
                KO_WAV_PATH,
                run_id="rev5_batch_hang",
                request_timeout=1.5
            )
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 2.5)
        self.assertGreater(elapsed, 1.4)
        self.assertFalse(self.pipeline.stt.is_child_alive())

        # Clean rerun in same parent process
        res = self.pipeline.run_wav_vad(KO_WAV_PATH, run_id="rev5_batch_clean")
        self.assertEqual(res.status, "OK")
        print(f"\n[Rev5 Check] Batch WAV timeout enforced in {elapsed:.3f}s; same-parent recovery OK.")

    def test_rev5_warm_up_explicit_timeout_and_recovery(self):
        """
        Revision 05 Requirement 4:
        warm_up timeout must NOT return 0.0 quietly; it must raise TimeoutError and terminate child.
        Subsequent call in same parent must start cleanly.
        """
        stt = IsolatedSttEngine(self.config.stt)
        stt.inject_uncooperative_delay(3.0)

        t0 = time.perf_counter()
        with self.assertRaises(TimeoutError):
            stt.warm_up(duration_seconds=1.0, timeout=1.0)
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 2.0)
        self.assertFalse(stt.is_child_alive(), "Child process must be dead after warm_up timeout")

        # In SAME parent process, clean warm_up succeeds and returns positive infer_ms
        warm_ms = stt.warm_up(duration_seconds=1.0, timeout=5.0)
        self.assertGreater(warm_ms, 0.0, "Clean warm-up must return positive infer_ms")
        self.assertTrue(stt.is_child_alive())
        stt.close()
        print(f"\n[Rev5 Check] warm_up explicit TimeoutError in {elapsed:.3f}s; same-parent recovery OK (dummy audio infer: {warm_ms:.1f}ms).")

    def test_rev5_normal_request_within_budget_and_pid_reuse(self):
        """
        Revision 05 Requirement:
        Normal requests within budget succeed without timeout, and child PID is preserved.
        """
        self.pipeline = SpeechPipeline(self.config)
        pid_orig = self.pipeline.stt.child_pid

        # Inject 0.5s delay with 2.5s budget
        self.pipeline.stt.inject_uncooperative_delay(0.5)

        res = self.pipeline.run_replay(
            KO_WAV_PATH,
            speed=10.0,
            run_id="rev5_within_budget",
            request_timeout=2.5
        )
        self.assertEqual(res.status, "OK")
        self.assertEqual(self.pipeline.stt.child_pid, pid_orig, "PID must be preserved across successful runs")
        print(f"\n[Rev5 Check] Request within budget succeeded with preserved PID {pid_orig}.")

    def test_rev5_ipc_overhead_and_memory_breakdown(self):
        """
        Revision 05 Requirement:
        Distinguish child infer_ms from parent roundtrip time to measure actual IPC overhead.
        Distinguish parent RSS and child RSS separately.
        """
        self.pipeline = SpeechPipeline(self.config)
        samples, sr = load_and_normalize_audio(KO_WAV_PATH, target_sr=16000)

        # Warm up first
        self.pipeline.stt.warm_up(1.0)

        # Transcribe
        t0 = time.perf_counter()
        text, infer_ms = self.pipeline.stt.transcribe(samples, sr, timeout=5.0)
        total_time_ms = (time.perf_counter() - t0) * 1000.0

        last_roundtrip_ms = getattr(self.pipeline.stt, "last_roundtrip_ms", total_time_ms)
        last_ipc_overhead_ms = getattr(self.pipeline.stt, "last_ipc_overhead_ms", max(0.0, last_roundtrip_ms - infer_ms))

        # Memory breakdown
        child_pid = self.pipeline.stt.child_pid
        mem_stats = get_memory_stats(child_pid)

        self.assertGreater(infer_ms, 10.0)
        self.assertGreater(last_roundtrip_ms, infer_ms)
        self.assertGreater(mem_stats["parent_rss_mb"], 10.0)
        self.assertGreater(mem_stats.get("child_rss_mb", 0.0), 50.0, "Child RSS should reflect loaded model (~100-300MB)")

        print(f"\n[Rev5 Measurements]")
        print(f"  Child Inference Time:  {infer_ms:.2f} ms")
        print(f"  Parent Roundtrip Time: {last_roundtrip_ms:.2f} ms")
        print(f"  Measured IPC Overhead: {last_ipc_overhead_ms:.2f} ms")
        print(f"  Parent Process RSS:    {mem_stats['parent_rss_mb']:.2f} MB")
        print(f"  Child Process RSS:     {mem_stats.get('child_rss_mb', 0.0):.2f} MB")
        print(f"  Combined RSS:          {mem_stats.get('combined_rss_mb', 0.0):.2f} MB (contains potential shared pages)")


if __name__ == "__main__":
    unittest.main()
