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
from src.stt import SttEngine, IsolatedSttEngine
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

    def __init__(self, config: Optional[PipelineConfig] = None, stt: Optional[Any] = None,
                 event_sink=None, terminal_output: bool = True):
        self.config = config or PipelineConfig()
        self.config.validate()

        self.vad = VadProcessor(self.config.vad)
        self.stt = stt or IsolatedSttEngine(self.config.stt)
        self._active_workers: List[threading.Thread] = []
        self._worker_lock = threading.Lock()
        self.last_run_timing: Dict[str, Any] = {}
        self.event_sink = event_sink
        self.terminal_output = terminal_output

    def _logger(self, run_id):
        return StructuredLogger(run_id=run_id, log_dir=self.config.log_dir,
                                terminal_output=self.terminal_output, event_sink=self.event_sink)

    def _call_stt_transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int = 16000,
        abort_event: Optional[Any] = None,
        timeout: Optional[float] = None
    ) -> Tuple[str, float]:
        """
        Invokes self.stt.transcribe with argument compatibility check via inspect.signature.
        Avoids broad try/except TypeError which would swallow internal inference errors.
        """
        import inspect
        sig = inspect.signature(self.stt.transcribe)
        kwargs = {}
        if "abort_event" in sig.parameters:
            kwargs["abort_event"] = abort_event
        if "timeout" in sig.parameters:
            kwargs["timeout"] = timeout
        return self.stt.transcribe(samples, sample_rate, **kwargs)

    def has_running_workers(self) -> bool:
        """Check if any worker threads from prior runs are still alive."""
        with self._worker_lock:
            self._active_workers = [t for t in self._active_workers if t.is_alive()]
            return len(self._active_workers) > 0

    def close(self) -> None:
        """Orderly shutdown of pipeline workers and child engine processes."""
        if hasattr(self.stt, "close"):
            self.stt.close()

    def run_wav_direct_stt(self, wav_path: str, run_id: Optional[str] = None, request_timeout: Optional[float] = None) -> PipelineResult:
        """
        Pure STT mode (bypasses VAD entirely).
        Feeds the entire audio file directly into SenseVoice for pure acoustic accuracy/RTF baseline.
        Enforces a finite deadline budget based on audio duration to prevent infinite hangs.
        """
        run_id = run_id or f"wav_direct_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        if request_timeout is not None:
            effective_timeout = request_timeout
        else:
            effective_timeout = max(
                self.config.stt.request_timeout_sec,
                self.config.stt.batch_timeout_base_sec + total_audio_dur * self.config.stt.batch_timeout_per_second
            )

        logger = self._logger(run_id)
        logger.log_event("run_start", {
            "mode": "wav_direct_stt",
            "wav_path": wav_path,
            "sample_rate": sr,
            "total_audio_seconds": round(total_audio_dur, 3),
            "bypasses_vad": True,
            "stt_threads": self.config.stt.num_threads,
            "request_timeout_sec": effective_timeout
        })

        t_start = time.perf_counter()
        try:
            text, infer_ms = self.stt.transcribe(samples, sr, timeout=effective_timeout)
        except Exception as e:
            logger.log_event("error", {
                "mode": "wav_direct_stt",
                "error": str(e),
                "error_type": type(e).__name__,
                "timeout_sec": effective_timeout
            })
            logger.close()
            if hasattr(self.stt, "terminate"):
                self.stt.terminate()
            raise
        t_end = time.perf_counter()

        infer_sec = infer_ms / 1000.0
        rtf = infer_sec / total_audio_dur if total_audio_dur > 0 else 0.0
        child_pid = getattr(self.stt, "child_pid", None)
        mem = get_memory_stats(child_pid)

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
            "memory": mem,
            "child_pid": child_pid
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
                "request_timeout_sec": effective_timeout,
                "endpoint_reason": "direct_bypass"
            }]
        )

    def run_wav_vad(self, wav_path: str, run_id: Optional[str] = None, request_timeout: Optional[float] = None) -> PipelineResult:
        """
        Batch VAD + STT mode on WAV file.
        Passes audio through VAD chunks and transcribes detected segments.
        Does not claim real-time streaming delay.
        Enforces per-segment finite deadline to prevent infinite hangs.
        """
        run_id = run_id or f"wav_vad_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        logger = self._logger(run_id)
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
            seg_dur_sec = seg.duration_ms / 1000.0
            if request_timeout is not None:
                effective_timeout = request_timeout
            else:
                effective_timeout = max(
                    self.config.stt.request_timeout_sec,
                    self.config.stt.batch_timeout_base_sec + seg_dur_sec * self.config.stt.batch_timeout_per_second
                )

            t0 = time.perf_counter()
            try:
                text, infer_ms = self.stt.transcribe(seg.samples, 16000, timeout=effective_timeout)
            except Exception as e:
                logger.log_event("error", {
                    "mode": "wav_vad",
                    "segment_id": seg.segment_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "timeout_sec": effective_timeout
                })
                logger.close()
                if hasattr(self.stt, "terminate"):
                    self.stt.terminate()
                raise
            t1 = time.perf_counter()

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
                "queue_wait_ms": 0.0,
                "infer_ms": infer_ms,
                "request_timeout_sec": effective_timeout,
                "endpoint_reason": seg.endpoint_reason
            })

        child_pid = getattr(self.stt, "child_pid", None)
        mem = get_memory_stats(child_pid)
        speech_rtf = infer_time_total / speech_dur_total if speech_dur_total > 0 else 0.0
        throughput_rtf = infer_time_total / total_audio_dur if total_audio_dur > 0 else 0.0

        logger.log_event("run_summary", {
            "mode": "wav_vad",
            "total_audio_seconds": round(total_audio_dur, 3),
            "total_speech_seconds": round(speech_dur_total, 3),
            "total_inference_seconds": round(infer_time_total, 3),
            "speech_rtf": round(speech_rtf, 4),
            "throughput_rtf": round(throughput_rtf, 4),
            "memory": mem,
            "child_pid": child_pid
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
        artificial_stt_delay_sec: float = 0.0,
        request_timeout: Optional[float] = None
    ) -> PipelineResult:
        """
        Simulated streaming replay mode.
        Feeds audio chunks at exact real-time speed (1.0x) to evaluate queue dynamics and post-speech latency.
        Enforces per-request STT deadline so uncooperative inference is reaped immediately
        even while input feeding is still active.
        Strict sentinel-based termination with abort_event guarantees zero race conditions or hangs.
        """
        if self.has_running_workers():
            raise RuntimeError("Cannot start pipeline run while previous workers are still active")

        run_id = run_id or f"replay_{uuid.uuid4().hex[:8]}"
        samples, sr = load_and_normalize_audio(wav_path, target_sr=16000)
        total_audio_dur = len(samples) / float(sr)

        stream_start_wall_ts = time.perf_counter()
        clock_origin_time = time.time()
        self.last_run_timing = {
            "stream_start_ts": stream_start_wall_ts,
            "mode": "replay"
        }

        effective_req_timeout = request_timeout if request_timeout is not None else self.config.stt.request_timeout_sec

        logger = self._logger(run_id)
        logger.log_event("run_start", {
            "mode": "replay",
            "wav_path": wav_path,
            "sample_rate": sr,
            "speed": speed,
            "total_audio_seconds": round(total_audio_dur, 3),
            "artificial_delay_sec": artificial_stt_delay_sec,
            "request_timeout_sec": effective_req_timeout,
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
                endpoint_sample = 0
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
                        if "first_segment_ready_ts" not in self.last_run_timing:
                            self.last_run_timing["first_segment_ready_ts"] = seg.ready_ts
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
                    watermark = self.vad.silence_watermark_sample
                    if watermark > endpoint_sample:
                        endpoint_sample = watermark
                        logger.log_event("speech_endpoint", {"audio_end_ms": watermark / self.config.vad.sample_rate * 1000})
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
                    if "first_stt_request_ts" not in self.last_run_timing:
                        self.last_run_timing["first_stt_request_ts"] = stt_start_ts
                    queue_wait_ms = (stt_start_ts - seg.ready_ts) * 1000.0

                    if artificial_stt_delay_sec > 0:
                        sleep_start = time.perf_counter()
                        while time.perf_counter() - sleep_start < artificial_stt_delay_sec:
                            if abort_event.is_set():
                                break
                            rem = artificial_stt_delay_sec - (time.perf_counter() - sleep_start)
                            if rem <= 0:
                                break
                            time.sleep(min(0.05, rem))

                    if abort_event.is_set():
                        segment_queue.task_done()
                        break

                    text, infer_ms = self._call_stt_transcribe(
                        seg.samples,
                        16000,
                        abort_event=abort_event,
                        timeout=effective_req_timeout
                    )

                    if abort_event.is_set():
                        segment_queue.task_done()
                        break

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
                        "queue_wait_ms": queue_wait_ms,
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
        with self._worker_lock:
            self._active_workers.extend([t_vad, t_stt])
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
                # F2 [P1] Clean shutdown guarantee:
                # If an error/abort occurred during execution, drain queues to unblock workers
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

                join_timeout = getattr(self.config, "worker_join_timeout_sec", 2.0)
                t_vad.join(timeout=join_timeout)
                t_stt.join(timeout=join_timeout)

                vad_timed_out = t_vad.is_alive()
                stt_timed_out = t_stt.is_alive()

                # If workers timed out or aborted, now trigger abort, terminate child process, and drain
                if vad_timed_out or stt_timed_out or abort_event.is_set():
                    abort_event.set()
                    if hasattr(self.stt, "terminate"):
                        self.stt.terminate()
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
                    t_vad.join(timeout=1.0)
                    t_stt.join(timeout=1.0)

                with self._worker_lock:
                    self._active_workers = [t for t in self._active_workers if t.is_alive()]

                t_now = time.perf_counter()
                self.last_run_timing["failure_caught_ts"] = t_now
                self.last_run_timing["child_timing"] = getattr(self.stt, "last_timing_event", {})
                self.last_run_timing["total_elapsed_sec"] = t_now - stream_start_wall_ts

            if vad_timed_out:
                raise TimeoutError(f"VAD worker failed to terminate within {join_timeout}s join deadline")
            if stt_timed_out:
                raise TimeoutError(f"STT worker failed to terminate within {join_timeout}s join deadline")

            if vad_exception:
                raise RuntimeError(f"VAD worker failed with exception: {vad_exception}") from vad_exception
            if stt_exception:
                if isinstance(stt_exception, TimeoutError):
                    raise stt_exception
                raise RuntimeError(f"STT worker failed with exception: {stt_exception}") from stt_exception

            child_pid = getattr(self.stt, "child_pid", None)
            mem = get_memory_stats(child_pid)
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
                "memory": mem,
                "child_pid": child_pid
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
            if not self.has_running_workers():
                logger.close()



    def _transcribe_live_segment(self, seg, logger, abort_event, timeout, recover):
        """Live-only retry on the same retained samples; benchmark policy stays fail-fast."""
        try:
            text, infer_ms = self._call_stt_transcribe(seg.samples, 16000, abort_event=abort_event, timeout=timeout)
            return text, infer_ms, "OK"
        except TimeoutError as exc:
            if not recover or abort_event.is_set():
                raise
            logger.log_event("stt_recovery", {"stage": "retry", "segment_id": seg.segment_id, "error": str(exc)})
            try:
                text, infer_ms = self._call_stt_transcribe(seg.samples, 16000, abort_event=abort_event, timeout=timeout)
                logger.log_event("stt_recovery", {"stage": "resumed", "segment_id": seg.segment_id})
                return text, infer_ms, "OK"
            except TimeoutError as retry_exc:
                logger.log_event("stt_recovery", {"stage": "failed_segment", "segment_id": seg.segment_id, "error": str(retry_exc)})
                return "", 0.0, "ERROR"

    def run_mic(
        self,
        duration_seconds: float = 10.0,
        run_id: Optional[str] = None,
        snapshot_interval_sec: float = 60.0,
        request_timeout: Optional[float] = None,
        stream_factory: Optional[Any] = None,
        stop_event: Optional[Any] = None,
        recover_stt_timeouts: bool = False
    ) -> PipelineResult:
        """
        Live microphone recording and real-time STT pipeline.
        Captures audio at hardware rate (e.g. 48kHz) strictly in callback,
        downsamples via resample_poly in VAD worker thread,
        and uses abort_event and request deadlines for guaranteed clean shutdown under errors.
        Supports mock stream_factory for deterministic non-physical mic validation.
        """
        if self.has_running_workers():
            raise RuntimeError("Cannot start pipeline run while previous workers are still active")

        run_id = run_id or f"mic_{uuid.uuid4().hex[:8]}"
        in_rate = self.config.audio.device_sample_rate   # 48000
        out_rate = self.config.audio.target_sample_rate  # 16000
        gcd = np.gcd(in_rate, out_rate)
        up = out_rate // gcd    # 1
        down = in_rate // gcd  # 3

        stream_start_wall_ts = time.perf_counter()
        clock_origin_time = time.time()
        self.last_run_timing = {
            "stream_start_ts": stream_start_wall_ts,
            "mode": "mic"
        }

        effective_req_timeout = request_timeout if request_timeout is not None else self.config.stt.request_timeout_sec

        logger = self._logger(run_id)
        logger.log_event("run_start", {
            "mode": "mic",
            "duration_seconds": duration_seconds,
            "device_sample_rate": in_rate,
            "device_index": self.config.audio.device_index,
            "target_sample_rate": out_rate,
            "resample_ratio": f"{up}/{down}",
            "request_timeout_sec": effective_req_timeout,
            "recover_stt_timeouts": recover_stt_timeouts,
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
        first_audio = threading.Event()
        last_callback_ts = 0.0

        # D [P2] Decoupled callback: acquisition and queue put ONLY (no heavy DSP/resample)
        def mic_callback(indata, frames, time_info, status):
            nonlocal overrun_count, dropped_audio_chunks, dropped_audio_samples, total_captured_frames, stream_start_wall_ts, last_callback_ts
            if status and status.input_overflow:
                overrun_count += 1

            now = time.perf_counter()
            last_callback_ts = now
            if total_captured_frames == 0:
                stream_start_wall_ts = now - frames / float(in_rate)
                first_audio.set()
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
                endpoint_sample = 0
                last_level_ts = 0.0
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

                    if chunk_item.capture_ts - last_level_ts >= .2:
                        last_level_ts = chunk_item.capture_ts
                        rms = float(np.sqrt(np.mean(samples_16k ** 2)))
                        peak = float(np.max(np.abs(samples_16k)))
                        logger.log_event("audio_level", {"dbfs": round(20 * np.log10(max(rms, 1e-6)), 1),
                            "peak_dbfs": round(20 * np.log10(max(peak, 1e-6)), 1),
                            "received_sec": chunk_item.stream_sample_idx_end / float(out_rate)})

                    # Pass stream_sample_idx_start to detect gaps from dropped audio chunks
                    ready_segs = self.vad.process_chunk(
                        samples_16k,
                        stream_sample_idx_start=chunk_item.stream_sample_idx_start
                    )
                    for seg in ready_segs:
                        if "first_segment_ready_ts" not in self.last_run_timing:
                            self.last_run_timing["first_segment_ready_ts"] = seg.ready_ts
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
                    watermark = self.vad.silence_watermark_sample
                    if watermark > endpoint_sample:
                        endpoint_sample = watermark
                        logger.log_event("speech_endpoint", {"audio_end_ms": watermark / self.config.vad.sample_rate * 1000})
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
            nonlocal stt_exception, total_infer_sec, total_speech_sec, dropped_segments_count
            consecutive_failures = 0
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
                    if "first_stt_request_ts" not in self.last_run_timing:
                        self.last_run_timing["first_stt_request_ts"] = stt_start_ts
                    queue_wait_ms = (stt_start_ts - seg.ready_ts) * 1000.0

                    if abort_event.is_set():
                        segment_queue.task_done()
                        break

                    text, infer_ms, result_status = self._transcribe_live_segment(
                        seg, logger, abort_event, effective_req_timeout, recover_stt_timeouts)
                    consecutive_failures = consecutive_failures + 1 if result_status == "ERROR" else 0
                    if result_status == "ERROR":
                        dropped_segments_count += 1
                        dropped_items.append(DroppedItem(item_type="segment", item_id=seg.segment_id,
                            stream_sample_start=seg.start_sample, stream_sample_end=seg.end_sample,
                            duration_ms=seg.duration_ms, reason="stt_timeout_after_retry", drop_ts=time.perf_counter()))

                    if abort_event.is_set():
                        segment_queue.task_done()
                        break

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
                        text=text, error_or_dropped_status=result_status
                    )

                    segment_results.append({
                        "segment_id": seg.segment_id,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "duration_ms": seg.duration_ms,
                        "text": text,
                        "rtf": rtf,
                        "queue_wait_ms": queue_wait_ms,
                        "delay_after_speech_ms": delay_after_speech_ms,
                        "continuous_latency_ms": latency_from_start_ms,
                        "infer_ms": infer_ms,
                        "endpoint_reason": seg.endpoint_reason, "status": result_status
                    })
                    segment_queue.task_done()
                    if consecutive_failures >= 3:
                        raise TimeoutError("STT failed for three consecutive segments after bounded retries")
            except Exception as e:
                stt_exception = e
                abort_event.set()

        t_vad = threading.Thread(target=vad_worker, daemon=True)
        t_stt = threading.Thread(target=stt_worker, daemon=True)
        with self._worker_lock:
            self._active_workers.extend([t_vad, t_stt])
        t_vad.start()
        t_stt.start()

        blocksize = self.config.audio.chunk_size_samples
        start_wall_time = time.perf_counter()
        last_snapshot_time = start_wall_time

        try:
            try:
                from src.microphone import open_input_stream, MicrophoneError
                stream_args = dict(samplerate=in_rate, channels=1, dtype="float32", blocksize=blocksize, callback=mic_callback)
                if stream_factory is not None:
                    if self.config.audio.device_index is not None:
                        stream_args["device"] = self.config.audio.device_index
                    stream_context = stream_factory(**stream_args)
                else:
                    stream_context = open_input_stream(device=self.config.audio.device_index,
                        expected_name=self.config.audio.device_name, **stream_args)
                with stream_context:
                    deadline = time.perf_counter() + 2
                    while not first_audio.wait(.05):
                        if stop_event is not None and stop_event.is_set():
                            break
                        if time.perf_counter() >= deadline:
                            raise MicrophoneError("마이크가 열렸지만 입력 프레임을 받지 못했습니다. 입력 장치와 마이크 접근 권한을 확인해 주세요.")
                    if first_audio.is_set():
                        self.last_run_timing["stream_start_ts"] = stream_start_wall_ts
                        logger.log_event("audio_stream_started", {"device_index": self.config.audio.device_index,
                            "device_name": self.config.audio.device_name, "sample_rate": in_rate,
                            "clock_origin_perf_counter": stream_start_wall_ts})
                    start_wall_time = time.perf_counter()
                    last_snapshot_time = start_wall_time
                    while time.perf_counter() - start_wall_time < duration_seconds:
                        if stream_factory is None and first_audio.is_set() and time.perf_counter() - last_callback_ts > 2:
                            raise MicrophoneError("마이크 입력이 중단됐습니다. 장치 연결을 확인하고 마이크를 다시 연결해 주세요.")
                        if abort_event.is_set() or (stop_event is not None and stop_event.is_set()):
                            break
                        time.sleep(0.05)
                        now = time.perf_counter()
                        if now - last_snapshot_time >= snapshot_interval_sec:
                            elapsed = now - start_wall_time
                            child_pid = getattr(self.stt, "child_pid", None)
                            mem = get_memory_stats(child_pid)
                            snapshot = {
                                "elapsed_seconds": round(elapsed, 1),
                                "current_rss_mb": mem["current_rss_mb"],
                                "peak_rss_mb": mem["peak_rss_mb"],
                                "parent_rss_mb": mem.get("parent_rss_mb", mem["current_rss_mb"]),
                                "child_rss_mb": mem.get("child_rss_mb", 0.0),
                                "combined_rss_mb": mem.get("combined_rss_mb", mem["current_rss_mb"]),
                                "audio_queue_size": audio_queue.qsize(),
                                "segment_queue_size": segment_queue.qsize(),
                                "overrun_count": overrun_count,
                                "dropped_chunks": dropped_audio_chunks,
                                "dropped_segments": dropped_segments_count,
                                "segments_so_far": len(segment_results)
                            }
                            periodic_snapshots.append(snapshot)
                            logger.log_event("capture_snapshot", snapshot)
                            print(f"[MIC Monitor] {elapsed:5.1f}s / {duration_seconds}s | "
                                  f"ParentRSS: {mem.get('parent_rss_mb', mem['current_rss_mb']):5.1f}MB | "
                                  f"ChildRSS: {mem.get('child_rss_mb', 0.0):5.1f}MB | "
                                  f"Overruns: {overrun_count} | Drops: {dropped_audio_chunks} | Segments: {len(segment_results)}")
                            last_snapshot_time = now

                # InputStream completed: send sentinel to VAD worker unless aborted
                if not abort_event.is_set():
                    sentinel_deadline = time.perf_counter() + 2.0
                    while not abort_event.is_set() and time.perf_counter() < sentinel_deadline:
                        try:
                            audio_queue.put(SENTINEL, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:
                abort_event.set()
                raise
            finally:
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

                join_timeout = getattr(self.config, "worker_join_timeout_sec", 2.0)
                t_vad.join(timeout=join_timeout)
                t_stt.join(timeout=join_timeout)

                vad_timed_out = t_vad.is_alive()
                stt_timed_out = t_stt.is_alive()

                # If workers timed out or aborted, now trigger abort, terminate child process, and drain
                if vad_timed_out or stt_timed_out or abort_event.is_set():
                    abort_event.set()
                    if hasattr(self.stt, "terminate"):
                        self.stt.terminate()
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
                    t_vad.join(timeout=1.0)
                    t_stt.join(timeout=1.0)

                with self._worker_lock:
                    self._active_workers = [t for t in self._active_workers if t.is_alive()]

                t_now = time.perf_counter()
                self.last_run_timing["failure_caught_ts"] = t_now
                self.last_run_timing["child_timing"] = getattr(self.stt, "last_timing_event", {})
                self.last_run_timing["total_elapsed_sec"] = t_now - stream_start_wall_ts

            if vad_timed_out:
                raise TimeoutError(f"VAD worker failed to terminate within {join_timeout}s join deadline")
            if stt_timed_out:
                raise TimeoutError(f"STT worker failed to terminate within {join_timeout}s join deadline")

            if vad_exception:
                raise RuntimeError(f"VAD worker failed with exception: {vad_exception}") from vad_exception
            if stt_exception:
                if isinstance(stt_exception, TimeoutError):
                    raise stt_exception
                raise RuntimeError(f"STT worker failed with exception: {stt_exception}") from stt_exception

            total_audio_sec = total_captured_frames / float(in_rate)
            speech_rtf = total_infer_sec / total_speech_sec if total_speech_sec > 0 else 0.0
            throughput_rtf = total_infer_sec / total_audio_sec if total_audio_sec > 0 else 0.0
            child_pid = getattr(self.stt, "child_pid", None)
            mem = get_memory_stats(child_pid)
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
                "memory": mem,
                "child_pid": child_pid
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
            if not self.has_running_workers():
                logger.close()
