import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import numpy as np
import scipy.signal as signal
import sounddevice as sd

from src.config import PipelineConfig
from src.vad import VadProcessor, Segment
from src.stt import SttEngine
from src.logger import StructuredLogger
from src.metrics import calculate_percentiles, get_memory_stats
from src.audio_utils import load_and_normalize_audio

def get_git_revision() -> str:
    """Retrieve short git commit hash for benchmark provenance."""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return rev
    except Exception:
        return "unknown"


@dataclass
class AudioChunk:
    """Audio chunk captured from mic or replay feeder."""
    samples: np.ndarray        # 1D float32 array
    sample_rate: int
    capture_ts: float          # monotonic timestamp when captured
    stream_sample_idx_start: int
    stream_sample_idx_end: int

@dataclass
class DroppedItem:
    """Record of dropped audio chunk or speech segment."""
    item_type: str             # 'audio_chunk' or 'segment'
    item_id: int
    stream_sample_start: int
    stream_sample_end: int
    duration_ms: float
    reason: str
    drop_ts: float

@dataclass
class PipelineResult:
    """Summary of a pipeline run."""
    run_id: str
    mode: str
    total_audio_seconds: float
    total_speech_seconds: float
    total_inference_seconds: float
    speech_rtf: float            # inference time / speech audio duration
    throughput_rtf: float        # inference time / total stream duration
    segment_count: int
    is_lossless: bool
    status: str                  # 'OK', 'DROPPED', 'WORKER_ERROR'
    rtf_stats: Dict[str, float]
    estimated_delay_stats: Dict[str, float]   # Delay from estimated speech end to emit
    continuous_latency_stats: Dict[str, float] # Latency from segment start to emit
    queue_wait_stats: Dict[str, float]
    overrun_count: int
    dropped_audio_chunks: int
    dropped_audio_seconds: float
    dropped_segments: int
    dropped_items: List[Dict[str, Any]]
    current_rss_mb: float
    peak_rss_mb: float
    segments: List[Dict[str, Any]] = field(default_factory=list)
    periodic_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def cumulative_rtf(self) -> float:
        """Backward compatibility alias for throughput_rtf."""
        return self.throughput_rtf

