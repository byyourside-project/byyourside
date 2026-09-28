#!/usr/bin/env python3
"""
Test Suite for Task 01 Revision 04:
Non-cooperative Native Inference Termination, Process Reclamation, and Same-Parent Rerun.

Covers:
  1. Injection of strictly non-cooperative STT into the actual execution path.
  2. Deadline timeout and forceful termination (SIGTERM/SIGKILL) of hung child worker.
  3. Reclamation of all child processes and worker threads (has_running_workers() == False).
  4. Flawless restart and execution in the SAME parent process without sys.exit.
  5. Process reuse across multiple utterances and sessions without per-utterance reload.
  6. Orderly pipeline shutdown and resource cleanup.
"""
import os
import sys
import time
import unittest
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig
from src.pipeline import SpeechPipeline
from src.stt import IsolatedSttEngine, UncooperativeSttEngine, SttEngine

FORCED_CUTOFF_WAV = "logs/test_fixtures/temp_forced_cutoff.wav"

class TestTask01Revision04(unittest.TestCase):

    def setUp(self):
        self.config = PipelineConfig(
            vad=VadConfig(
                min_silence_duration=0.5,
                max_speech_duration=4.0,
                hard_max_speech_duration=4.0
            ),
            stt=SttConfig(num_threads=4)
        )
        self.pipeline: Optional[SpeechPipeline] = None

    def tearDown(self):
        if self.pipeline is not None:
            self.pipeline.close()
            self.pipeline = None

    def test_rev4_uncooperative_stt_injection_and_process_reaping(self):
        """
        Revision 04 Requirement 1 & 2 & 3:
        Inject an uncooperative STT hang (10.0s) into the actual child process execution path.
        Verify that within the join deadline (<3.5s), TimeoutError is raised,
        the hung child process is terminated (SIGTERM/SIGKILL), and zero zombie threads or child
        processes remain alive in the parent process.
        """
        self.pipeline = SpeechPipeline(self.config)
        self.assertTrue(self.pipeline.stt.is_child_alive(), "STT child process should be alive")
        child_pid = self.pipeline.stt.child_pid
        self.assertIsNotNone(child_pid)

        # Inject 10.0s uncooperative delay in the child process
        self.pipeline.stt.inject_uncooperative_delay(10.0)

        t0 = time.time()
        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="rev4_uncoop_hang")
        elapsed = time.time() - t0

        # Verification 1: Must timeout within 3.5s
        self.assertLess(elapsed, 3.5, f"Expected timeout under 3.5s, took {elapsed:.3f}s")
        self.assertGreater(elapsed, 1.8, "Should wait for the 2.0s worker join deadline")

        # Verification 2: The child process must be dead
        self.assertFalse(self.pipeline.stt.is_child_alive(), "Child process must be terminated")

        # Verification 3: No running worker threads
        self.assertFalse(self.pipeline.has_running_workers(), "All workers must be reported as dead")
        threads_alive = [t for t in self.pipeline._active_workers if t.is_alive()]
        self.assertEqual(len(threads_alive), 0, f"Found active worker threads: {threads_alive}")
        print(f"\n[Rev4 Check] Uncooperative hang terminated in {elapsed:.3f}s; child and workers reaped.")

    def test_rev4_parent_process_rerun_success_in_same_process(self):
        """
        Revision 04 Requirement 4:
        In the EXACT SAME parent process, after the child process was terminated due to timeout,
        execute a new normal session and verify that:
          - A fresh child process is spawned automatically.
          - Audio is processed and transcribed with status OK.
          - has_running_workers() is False after completion.
        """
        self.pipeline = SpeechPipeline(self.config)

        # 1. Trigger timeout with injected uncooperative hang
        self.pipeline.stt.inject_uncooperative_delay(10.0)
        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="rev4_hang_before_rerun")

        self.assertFalse(self.pipeline.has_running_workers())
        self.assertFalse(self.pipeline.stt.is_child_alive())

        # 2. In the SAME parent process, execute a clean rerun
        t1 = time.time()
        res = self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="rev4_clean_rerun")
        rerun_duration = time.time() - t1

        self.assertEqual(res.status, "OK", f"Expected OK status, got {res.status}")
        self.assertGreaterEqual(len(res.segments), 2, "Expected at least 2 segments")
        full_text = " ".join([s["text"] for s in res.segments])
        self.assertTrue("생각" in full_text or "조금" in full_text, f"Unexpected text: {full_text}")
        self.assertFalse(self.pipeline.has_running_workers(), "No zombie workers after clean rerun")
        self.assertTrue(self.pipeline.stt.is_child_alive(), "New child process should be alive and healthy")
        print(f"[Rev4 Check] Same-parent rerun completed in {rerun_duration:.3f}s: \"{full_text}\"")

    def test_rev4_uncooperative_stt_engine_class_injection(self):
        """
        Revision 04: Verify injection via UncooperativeSttEngine subclass.
        """
        uncoop_stt = UncooperativeSttEngine(self.config.stt, hang_seconds=10.0)
        self.pipeline = SpeechPipeline(self.config, stt=uncoop_stt)

        with self.assertRaises(TimeoutError):
            self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="rev4_subclass_hang")

        self.assertFalse(self.pipeline.has_running_workers())
        self.assertFalse(self.pipeline.stt.is_child_alive())

        # Replace with fresh healthy engine in same pipeline and rerun
        self.pipeline.stt = IsolatedSttEngine(self.config.stt)
        res = self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=100.0, run_id="rev4_subclass_recovery")
        self.assertEqual(res.status, "OK")
        self.assertFalse(self.pipeline.has_running_workers())
        print("[Rev4 Check] UncooperativeSttEngine class injection and parent recovery verified.")

    def test_rev4_child_process_reuse_without_reload_across_utterances(self):
        """
        Revision 04 Requirement:
        The child process must be reused across multiple utterances and sessions without per-utterance reload.
        """
        self.pipeline = SpeechPipeline(self.config)
        pid_initial = self.pipeline.stt.child_pid
        self.assertIsNotNone(pid_initial)

        # Run 1
        res1 = self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=10.0, run_id="rev4_reuse_run1")
        pid_run1 = self.pipeline.stt.child_pid

        # Run 2
        res2 = self.pipeline.run_replay(FORCED_CUTOFF_WAV, speed=10.0, run_id="rev4_reuse_run2")
        pid_run2 = self.pipeline.stt.child_pid

        self.assertEqual(pid_initial, pid_run1, "Child process should be preserved across run 1")
        self.assertEqual(pid_run1, pid_run2, "Child process should be preserved across run 2")
        self.assertEqual(res1.status, "OK")
        self.assertEqual(res2.status, "OK")
        print(f"[Rev4 Check] Model child process PID {pid_initial} preserved across multiple sessions.")

    def test_rev4_clean_orderly_pipeline_shutdown(self):
        """
        Revision 04: Orderly shutdown of pipeline workers and child engine processes.
        """
        pipeline = SpeechPipeline(self.config)
        self.assertTrue(pipeline.stt.is_child_alive())
        pipeline.close()
        self.assertFalse(pipeline.stt.is_child_alive())
        self.assertFalse(pipeline.has_running_workers())
        print("[Rev4 Check] Orderly pipeline.close() verified.")

if __name__ == "__main__":
    unittest.main()
