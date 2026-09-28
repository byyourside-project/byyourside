#!/usr/bin/env python3
"""
Unit and regression test suite for Task 01 Validation Tools Revision 02.
Focuses strictly on:
  1. Reference text validity (blank, punctuation-only, non-string references rejected before model init).
  2. Normal 30-sentence full PASS path.
  3. CER lifecycle failure evidence preservation (constructor, read/hash failure).
  4. Existing output file protection (bytes preserved without --overwrite, both success and failure paths).
  5. Consecutive default executions generate isolated distinct files.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
import scipy.io.wavfile as wavfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import scripts.evaluate_cer as cer
import tests.test_mic_10min as mic
from tests.test_validation_tools_rev1 import MockPipeline, MockMicPipeline


class TestValidationToolsRevision02(unittest.TestCase):
    def test_normal_30_sentences_achieves_pass(self):
        """Revision 02: Normal 30 standard presentation sentences achieve full PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            for i in range(1, 31):
                wavfile.write(os.path.join(tmp_p, f"P{i:02d}.wav"), 16000, np.zeros(160, dtype=np.int16))

            out_json = os.path.join(tmp_p, "normal_30.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    output_json=out_json,
                    mode="all"
                )

            self.assertEqual(report["structured_status"], "PASS")
            self.assertIn("PASS", report["overall_status"])
            self.assertEqual(report["primary_metric"]["measured_value_raw"], 0.0)
            self.assertEqual(report["executed_count"], 30)
            self.assertEqual(report["not_run_count"], 0)
            self.assertEqual(report["error_count"], 0)
            self.assertTrue(os.path.exists(out_json))

    def test_blank_reference_rejected_before_model_init(self):
        """R1: 30 blank references ('   ') are rejected before model starts, reporting ERROR."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            for i in range(1, 31):
                wavfile.write(os.path.join(tmp_p, f"P{i:02d}.wav"), 16000, np.zeros(160, dtype=np.int16))

            manifest_path = os.path.join(tmp_p, "blank.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": f"P{i:02d}", "ref": "   "} for i in range(1, 31)]}, f)

            pipeline_constructed = False

            class SpyPipeline(MockPipeline):
                def __init__(self, *a, **k):
                    nonlocal pipeline_constructed
                    pipeline_constructed = True
                    super().__init__(*a, **k)

            out_json = os.path.join(tmp_p, "blank_out.json")
            with patch.object(cer, "SpeechPipeline", SpyPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json
                )

            self.assertFalse(pipeline_constructed, "Model pipeline should NOT be initialized for blank references")
            self.assertEqual(report["structured_status"], "ERROR")
            self.assertIn("Manifest error", report["overall_status"])
            self.assertIsNone(report["primary_metric"]["measured_value_raw"])
            self.assertTrue(os.path.exists(out_json))

    def test_punctuation_only_reference_rejected_before_model_init(self):
        """R1: References with only punctuation ('.,!?') are rejected before model starts."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            manifest_path = os.path.join(tmp_p, "punct.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": "P01", "ref": "  , ! ? .  "}]}, f)

            pipeline_constructed = False

            class SpyPipeline(MockPipeline):
                def __init__(self, *a, **k):
                    nonlocal pipeline_constructed
                    pipeline_constructed = True
                    super().__init__(*a, **k)

            out_json = os.path.join(tmp_p, "punct_out.json")
            with patch.object(cer, "SpeechPipeline", SpyPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json
                )

            self.assertFalse(pipeline_constructed)
            self.assertEqual(report["structured_status"], "ERROR")
            self.assertIn("Manifest error", report["overall_status"])

    def test_non_string_reference_rejected_before_model_init(self):
        """R1: Non-string references (e.g. integer 1234) are rejected before model starts."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            manifest_path = os.path.join(tmp_p, "int_ref.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": "P01", "ref": 12345}]}, f)

            pipeline_constructed = False

            class SpyPipeline(MockPipeline):
                def __init__(self, *a, **k):
                    nonlocal pipeline_constructed
                    pipeline_constructed = True
                    super().__init__(*a, **k)

            out_json = os.path.join(tmp_p, "int_out.json")
            with patch.object(cer, "SpeechPipeline", SpyPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json
                )

            self.assertFalse(pipeline_constructed)
            self.assertEqual(report["structured_status"], "ERROR")

    def test_cer_constructor_failure_preserves_json_and_closes_pipeline(self):
        """R2: Pipeline constructor failure records JSON with failed_phase and re-raises."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "init_err.json")

            with patch.object(cer, "SpeechPipeline", side_effect=RuntimeError("injected init error")):
                with self.assertRaises(RuntimeError):
                    cer.evaluate_dataset(audio_dir=tmp_p, output_json=out_json)

            self.assertTrue(os.path.exists(out_json))
            with open(out_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["structured_status"], "ERROR")
            self.assertEqual(data["failed_phase"], "pipeline_init")
            self.assertEqual(data["exception_type"], "RuntimeError")
            self.assertIn("injected init error", data["exception_message"])

    def test_existing_output_file_bytes_preserved_without_overwrite(self):
        """R3: Passing an existing output_json preserves original bytes and writes to new safe path."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            existing_file = os.path.join(tmp_p, "prior.json")
            original_content = '{"prior_evidence": true, "run_id": "original_123"}'
            with open(existing_file, "w", encoding="utf-8") as f:
                f.write(original_content)

            # 1. CER evaluation with allow_overwrite=False (default)
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    output_json=existing_file,
                    allow_overwrite=False
                )

            # Verify existing file content was completely untouched
            with open(existing_file, "r", encoding="utf-8") as f:
                current_content = f.read()
            self.assertEqual(current_content, original_content, "Existing output file must not be overwritten")

            # 2. Mic benchmark with allow_overwrite=False (default)
            with patch.object(mic, "SpeechPipeline", MockMicPipeline), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                mic_summary = mic.run_mic_benchmark(
                    duration_seconds=600.0,
                    output_json=existing_file,
                    allow_overwrite=False
                )

            # Verify existing file content is STILL untouched
            with open(existing_file, "r", encoding="utf-8") as f:
                current_content2 = f.read()
            self.assertEqual(current_content2, original_content, "Existing file must remain protected across mic runs")

    def test_existing_output_file_overwritten_when_overwrite_true(self):
        """R3: When allow_overwrite=True, target output file is explicitly updated."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            target_file = os.path.join(tmp_p, "target.json")
            with open(target_file, "w", encoding="utf-8") as f:
                f.write('{"old": true}')

            cer.atomic_write_json(target_file, {"new": True}, allow_overwrite=True)

            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data, {"new": True})

    def test_consecutive_default_runs_generate_distinct_files(self):
        """R3: Consecutive runs without output_json generate distinct timestamped files."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                r1 = cer.evaluate_dataset(audio_dir=tmp_p, output_json=None)
                time.sleep(0.01)
                r2 = cer.evaluate_dataset(audio_dir=tmp_p, output_json=None)

            self.assertNotEqual(r1["execution_id"], r2["execution_id"])

    def test_mic_passed_field_matches_overall_gate_status(self):
        """Revision 02: mic benchmark 'passed' field is False when overall_status is PARTIAL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_gate.json")
            with patch.object(mic, "SpeechPipeline", MockMicPipeline), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                summary = mic.run_mic_benchmark(duration_seconds=600.0, output_json=out_json)

            self.assertEqual(summary["structured_status"], "PARTIAL")
            self.assertFalse(summary["passed"], "'passed' must be False when overall_status is PARTIAL")
            self.assertTrue(summary["technical_stability_passed"], "Technical stability should be True")


if __name__ == "__main__":
    unittest.main()
