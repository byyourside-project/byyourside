#!/usr/bin/env python3
"""
Scenario 5 (Revision 01): 10-minute Live Microphone Stability and Resource Benchmark.
Uses the unified SpeechPipeline.run_mic() path.
Evaluates:
  1. Zero worker crashes or timeouts
  2. Zero capture overruns
  3. Zero dropped audio chunks
  4. Zero dropped speech segments (lossless)
  5. No queue latency drift
  6. Peak vs Current RSS memory stability
Saves to logs/task_01_mic_10min_revised_result.json.
"""
import json
import os
import sys
import time
from typing import Dict, Any

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig, AudioConfig, QueueConfig
from src.pipeline import SpeechPipeline, get_git_revision

REQUIRED_STABILITY_SECONDS = 600.0

def run_mic_benchmark(duration_seconds: float = 600.0) -> Dict[str, Any]:
    is_full_10min_gate = (duration_seconds >= REQUIRED_STABILITY_SECONDS)
    test_type = "10min_stability_gate" if is_full_10min_gate else "smoke_test"
    output_json = "logs/task_01_mic_10min_rev3_result.json" if is_full_10min_gate else "logs/task_01_mic_smoke_rev3_result.json"

    print("=================================================================")
    print(f" Starting Scenario 5 (Revision 03): Unified SpeechPipeline Mic ({duration_seconds}s)")
    print(f" Test Type: {test_type} | Output: {output_json}")
    print("=================================================================")

    config = PipelineConfig(
        vad=VadConfig(
            min_silence_duration=0.5,
            max_speech_duration=4.0,
            hard_max_speech_duration=4.0
        ),
        stt=SttConfig(num_threads=4),
        audio=AudioConfig(device_sample_rate=48000, target_sample_rate=16000),
        queue=QueueConfig(max_audio_queue_size=300, max_segment_queue_size=100)
    )

    pipeline = SpeechPipeline(config)
    pipeline.stt.warm_up(0.5)

    run_id = f"mic_{'10m' if is_full_10min_gate else 'smoke'}_rev3_{int(time.time())}"

    # Run the unified pipeline
    res = pipeline.run_mic(
        duration_seconds=duration_seconds,
        run_id=run_id,
        snapshot_interval_sec=60.0
    )

    # Revision 03 Evaluation:
    # F3 [P2]: Strictly verify per-segment queue_wait_ms. Do not default to 0.0.
    missing_queue_wait = any("queue_wait_ms" not in s for s in res.segments)

    if missing_queue_wait:
        queue_wait_valid = False
        trend_drift = 0.0
        judgment = "FAIL (Validation Error: queue_wait_ms field missing in segment results)"
        passed = False
    elif res.segment_count == 0:
        queue_wait_valid = True
        trend_drift = 0.0
        judgment = "NOT_RUN (No real speech utterances detected; latency drift & speech RTF cannot be validated on silence alone)"
        passed = False
    else:
        queue_wait_valid = True
        queue_waits = [s["queue_wait_ms"] for s in res.segments]
        trend_drift = (queue_waits[-1] - queue_waits[0]) if len(queue_waits) >= 2 else 0.0
        has_runaway_queue = trend_drift > 200.0

        smoke_passed = (
            res.is_lossless and
            res.overrun_count == 0 and
            res.dropped_audio_chunks == 0 and
            res.dropped_segments == 0 and
            res.status == "OK" and
            res.rtf_stats["p95"] <= 0.5 and
            res.estimated_delay_stats["p95"] <= 1500.0 and
            not has_runaway_queue
        )

        if not is_full_10min_gate:
            passed = smoke_passed
            judgment = "SMOKE_PASS (600s stability requirement remains NOT_RUN/PARTIAL)" if smoke_passed else "FAIL"
        else:
            passed = smoke_passed
            judgment = "PASS" if passed else "FAIL"

    gate_10min_status = "PASS" if (is_full_10min_gate and passed) else (
        "PARTIAL (Short smoke test executed; 600s stability requirement remains NOT_RUN/PARTIAL)"
        if not is_full_10min_gate else "FAIL"
    )

    summary = {
        "scenario": 5,
        "revision": "03",
        "git_revision": get_git_revision(),
        "name": f"Live Microphone {'10-minute Stability Gate' if is_full_10min_gate else 'Smoke Test'} (Unified Pipeline)",
        "test_type": test_type,
        "is_full_10min_gate": is_full_10min_gate,
        "gate_10min_status": gate_10min_status,
        "gate_10min_passed": (is_full_10min_gate and passed),
        "judgment": judgment,
        "passed": passed,
        "run_id": res.run_id,
        "target_duration_seconds": duration_seconds,
        "total_audio_captured_seconds": round(res.total_audio_seconds, 2),
        "total_speech_seconds": round(res.total_speech_seconds, 2),
        "total_inference_seconds": round(res.total_inference_seconds, 2),
        "speech_rtf": round(res.speech_rtf, 4),
        "throughput_rtf": round(res.throughput_rtf, 4),
        "is_lossless": res.is_lossless,
        "overrun_count": res.overrun_count,
        "dropped_audio_chunks": res.dropped_audio_chunks,
        "dropped_audio_seconds": round(res.dropped_audio_seconds, 3),
        "dropped_segments": res.dropped_segments,
        "dropped_items": res.dropped_items,
        "total_segments_detected": res.segment_count,
        "queue_wait_trend_drift_ms": round(trend_drift, 2) if queue_wait_valid else None,
        "queue_wait_valid": queue_wait_valid,
        "current_rss_mb": res.current_rss_mb,
        "peak_rss_mb": res.peak_rss_mb,
        "rtf_stats": res.rtf_stats,
        "estimated_delay_after_speech_stats": res.estimated_delay_stats,
        "continuous_latency_stats": res.continuous_latency_stats,
        "queue_wait_stats": res.queue_wait_stats,
        "periodic_snapshots": res.periodic_snapshots,
        "detected_segments": res.segments
    }

    os.makedirs("logs", exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=================================================================")
    print(f" Scenario 5 Result: {judgment}")
    print(f" Captured: {res.total_audio_seconds:.2f}s | Overruns: {res.overrun_count} | Chunks Dropped: {res.dropped_audio_chunks} | Segments Dropped: {res.dropped_segments}")
    print(f" Memory: Current RSS {res.current_rss_mb:.1f} MB | Peak RSS {res.peak_rss_mb:.1f} MB")
    print(f" 10-Minute Gate Status: {gate_10min_status}")
    print(f" Summary saved to {output_json}")
    print("=================================================================")
    return summary

if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    run_mic_benchmark(dur)
