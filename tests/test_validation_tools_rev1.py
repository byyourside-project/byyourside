#!/usr/bin/env python3
"""
Unit and regression test suite for Task 01 Validation Tools Revision 01.
Tests all judgment logic, manifest handling, queue metrics, failure evidence preservation,
and status separation without requiring live microphone or actual ONNX model inference.
"""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
import scipy.io.wavfile as wavfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import scripts.evaluate_cer as cer
import tests.test_mic_10min as mic


class MockPipeline:
    fail_direct = False
    fail_vad = False
    closed = False

    def __init__(self, *args, **kwargs):
        self.stt = NS(warm_up=lambda *a, **k: None, child_pid=None)
        MockPipeline.closed = False

    def close(self):
        MockPipeline.closed = True

    def _get_ref(self, wav_path, run_id):
        base = os.path.splitext(os.path.basename(wav_path))[0].upper()
        for it in cer.STANDARD_DATASET:
            if it["id"] == base:
                return it["ref"]
        return "지금부터 발표를 시작하도록 하겠습니다."

    def run_wav_direct_stt(self, wav_path, run_id=None):
        if self.fail_direct:
            raise TimeoutError("Injected direct STT failure")
        ref_text = self._get_ref(wav_path, run_id)
        return NS(segments=[{"text": ref_text, "infer_ms": 15.0}])

    def run_wav_vad(self, wav_path, run_id=None):
        if self.fail_vad:
            raise TimeoutError("Injected VAD STT failure")
        ref_text = self._get_ref(wav_path, run_id)
        return NS(segments=[{"text": ref_text, "infer_ms": 20.0}])


class MockMicPipeline(MockPipeline):
    fail_init = False
    fail_warmup = False
    fail_mic = False

    def __init__(self, *args, **kwargs):
        if self.fail_init:
            raise RuntimeError("Injected mic pipeline init failure")
        super().__init__(*args, **kwargs)
        if self.fail_warmup:
            self.stt.warm_up = self._raise_warmup

    def _raise_warmup(self, *a, **k):
        raise TimeoutError("Injected warmup failure")

    def run_mic(self, duration_seconds=600.0, run_id="mock_run", snapshot_interval_sec=60.0):
        if self.fail_mic:
            raise TimeoutError("Injected run_mic timeout")

        # Default mock: 5 segments of 12s across 5 minutes with valid queue_wait_ms
        segs = [
            {
                "start_ms": m * 60000.0,
                "end_ms": m * 60000.0 + 12000.0,
                "text": f"발표 구간 {m+1}",
                "queue_wait_ms": 10.0 + m * 5.0
            }
            for m in range(5)
        ]
        return NS(
            total_audio_seconds=duration_seconds,
            total_speech_seconds=60.0,
            segments=segs,
            segment_count=len(segs),
            is_lossless=True,
            overrun_count=0,
            dropped_audio_chunks=0,
            dropped_segments=0,
            dropped_audio_seconds=0.0,
            status="OK",
            run_id=run_id,
            total_inference_seconds=1.5,
            speech_rtf=0.025,
            throughput_rtf=0.0025,
            rtf_stats={"p95": 0.05, "avg": 0.03},
            estimated_delay_stats={"p95": 250.0, "avg": 200.0},
            continuous_latency_stats={"p95": 1200.0},
            queue_wait_stats={"p95": 30.0},
            periodic_snapshots=[]
        )


