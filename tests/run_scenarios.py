#!/usr/bin/env python3
"""
Test runner for Task 01 Required Scenarios (Scenarios 1 to 6).
Logs raw metrics to logs/scenario_results.json.
"""
import json
import os
import socket
import sys
import time
from typing import Dict, Any, List
import numpy as np
import scipy.io.wavfile as wavfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig
from src.pipeline import SpeechPipeline
from src.vad import VadProcessor
from src.stt import SttEngine
from src.metrics import compute_cer, normalize_text, get_process_memory_mb

RESULTS_FILE = "logs/task_01_scenario_results.json"

def run_scenario_1() -> Dict[str, Any]:
    """
    Scenario 1: Short Korean utterance and final segment flush.
    Ensures that an utterance truncated without trailing silence is properly emitted via flush.
    """
    print("\n--- Running Scenario 1: Short utterance & final flush ---")
    sr, raw = wavfile.read("models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav")
    samples = raw.astype(np.float32) / 32768.0

    # Truncate mid-speech at 2.0s without trailing silence
    truncated = samples[:int(sr * 2.0)]

    vad = VadProcessor(VadConfig(min_silence_duration=0.5))
    stt = SttEngine(SttConfig())

    window = 512
    segments = []
    for i in range(0, len(truncated), window):
        chunk = truncated[i:i + window]
        segments.extend(vad.process_chunk(chunk))

    # Before flush, speech may be buffered because min_silence has not elapsed
    segs_before_flush = len(segments)

    # Flush
    flushed = vad.flush()
    segments.extend(flushed)

    transcripts = []
    for seg in segments:
        text, _ = stt.transcribe(seg.samples, sr)
        transcripts.append(text)

    full_text = " ".join(transcripts)
    passed = len(segments) > 0 and ("생각" in full_text or "조금" in full_text)

    res = {
        "scenario": 1,
        "name": "Short utterance & final flush",
        "passed": passed,
        "segments_before_flush": segs_before_flush,
        "segments_after_flush": len(segments),
        "transcript": full_text,
        "flush_reason": segments[-1].endpoint_reason if segments else None
    }
    print(f"Scenario 1 Result: {'PASS' if passed else 'FAIL'} | Transcript: '{full_text}'")
    return res

