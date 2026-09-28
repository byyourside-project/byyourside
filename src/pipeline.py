import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal
import sounddevice as sd

from src.config import PipelineConfig
from src.vad import VadProcessor, Segment
from src.stt import SttEngine
from src.logger import StructuredLogger
from src.metrics import calculate_percentiles, get_process_memory_mb

@dataclass
class AudioChunk:
    """Audio chunk captured from mic or replay feeder."""
    samples: np.ndarray      # float32 array
    sample_rate: int
    capture_ts: float        # monotonic timestamp when captured
    sample_idx_end: int      # stream sample index at end of this chunk

@dataclass
class SegmentTask:
    """Task passed from VAD worker to STT worker."""
    segment: Segment
    capture_end_ts: float    # monotonic timestamp when segment's last sample was fed/captured

@dataclass
class PipelineResult:
    """Summary of a pipeline run."""
    run_id: str
    mode: str
    total_audio_seconds: float
    total_inference_seconds: float
    cumulative_rtf: float
    segment_count: int
    rtf_stats: Dict[str, float]
    delay_stats: Dict[str, float]
    queue_wait_stats: Dict[str, float]
    overrun_count: int
    dropped_audio_chunks: int
    dropped_audio_seconds: float
    peak_memory_mb: float
    segments: List[Dict[str, Any]] = field(default_factory=list)