class SpeechPipeline:
    """Unified audio pipeline supporting direct WAV, simulated replay, and live microphone."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.config.validate()

        self.vad = VadProcessor(self.config.vad)
        self.stt = SttEngine(self.config.stt)

    def run_wav_direct_stt(self, wav_path: str, run_id: Optional[str] = None) -> PipelineResult:
        """
        Pure STT mode (bypasses VAD entirely).
        Feeds the entire audio file directly into SenseVoice for pure acoustic accuracy/RTF baseline.
        """
        run_id = run_id or f"wav_direct_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)
        logger.log_event("run_start", {
            "mode": "wav_direct_stt",
            "wav_path": wav_path,
            "sample_rate": sr,
            "total_audio_seconds": round(total_audio_dur, 3),
            "bypasses_vad": True,
            "stt_threads": self.config.stt.num_threads
        })

        t_start = time.perf_counter()
        text, infer_ms = self.stt.transcribe(samples, sr)
        t_end = time.perf_counter()

        infer_sec = infer_ms / 1000.0
        rtf = infer_sec / total_audio_dur if total_audio_dur > 0 else 0.0
        mem = get_memory_stats()

        logger.log_segment_result(
            segment_id=1,
            input_mode="wav_direct_stt",
            audio_start_ms=0.0,
            audio_end_ms=total_audio_dur * 1000.0,
            audio_duration_ms=total_audio_dur * 1000.0,
            endpoint_reason="direct_bypass",
            segment_ready_ts=t_start,
            stt_start_ts=t_start,
            stt_end_ts=t_end,
            result_emit_ts=t_end,
            queue_wait_ms=0.0,
            stt_inference_ms=infer_ms,
            rtf=rtf,
            delay_after_speech_ms=infer_ms,
            total_latency_ms=infer_ms,
            text=text
        )

        logger.log_event("run_summary", {
            "mode": "wav_direct_stt",
            "total_audio_seconds": round(total_audio_dur, 3),
            "total_inference_seconds": round(infer_sec, 3),
            "rtf": round(rtf, 4),
            "memory": mem
        })
        logger.close()

        return PipelineResult(
            run_id=run_id,
            mode="wav_direct_stt",
            total_audio_seconds=total_audio_dur,
            total_speech_seconds=total_audio_dur,
            total_inference_seconds=infer_sec,
            speech_rtf=rtf,
            throughput_rtf=rtf,
            segment_count=1,
            is_lossless=True,
            status="OK",
            rtf_stats=calculate_percentiles([rtf]),
            estimated_delay_stats=calculate_percentiles([infer_ms]),
            continuous_latency_stats=calculate_percentiles([infer_ms]),
            queue_wait_stats=calculate_percentiles([0.0]),
            overrun_count=0,
            dropped_audio_chunks=0,
            dropped_audio_seconds=0.0,
            dropped_segments=0,
            dropped_items=[],
            current_rss_mb=mem["current_rss_mb"],
            peak_rss_mb=mem["peak_rss_mb"],
            segments=[{
                "segment_id": 1,
                "start_ms": 0.0,
                "end_ms": total_audio_dur * 1000.0,
                "duration_ms": total_audio_dur * 1000.0,
                "text": text,
                "rtf": rtf,
                "infer_ms": infer_ms,
                "endpoint_reason": "direct_bypass"
            }]
        )

    def run_wav_vad(self, wav_path: str, run_id: Optional[str] = None) -> PipelineResult:
        """
        Batch VAD + STT mode on WAV file.
        Passes audio through VAD chunks and transcribes detected segments.
        Does not claim real-time streaming delay.
        """
        run_id = run_id or f"wav_vad_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)
        logger.log_event("run_start", {
            "mode": "wav_vad",
            "wav_path": wav_path,
            "sample_rate": sr,
            "total_audio_seconds": round(total_audio_dur, 3),
        })

        self.vad.reset()
        window_size = self.config.vad.window_size
        segments: List[Segment] = []

        for i in range(0, len(samples), window_size):
            chunk = samples[i:i + window_size]
            ready = self.vad.process_chunk(chunk)
            segments.extend(ready)

        segments.extend(self.vad.flush())

        segment_results = []
        rtf_list = []
        infer_time_total = 0.0
        speech_dur_total = 0.0

        for seg in segments:
            t0 = time.perf_counter()
            text, infer_ms = self.stt.transcribe(seg.samples, 16000)
            t1 = time.perf_counter()

            seg_dur_sec = seg.duration_ms / 1000.0
            speech_dur_total += seg_dur_sec
            infer_sec = infer_ms / 1000.0
            infer_time_total += infer_sec
            rtf = infer_sec / seg_dur_sec if seg_dur_sec > 0 else 0.0
            rtf_list.append(rtf)

            logger.log_segment_result(
                segment_id=seg.segment_id,
                input_mode="wav_vad",
                audio_start_ms=seg.start_ms,
                audio_end_ms=seg.end_ms,
                audio_duration_ms=seg.duration_ms,
                endpoint_reason=seg.endpoint_reason,
                segment_ready_ts=seg.ready_ts,
                stt_start_ts=t0,
                stt_end_ts=t1,
                result_emit_ts=t1,
                queue_wait_ms=0.0,
                stt_inference_ms=infer_ms,
                rtf=rtf,
                delay_after_speech_ms=infer_ms,
                total_latency_ms=infer_ms,
                text=text
            )

            segment_results.append({
                "segment_id": seg.segment_id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "duration_ms": seg.duration_ms,
                "text": text,
                "rtf": rtf,
                "infer_ms": infer_ms,
                "endpoint_reason": seg.endpoint_reason
            })

        mem = get_memory_stats()
        speech_rtf = infer_time_total / speech_dur_total if speech_dur_total > 0 else 0.0
        throughput_rtf = infer_time_total / total_audio_dur if total_audio_dur > 0 else 0.0

        logger.log_event("run_summary", {
            "mode": "wav_vad",
            "total_audio_seconds": round(total_audio_dur, 3),
            "total_speech_seconds": round(speech_dur_total, 3),
            "total_inference_seconds": round(infer_time_total, 3),
            "speech_rtf": round(speech_rtf, 4),
            "throughput_rtf": round(throughput_rtf, 4),
            "memory": mem
        })
        logger.close()

        return PipelineResult(
            run_id=run_id,
            mode="wav_vad",
            total_audio_seconds=total_audio_dur,
            total_speech_seconds=speech_dur_total,
            total_inference_seconds=infer_time_total,
            speech_rtf=speech_rtf,
            throughput_rtf=throughput_rtf,
            segment_count=len(segments),
            is_lossless=True,
            status="OK",
            rtf_stats=calculate_percentiles(rtf_list),
            estimated_delay_stats=calculate_percentiles([]),  # Not applicable for batch mode
            continuous_latency_stats=calculate_percentiles([]),
            queue_wait_stats=calculate_percentiles([0.0]),
            overrun_count=0,
            dropped_audio_chunks=0,
            dropped_audio_seconds=0.0,
            dropped_segments=0,
            dropped_items=[],
            current_rss_mb=mem["current_rss_mb"],
            peak_rss_mb=mem["peak_rss_mb"],
            segments=segment_results
        )

    def run_wav_direct(self, wav_path: str, run_id: Optional[str] = None) -> PipelineResult:
        """Backward compatibility alias for run_wav_vad."""
        return self.run_wav_vad(wav_path, run_id=run_id)

    def run_replay(
        self,
        wav_path: str,
        speed: float = 1.0,
        run_id: Optional[str] = None,
        artificial_stt_delay_sec: float = 0.0
    ) -> PipelineResult:
        """
        Simulated streaming replay mode (Revision 02).
        Feeds audio chunks at exact real-time speed (1.0x) to evaluate queue dynamics and post-speech latency.
        Strict sentinel-based termination with abort_event guarantees zero race conditions or hangs.
        """
        run_id = run_id or f"replay_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        stream_start_wall_ts = time.perf_counter()
        clock_origin_time = time.time()

        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)
        logger.log_event("run_start", {
            "mode": "replay",
            "wav_path": wav_path,
            "sample_rate": sr,
            "speed": speed,
            "total_audio_seconds": round(total_audio_dur, 3),
            "artificial_delay_sec": artificial_stt_delay_sec,
            "git_revision": get_git_revision(),
            "clock_origin_perf_counter": stream_start_wall_ts,
            "clock_origin_time": clock_origin_time,
            "vad_config": self.config.vad.__dict__,
            "queue_config": self.config.queue.__dict__,
        })

        audio_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_audio_queue_size)
        segment_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_segment_queue_size)
        abort_event = threading.Event()

        dropped_items: List[DroppedItem] = []
        dropped_audio_chunks = 0
        dropped_audio_samples = 0
        dropped_segments_count = 0

        segment_results = []
        rtf_list = []
        estimated_delay_list = []
        continuous_latency_list = []
        queue_wait_list = []
        total_speech_sec = 0.0
        total_infer_sec = 0.0

        vad_exception: Optional[Exception] = None
        stt_exception: Optional[Exception] = None

        SENTINEL = object()

        # Worker 1: VAD worker
        def vad_worker():
            nonlocal vad_exception, dropped_segments_count
            try:
                self.vad.reset()
                while not abort_event.is_set():
                    try:
                        item = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item is SENTINEL:
                        audio_queue.task_done()
                        break

                    chunk_item: AudioChunk = item
                    # C [P1] Pass stream_sample_idx_start to detect gaps from dropped audio
                    ready_segs = self.vad.process_chunk(
                        chunk_item.samples,
                        stream_sample_idx_start=chunk_item.stream_sample_idx_start
                    )
                    for seg in ready_segs:
                        while not abort_event.is_set():
                            try:
                                segment_queue.put(seg, timeout=self.config.queue.put_timeout)
                                break
                            except queue.Full:
                                dropped_segments_count += 1
                                dropped_items.append(DroppedItem(
                                    item_type="segment",
                                    item_id=seg.segment_id,
                                    stream_sample_start=seg.start_sample,
                                    stream_sample_end=seg.end_sample,
                                    duration_ms=seg.duration_ms,
                                    reason="segment_queue_full",
                                    drop_ts=time.perf_counter()
                                ))
                                break
                    audio_queue.task_done()

                if not abort_event.is_set():
                    # Stream ended normally: flush remaining VAD speech
                    flushed_segs = self.vad.flush()
                    for seg in flushed_segs:
                        while not abort_event.is_set():
                            try:
                                segment_queue.put(seg, timeout=self.config.queue.put_timeout)
                                break
                            except queue.Full:
                                dropped_segments_count += 1
                                dropped_items.append(DroppedItem(
                                    item_type="segment",
                                    item_id=seg.segment_id,
                                    stream_sample_start=seg.start_sample,
                                    stream_sample_end=seg.end_sample,
                                    duration_ms=seg.duration_ms,
                                    reason="segment_queue_full_on_flush",
                                    drop_ts=time.perf_counter()
                                ))
                                break

                    # Notify STT worker to terminate after segments are drained
                    while not abort_event.is_set():
                        try:
                            segment_queue.put(SENTINEL, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:
                vad_exception = e
                abort_event.set()
                try:
                    segment_queue.put_nowait(SENTINEL)
                except Exception:
                    pass

        # Worker 2: STT worker
        def stt_worker():
            nonlocal stt_exception, total_infer_sec, total_speech_sec
            try:
                while not abort_event.is_set():
                    try:
                        item = segment_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item is SENTINEL:
                        segment_queue.task_done()
                        break

                    seg: Segment = item
                    stt_start_ts = time.perf_counter()
                    queue_wait_ms = (stt_start_ts - seg.ready_ts) * 1000.0

                    if artificial_stt_delay_sec > 0:
                        sleep_start = time.perf_counter()
                        while time.perf_counter() - sleep_start < artificial_stt_delay_sec:
                            if abort_event.is_set():
                                break
                            time.sleep(min(0.05, artificial_stt_delay_sec - (time.perf_counter() - sleep_start)))

                    if abort_event.is_set():
                        segment_queue.task_done()
                        break

                    text, infer_ms = self.stt.transcribe(seg.samples, 16000)
                    stt_end_ts = time.perf_counter()
                    result_emit_ts = stt_end_ts

                    infer_sec = infer_ms / 1000.0
                    seg_dur_sec = seg.duration_ms / 1000.0
                    total_infer_sec += infer_sec
                    total_speech_sec += seg_dur_sec
                    rtf = infer_sec / seg_dur_sec if seg_dur_sec > 0 else 0.0

                    # Latency calculations in replay mode:
                    audio_speech_end_wall_ts = stream_start_wall_ts + (seg.end_sample / float(sr)) / speed
                    audio_speech_start_wall_ts = stream_start_wall_ts + (seg.start_sample / float(sr)) / speed

                    # Post-speech delay (VAD-estimated endpoint to emission)
                    delay_after_speech_ms = (result_emit_ts - audio_speech_end_wall_ts) * 1000.0
                    # Continuous latency from speech onset to emission
                    latency_from_start_ms = (result_emit_ts - audio_speech_start_wall_ts) * 1000.0

                    rtf_list.append(rtf)
                    estimated_delay_list.append(delay_after_speech_ms)
                    continuous_latency_list.append(latency_from_start_ms)
                    queue_wait_list.append(queue_wait_ms)

                    logger.log_segment_result(
                        segment_id=seg.segment_id,
                        input_mode="replay",
                        audio_start_ms=seg.start_ms,
                        audio_end_ms=seg.end_ms,
                        audio_duration_ms=seg.duration_ms,
                        endpoint_reason=seg.endpoint_reason,
                        segment_ready_ts=seg.ready_ts,
                        stt_start_ts=stt_start_ts,
                        stt_end_ts=stt_end_ts,
                        result_emit_ts=result_emit_ts,
                        queue_wait_ms=queue_wait_ms,
                        stt_inference_ms=infer_ms,
                        rtf=rtf,
                        delay_after_speech_ms=delay_after_speech_ms,
                        total_latency_ms=latency_from_start_ms,
                        text=text
                    )

                    segment_results.append({
                        "segment_id": seg.segment_id,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "duration_ms": seg.duration_ms,
                        "text": text,
                        "rtf": rtf,
                        "delay_after_speech_ms": delay_after_speech_ms,
                        "continuous_latency_ms": latency_from_start_ms,
                        "infer_ms": infer_ms,
                        "endpoint_reason": seg.endpoint_reason
                    })
                    segment_queue.task_done()
            except Exception as e:
                stt_exception = e
                abort_event.set()

        t_vad = threading.Thread(target=vad_worker, daemon=True)
        t_stt = threading.Thread(target=stt_worker, daemon=True)
        t_vad.start()
        t_stt.start()

        chunk_size = self.config.vad.window_size
        stream_sample_idx = 0
        chunk_idx = 0

        try:
            try:
                for i in range(0, len(samples), chunk_size):
                    if abort_event.is_set():
                        break

                    chunk = samples[i:i + chunk_size]
                    if len(chunk) < chunk_size:
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

                    chunk_idx += 1
                    chunk_start = stream_sample_idx
                    stream_sample_idx += len(chunk)
                    chunk_end = stream_sample_idx

                    # D [P2] Pacing: Wait until audio clock physically reaches chunk_end before feeding
                    target_available_ts = stream_start_wall_ts + (chunk_end / float(sr)) / speed
                    sleep_needed = target_available_ts - time.perf_counter()
                    if sleep_needed > 0.0005:
                        time.sleep(sleep_needed)

                    if abort_event.is_set():
                        break

                    now = time.perf_counter()
                    item = AudioChunk(
                        samples=chunk,
                        sample_rate=16000,
                        capture_ts=now,
                        stream_sample_idx_start=chunk_start,
                        stream_sample_idx_end=chunk_end
                    )

                    try:
                        audio_queue.put(item, timeout=self.config.queue.put_timeout)
                    except queue.Full:
                        dropped_audio_chunks += 1
                        dropped_audio_samples += len(chunk)
                        dropped_items.append(DroppedItem(
                            item_type="audio_chunk",
                            item_id=chunk_idx,
                            stream_sample_start=chunk_start,
                            stream_sample_end=chunk_end,
                            duration_ms=(len(chunk) / 16000.0) * 1000.0,
                            reason="audio_queue_full",
                            drop_ts=now
                        ))

                # Feeder completed: send sentinel to VAD worker unless aborted
                if not abort_event.is_set():
                    sentinel_deadline = time.perf_counter() + 2.0
                    while not abort_event.is_set() and time.perf_counter() < sentinel_deadline:
                        try:
                            audio_queue.put(SENTINEL, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            finally:
                # B [P1] Clean shutdown guarantee: drain queues on abort and join workers
                if abort_event.is_set():
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                            audio_queue.task_done()
                        except Exception:
                            break
                    while not segment_queue.empty():
                        try:
                            segment_queue.get_nowait()
                            segment_queue.task_done()
                        except Exception:
                            break

                t_vad.join(timeout=2.0)
                t_stt.join(timeout=2.0)

            if t_vad.is_alive():
                raise TimeoutError("VAD worker failed to terminate within 2.0s")
            if t_stt.is_alive():
                raise TimeoutError("STT worker failed to terminate within 2.0s")

            if vad_exception:
                raise RuntimeError(f"VAD worker failed with exception: {vad_exception}") from vad_exception
            if stt_exception:
                raise RuntimeError(f"STT worker failed with exception: {stt_exception}") from stt_exception

            mem = get_memory_stats()
            speech_rtf = total_infer_sec / total_speech_sec if total_speech_sec > 0 else 0.0
            throughput_rtf = total_infer_sec / total_audio_dur if total_audio_dur > 0 else 0.0
            is_lossless = (dropped_audio_chunks == 0) and (dropped_segments_count == 0)

            logger.log_event("run_summary", {
                "mode": "replay",
                "total_audio_seconds": round(total_audio_dur, 3),
                "total_speech_seconds": round(total_speech_sec, 3),
                "total_inference_seconds": round(total_infer_sec, 3),
                "speech_rtf": round(speech_rtf, 4),
                "throughput_rtf": round(throughput_rtf, 4),
                "is_lossless": is_lossless,
                "dropped_audio_chunks": dropped_audio_chunks,
                "dropped_segments": dropped_segments_count,
                "memory": mem
            })

            return PipelineResult(
                run_id=run_id,
                mode="replay",
                total_audio_seconds=total_audio_dur,
                total_speech_seconds=total_speech_sec,
                total_inference_seconds=total_infer_sec,
                speech_rtf=speech_rtf,
                throughput_rtf=throughput_rtf,
                segment_count=len(segment_results),
                is_lossless=is_lossless,
                status="OK" if is_lossless else "DROPPED",
                rtf_stats=calculate_percentiles(rtf_list),
                estimated_delay_stats=calculate_percentiles(estimated_delay_list),
                continuous_latency_stats=calculate_percentiles(continuous_latency_list),
                queue_wait_stats=calculate_percentiles(queue_wait_list),
                overrun_count=0,
                dropped_audio_chunks=dropped_audio_chunks,
                dropped_audio_seconds=dropped_audio_samples / 16000.0,
                dropped_segments=dropped_segments_count,
                dropped_items=[d.__dict__ for d in dropped_items],
                current_rss_mb=mem["current_rss_mb"],
                peak_rss_mb=mem["peak_rss_mb"],
                segments=segment_results
            )
        finally:
            logger.close()



    def run_mic(
        self,
        duration_seconds: float = 10.0,
        run_id: Optional[str] = None,
        snapshot_interval_sec: float = 60.0
    ) -> PipelineResult:
        """
        Live microphone recording and real-time STT pipeline (Revision 02).
        Captures audio at hardware rate (e.g. 48kHz) strictly in callback,
        downsamples via resample_poly in VAD worker thread,
        and uses abort_event and timeouts for guaranteed clean shutdown under errors.
        """
        run_id = run_id or f"mic_{uuid.uuid4().hex[:8]}"
        in_rate = self.config.audio.device_sample_rate   # 48000
        out_rate = self.config.audio.target_sample_rate  # 16000
        gcd = np.gcd(in_rate, out_rate)
        up = out_rate // gcd    # 1
        down = in_rate // gcd  # 3

        stream_start_wall_ts = time.perf_counter()
        clock_origin_time = time.time()

        logger = StructuredLogger(run_id=run_id, log_dir=self.config.log_dir)
        logger.log_event("run_start", {
            "mode": "mic",
            "duration_seconds": duration_seconds,
            "device_sample_rate": in_rate,
            "target_sample_rate": out_rate,
            "resample_ratio": f"{up}/{down}",
            "git_revision": get_git_revision(),
            "clock_origin_perf_counter": stream_start_wall_ts,
            "clock_origin_time": clock_origin_time,
            "vad_config": self.config.vad.__dict__,
            "queue_config": self.config.queue.__dict__,
        })

        audio_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_audio_queue_size)
        segment_queue: queue.Queue = queue.Queue(maxsize=self.config.queue.max_segment_queue_size)
        abort_event = threading.Event()

        dropped_items: List[DroppedItem] = []
        overrun_count = 0
        dropped_audio_chunks = 0
        dropped_audio_samples = 0
        dropped_segments_count = 0
        total_captured_frames = 0

        segment_results = []
        rtf_list = []
        estimated_delay_list = []
        continuous_latency_list = []
        queue_wait_list = []
        total_speech_sec = 0.0
        total_infer_sec = 0.0
        periodic_snapshots = []

        vad_exception: Optional[Exception] = None
        stt_exception: Optional[Exception] = None

        SENTINEL = object()

        # D [P2] Decoupled callback: acquisition and queue put ONLY (no heavy DSP/resample)
        def mic_callback(indata, frames, time_info, status):
            nonlocal overrun_count, dropped_audio_chunks, dropped_audio_samples, total_captured_frames
            if status and status.input_overflow:
                overrun_count += 1

            now = time.perf_counter()
            start_frame = total_captured_frames
            total_captured_frames += frames
            end_frame = total_captured_frames

            # Map raw frames to 16kHz target sample indices
            start_sample_16k = int(round(start_frame * out_rate / float(in_rate)))
            end_sample_16k = int(round(end_frame * out_rate / float(in_rate)))

            chunk_native = indata[:, 0].copy()

            item = AudioChunk(
                samples=chunk_native,
                sample_rate=in_rate,
                capture_ts=now,
                stream_sample_idx_start=start_sample_16k,
                stream_sample_idx_end=end_sample_16k
            )

            try:
                audio_queue.put_nowait(item)
            except queue.Full:
                dropped_audio_chunks += 1
                dropped_len = end_sample_16k - start_sample_16k
                dropped_audio_samples += dropped_len
                dropped_items.append(DroppedItem(
                    item_type="audio_chunk",
                    item_id=total_captured_frames // frames,
                    stream_sample_start=start_sample_16k,
                    stream_sample_end=end_sample_16k,
                    duration_ms=(dropped_len / float(out_rate)) * 1000.0,
                    reason="audio_queue_full",
                    drop_ts=now
                ))

        # Worker 1: VAD worker (performs resampling and gap detection)
        def vad_worker():
            nonlocal vad_exception, dropped_segments_count
            try:
                self.vad.reset()
                while not abort_event.is_set():
                    try:
                        item = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item is SENTINEL:
                        audio_queue.task_done()
                        break

                    chunk_item: AudioChunk = item

                    # Resample in VAD worker thread (offload from audio callback)
                    if chunk_item.sample_rate != out_rate:
                        samples_16k = signal.resample_poly(chunk_item.samples, up, down).astype(np.float32)
                    else:
                        samples_16k = chunk_item.samples

                    # Pass stream_sample_idx_start to detect gaps from dropped audio chunks
                    ready_segs = self.vad.process_chunk(
                        samples_16k,
                        stream_sample_idx_start=chunk_item.stream_sample_idx_start
                    )
                    for seg in ready_segs:
                        while not abort_event.is_set():
                            try:
                                segment_queue.put(seg, timeout=self.config.queue.put_timeout)
                                break
                            except queue.Full:
                                dropped_segments_count += 1
                                dropped_items.append(DroppedItem(
                                    item_type="segment",
                                    item_id=seg.segment_id,
                                    stream_sample_start=seg.start_sample,
                                    stream_sample_end=seg.end_sample,
                                    duration_ms=seg.duration_ms,
                                    reason="segment_queue_full",
                                    drop_ts=time.perf_counter()
                                ))
                                break
                    audio_queue.task_done()

                if not abort_event.is_set():
                    flushed_segs = self.vad.flush()
                    for seg in flushed_segs:
                        while not abort_event.is_set():
                            try:
                                segment_queue.put(seg, timeout=self.config.queue.put_timeout)
                                break
                            except queue.Full:
                                dropped_segments_count += 1
                                dropped_items.append(DroppedItem(
                                    item_type="segment",
                                    item_id=seg.segment_id,
                                    stream_sample_start=seg.start_sample,
                                    stream_sample_end=seg.end_sample,
                                    duration_ms=seg.duration_ms,
                                    reason="segment_queue_full_on_flush",
                                    drop_ts=time.perf_counter()
                                ))
                                break

                    while not abort_event.is_set():
                        try:
                            segment_queue.put(SENTINEL, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:
                vad_exception = e
                abort_event.set()
                try:
                    segment_queue.put_nowait(SENTINEL)
                except Exception:
                    pass

        # Worker 2: STT worker
        def stt_worker():
            nonlocal stt_exception, total_infer_sec, total_speech_sec
            try:
                while not abort_event.is_set():
                    try:
                        item = segment_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item is SENTINEL:
                        segment_queue.task_done()
                        break

                    seg: Segment = item
                    stt_start_ts = time.perf_counter()
                    queue_wait_ms = (stt_start_ts - seg.ready_ts) * 1000.0

                    text, infer_ms = self.stt.transcribe(seg.samples, 16000)
                    stt_end_ts = time.perf_counter()
                    result_emit_ts = stt_end_ts

                    infer_sec = infer_ms / 1000.0
                    seg_dur_sec = seg.duration_ms / 1000.0
                    total_infer_sec += infer_sec
                    total_speech_sec += seg_dur_sec
                    rtf = infer_sec / seg_dur_sec if seg_dur_sec > 0 else 0.0

                    audio_speech_end_wall_ts = stream_start_wall_ts + (seg.end_sample / 16000.0)
                    audio_speech_start_wall_ts = stream_start_wall_ts + (seg.start_sample / 16000.0)

                    delay_after_speech_ms = (result_emit_ts - audio_speech_end_wall_ts) * 1000.0
                    latency_from_start_ms = (result_emit_ts - audio_speech_start_wall_ts) * 1000.0

                    rtf_list.append(rtf)
                    estimated_delay_list.append(delay_after_speech_ms)
                    continuous_latency_list.append(latency_from_start_ms)
                    queue_wait_list.append(queue_wait_ms)

                    logger.log_segment_result(
                        segment_id=seg.segment_id,
                        input_mode="mic",
                        audio_start_ms=seg.start_ms,
                        audio_end_ms=seg.end_ms,
                        audio_duration_ms=seg.duration_ms,
                        endpoint_reason=seg.endpoint_reason,
                        segment_ready_ts=seg.ready_ts,
                        stt_start_ts=stt_start_ts,
                        stt_end_ts=stt_end_ts,
                        result_emit_ts=result_emit_ts,
                        queue_wait_ms=queue_wait_ms,
                        stt_inference_ms=infer_ms,
                        rtf=rtf,
                        delay_after_speech_ms=delay_after_speech_ms,
                        total_latency_ms=latency_from_start_ms,
                        text=text
                    )

                    segment_results.append({
                        "segment_id": seg.segment_id,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "duration_ms": seg.duration_ms,
                        "text": text,
                        "rtf": rtf,
                        "delay_after_speech_ms": delay_after_speech_ms,
                        "continuous_latency_ms": latency_from_start_ms,
                        "infer_ms": infer_ms,
                        "endpoint_reason": seg.endpoint_reason
                    })
                    segment_queue.task_done()
            except Exception as e:
                stt_exception = e
                abort_event.set()

        t_vad = threading.Thread(target=vad_worker, daemon=True)
        t_stt = threading.Thread(target=stt_worker, daemon=True)
        t_vad.start()
        t_stt.start()

        blocksize = self.config.audio.chunk_size_samples
        start_wall_time = time.time()
        last_snapshot_time = start_wall_time

        try:
            try:
                with sd.InputStream(
                    samplerate=in_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=blocksize,
                    callback=mic_callback
                ):
                    while time.time() - start_wall_time < duration_seconds:
                        if abort_event.is_set():
                            break
                        time.sleep(0.2)
                        now = time.time()
                        if now - last_snapshot_time >= snapshot_interval_sec:
                            elapsed = now - start_wall_time
                            mem = get_memory_stats()
                            snapshot = {
                                "elapsed_seconds": round(elapsed, 1),
                                "current_rss_mb": mem["current_rss_mb"],
                                "peak_rss_mb": mem["peak_rss_mb"],
                                "audio_queue_size": audio_queue.qsize(),
                                "segment_queue_size": segment_queue.qsize(),
                                "overrun_count": overrun_count,
                                "dropped_chunks": dropped_audio_chunks,
                                "dropped_segments": dropped_segments_count,
                                "segments_so_far": len(segment_results)
                            }
                            periodic_snapshots.append(snapshot)
                            print(f"[MIC Monitor] {elapsed:5.1f}s / {duration_seconds}s | "
                                  f"CurRSS: {mem['current_rss_mb']:5.1f}MB | PeakRSS: {mem['peak_rss_mb']:5.1f}MB | "
                                  f"Overruns: {overrun_count} | Drops: {dropped_audio_chunks} | Segments: {len(segment_results)}")
                            last_snapshot_time = now
            except Exception as e:
                abort_event.set()
                raise
            finally:
                if not abort_event.is_set():
                    sentinel_deadline = time.perf_counter() + 2.0
                    while not abort_event.is_set() and time.perf_counter() < sentinel_deadline:
                        try:
                            audio_queue.put(SENTINEL, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                else:
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                            audio_queue.task_done()
                        except Exception:
                            break
                    while not segment_queue.empty():
                        try:
                            segment_queue.get_nowait()
                            segment_queue.task_done()
                        except Exception:
                            break

                t_vad.join(timeout=2.0)
                t_stt.join(timeout=2.0)

            if t_vad.is_alive():
                raise TimeoutError("VAD worker failed to terminate within 2.0s")
            if t_stt.is_alive():
                raise TimeoutError("STT worker failed to terminate within 2.0s")

            if vad_exception:
                raise RuntimeError(f"VAD worker failed with exception: {vad_exception}") from vad_exception
            if stt_exception:
                raise RuntimeError(f"STT worker failed with exception: {stt_exception}") from stt_exception

            total_audio_sec = total_captured_frames / float(in_rate)
            speech_rtf = total_infer_sec / total_speech_sec if total_speech_sec > 0 else 0.0
            throughput_rtf = total_infer_sec / total_audio_sec if total_audio_sec > 0 else 0.0
            mem = get_memory_stats()
            is_lossless = (overrun_count == 0) and (dropped_audio_chunks == 0) and (dropped_segments_count == 0)

            logger.log_event("run_summary", {
                "mode": "mic",
                "total_audio_seconds": round(total_audio_sec, 3),
                "total_speech_seconds": round(total_speech_sec, 3),
                "total_inference_seconds": round(total_infer_sec, 3),
                "speech_rtf": round(speech_rtf, 4),
                "throughput_rtf": round(throughput_rtf, 4),
                "is_lossless": is_lossless,
                "overrun_count": overrun_count,
                "dropped_chunks": dropped_audio_chunks,
                "dropped_segments": dropped_segments_count,
                "memory": mem
            })

            return PipelineResult(
                run_id=run_id,
                mode="mic",
                total_audio_seconds=total_audio_sec,
                total_speech_seconds=total_speech_sec,
                total_inference_seconds=total_infer_sec,
                speech_rtf=speech_rtf,
                throughput_rtf=throughput_rtf,
                segment_count=len(segment_results),
                is_lossless=is_lossless,
                status="OK" if is_lossless else "DROPPED",
                rtf_stats=calculate_percentiles(rtf_list),
                estimated_delay_stats=calculate_percentiles(estimated_delay_list),
                continuous_latency_stats=calculate_percentiles(continuous_latency_list),
                queue_wait_stats=calculate_percentiles(queue_wait_list),
                overrun_count=overrun_count,
                dropped_audio_chunks=dropped_audio_chunks,
                dropped_audio_seconds=dropped_audio_samples / 16000.0,
                dropped_segments=dropped_segments_count,
                dropped_items=[d.__dict__ for d in dropped_items],
                current_rss_mb=mem["current_rss_mb"],
                peak_rss_mb=mem["peak_rss_mb"],
                segments=segment_results,
                periodic_snapshots=periodic_snapshots
            )
        finally:
            logger.close()


