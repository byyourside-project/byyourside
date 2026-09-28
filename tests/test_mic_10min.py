#!/usr/bin/env python3
"""
Scenario 5: 10-Minute Live Microphone Stability and Resource Benchmark.
Uses the unified SpeechPipeline.run_mic() path.

Evaluates against PM requirements:
  1. Actual captured audio duration (not merely the target setting duration).
  2. Per-minute speech distribution (ensures real continuous presentation, detects silence cheating).
  3. Lossless streaming: 0 capture overruns, 0 dropped audio chunks, 0 dropped speech segments.
  4. Memory stability: Separate Parent Process RSS vs Child Process RSS tracking across 10 minutes.
  5. Queue latency stability: Measures trend drift and per-minute queue wait distributions.
  6. Isolated execution run_id and timestamped output files to prevent log pollution.
"""

import argparse
import json
import os
import sys
import time
import uuid
from typing import Dict, Any, List, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig, AudioConfig, QueueConfig
from src.pipeline import SpeechPipeline, get_git_revision
from src.metrics import get_memory_stats

REQUIRED_STABILITY_SECONDS = 600.0


def analyze_per_minute_speech(
    segments: List[Dict[str, Any]],
    total_captured_sec: float,
    minute_count: int
) -> List[Dict[str, Any]]:
    """
    Analyzes detected speech segments into 1-minute bins (0~60s, 60~120s, etc.).
    Returns per-minute speech statistics to ensure continuous presentation.
    """
    bins = []
    for m in range(minute_count):
        m_start_sec = m * 60.0
        m_end_sec = min((m + 1) * 60.0, total_captured_sec)
        m_dur_sec = max(0.0, m_end_sec - m_start_sec)

        m_segments = []
        speech_sec_in_min = 0.0

        for seg in segments:
            seg_start_sec = seg.get("start_ms", 0.0) / 1000.0
            seg_end_sec = seg.get("end_ms", 0.0) / 1000.0

            # Overlap with this minute
            overlap_start = max(m_start_sec, seg_start_sec)
            overlap_end = min(m_end_sec, seg_end_sec)
            if overlap_end > overlap_start:
                speech_sec_in_min += (overlap_end - overlap_start)
                m_segments.append(seg)

        speech_ratio = (speech_sec_in_min / m_dur_sec) if m_dur_sec > 0 else 0.0
        bins.append({
            "minute_index": m + 1,
            "window_range_sec": f"{m_start_sec:.1f}s - {m_end_sec:.1f}s",
            "speech_seconds": round(speech_sec_in_min, 2),
            "speech_ratio": round(speech_ratio, 4),
            "segment_count": len(m_segments),
            "has_active_speech": speech_sec_in_min >= 3.0
        })
    return bins