class SpeechPipeline:
    """Unified audio pipeline supporting direct WAV, simulated replay, and live microphone."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.config.validate()

        self.vad = VadProcessor(self.config.vad)
        self.stt = SttEngine(self.config.stt)

    def run_wav_direct(self, wav_path: str, run_id: Optional[str] = None) -> PipelineResult:
        """
        Direct WAV recognition mode.
        Feeds entire audio into VAD and STT sequentially for pure accuracy / throughput benchmark.
        """
        run_id = run_id or f"wav_{uuid.uuid4().hex[:8]}"
        sr, raw_data = wavfile.read(wav_path)

        # Convert to mono float32
        if raw_data.ndim > 1:
            raw_data = raw_data.mean(axis=1)
        if raw_data.dtype == np.int16:
            samples = raw_data.astype(np.float32) / 32768.0
        elif raw_data.dtype == np.int32:
            samples = raw_data.astype(np.float32) / 2147483648.0
        else:
            samples = raw_data.astype(np.float32)

        # Resample to 16kHz if necessary
        if sr != 16000:
            gcd = np.gcd(sr, 16000)
            up = 16000 // gcd
            down = sr // gcd
            samples = signal.resample_poly(samples, up, down).astype(np.float32)
            sr = 16000

        total_audio_dur = len(samples) / float(sr)
        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)

        logger.log_event("run_start", {
            "mode": "wav_direct",
            "wav_path": wav_path,
            "sample_rate": sr,
            "total_audio_seconds": round(total_audio_dur, 3),
            "cold_start_load_time_ms": round(self.stt.cold_start_load_time_ms, 2)
        })

        self.vad.reset()
        window_size = self.config.vad.window_size
        segments: List[Segment] = []

        # Process chunks
        for i in range(0, len(samples), window_size):
            chunk = samples[i:i + window_size]
            ready = self.vad.process_chunk(chunk)
            segments.extend(ready)

        # Flush VAD
        segments.extend(self.vad.flush())

        # If VAD produced no segments (e.g. short audio below threshold), fallback to direct decode
        if not segments:
            segments = [
                Segment(
                    segment_id=1,
                    samples=samples,
                    start_sample=0,
                    end_sample=len(samples),
                    start_ms=0.0,
                    end_ms=round(total_audio_dur * 1000.0, 2),
                    duration_ms=round(total_audio_dur * 1000.0, 2),
                    endpoint_reason="direct_fallback",
                    ready_ts=time.perf_counter()
                )
            ]

        # STT decoding
        segment_results = []
        rtf_list = []
        delay_list = []
        queue_wait_list = []
        total_infer_time = 0.0

        for seg in segments:
            stt_start = time.perf_counter()
            text, infer_ms = self.stt.transcribe(seg.samples, 16000)
            stt_end = time.perf_counter()
            result_emit_ts = stt_end

            total_infer_time += (infer_ms / 1000.0)
            rtf = (infer_ms / 1000.0) / (seg.duration_ms / 1000.0) if seg.duration_ms > 0 else 0.0
            queue_wait_ms = (stt_start - seg.ready_ts) * 1000.0
            delay_after_speech_ms = (result_emit_ts - seg.ready_ts) * 1000.0

            rtf_list.append(rtf)
            delay_list.append(delay_after_speech_ms)
            queue_wait_list.append(queue_wait_ms)

            logger.log_segment_result(
                segment_id=seg.segment_id,
                input_mode="wav_direct",
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

            segment_results.append({
                "segment_id": seg.segment_id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "duration_ms": seg.duration_ms,
                "text": text,
                "rtf": rtf,
                "infer_ms": infer_ms
            })

        cum_rtf = total_infer_time / total_audio_dur if total_audio_dur > 0 else 0.0
        peak_mem = get_process_memory_mb()

        logger.log_event("run_summary", {
            "total_audio_seconds": round(total_audio_dur, 3),
            "total_inference_seconds": round(total_infer_time, 3),
            "cumulative_rtf": round(cum_rtf, 4),
            "peak_memory_mb": round(peak_mem, 2)
        })
        logger.close()

        return PipelineResult(
            run_id=run_id,
            mode="wav_direct",
            total_audio_seconds=total_audio_dur,
            total_inference_seconds=total_infer_time,
            cumulative_rtf=cum_rtf,
            segment_count=len(segments),
            rtf_stats=calculate_percentiles(rtf_list),
            delay_stats=calculate_percentiles(delay_list),
            queue_wait_stats=calculate_percentiles(queue_wait_list),
            overrun_count=0,
            dropped_audio_chunks=0,
            dropped_audio_seconds=0.0,
            peak_memory_mb=peak_mem,
            segments=segment_results
        )

    def run_replay(self, wav_path: str, speed: float = 1.0, run_id: Optional[str] = None) -> PipelineResult:
        """
        Simulated streaming replay mode.
        Feeds audio chunks in real-time pace (1.0x speed) to accurately measure queue latency and delay after speech.
        """
        run_id = run_id or f"replay_{uuid.uuid4().hex[:8]}"
        sr, raw_data = wavfile.read(wav_path)

        if raw_data.ndim > 1:
            raw_data = raw_data.mean(axis=1)
        if raw_data.dtype == np.int16:
            samples = raw_data.astype(np.float32) / 32768.0
        elif raw_data.dtype == np.int32:
            samples = raw_data.astype(np.float32) / 2147483648.0
        else:
            samples = raw_data.astype(np.float32)

        if sr != 16000:
            gcd = np.gcd(sr, 16000)
            up = 16000 // gcd
            down = sr // gcd
            samples = signal.resample_poly(samples, up, down).astype(np.float32)
            sr = 16000

        total_audio_dur = len(samples) / float(sr)
        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)

        logger.log_event("run_start", {
            "mode": "replay",
            "wav_path": wav_path,
            "sample_rate": sr,
            "speed": speed,
            "total_audio_seconds": round(total_audio_dur, 3),
        })

        audio_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_audio_queue_size)
        segment_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_segment_queue_size)
        stop_event = threading.Event()

        # Metrics collection
        dropped_chunks = 0
        dropped_samples = 0
        segment_results = []
        rtf_list = []
        delay_list = []
        queue_wait_list = []
        total_infer_time = 0.0

        # Worker 1: VAD worker
        def vad_worker():
            self.vad.reset()
            while not stop_event.is_set() or not audio_queue.empty():
                try:
                    chunk_item: Optional[AudioChunk] = audio_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if chunk_item is None:  # Sentinel
                    break

                ready_segments = self.vad.process_chunk(chunk_item.samples)
                for seg in ready_segments:
                    task = SegmentTask(segment=seg, capture_end_ts=chunk_item.capture_ts)
                    try:
                        segment_queue.put(task, timeout=0.5)
                    except queue.Full:
                        # Queue overflow handling
                        pass
                audio_queue.task_done()

            # Flush VAD
            flushed_segments = self.vad.flush()
            for seg in flushed_segments:
                task = SegmentTask(segment=seg, capture_end_ts=time.perf_counter())
                segment_queue.put(task)

            # Signal STT worker
            segment_queue.put(None)

        # Worker 2: STT worker
        def stt_worker():
            nonlocal total_infer_time
            while True:
                try:
                    task: Optional[SegmentTask] = segment_queue.get(timeout=0.1)
                except queue.Empty:
                    if stop_event.is_set() and audio_queue.empty():
                        break
                    continue

                if task is None:
                    break

                seg = task.segment
                stt_start = time.perf_counter()
                queue_wait_ms = (stt_start - seg.ready_ts) * 1000.0

                text, infer_ms = self.stt.transcribe(seg.samples, 16000)
                stt_end = time.perf_counter()
                result_emit_ts = stt_end

                total_infer_time += (infer_ms / 1000.0)
                rtf = (infer_ms / 1000.0) / (seg.duration_ms / 1000.0) if seg.duration_ms > 0 else 0.0

                # True post-speech delay: from the actual moment the last speech sample was produced in real-time
                # to the moment result is emitted.
                delay_after_speech_ms = (result_emit_ts - task.capture_end_ts) * 1000.0
                total_latency_ms = (result_emit_ts - seg.ready_ts) * 1000.0

                rtf_list.append(rtf)
                delay_list.append(delay_after_speech_ms)
                queue_wait_list.append(queue_wait_ms)

                logger.log_segment_result(
                    segment_id=seg.segment_id,
                    input_mode="replay",
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
                    total_latency_ms=total_latency_ms,
                    text=text
                )

                segment_results.append({
                    "segment_id": seg.segment_id,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "duration_ms": seg.duration_ms,
                    "text": text,
                    "rtf": rtf,
                    "delay_ms": delay_after_speech_ms,
                    "infer_ms": infer_ms
                })
                segment_queue.task_done()

        t_vad = threading.Thread(target=vad_worker, daemon=True)
        t_stt = threading.Thread(target=stt_worker, daemon=True)
        t_vad.start()
        t_stt.start()

        # Feeder: feed 512 samples every 32ms (scaled by speed)
        chunk_size = self.config.vad.window_size
        chunk_dur = chunk_size / float(sr) / speed
        stream_sample_idx = 0

        start_wall_time = time.perf_counter()
        for i in range(0, len(samples), chunk_size):
            chunk = samples[i:i + chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

            stream_sample_idx += len(chunk)
            now = time.perf_counter()

            item = AudioChunk(
                samples=chunk,
                sample_rate=16000,
                capture_ts=now,
                sample_idx_end=stream_sample_idx
            )

            try:
                audio_queue.put_nowait(item)
            except queue.Full:
                dropped_chunks += 1
                dropped_samples += len(chunk)

            # Precise pacing
            target_elapsed = (i + chunk_size) / float(sr) / speed
            actual_elapsed = time.perf_counter() - start_wall_time
            sleep_needed = target_elapsed - actual_elapsed
            if sleep_needed > 0.001:
                time.sleep(sleep_needed)

        # Notify stop and wait for workers
        audio_queue.put(None)
        stop_event.set()
        t_vad.join(timeout=10.0)
        t_stt.join(timeout=15.0)

        cum_rtf = total_infer_time / total_audio_dur if total_audio_dur > 0 else 0.0
        peak_mem = get_process_memory_mb()

        logger.log_event("run_summary", {
            "total_audio_seconds": round(total_audio_dur, 3),
            "total_inference_seconds": round(total_infer_time, 3),
            "cumulative_rtf": round(cum_rtf, 4),
            "peak_memory_mb": round(peak_mem, 2),
            "dropped_chunks": dropped_chunks,
        })
        logger.close()

        return PipelineResult(
            run_id=run_id,
            mode="replay",
            total_audio_seconds=total_audio_dur,
            total_inference_seconds=total_infer_time,
            cumulative_rtf=cum_rtf,
            segment_count=len(segment_results),
            rtf_stats=calculate_percentiles(rtf_list),
            delay_stats=calculate_percentiles(delay_list),
            queue_wait_stats=calculate_percentiles(queue_wait_list),
            overrun_count=0,
            dropped_audio_chunks=dropped_chunks,
            dropped_audio_seconds=dropped_samples / 16000.0,
            peak_memory_mb=peak_mem,
            segments=segment_results
        )

    def run_mic(self, duration_seconds: float = 10.0, run_id: Optional[str] = None) -> PipelineResult:
        """
        Live microphone recording and real-time STT pipeline.
        Captures audio at hardware rate (e.g. 48kHz), downsamples via resample_poly to 16kHz,
        feeds into VAD & STT worker threads, and measures latency & queue health.
        """
        run_id = run_id or f"mic_{uuid.uuid4().hex[:8]}"
        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)

        in_rate = self.config.audio.device_sample_rate  # 48000
        out_rate = self.config.audio.target_sample_rate  # 16000
        # GCD ratio for downsampling
        gcd = np.gcd(in_rate, out_rate)
        up = out_rate // gcd    # 1
        down = in_rate // gcd  # 3

        logger.log_event("run_start", {
            "mode": "mic",
            "duration_seconds": duration_seconds,
            "device_sample_rate": in_rate,
            "target_sample_rate": out_rate,
            "resample_ratio": f"{up}/{down}",
        })

        audio_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_audio_queue_size)
        segment_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_segment_queue_size)
        stop_event = threading.Event()

        overrun_count = 0
        dropped_chunks = 0
        dropped_samples = 0
        total_captured_samples = 0

        segment_results = []
        rtf_list = []
        delay_list = []
        queue_wait_list = []
        total_infer_time = 0.0

        # Mic input callback: MUST BE EXTREMELY LIGHTWEIGHT
        def mic_callback(indata, frames, time_info, status):
            nonlocal overrun_count, dropped_chunks, dropped_samples, total_captured_samples
            if status:
                if status.input_overflow:
                    overrun_count += 1
            now = time.perf_counter()
            total_captured_samples += frames

            # Copy data slice (mono)
            chunk = indata[:, 0].copy()
            item = AudioChunk(
                samples=chunk,
                sample_rate=in_rate,
                capture_ts=now,
                sample_idx_end=total_captured_samples
            )
            try:
                audio_queue.put_nowait(item)
            except queue.Full:
                dropped_chunks += 1
                dropped_samples += len(chunk)

        # Worker 1: Resample & VAD Worker
        def vad_worker():
            self.vad.reset()
            while not stop_event.is_set() or not audio_queue.empty():
                try:
                    chunk_item: Optional[AudioChunk] = audio_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if chunk_item is None:
                    break

                # High quality anti-aliasing resampling: 48kHz -> 16kHz
                if chunk_item.sample_rate != 16000:
                    resampled = signal.resample_poly(chunk_item.samples, up, down).astype(np.float32)
                else:
                    resampled = chunk_item.samples

                ready_segments = self.vad.process_chunk(resampled)
                for seg in ready_segments:
                    task = SegmentTask(segment=seg, capture_end_ts=chunk_item.capture_ts)
                    try:
                        segment_queue.put(task, timeout=0.5)
                    except queue.Full:
                        pass
                audio_queue.task_done()

            # Flush remaining audio
            flushed = self.vad.flush()
            for seg in flushed:
                task = SegmentTask(segment=seg, capture_end_ts=time.perf_counter())
                segment_queue.put(task)

            segment_queue.put(None)

        # Worker 2: STT Worker
        def stt_worker():
            nonlocal total_infer_time
            while True:
                try:
                    task: Optional[SegmentTask] = segment_queue.get(timeout=0.1)
                except queue.Empty:
                    if stop_event.is_set() and audio_queue.empty():
                        break
                    continue

                if task is None:
                    break

                seg = task.segment
                stt_start = time.perf_counter()
                queue_wait_ms = (stt_start - seg.ready_ts) * 1000.0

                text, infer_ms = self.stt.transcribe(seg.samples, 16000)
                stt_end = time.perf_counter()
                result_emit_ts = stt_end

                total_infer_time += (infer_ms / 1000.0)
                rtf = (infer_ms / 1000.0) / (seg.duration_ms / 1000.0) if seg.duration_ms > 0 else 0.0
                delay_after_speech_ms = (result_emit_ts - task.capture_end_ts) * 1000.0
                total_latency_ms = (result_emit_ts - seg.ready_ts) * 1000.0

                rtf_list.append(rtf)
                delay_list.append(delay_after_speech_ms)
                queue_wait_list.append(queue_wait_ms)

                logger.log_segment_result(
                    segment_id=seg.segment_id,
                    input_mode="mic",
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
                    total_latency_ms=total_latency_ms,
                    text=text
                )

                segment_results.append({
                    "segment_id": seg.segment_id,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "duration_ms": seg.duration_ms,
                    "text": text,
                    "rtf": rtf,
                    "delay_ms": delay_after_speech_ms,
                    "infer_ms": infer_ms
                })
                segment_queue.task_done()

        t_vad = threading.Thread(target=vad_worker, daemon=True)
        t_stt = threading.Thread(target=stt_worker, daemon=True)
        t_vad.start()
        t_stt.start()

        # Start sounddevice input stream
        # 1536 samples blocksize at 48kHz = exactly 32ms
        blocksize = self.config.audio.chunk_size_samples
        with sd.InputStream(
            samplerate=in_rate,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
            callback=mic_callback
        ):
            print(f"[MIC] Listening for {duration_seconds} seconds... (Speak in Korean)")
            time.sleep(duration_seconds)

        print("[MIC] Stopping capture and finalizing segments...")
        stop_event.set()
        audio_queue.put(None)
        t_vad.join(timeout=5.0)
        t_stt.join(timeout=10.0)

        total_audio_sec = total_captured_samples / float(in_rate)
        cum_rtf = total_infer_time / total_audio_sec if total_audio_sec > 0 else 0.0
        peak_mem = get_process_memory_mb()

        logger.log_event("run_summary", {
            "total_audio_seconds": round(total_audio_sec, 3),
            "total_inference_seconds": round(total_infer_time, 3),
            "cumulative_rtf": round(cum_rtf, 4),
            "peak_memory_mb": round(peak_mem, 2),
            "overrun_count": overrun_count,
            "dropped_chunks": dropped_chunks,
        })
        logger.close()

        return PipelineResult(
            run_id=run_id,
            mode="mic",
            total_audio_seconds=total_audio_sec,
            total_inference_seconds=total_infer_time,
            cumulative_rtf=cum_rtf,
            segment_count=len(segment_results),
            rtf_stats=calculate_percentiles(rtf_list),
            delay_stats=calculate_percentiles(delay_list),
            queue_wait_stats=calculate_percentiles(queue_wait_list),
            overrun_count=overrun_count,
            dropped_audio_chunks=dropped_chunks,
            dropped_audio_seconds=(dropped_samples / float(in_rate)),
            peak_memory_mb=peak_mem,
            segments=segment_results
        )
