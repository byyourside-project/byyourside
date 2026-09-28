#!/usr/bin/env python3
"""
CLI entry point for Task 01: Environment and Korean VAD/STT PoC.
"""
import argparse
import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import PipelineConfig, VadConfig, SttConfig, AudioConfig, QueueConfig
from src.pipeline import SpeechPipeline

def main():
    parser = argparse.ArgumentParser(description="Task 01 - Korean VAD/STT PoC Pipeline")
    parser.add_argument("--mode", choices=["wav", "replay", "mic"], default="wav",
                        help="Execution mode: direct wav, simulated real-time replay, or live mic")
    parser.add_argument("--wav", type=str, default="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav",
                        help="Path to WAV file for wav or replay mode")
    parser.add_argument("--mic-duration", type=float, default=10.0,
                        help="Recording duration in seconds for mic mode")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Playback speed multiplier for replay mode (default: 1.0)")
    parser.add_argument("--min-silence", type=float, default=0.5,
                        help="VAD min silence duration in seconds (default: 0.5)")
    parser.add_argument("--max-speech", type=float, default=4.0,
                        help="VAD max speech duration in seconds (default: 4.0)")
    parser.add_argument("--threads", type=int, default=4,
                        help="Number of threads for STT decoder (default: 4)")
    parser.add_argument("--no-itn", action="store_true",
                        help="Disable Inverse Text Normalization (ITN)")
    parser.add_argument("--run-id", type=str, default=None,
                        help="Custom run ID for logging")

    args = parser.parse_args()

    config = PipelineConfig(
        vad=VadConfig(
            min_silence_duration=args.min_silence,
            max_speech_duration=args.max_speech,
        ),
        stt=SttConfig(
            num_threads=args.threads,
            use_itn=not args.no_itn,
        ),
        audio=AudioConfig(),
        queue=QueueConfig()
    )

    print("=================================================================")
    print(" Task 01: Korean VAD + SenseVoice STT PoC")
    print(f" Mode: {args.mode.upper()}")
    print(f" VAD: Silero (min_silence={args.min_silence}s, max_speech={args.max_speech}s, 1 thread)")
    print(f" STT: SenseVoice INT8 (threads={args.threads}, ITN={not args.no_itn})")
    print("=================================================================")

    pipeline = SpeechPipeline(config)

    # Warm-up once
    pipeline.stt.warm_up(0.5)

    if args.mode == "wav":
        if not os.path.exists(args.wav):
            print(f"Error: WAV file not found at {args.wav}", file=sys.stderr)
            sys.exit(1)
        res = pipeline.run_wav_direct(args.wav, run_id=args.run_id)
    elif args.mode == "replay":
        if not os.path.exists(args.wav):
            print(f"Error: WAV file not found at {args.wav}", file=sys.stderr)
            sys.exit(1)
        res = pipeline.run_replay(args.wav, speed=args.speed, run_id=args.run_id)
    elif args.mode == "mic":
        res = pipeline.run_mic(duration_seconds=args.mic_duration, run_id=args.run_id)

    print("-----------------------------------------------------------------")
    print(" Summary:")
    print(f"  Run ID: {res.run_id}")
    print(f"  Total Audio: {res.total_audio_seconds:.2f}s | Infer Time: {res.total_inference_seconds:.2f}s | Cumulative RTF: {res.cumulative_rtf:.4f}")
    print(f"  Segments: {res.segment_count}")
    print(f"  RTF (p50 / p95 / max): {res.rtf_stats['p50']:.4f} / {res.rtf_stats['p95']:.4f} / {res.rtf_stats['max']:.4f}")
    print(f"  Delay (p50 / p95 / max): {res.delay_stats['p50']:.1f}ms / {res.delay_stats['p95']:.1f}ms / {res.delay_stats['max']:.1f}ms")
    print(f"  Queue Wait (p50 / p95 / max): {res.queue_wait_stats['p50']:.1f}ms / {res.queue_wait_stats['p95']:.1f}ms / {res.queue_wait_stats['max']:.1f}ms")
    print(f"  Overrun: {res.overrun_count} | Dropped: {res.dropped_audio_chunks} chunks ({res.dropped_audio_seconds:.2f}s)")
    print(f"  Peak RSS Memory: {res.peak_memory_mb:.1f} MB")
    print("=================================================================")

if __name__ == "__main__":
    main()