def run_scenario_2() -> Dict[str, Any]:
    """
    Scenario 2: 60 seconds silence & low-level environmental noise.
    Verifies that no false triggers or hallucinations occur during 60s of non-speech.
    """
    print("\n--- Running Scenario 2: 60s silence & ambient noise ---")
    sr = 16000
    duration_sec = 60.0
    num_samples = int(sr * duration_sec)

    # Create 30s absolute silence + 30s low-level white noise (approx -50 dBFS)
    silence = np.zeros(num_samples // 2, dtype=np.float32)
    noise = np.random.normal(0, 0.003, num_samples // 2).astype(np.float32)
    audio = np.concatenate([silence, noise])

    vad = VadProcessor(VadConfig(min_silence_duration=0.5, threshold=0.5))
    stt = SttEngine(SttConfig())

    window = 512
    detected_segments = []
    for i in range(0, len(audio), window):
        chunk = audio[i:i + window]
        ready = vad.process_chunk(chunk)
        detected_segments.extend(ready)
    detected_segments.extend(vad.flush())

    false_transcriptions = []
    for seg in detected_segments:
        text, _ = stt.transcribe(seg.samples, sr)
        if text.strip():
            false_transcriptions.append(text.strip())

    passed = len(detected_segments) == 0 and len(false_transcriptions) == 0

    res = {
        "scenario": 2,
        "name": "60s silence & ambient noise",
        "passed": passed,
        "audio_duration_seconds": duration_sec,
        "detected_segments_count": len(detected_segments),
        "false_transcriptions": false_transcriptions
    }
    print(f"Scenario 2 Result: {'PASS' if passed else 'FAIL'} | Segments detected: {len(detected_segments)}")
    return res

def run_scenario_3() -> Dict[str, Any]:
    """
    Scenario 3: Continuous speech (>30s) with short pauses (0.2s) less than min_silence (0.5s).
    Forces VAD to split at max_speech_duration (4.0s) and measures total end-to-end latency.
    """
    print("\n--- Running Scenario 3: Continuous speech (>30s) & max_duration splitting ---")
    sr, raw = wavfile.read("models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav")
    samples = raw.astype(np.float32) / 32768.0

    # Extract voiced region (0.8s to 3.7s, length ~2.9s)
    speech_unit = samples[int(0.8 * sr):int(3.7 * sr)]
    short_pause = np.zeros(int(0.2 * sr), dtype=np.float32)  # 200ms pause (< 500ms min_silence)

    # Chain to create ~34 seconds of continuous presentation speech
    chain = []
    for _ in range(11):
        chain.append(speech_unit)
        chain.append(short_pause)
    continuous_audio = np.concatenate(chain)
    total_dur_sec = len(continuous_audio) / float(sr)

    # Save to temp wav
    temp_wav = "logs/temp_continuous_speech.wav"
    wavfile.write(temp_wav, sr, (continuous_audio * 32767).astype(np.int16))

    config = PipelineConfig(
        vad=VadConfig(min_silence_duration=0.5, max_speech_duration=4.0),
        stt=SttConfig(num_threads=4)
    )
    pipeline = SpeechPipeline(config)
    res = pipeline.run_replay(temp_wav, speed=1.0, run_id="scenario3_replay")

    # Metrics for continuous speech:
    # 1. Total latency from segment start to result emit
    total_segment_latencies = []
    for seg in res.segments:
        # Segment duration + STT inference time
        total_segment_latencies.append(seg["duration_ms"] + seg["infer_ms"])

    p95_total_lat = float(np.percentile(total_segment_latencies, 95)) if total_segment_latencies else 0.0
    passed = p95_total_lat <= 5500.0 and res.segment_count >= 8  # 5.5s PM target

    result_data = {
        "scenario": 3,
        "name": "Continuous speech (>30s) max_duration splitting",
        "passed": passed,
        "total_audio_seconds": round(total_dur_sec, 2),
        "segment_count": res.segment_count,
        "p95_total_latency_ms": round(p95_total_lat, 1),
        "target_p95_ms": 5500.0,
        "rtf_p95": res.rtf_stats["p95"],
        "delay_after_speech_p95_ms": res.delay_stats["p95"],
    }
    print(f"Scenario 3 Result: {'PASS' if passed else 'FAIL'} | Segments: {res.segment_count} | p95 Total Latency: {p95_total_lat:.1f}ms")
    return result_data

def run_scenario_4() -> Dict[str, Any]:
    """
    Scenario 4: Boundary loss and duplication across split segments.
    Analyzes whether words spanning across the 4-second forced boundary are lost or duplicated.
    """
    print("\n--- Running Scenario 4: Boundary loss and duplication ---")
    sr, raw = wavfile.read("models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav")
    samples = raw.astype(np.float32) / 32768.0

    # Repeat audio twice with no silence in between
    doubled_audio = np.concatenate([samples, samples])
    temp_wav = "logs/temp_doubled.wav"
    wavfile.write(temp_wav, sr, (doubled_audio * 32767).astype(np.int16))

    config = PipelineConfig(
        vad=VadConfig(min_silence_duration=0.5, max_speech_duration=4.0),
        stt=SttConfig(num_threads=4)
    )
    pipeline = SpeechPipeline(config)
    res = pipeline.run_wav_direct(temp_wav, run_id="scenario4_boundary")

    full_hypothesis = " ".join([s["text"] for s in res.segments])
    norm_hyp = normalize_text(full_hypothesis)

    ref_single = "조금만 생각을 하면서 살면 훨씬 편할 거야"
    ref_doubled = f"{ref_single} {ref_single}"
    cer_res = compute_cer(ref_doubled, full_hypothesis)

    result_data = {
        "scenario": 4,
        "name": "Boundary loss and duplication",
        "passed": cer_res["cer"] <= 0.15,
        "segment_count": len(res.segments),
        "reference": ref_doubled,
        "hypothesis": full_hypothesis,
        "cer": round(cer_res["cer"], 4),
        "distance": cer_res["distance"],
        "substitutions": cer_res["substitutions"],
        "deletions": cer_res["deletions"],
        "insertions": cer_res["insertions"],
    }
    print(f"Scenario 4 Result: {'PASS' if result_data['passed'] else 'FAIL'} | CER: {cer_res['cer']*100:.2f}% | Hyp: '{full_hypothesis}'")
    return result_data

def run_scenario_6() -> Dict[str, Any]:
    """
    Scenario 6: Completely offline execution with network calls blocked.
    Blocks socket creation/connection at runtime and verifies local STT inference succeeds.
    """
    print("\n--- Running Scenario 6: Offline execution (network blocked) ---")
    orig_connect = socket.socket.connect

    def blocked_connect(self, *args, **kwargs):
        raise OSError("Scenario 6: Network connection blocked for offline test!")

    socket.socket.connect = blocked_connect

    try:
        pipeline = SpeechPipeline()
        wav_path = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"
        res = pipeline.run_wav_direct(wav_path, run_id="scenario6_offline")

        text = res.segments[0]["text"] if res.segments else ""
        passed = ("생각" in text or "조금" in text) and res.cumulative_rtf < 0.5
        err = None
    except Exception as e:
        passed = False
        err = str(e)
    finally:
        socket.socket.connect = orig_connect

    result_data = {
        "scenario": 6,
        "name": "Offline execution (network blocked)",
        "passed": passed,
        "error": err,
        "transcript": text if passed else None,
        "rtf": res.cumulative_rtf if passed else None
    }
    print(f"Scenario 6 Result: {'PASS' if passed else 'FAIL'} | Offline transcript: '{text}'")
    return result_data

def main():
    print("=================================================================")
    print(" Running Task 01 Required Scenarios (1, 2, 3, 4, 6)")
    print(" Note: Scenario 5 (10-min mic test) is executed separately.")
    print("=================================================================")

    results = {}
    results["scenario_1"] = run_scenario_1()
    results["scenario_2"] = run_scenario_2()
    results["scenario_3"] = run_scenario_3()
    results["scenario_4"] = run_scenario_4()
    results["scenario_6"] = run_scenario_6()

    os.makedirs("logs", exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n=================================================================")
    print(f" Scenarios completed. Saved summary to {RESULTS_FILE}")
    print("=================================================================")

if __name__ == "__main__":
    main()