def run_mic_benchmark(
    duration_seconds: float = 600.0,
    output_json: Optional[str] = None
) -> Dict[str, Any]:
    run_timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    run_uuid = uuid.uuid4().hex[:8]
    is_full_10min_gate = (duration_seconds >= REQUIRED_STABILITY_SECONDS)
    test_type = "10min_stability_gate" if is_full_10min_gate else "smoke_test"

    if output_json is None:
        prefix = "task_01_mic_10min" if is_full_10min_gate else "task_01_mic_smoke"
        output_json = f"logs/{prefix}_{run_timestamp}_{run_uuid}.json"

    run_id = f"mic_{'10m' if is_full_10min_gate else 'smoke'}_{run_timestamp}_{run_uuid}"

    print("=================================================================")
    print(f" Starting Scenario 5: Live Microphone Evaluation ({duration_seconds}s)")
    print(f" Test Type:       {test_type}")
    print(f" Execution ID:    {run_id}")
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
    try:
        pipeline = SpeechPipeline(config)
        pipeline.stt.warm_up(0.5)

        t_wall_start = time.perf_counter()
        res = pipeline.run_mic(
            duration_seconds=duration_seconds,
            run_id=run_id,
            snapshot_interval_sec=60.0
        )
        t_wall_elapsed = time.perf_counter() - t_wall_start

        # Audio capture duration verification
        captured_audio_sec = res.total_audio_seconds
        dur_tolerance = 2.0  # 2 seconds tolerance for audio ringbuffer startup/teardown
        duration_satisfied = (captured_audio_sec >= duration_seconds - dur_tolerance)

        # Per-minute speech distribution analysis
        minute_count = max(1, int(round(captured_audio_sec / 60.0)))
        per_minute_stats = analyze_per_minute_speech(res.segments, captured_audio_sec, minute_count)
        active_minutes = sum(1 for m in per_minute_stats if m["has_active_speech"])

        # Check for silence cheating (e.g. speaking 1 sentence and staying silent for 9 minutes)
        if is_full_10min_gate:
            continuous_speech_valid = (active_minutes >= 5) and (res.total_speech_seconds >= 60.0)
        else:
            continuous_speech_valid = (res.segment_count > 0)

        # Memory separation
        child_pid = getattr(pipeline.stt, "child_pid", None)
        final_mem = get_memory_stats(child_pid)

        # Queue wait drift
        queue_waits = [s["queue_wait_ms"] for s in res.segments if "queue_wait_ms" in s]
        if len(queue_waits) >= 2:
            trend_drift = queue_waits[-1] - queue_waits[0]
            has_runaway_queue = (trend_drift > 200.0)
        else:
            trend_drift = 0.0
            has_runaway_queue = False

        # Evaluation criteria
        lossless_ok = res.is_lossless and (res.overrun_count == 0) and (res.dropped_audio_chunks == 0) and (res.dropped_segments == 0)
        rtf_ok = (res.rtf_stats.get("p95", 1.0) <= 0.5)
        delay_ok = (res.estimated_delay_stats.get("p95", 9999.0) <= 1500.0)
        status_ok = (res.status == "OK")

        if not duration_satisfied:
            judgment = f"FAIL (Captured duration {captured_audio_sec:.1f}s was less than target {duration_seconds:.1f}s)"
            passed = False
        elif res.segment_count == 0:
            judgment = "NOT_RUN (No real speech detected; stability and RTF cannot be evaluated on silence alone)"
            passed = False
        elif is_full_10min_gate and not continuous_speech_valid:
            judgment = (f"PARTIAL (Silence/cheating detected: only {active_minutes}/10 minutes had active speech, "
                        f"total speech {res.total_speech_seconds:.1f}s < 60s target)")
            passed = False
        elif lossless_ok and rtf_ok and delay_ok and status_ok and not has_runaway_queue:
            if is_full_10min_gate:
                judgment = "PASS (10-minute continuous presentation stability validated)"
                passed = True
            else:
                judgment = "SMOKE_PASS (Smoke test passed; 600s continuous stability remains NOT_RUN/PARTIAL)"
                passed = True
        else:
            judgment = f"FAIL (lossless={lossless_ok}, rtf={rtf_ok}, delay={delay_ok}, queue={not has_runaway_queue})"
            passed = False

        gate_10min_status = "PASS" if (is_full_10min_gate and passed) else (
            "PARTIAL (Smoke test executed; 600s continuous stability requires user 10min live presentation)"
            if not is_full_10min_gate else "FAIL"
        )

        summary = {
            "scenario": 5,
            "benchmark": "10-Minute Live Microphone Stability Benchmark",
            "git_revision": get_git_revision(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": res.run_id,
            "test_type": test_type,
            "is_full_10min_gate": is_full_10min_gate,
            "overall_status": gate_10min_status,
            "passed": passed,
            "judgment": judgment,
            "target_duration_seconds": duration_seconds,
            "measured_wall_seconds": round(t_wall_elapsed, 2),
            "captured_audio_seconds": round(captured_audio_sec, 2),
            "total_speech_seconds": round(res.total_speech_seconds, 2),
            "total_inference_seconds": round(res.total_inference_seconds, 2),
            "speech_rtf": round(res.speech_rtf, 4),
            "throughput_rtf": round(res.throughput_rtf, 4),
            "stability_metrics": {
                "is_lossless": res.is_lossless,
                "overrun_count": res.overrun_count,
                "dropped_audio_chunks": res.dropped_audio_chunks,
                "dropped_audio_seconds": round(res.dropped_audio_seconds, 3),
                "dropped_segments": res.dropped_segments,
                "worker_status": res.status,
                "queue_wait_trend_drift_ms": round(trend_drift, 2)
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
                "continuous_speech_valid": continuous_speech_valid,
                "minute_distribution": per_minute_stats
            },
            "latency_percentiles": {
                "rtf_p95": res.rtf_stats.get("p95"),
                "estimated_delay_after_speech_p95_ms": res.estimated_delay_stats.get("p95"),
                "continuous_latency_p95_ms": res.continuous_latency_stats.get("p95"),
                "queue_wait_p95_ms": res.queue_wait_stats.get("p95")
            },
            "periodic_snapshots": res.periodic_snapshots,
            "detected_segments": res.segments
        }

        os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        print("=================================================================")
        print(f" Evaluation Result: {judgment}")
        print(f" Captured Audio:    {captured_audio_sec:.2f}s (Target: {duration_seconds:.1f}s)")
        print(f" Speech Duration:   {res.total_speech_seconds:.2f}s ({active_minutes}/{minute_count} active minutes)")
        print(f" Lossless Stream:   {'YES' if lossless_ok else 'NO'} (Overruns: {res.overrun_count}, Drops: {res.dropped_audio_chunks})")
        print(f" Memory (RSS):      Parent: {final_mem['parent_rss_mb']:.1f} MB | Child: {final_mem['child_rss_mb']:.1f} MB")
        print(f" 10-Min Gate:       {gate_10min_status}")
        print(f" Result saved to:   {output_json}")
        print("=================================================================")
        return summary

    finally:
        if pipeline is not None:
            pipeline.close()


def main():
    parser = argparse.ArgumentParser(description="Scenario 5 Live Microphone Stability Evaluation Tool")
    parser.add_argument("--duration", type=float, default=600.0,
                        help="Target duration in seconds (default: 600.0 for 10-min gate)")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Path to output JSON file (defaults to timestamped isolated file)")
    args = parser.parse_args()

    run_mic_benchmark(duration_seconds=args.duration, output_json=args.output_json)


if __name__ == "__main__":
    main()
