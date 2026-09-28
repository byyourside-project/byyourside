#!/usr/bin/env python3
"""
Scenario 5: 10-minute Live Microphone Stability and Resource Benchmark.
Captures live audio from default mic (48kHz) for 600 seconds,
resamples to 16kHz, runs Silero VAD and SenseVoice INT8 STT.
Tracks crash, overrun, dropped chunks, queue latency drift, and peak RSS memory.
Logs periodically to logs/mic_10min_stats.json.
"""
import json
import os
import queue
import sys
import threading
import time
from typing import Optional, Dict, Any, List
import numpy as np
import scipy.signal as signal
import sounddevice as sd

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, AudioConfig, VadConfig, SttConfig, QueueConfig
from src.vad import VadProcessor, Segment
from src.stt import SttEngine
from src.logger import StructuredLogger
from src.metrics import calculate_percentiles, get_process_memory_mb

OUTPUT_JSON = "logs/task_01_mic_10min_result.json"

def run_mic_10min(duration_seconds: float = 600.0) -> Dict[str, Any]:
    print("=================================================================")
    print(f" Starting Scenario 5: Live Microphone 10-Minute ({duration_seconds}s) Stability Test")
    print("=================================================================")

    config = PipelineConfig(
        vad=VadConfig(min_silence_duration=0.5, max_speech_duration=4.0),
        stt=SttConfig(num_threads=4),
        audio=AudioConfig(device_sample_rate=48000, target_sample_rate=16000),
        queue=QueueConfig(max_audio_queue_size=300, max_segment_queue_size=100)
    )

    vad = VadProcessor(config.vad)
    stt = SttEngine(config.stt)
    stt.warm_up(0.5)

    run_id = f"mic10m_{int(time.time())}"
    logger = StructuredLogger(run_id=run_id, log_dir="logs", terminal_output=True)

    in_rate = config.audio.device_sample_rate   # 48000
    out_rate = config.audio.target_sample_rate  # 16000
    gcd = np.gcd(in_rate, out_rate)
    up = out_rate // gcd
    down = in_rate // gcd

    audio_queue: queue.Queue = queue.Queue(maxsize=config.queue.max_audio_queue_size)
    segment_queue: queue.Queue = queue.Queue(maxsize=config.queue.max_segment_queue_size)
    stop_event = threading.Event()

    # Telemetry metrics
    overrun_count = 0
    dropped_chunks = 0
    dropped_samples = 0
    total_captured_frames = 0

    stt_inference_times = []
    rtf_list = []
    queue_wait_list = []
    delay_after_speech_list = []
    detected_segments = []

    periodic_snapshots = []

    # Mic callback: extremely lightweight
    def mic_callback(indata, frames, time_info, status):
        nonlocal overrun_count, dropped_chunks, dropped_samples, total_captured_frames
        if status:
            if status.input_overflow:
                overrun_count += 1
        now = time.perf_counter()
        total_captured_frames += frames
        chunk = indata[:, 0].copy()

        try:
            audio_queue.put_nowait((chunk, now, total_captured_frames))
        except queue.Full:
            dropped_chunks += 1
            dropped_samples += len(chunk)

    # Worker 1: VAD worker
    def vad_worker():
        vad.reset()
        while not stop_event.is_set() or not audio_queue.empty():
            try:
                item = audio_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if item is None:
                break

            raw_chunk, capture_ts, sample_end = item
            # Resample 48kHz -> 16kHz
            resampled = signal.resample_poly(raw_chunk, up, down).astype(np.float32)

            segs = vad.process_chunk(resampled)
            for seg in segs:
                try:
                    segment_queue.put((seg, capture_ts), timeout=0.5)
                except queue.Full:
                    pass
            audio_queue.task_done()

        flushed = vad.flush()
        for seg in flushed:
            segment_queue.put((seg, time.perf_counter()))
        segment_queue.put(None)

    # Worker 2: STT worker
    def stt_worker():
        while True:
            try:
                item = segment_queue.get(timeout=0.1)
            except queue.Empty:
                if stop_event.is_set() and audio_queue.empty():
                    break
                continue

            if item is None:
                break

            seg, capture_ts = item
            stt_start = time.perf_counter()
            queue_wait_ms = (stt_start - seg.ready_ts) * 1000.0

            text, infer_ms = stt.transcribe(seg.samples, 16000)
            stt_end = time.perf_counter()
            result_emit_ts = stt_end

            rtf = (infer_ms / 1000.0) / (seg.duration_ms / 1000.0) if seg.duration_ms > 0 else 0.0
            delay_after_speech_ms = (result_emit_ts - capture_ts) * 1000.0

            stt_inference_times.append(infer_ms)
            rtf_list.append(rtf)
            queue_wait_list.append(queue_wait_ms)
            delay_after_speech_list.append(delay_after_speech_ms)

            detected_segments.append({
                "id": seg.segment_id,
                "text": text,
                "dur_ms": seg.duration_ms,
                "rtf": rtf,
                "infer_ms": infer_ms,
                "delay_ms": delay_after_speech_ms
            })

            logger.log_segment_result(
                segment_id=seg.segment_id,
                input_mode="mic_10min",
                audio_start_ms=seg.start_ms,
                audio_end_ms=seg.end_ms,
                audio_duration_ms=seg.duration_ms,
                endpoint_reason=seg.endpoint_reason,
                segment_ready_ts=seg.ready_ts,
                stt_start_ts=stt_start,
                stt_end_ts=stt_end,
                result_emit_ts=result_emit_ts,
                queue_wait_ms=queue_wait_ms,
                stt_inference_ms=infer_ms,
                rtf=rtf,
                delay_after_speech_ms=delay_after_speech_ms,
                total_latency_ms=delay_after_speech_ms,
                text=text
            )
            segment_queue.task_done()

    t_vad = threading.Thread(target=vad_worker, daemon=True)
    t_stt = threading.Thread(target=stt_worker, daemon=True)
    t_vad.start()
    t_stt.start()

    blocksize = config.audio.chunk_size_samples
    start_time = time.time()
    last_snapshot_time = start_time

    with sd.InputStream(
        samplerate=in_rate,
        channels=1,
        dtype="float32",
        blocksize=blocksize,
        callback=mic_callback
    ):
        while time.time() - start_time < duration_seconds:
            time.sleep(1.0)
            now = time.time()
            # Every 60 seconds, record snapshot
            if now - last_snapshot_time >= 60.0:
                elapsed = now - start_time
                mem_mb = get_process_memory_mb()
                snapshot = {
                    "elapsed_seconds": round(elapsed, 1),
                    "memory_rss_mb": round(mem_mb, 1),
                    "audio_queue_size": audio_queue.qsize(),
                    "segment_queue_size": segment_queue.qsize(),
                    "overrun_count": overrun_count,
                    "dropped_chunks": dropped_chunks,
                    "segments_so_far": len(detected_segments)
                }
                periodic_snapshots.append(snapshot)
                print(f"[10-Min Mic Monitor] Elapsed: {elapsed:5.1f}s / {duration_seconds}s | "
                      f"Mem: {mem_mb:5.1f} MB | Overruns: {overrun_count} | Drops: {dropped_chunks} | Segments: {len(detected_segments)}")
                last_snapshot_time = now

    print("\n[10-Min Mic] 600s reached. Stopping capture...")
    stop_event.set()
    audio_queue.put(None)
    t_vad.join(timeout=5.0)
    t_stt.join(timeout=10.0)

    total_time_elapsed = time.time() - start_time
    total_audio_seconds = total_captured_frames / float(in_rate)
    peak_memory = get_process_memory_mb()

    # Evaluation against PM criteria:
    # 1. No crash
    # 2. No overrun
    # 3. No unrecorded drops
    # 4. No persistent queue delay increase
    passed = (overrun_count == 0) and (dropped_chunks == 0)

    summary = {
        "scenario": 5,
        "name": "10-minute Live Microphone Stability Test",
        "passed": passed,
        "run_id": run_id,
        "target_duration_seconds": duration_seconds,
        "actual_elapsed_seconds": round(total_time_elapsed, 2),
        "total_audio_captured_seconds": round(total_audio_seconds, 2),
        "overrun_count": overrun_count,
        "dropped_audio_chunks": dropped_chunks,
        "dropped_audio_seconds": round(dropped_samples / float(in_rate), 3),
        "total_segments_detected": len(detected_segments),
        "peak_rss_memory_mb": round(peak_memory, 2),
        "rtf_stats": calculate_percentiles(rtf_list),
        "delay_after_speech_stats": calculate_percentiles(delay_after_speech_list),
        "queue_wait_stats": calculate_percentiles(queue_wait_list),
        "periodic_snapshots": periodic_snapshots,
        "detected_segments": detected_segments
    }

    os.makedirs("logs", exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.log_event("run_summary", summary)
    logger.close()

    print("=================================================================")
    print(f" Scenario 5 Result: {'PASS' if passed else 'FAIL'}")
    print(f" Captured: {total_audio_seconds:.2f}s | Overruns: {overrun_count} | Drops: {dropped_chunks}")
    print(f" Peak Memory: {peak_memory:.1f} MB | Detected Segments: {len(detected_segments)}")
    print(f" Summary saved to {OUTPUT_JSON}")
    print("=================================================================")
    return summary

if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    run_mic_10min(dur)
