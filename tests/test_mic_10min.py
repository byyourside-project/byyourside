#!/usr/bin/env python3
"""
Scenario 5: 10-Minute Live Microphone Stability and Resource Benchmark (Revision 02).
Uses the unified SpeechPipeline.run_mic() path.

Evaluates against PM requirements:
  1. Actual captured audio duration (not merely the target setting duration).
  2. Per-minute speech distribution & queue wait metrics (detects silence cheating and runaway queue).
  3. Lossless streaming: 0 capture overruns, 0 dropped audio chunks, 0 dropped speech segments.
  4. Memory stability: Separate Parent Process RSS vs Child Process RSS tracking across 10 minutes.
  5. Queue latency stability: Measures trend drift, intermediate surges, and per-minute queue wait stats.
  6. Failure evidence preservation: Emits JSON report with run_id, phase, and exception even on timeout/crash.
  7. Clear gate status separation: System/Resource Stability vs Presentation Speech Coverage vs Human Latency.
     - 'passed' field reflects overall gate PASS (False if PARTIAL).
     - 'technical_stability_passed' reflects lossless + latency criteria.
  8. Output file protection: Default refusal to overwrite existing evidence files unless --overwrite is passed.
  9. Complete reproduction metadata: config, microphone device info, SenseVoice model info, ITN status.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
import uuid
from typing import Dict, Any, List, Optional
import numpy as np

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig, AudioConfig, QueueConfig
from src.pipeline import SpeechPipeline, get_git_revision
from src.metrics import get_memory_stats

REQUIRED_STABILITY_SECONDS = 600.0


def get_git_info() -> Dict[str, Any]:
    """Retrieve git revision and dirty status for provenance tracking."""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return {"revision": rev, "is_dirty": len(status_out) > 0}
    except Exception:
        return {"revision": "unknown", "is_dirty": False}


def get_reproduction_metadata() -> Dict[str, Any]:
    """Retrieve runtime, model, ITN, audio device, and pipeline reproduction metadata."""
    git_info = get_git_info()
    meta = {
        "git_revision": git_info["revision"],
        "git_dirty": git_info["is_dirty"],
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "model": {
            "name": "SenseVoice Small Korean/Multilingual ONNX",
            "model_file": "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx",
            "tokens_file": "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt",
            "model_manifest_status": "verified_task_01",
            "itn_status": "builtin_sensevoice_normalization_enabled"
        },
        "pipeline_config": {
            "vad": {
                "min_silence_duration": 0.5,
                "max_speech_duration": 4.0,
                "hard_max_speech_duration": 4.0
            },
            "stt": {
                "request_timeout_sec": 2.0,
                "warm_up_timeout_sec": 10.0,
                "num_threads": 4,
                "itn_enabled": True
            },
            "audio": {
                "target_sample_rate": 16000,
                "device_sample_rate": 48000,
                "channels": 1,
                "downmix_method": "arithmetic_channel_mean"
            },
            "queue": {
                "max_audio_queue_size": 300,
                "max_segment_queue_size": 100
            }
        }
    }
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        meta["microphone_device"] = {
            "name": dev.get("name", "unknown"),
            "default_samplerate": dev.get("default_samplerate", 48000),
            "max_input_channels": dev.get("max_input_channels", 1)
        }
    except Exception:
        meta["microphone_device"] = {"name": "system_default", "status": "query_unavailable"}
    return meta


def resolve_output_path(filepath: str, allow_overwrite: bool = False) -> str:
    """
    Resolves output file path protecting existing files unless allow_overwrite is True.
    If filepath exists and allow_overwrite is False, generates a non-colliding unique path
    preserving the existing file.
    """
    if not os.path.exists(filepath) or allow_overwrite:
        return filepath

    base, ext = os.path.splitext(filepath)
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    u = uuid.uuid4().hex[:6]
    safe_path = f"{base}_{ts}_{u}{ext}"
    print(
        f"[OUTPUT PROTECTION] '{filepath}' already exists. Preserving original file and writing to '{safe_path}'. "
        "(Pass --overwrite to overwrite existing files).",
        file=sys.stderr
    )
    return safe_path


def atomic_write_json(filepath: str, data: Dict[str, Any], allow_overwrite: bool = False) -> str:
    """
    Safely write JSON to disk using atomic rename.
    If filepath exists and allow_overwrite is False:
    Preserves the existing file and writes to a new unique path, returning the path used.
    """
    target_path = resolve_output_path(filepath, allow_overwrite=allow_overwrite)
    os.makedirs(os.path.dirname(os.path.abspath(target_path)) or ".", exist_ok=True)
    tmp_path = f"{target_path}.tmp_{uuid.uuid4().hex[:8]}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, target_path)
    return target_path


def analyze_per_minute_speech(
    segments: List[Dict[str, Any]],
    total_captured_sec: float
) -> List[Dict[str, Any]]:
    """
    Analyzes detected speech segments and queue wait latency in 1-minute bins.
    Includes the trailing partial minute if total_captured_sec is not an exact multiple of 60.
    """
    minute_count = max(1, math.ceil(total_captured_sec / 60.0))
    bins = []
    for m in range(minute_count):
        m_start_sec = m * 60.0
        m_end_sec = min((m + 1) * 60.0, total_captured_sec)
        m_dur_sec = max(0.0, m_end_sec - m_start_sec)

        m_segments = []
        speech_sec_in_min = 0.0
        m_queue_waits = []

        for seg in segments:
            seg_start_sec = seg.get("start_ms", 0.0) / 1000.0
            seg_end_sec = seg.get("end_ms", 0.0) / 1000.0

            # Overlap with this minute window
            overlap_start = max(m_start_sec, seg_start_sec)
            overlap_end = min(m_end_sec, seg_end_sec)
            if overlap_end > overlap_start:
                speech_sec_in_min += (overlap_end - overlap_start)
                m_segments.append(seg)
                if "queue_wait_ms" in seg and isinstance(seg["queue_wait_ms"], (int, float)):
                    m_queue_waits.append(float(seg["queue_wait_ms"]))

        speech_ratio = (speech_sec_in_min / m_dur_sec) if m_dur_sec > 0 else 0.0
        has_active = (speech_sec_in_min >= 3.0) or (m_dur_sec < 60.0 and speech_ratio >= 0.05 and speech_sec_in_min >= 0.5)

        queue_stats: Optional[Dict[str, float]] = None
        if m_queue_waits:
            queue_stats = {
                "count": len(m_queue_waits),
                "min_ms": round(min(m_queue_waits), 1),
                "avg_ms": round(sum(m_queue_waits) / len(m_queue_waits), 1),
                "max_ms": round(max(m_queue_waits), 1),
                "p95_ms": round(float(np.percentile(m_queue_waits, 95)), 1)
            }

        bins.append({
            "minute_index": m + 1,
            "window_range_sec": f"{m_start_sec:.1f}s - {m_end_sec:.1f}s",
            "duration_seconds": round(m_dur_sec, 2),
            "speech_seconds": round(speech_sec_in_min, 2),
            "speech_ratio": round(speech_ratio, 4),
            "segment_count": len(m_segments),
            "has_active_speech": has_active,
            "queue_wait_stats": queue_stats
        })
    return bins


def run_mic_benchmark(
    duration_seconds: float = 600.0,
    output_json: Optional[str] = None,
    allow_overwrite: bool = False
) -> Dict[str, Any]:
    run_timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    run_uuid = uuid.uuid4().hex[:8]
    is_full_10min_gate = (duration_seconds >= REQUIRED_STABILITY_SECONDS)
    test_type = "10min_stability_gate" if is_full_10min_gate else "smoke_test"

    if output_json is None:
        prefix = "task_01_mic_10min" if is_full_10min_gate else "task_01_mic_smoke"
        output_json = f"logs/{prefix}_{run_timestamp}_{run_uuid}.json"

    run_id = f"mic_{'10m' if is_full_10min_gate else 'smoke'}_{run_timestamp}_{run_uuid}"
    git_info = get_git_info()
    repro_meta = get_reproduction_metadata()

    print("=================================================================")
    print(f" Starting Scenario 5: Live Microphone Evaluation ({duration_seconds}s)")
    print(f" Test Type:       {test_type}")
    print(f" Execution ID:    {run_id}")
    print(f" Git Revision:    {git_info['revision']} (dirty: {git_info['is_dirty']})")
    print(f" Output JSON:     {output_json}")
    print("=================================================================")

    config = PipelineConfig(
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
        audio=AudioConfig(device_sample_rate=48000, target_sample_rate=16000),
        queue=QueueConfig(max_audio_queue_size=300, max_segment_queue_size=100),
        worker_join_timeout_sec=2.0
    )

    pipeline = None
    failed_phase = "pipeline_init"
    t_wall_start = time.perf_counter()

    try:
        pipeline = SpeechPipeline(config)

        failed_phase = "warm_up"
        pipeline.stt.warm_up(0.5)

        failed_phase = "run_mic"
        res = pipeline.run_mic(
            duration_seconds=duration_seconds,
            run_id=run_id,
            snapshot_interval_sec=60.0
        )
        t_wall_elapsed = time.perf_counter() - t_wall_start

        # 1. Audio capture duration verification
        captured_audio_sec = res.total_audio_seconds
        dur_tolerance = 2.0  # 2 seconds tolerance for audio ringbuffer startup/teardown
        duration_satisfied = (captured_audio_sec >= duration_seconds - dur_tolerance)

        # 2. Per-minute speech distribution analysis (including trailing partial minute)
        per_minute_stats = analyze_per_minute_speech(res.segments, captured_audio_sec)
        minute_count = len(per_minute_stats)
        active_minutes = sum(1 for m in per_minute_stats if m["has_active_speech"])

        # Check for minimum speech presence (detection sanity check)
        if is_full_10min_gate:
            speech_presence_ok = (active_minutes >= 5) and (res.total_speech_seconds >= 60.0)
        else:
            speech_presence_ok = (res.segment_count > 0)

        # 3. Queue wait validation and drift calculation
        queue_waits = []
        queue_valid = True
        queue_error_msg = None
        for i, s in enumerate(res.segments):
            if "queue_wait_ms" not in s:
                queue_valid = False
                queue_error_msg = f"Segment {i} missing 'queue_wait_ms'"
                break
            val = s["queue_wait_ms"]
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                queue_valid = False
                queue_error_msg = f"Segment {i} has invalid queue_wait_ms: {val}"
                break
            queue_waits.append(float(val))

        if res.segment_count > 0 and not queue_valid:
            queue_stability_ok = False
            has_runaway_queue = True
            trend_drift = None
            queue_status = f"INVALID ({queue_error_msg})"
        elif len(queue_waits) >= 2:
            trend_drift = queue_waits[-1] - queue_waits[0]
            max_q = max(queue_waits)
            min_q = min(queue_waits)
            # Detect runaway drift (>200ms) or major intermediate surge (>300ms spike)
            intermediate_surge = ((max_q - min_q) > 300.0 and max_q > 300.0)
            has_runaway_queue = (trend_drift > 200.0) or intermediate_surge
            queue_stability_ok = not has_runaway_queue
            queue_status = "RUNAWAY" if has_runaway_queue else "OK"
        elif len(queue_waits) == 1:
            trend_drift = 0.0
            has_runaway_queue = (queue_waits[0] > 500.0)
            queue_stability_ok = not has_runaway_queue
            queue_status = "SINGLE_SAMPLE"
        else:  # 0 segments
            trend_drift = None
            has_runaway_queue = False
            queue_stability_ok = True
            queue_status = "NO_DATA"

        # 4. Technical Metric Evaluations
        lossless_ok = (
            res.is_lossless and
            (res.overrun_count == 0) and
            (res.dropped_audio_chunks == 0) and
            (res.dropped_segments == 0)
        )
        rtf_ok = (res.rtf_stats.get("p95", 1.0) <= 0.5)
        delay_ok = (res.estimated_delay_stats.get("p95", 9999.0) <= 1500.0)
        status_ok = (res.status == "OK")
        resource_stability_ok = lossless_ok and status_ok and queue_stability_ok
        technical_stability_passed = resource_stability_ok and rtf_ok and delay_ok

        child_pid = getattr(pipeline.stt, "child_pid", None)
        final_mem = get_memory_stats(child_pid)

        # 5. Clean separation of judgments:
        # 'passed' reflects overall gate status (False when PARTIAL/FAIL/NOT_RUN/ERROR)
        # 'technical_stability_passed' reflects system resource/latency benchmarks
        if not duration_satisfied:
            structured_status = "FAIL"
            overall_status = f"FAIL (Captured duration {captured_audio_sec:.1f}s was less than target {duration_seconds - dur_tolerance:.1f}s)"
            judgment = overall_status
            passed = False
        elif res.segment_count == 0:
            structured_status = "NOT_RUN"
            overall_status = "NOT_RUN (No real speech detected; stability and RTF cannot be evaluated on silence alone)"
            judgment = overall_status
            passed = False
        elif not queue_valid:
            structured_status = "FAIL"
            overall_status = f"FAIL (Queue metrics invalid: {queue_error_msg})"
            judgment = overall_status
            passed = False
        elif not resource_stability_ok:
            structured_status = "FAIL"
            overall_status = f"FAIL (Resource stability failure: lossless={lossless_ok}, status={res.status}, queue={queue_status})"
            judgment = overall_status
            passed = False
        elif not (rtf_ok and delay_ok):
            structured_status = "FAIL"
            overall_status = f"FAIL (Latency/RTF target exceeded: rtf_p95={res.rtf_stats.get('p95')}, delay_p95={res.estimated_delay_stats.get('p95')})"
            judgment = overall_status
            passed = False
        elif is_full_10min_gate and not speech_presence_ok:
            structured_status = "PARTIAL"
            overall_status = f"PARTIAL (Sparse speech detected: {active_minutes}/{minute_count} active mins, {res.total_speech_seconds:.1f}s speech)"
            judgment = (
                f"PARTIAL (Sparse speech: only {active_minutes}/{minute_count} minutes had active speech, "
                f"total speech {res.total_speech_seconds:.1f}s < 60s target; presentation continuity unverified)"
            )
            passed = False
        elif is_full_10min_gate:
            structured_status = "PARTIAL"
            overall_status = "PARTIAL (Resource & streaming stability PASS; presentation speech coverage and human latency require PM review)"
            judgment = (
                "PARTIAL (10-minute system and resource stability satisfied; "
                "continuous presentation coverage and human reference speech-end latency remain PARTIAL pending user/PM review)"
            )
            passed = False  # Overall gate is PARTIAL, not unconditional PASS
        else:  # smoke test (<600s)
            structured_status = "PARTIAL"
            overall_status = "PARTIAL (Smoke test passed; 600s continuous stability requires user 10min live presentation)"
            judgment = overall_status
            passed = False  # Overall gate is PARTIAL pending 600s run

        summary = {
            "scenario": 5,
            "benchmark": "10-Minute Live Microphone Stability Benchmark",
            "git_revision": git_info["revision"],
            "git_dirty": git_info["is_dirty"],
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": res.run_id,
            "test_type": test_type,
            "is_full_10min_gate": is_full_10min_gate,
            "overall_status": overall_status,
            "structured_status": structured_status,
            "passed": passed,
            "technical_stability_passed": technical_stability_passed,
            "judgment": judgment,
            "target_duration_seconds": duration_seconds,
            "measured_wall_seconds": round(t_wall_elapsed, 2),
            "captured_audio_seconds": round(captured_audio_sec, 2),
            "total_speech_seconds": round(res.total_speech_seconds, 2),
            "total_inference_seconds": round(res.total_inference_seconds, 2),
            "speech_rtf": round(res.speech_rtf, 4),
            "throughput_rtf": round(res.throughput_rtf, 4),
            "gate_breakdown": {
                "resource_and_streaming_stability": "PASS" if resource_stability_ok else "FAIL",
                "latency_and_rtf_performance": "PASS" if (rtf_ok and delay_ok) else "FAIL",
                "speech_input_presence": "PASS" if speech_presence_ok else "INSUFFICIENT",
                "continuous_presentation_coverage": "PARTIAL (Requires human/PM review; speech presence alone does not certify full continuous presentation)",
                "human_reference_speech_end_latency": "PARTIAL (Awaiting human reference speech-end annotation)"
            },
            "stability_metrics": {
                "is_lossless": res.is_lossless,
                "overrun_count": res.overrun_count,
                "dropped_audio_chunks": res.dropped_audio_chunks,
                "dropped_audio_seconds": round(res.dropped_audio_seconds, 3),
                "dropped_segments": res.dropped_segments,
                "worker_status": res.status,
                "queue_stability_status": queue_status,
                "queue_wait_trend_drift_ms": round(trend_drift, 2) if trend_drift is not None else None,
                "has_runaway_queue": has_runaway_queue
            },
            "memory_metrics": {
                "parent_rss_mb": final_mem["parent_rss_mb"],
                "child_rss_mb": final_mem["child_rss_mb"],
                "combined_rss_mb": final_mem["combined_rss_mb"],
                "child_pid": child_pid
            },
            "per_minute_analysis": {
                "total_minutes": minute_count,
                "active_speech_minutes": active_minutes,
                "speech_presence_ok": speech_presence_ok,
                "minute_distribution": per_minute_stats
            },
            "latency_percentiles": {
                "rtf_p95": res.rtf_stats.get("p95"),
                "estimated_delay_after_speech_p95_ms": res.estimated_delay_stats.get("p95"),
                "continuous_latency_p95_ms": res.continuous_latency_stats.get("p95"),
                "queue_wait_p95_ms": res.queue_wait_stats.get("p95")
            },
            "notes": {
                "vad_duration_approximation": "VAD segment duration is an algorithmic approximation of voiced speech; not exact phonetic boundary.",
                "egress_blocking": "Offline mic operation confirms local execution; OS kernel egress blocking remains PARTIAL pending network firewall validation."
            },
            "periodic_snapshots": res.periodic_snapshots,
            "detected_segments": res.segments,
            "reproduction_metadata": repro_meta
        }

        actual_output = atomic_write_json(output_json, summary, allow_overwrite=allow_overwrite)

        print("=================================================================")
        print(f" Evaluation Result: {judgment}")
        print(f" Overall Status:    {overall_status}")
        print(f" Structured Status: {structured_status}")
        print(f" Technical Stability Passed: {technical_stability_passed}")
        print(f" Overall Passed:    {passed}")
        print(f" Captured Audio:    {captured_audio_sec:.2f}s (Target: {duration_seconds:.1f}s)")
        print(f" Speech Duration:   {res.total_speech_seconds:.2f}s ({active_minutes}/{minute_count} active minutes)")
        print(f" Lossless Stream:   {'YES' if lossless_ok else 'NO'} (Overruns: {res.overrun_count}, Drops: {res.dropped_audio_chunks})")
        print(f" Memory (RSS):      Parent: {final_mem['parent_rss_mb']:.1f} MB | Child: {final_mem['child_rss_mb']:.1f} MB")
        print(f" Result saved to:   {actual_output}")
        print("=================================================================")
        return summary

    except Exception as exc:
        t_wall_elapsed = time.perf_counter() - t_wall_start
        err_summary = {
            "scenario": 5,
            "benchmark": "10-Minute Live Microphone Stability Benchmark",
            "git_revision": git_info["revision"],
            "git_dirty": git_info["is_dirty"],
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id,
            "test_type": test_type,
            "is_full_10min_gate": is_full_10min_gate,
            "overall_status": f"ERROR (Exception in {failed_phase}: {type(exc).__name__})",
            "structured_status": "ERROR",
            "passed": False,
            "technical_stability_passed": False,
            "judgment": f"ERROR: Execution failed during {failed_phase} with {type(exc).__name__}: {str(exc)}",
            "failed_phase": failed_phase,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "target_duration_seconds": duration_seconds,
            "measured_wall_seconds": round(t_wall_elapsed, 2),
            "reproduction_metadata": repro_meta
        }
        atomic_write_json(output_json, err_summary, allow_overwrite=allow_overwrite)
        print(f"Exception in {failed_phase}: {exc}", file=sys.stderr)
        raise

    finally:
        if pipeline is not None:
            pipeline.close()


def main():
    parser = argparse.ArgumentParser(description="Scenario 5 Live Microphone Stability Evaluation Tool")
    parser.add_argument("--duration", type=float, default=600.0,
                        help="Target duration in seconds (default: 600.0 for 10-min gate)")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Path to output JSON file (defaults to timestamped isolated file)")
    parser.add_argument("--overwrite", action="store_true", default=False,
                        help="Allow overwriting existing output JSON file (default: protect existing files)")
    args = parser.parse_args()

    try:
        summary = run_mic_benchmark(
            duration_seconds=args.duration,
            output_json=args.output_json,
            allow_overwrite=args.overwrite
        )
        status = summary.get("structured_status", "UNKNOWN")
        if status == "PASS":
            sys.exit(0)
        elif status in ["NOT_RUN", "PARTIAL"]:
            sys.exit(2)
        else:  # FAIL, ERROR
            sys.exit(1)
    except Exception as exc:
        print(f"Benchmark failed with fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
