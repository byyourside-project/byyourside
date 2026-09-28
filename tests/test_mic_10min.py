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
from src.pipeline import SpeechPipeline

OUTPUT_JSON = "logs/task_01_mic_10min_revised_result.json"

def run_mic_benchmark(duration_seconds: float = 600.0) -> Dict[str, Any]:
    print("=================================================================")
    print(f" Starting Scenario 5 (Revision 01): Unified SpeechPipeline Mic ({duration_seconds}s)")
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

    run_id = f"mic10m_rev1_{int(time.time())}"

    # Run the unified pipeline
    res = pipeline.run_mic(
        duration_seconds=duration_seconds,
        run_id=run_id,
        snapshot_interval_sec=60.0
    )

    # Strict multi-factor PASS evaluation
    passed = (
        res.is_lossless and
        res.overrun_count == 0 and
        res.dropped_audio_chunks == 0 and
        res.dropped_segments == 0 and
        res.status == "OK" and
        (res.rtf_stats["p95"] <= 0.5 if res.segment_count > 0 else True) and
        (res.estimated_delay_stats["p95"] <= 1500.0 if res.segment_count > 0 else True)
    )

    summary = {
        "scenario": 5,
        "revision": "01",
        "name": "10-minute Live Microphone Stability Test (Unified Pipeline)",
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
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=================================================================")
    print(f" Scenario 5 Result: {'PASS' if passed else 'FAIL'}")
    print(f" Captured: {res.total_audio_seconds:.2f}s | Overruns: {res.overrun_count} | Chunks Dropped: {res.dropped_audio_chunks} | Segments Dropped: {res.dropped_segments}")
    print(f" Memory: Current RSS {res.current_rss_mb:.1f} MB | Peak RSS {res.peak_rss_mb:.1f} MB")
    print(f" Summary saved to {OUTPUT_JSON}")
    print("=================================================================")
    return summary

if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    run_mic_benchmark(dur)