class TestValidationToolsRevision01(unittest.TestCase):
    def setUp(self):
        MockPipeline.fail_direct = False
        MockPipeline.fail_vad = False
        MockPipeline.closed = False
        MockMicPipeline.fail_init = False
        MockMicPipeline.fail_warmup = False
        MockMicPipeline.fail_mic = False

    def test_cer_single_item_manifest_is_partial_not_pass(self):
        """F1: 1 item manifest cannot achieve PASS even with 0% CER; must be PARTIAL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            wav_path = os.path.join(tmp_p, "P01.wav")
            wavfile.write(wav_path, 16000, np.zeros(16000, dtype=np.int16))

            manifest_path = os.path.join(tmp_p, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": "1.0",
                    "mode": "complete",
                    "items": [{"id": "P01", "ref": "지금부터 발표를 시작하도록 하겠습니다.", "category": "general"}]
                }, f)

            out_json = os.path.join(tmp_p, "out.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json,
                    mode="all"
                )

            self.assertEqual(report["structured_status"], "PARTIAL")
            self.assertIn("PARTIAL", report["overall_status"])
            self.assertIn("1/30", report["overall_status"])
            self.assertFalse(report["standard_30_covered"])
            self.assertEqual(report["executed_count"], 1)

    def test_cer_29_items_manifest_is_partial_not_pass(self):
        """F1: 29 items manifest (missing P30) must be PARTIAL, not PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            items = []
            for i in range(1, 30):
                item_id = f"P{i:02d}"
                wav_path = os.path.join(tmp_p, f"{item_id}.wav")
                wavfile.write(wav_path, 16000, np.zeros(16000, dtype=np.int16))
                items.append({"id": item_id, "ref": "지금부터 발표를 시작하도록 하겠습니다.", "category": "general"})

            manifest_path = os.path.join(tmp_p, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"mode": "complete", "items": items}, f)

            out_json = os.path.join(tmp_p, "out.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json,
                    mode="all"
                )

            self.assertEqual(report["structured_status"], "PARTIAL")
            self.assertIn("29/30", report["overall_status"])
            self.assertFalse(report["standard_30_covered"])

    def test_cer_duplicate_id_in_manifest_reports_error(self):
        """F1: Duplicate IDs in manifest must report structured ERROR and preserve report."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            manifest_path = os.path.join(tmp_p, "dup_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({
                    "items": [
                        {"id": "P01", "ref": "안녕 1"},
                        {"id": "P01", "ref": "안녕 2"}
                    ]
                }, f)

            out_json = os.path.join(tmp_p, "err_dup.json")
            report = cer.evaluate_dataset(
                audio_dir=tmp_p,
                manifest_path=manifest_path,
                output_json=out_json
            )

            self.assertEqual(report["structured_status"], "ERROR")
            self.assertIn("Duplicate item IDs detected", report["overall_status"])
            self.assertTrue(os.path.exists(out_json))

    def test_cer_missing_explicit_manifest_reports_error_not_fallback(self):
        """F1: Specified manifest that does not exist must report ERROR, never silently fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            non_existent = os.path.join(tmp_p, "missing_manifest.json")
            out_json = os.path.join(tmp_p, "err_missing.json")

            report = cer.evaluate_dataset(
                audio_dir=tmp_p,
                manifest_path=non_existent,
                output_json=out_json
            )

            self.assertEqual(report["structured_status"], "ERROR")
            self.assertIn("Manifest error", report["overall_status"])
            self.assertEqual(report["total_dataset_items"], 0)
            self.assertTrue(os.path.exists(out_json))

    def test_cer_merge_manifest_mode_succeeds_with_overrides(self):
        """Manifest mode 'merge' overrides specified items and retains standard 30."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            manifest_path = os.path.join(tmp_p, "merge_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({
                    "mode": "merge",
                    "items": [{"id": "P01", "ref": "사용자 커스텀 발표 시작 문장"}]
                }, f)

            dataset, meta = cer.load_manifest(manifest_path)
            self.assertEqual(len(dataset), 30)
            p01 = next(it for it in dataset if it["id"] == "P01")
            self.assertEqual(p01["ref"], "사용자 커스텀 발표 시작 문장")
            self.assertEqual(meta["mode"], "merge")

    def test_cer_direct_mode_leaves_vad_cer_none_and_not_100_percent_fail(self):
        """F2: Direct mode must leave VAD CER as None, not a fake 100% FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            # Create all 30 WAV files
            for i in range(1, 31):
                item_id = f"P{i:02d}"
                wavfile.write(os.path.join(tmp_p, f"{item_id}.wav"), 16000, np.zeros(16000, dtype=np.int16))

            out_json = os.path.join(tmp_p, "direct_out.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    output_json=out_json,
                    mode="direct"
                )

            # VAD CER must be None
            self.assertIsNone(report["aggregate_metrics"]["vad_corpus_cer_nospace"])
            self.assertIsNotNone(report["aggregate_metrics"]["direct_corpus_cer_nospace"])
            # Status must be PARTIAL because Task 01 VAD gate was not run, NOT FAIL 100%
            self.assertEqual(report["structured_status"], "PARTIAL")
            self.assertIn("Task 01 VAD+STT gate NOT_RUN", report["overall_status"])

    def test_cer_all_wavs_fail_reports_error_not_not_run(self):
        """F2: When WAV exists but inference raises TimeoutError, status must be ERROR, not NOT_RUN."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            wav_path = os.path.join(tmp_p, "P01.wav")
            wavfile.write(wav_path, 16000, np.zeros(16000, dtype=np.int16))

            manifest_path = os.path.join(tmp_p, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": "P01", "ref": "안녕"}]}, f)

            MockPipeline.fail_direct = True
            MockPipeline.fail_vad = True

            out_json = os.path.join(tmp_p, "all_err.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json,
                    mode="all"
                )

            self.assertEqual(report["structured_status"], "ERROR")
            self.assertIn("ERROR", report["overall_status"])
            self.assertNotIn("NOT_RUN", report["overall_status"])
            self.assertEqual(report["error_count"], 1)
            self.assertEqual(report["not_run_count"], 0)

    def test_cer_direct_success_and_vad_fail_reports_partial_item(self):
        """F2: Direct success + VAD fail is marked PARTIAL and aggregates Direct properly."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            wavfile.write(os.path.join(tmp_p, "P01.wav"), 16000, np.zeros(16000, dtype=np.int16))
            manifest_path = os.path.join(tmp_p, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": "P01", "ref": "지금부터 발표를 시작하도록 하겠습니다.", "category": "general"}]}, f)

            MockPipeline.fail_direct = False
            MockPipeline.fail_vad = True

            out_json = os.path.join(tmp_p, "partial_err.json")
            with patch.object(cer, "SpeechPipeline", MockPipeline):
                report = cer.evaluate_dataset(
                    audio_dir=tmp_p,
                    manifest_path=manifest_path,
                    output_json=out_json,
                    mode="all"
                )

            item = report["items"][0]
            self.assertEqual(item["status"], "PARTIAL")
            self.assertEqual(item["status_direct"], "SUCCESS")
            self.assertEqual(item["status_vad"], "ERROR")
            self.assertEqual(report["aggregate_metrics"]["direct_evaluated_count"], 1)
            self.assertEqual(report["aggregate_metrics"]["vad_evaluated_count"], 0)
            self.assertIsNotNone(report["aggregate_metrics"]["direct_corpus_cer_nospace"])
            self.assertIsNone(report["aggregate_metrics"]["vad_corpus_cer_nospace"])

    def test_mic_missing_queue_wait_fails_stability(self):
        """F3: Segments missing queue_wait_ms must fail queue stability check, not default to 0."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_missing_q.json")

            class MicMissingQueue(MockMicPipeline):
                def run_mic(self, *a, **k):
                    res = super().run_mic(*a, **k)
                    # Strip queue_wait_ms from all segments
                    for s in res.segments:
                        s.pop("queue_wait_ms", None)
                    return res

            with patch.object(mic, "SpeechPipeline", MicMissingQueue), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                summary = mic.run_mic_benchmark(600.0, output_json=out_json)

            self.assertEqual(summary["structured_status"], "FAIL")
            self.assertIn("Queue metrics invalid", summary["overall_status"])
            self.assertEqual(summary["stability_metrics"]["queue_stability_status"], "INVALID (Segment 0 missing 'queue_wait_ms')")
            self.assertFalse(summary["passed"])

    def test_mic_non_numeric_queue_wait_fails_stability(self):
        """F3: Non-numeric queue_wait_ms (e.g. NaN or string) must fail queue stability check."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_nan_q.json")

            class MicNaNQueue(MockMicPipeline):
                def run_mic(self, *a, **k):
                    res = super().run_mic(*a, **k)
                    res.segments[0]["queue_wait_ms"] = float("nan")
                    return res

            with patch.object(mic, "SpeechPipeline", MicNaNQueue), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                summary = mic.run_mic_benchmark(600.0, output_json=out_json)

            self.assertEqual(summary["structured_status"], "FAIL")
            self.assertIn("Queue metrics invalid", summary["overall_status"])

    def test_mic_intermediate_queue_surge_detected(self):
        """F3: Queue that surges in the middle (>300ms spike) but drops at the end must be flagged as runaway."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_surge_q.json")

            class MicSurgeQueue(MockMicPipeline):
                def run_mic(self, *a, **k):
                    res = super().run_mic(*a, **k)
                    # 1st seg: 10ms, 2nd seg: 450ms (surge!), 5th seg: 15ms
                    res.segments[0]["queue_wait_ms"] = 10.0
                    res.segments[1]["queue_wait_ms"] = 450.0
                    res.segments[2]["queue_wait_ms"] = 350.0
                    res.segments[3]["queue_wait_ms"] = 50.0
                    res.segments[4]["queue_wait_ms"] = 15.0
                    return res

            with patch.object(mic, "SpeechPipeline", MicSurgeQueue), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                summary = mic.run_mic_benchmark(600.0, output_json=out_json)

            self.assertEqual(summary["structured_status"], "FAIL")
            self.assertTrue(summary["stability_metrics"]["has_runaway_queue"])
            self.assertEqual(summary["stability_metrics"]["queue_stability_status"], "RUNAWAY")

    def test_mic_sparse_speech_is_not_full_continuous_presentation_pass(self):
        """F3: 60s speech in 600s must NOT be declared continuous presentation PASS; remains PARTIAL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_sparse.json")

            with patch.object(mic, "SpeechPipeline", MockMicPipeline), \
                 patch.object(mic, "get_memory_stats", return_value={"parent_rss_mb": 10.0, "child_rss_mb": 50.0, "combined_rss_mb": 60.0}):
                summary = mic.run_mic_benchmark(600.0, output_json=out_json)

            # Even though resource stability passed, presentation coverage remains PARTIAL
            self.assertEqual(summary["structured_status"], "PARTIAL")
            self.assertIn("PARTIAL", summary["overall_status"])
            self.assertIn("presentation speech coverage and human latency require PM review", summary["overall_status"])
            self.assertNotEqual(summary["overall_status"], "PASS")

    def test_mic_trailing_partial_minute_binning(self):
        """F3: Bins include trailing partial minute (e.g. 605.3s yields 11 bins)."""
        segs = [{"start_ms": 601000.0, "end_ms": 604000.0, "queue_wait_ms": 12.0}]
        bins = mic.analyze_per_minute_speech(segs, 605.3)
        self.assertEqual(len(bins), 11)
        self.assertEqual(bins[10]["minute_index"], 11)
        self.assertAlmostEqual(bins[10]["duration_seconds"], 5.3, places=1)
        self.assertAlmostEqual(bins[10]["speech_seconds"], 3.0, places=1)
        self.assertIsNotNone(bins[10]["queue_wait_stats"])

    def test_mic_exception_preserves_json_report_and_closes_pipeline(self):
        """F2/F3: Exception during mic execution preserves structured error JSON and calls close()."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = os.path.abspath(tmp)
            out_json = os.path.join(tmp_p, "mic_exception.json")

            MockMicPipeline.fail_mic = True
            with patch.object(mic, "SpeechPipeline", MockMicPipeline):
                with self.assertRaises(TimeoutError):
                    mic.run_mic_benchmark(600.0, output_json=out_json)

            self.assertTrue(os.path.exists(out_json))
            with open(out_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["structured_status"], "ERROR")
            self.assertEqual(data["failed_phase"], "run_mic")
            self.assertEqual(data["exception_type"], "TimeoutError")
            self.assertTrue(MockPipeline.closed)


if __name__ == "__main__":
    unittest.main()
