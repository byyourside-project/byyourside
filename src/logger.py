import json
import os
import time
import threading
from typing import Dict, Any, Optional

class StructuredLogger:
    """Structured JSONL logger for VAD and STT pipeline benchmarks."""

    def __init__(self, run_id: str, log_dir: str = "logs", terminal_output: bool = True, event_sink=None):
        self.run_id = run_id
        self.log_dir = log_dir
        self.terminal_output = terminal_output
        self.event_sink = event_sink
        self._lock = threading.Lock()
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file_path = os.path.join(self.log_dir, f"stt_run_{run_id}.jsonl")
        self._file = open(self.log_file_path, "a", encoding="utf-8")

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._file.closed

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log a generic event dictionary to JSONL."""
        with self._lock:
            if self._file.closed:
                # F2: Gracefully ignore attempts to write to closed logger without crashing
                return
            entry = {
                "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "run_id": self.run_id,
                "event_type": event_type,
                **data
            }
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
        # Consumers should enqueue only; never run coaching in the audio/STT path.
        if self.event_sink is not None:
            try:
                self.event_sink(dict(entry))
            except Exception as exc:
                with self._lock:
                    if not self._file.closed:
                        self._file.write(json.dumps({
                            "run_id": self.run_id, "event_type": "event_sink_error",
                            "error": str(exc)
                        }, ensure_ascii=False) + "\n")
                        self._file.flush()

    def log_segment_result(
        self,
        segment_id: int,
        input_mode: str,
        audio_start_ms: float,
        audio_end_ms: float,
        audio_duration_ms: float,
        endpoint_reason: str,
        segment_ready_ts: float,
        stt_start_ts: float,
        stt_end_ts: float,
        result_emit_ts: float,
        queue_wait_ms: float,
        stt_inference_ms: float,
        rtf: float,
        delay_after_speech_ms: float,
        total_latency_ms: float,
        text: str,
        error_or_dropped_status: str = "OK",
        extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log a completed speech segment recognition result."""
        data = {
            "segment_id": segment_id,
            "input_mode": input_mode,
            "audio_start_ms": round(audio_start_ms, 2),
            "audio_end_ms": round(audio_end_ms, 2),
            "audio_duration_ms": round(audio_duration_ms, 2),
            "endpoint_reason": endpoint_reason,
            "segment_ready_ts": segment_ready_ts,
            "stt_start_ts": stt_start_ts,
            "stt_end_ts": stt_end_ts,
            "result_emit_ts": result_emit_ts,
            "queue_wait_ms": round(queue_wait_ms, 2),
            "stt_inference_ms": round(stt_inference_ms, 2),
            "rtf": round(rtf, 4),
            "delay_after_speech_ms": round(delay_after_speech_ms, 2),
            "total_latency_ms": round(total_latency_ms, 2),
            "text": text,
            "status": error_or_dropped_status,
        }
        if extra:
            data.update(extra)

        self.log_event("segment_result", data)

        if self.terminal_output:
            status_indicator = f"[{error_or_dropped_status}]" if error_or_dropped_status != "OK" else ""
            print(
                f"[Seg #{segment_id:03d} | {audio_start_ms/1000:5.2f}s - {audio_end_ms/1000:5.2f}s | "
                f"Dur: {audio_duration_ms/1000:4.2f}s | Infer: {stt_inference_ms:5.1f}ms | "
                f"RTF: {rtf:5.3f} | Delay: {delay_after_speech_ms:5.1f}ms] {status_indicator} \"{text}\""
            )

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
