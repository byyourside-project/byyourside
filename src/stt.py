import time
from typing import Tuple, Optional, Any
import numpy as np
import sherpa_onnx
from src.config import SttConfig

class SttEngine:
    """Wrapper around sherpa-onnx OfflineRecognizer with SenseVoice."""

    def __init__(self, config: Optional[SttConfig] = None):
        self.config = config or SttConfig()
        self.config.validate()

        t0 = time.perf_counter()
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.config.model_path,
            tokens=self.config.tokens_path,
            language=self.config.language,
            use_itn=self.config.use_itn,
            num_threads=self.config.num_threads,
            provider=self.config.provider,
        )
        self.cold_start_load_time_ms = (time.perf_counter() - t0) * 1000.0
        self.inference_count = 0
        self.total_audio_seconds = 0.0
        self.total_inference_time_seconds = 0.0

    def warm_up(self, duration_seconds: float = 1.0, timeout: Optional[float] = None) -> float:
        """Run a warm-up inference with dummy audio. Returns inference time in ms."""
        dummy_audio = np.zeros(int(16000 * duration_seconds), dtype=np.float32)
        effective_timeout = timeout if timeout is not None else self.config.warm_up_timeout_sec
        _, infer_ms = self.transcribe(dummy_audio, 16000, is_warmup=True, timeout=effective_timeout)
        return infer_ms

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int = 16000,
        is_warmup: bool = False,
        abort_event: Optional[Any] = None,
        timeout: Optional[float] = None
    ) -> Tuple[str, float]:
        """
        Transcribe audio samples (1D float32 array in [-1.0, 1.0]).
        Returns (recognized_text, inference_time_ms).
        Supports cooperative cancellation via abort_event.
        """
        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
            return "", 0.0

        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)

        effective_timeout = timeout if timeout is not None else self.config.request_timeout_sec
        t_start = time.perf_counter()
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self.recognizer.decode_stream(stream)
        t_end = time.perf_counter()

        infer_ms = (t_end - t_start) * 1000.0
        if effective_timeout and (infer_ms / 1000.0) > effective_timeout:
            raise TimeoutError(f"SttEngine inference timed out after {effective_timeout:.2f}s")
        text = stream.result.text.strip()


        if not is_warmup:
            self.inference_count += 1
            audio_dur = len(samples) / float(sample_rate)
            self.total_audio_seconds += audio_dur
            self.total_inference_time_seconds += (infer_ms / 1000.0)

        return text, infer_ms

    @property
    def cumulative_rtf(self) -> float:
        """Cumulative RTF across all non-warmup decodes."""
        if self.total_audio_seconds <= 0:
            return 0.0
        return self.total_inference_time_seconds / self.total_audio_seconds


def _stt_process_worker_loop(
    config: SttConfig,
    conn: Any,
    initial_uncooperative_delay: float = 0.0
):
    """
    Child process worker loop.
    Instantiates SenseVoice ONNX model in its own process space (macOS spawn safe).
    Processes IPC transcribe requests sequentially without reloading the model.
    """
    try:
        engine = SttEngine(config)
        conn.send(("ready", engine.cold_start_load_time_ms))
    except Exception as e:
        try:
            conn.send(("error", str(e)))
        except Exception:
            pass
        return

    uncooperative_delay = initial_uncooperative_delay

    while True:
        try:
            if not conn.poll(timeout=0.1):
                continue
            msg = conn.recv()
        except (EOFError, KeyboardInterrupt):
            break
        except Exception:
            break

        cmd = msg[0]
        if cmd == "transcribe":
            req_id, samples, sample_rate, is_warmup = msg[1], msg[2], msg[3], msg[4]
            req_timeout = msg[5] if len(msg) > 5 else None
            if uncooperative_delay > 0:
                # Simulates uncooperative native inference hang (e.g. C/ONNX stuck in decode_stream)
                # Does NOT check any cancellation token, strictly hangs for specified duration
                time.sleep(uncooperative_delay)
            try:
                text, infer_ms = engine.transcribe(
                    samples, sample_rate, is_warmup=is_warmup, timeout=req_timeout
                )
                conn.send(("result", req_id, text, infer_ms))
            except TimeoutError as te:
                try:
                    conn.send(("result_timeout", req_id, str(te), req_timeout))
                except Exception:
                    break
            except Exception as e:
                try:
                    conn.send(("result_error", req_id, str(e)))
                except Exception:
                    break

        elif cmd == "set_uncooperative_delay":
            uncooperative_delay = float(msg[1])
            conn.send(("delay_set", uncooperative_delay))

        elif cmd == "warm_up":
            duration_sec = msg[1]
            warm_up_timeout = msg[2] if len(msg) > 2 else None
            if uncooperative_delay > 0:
                time.sleep(uncooperative_delay)
            try:
                infer_ms = engine.warm_up(duration_sec, timeout=warm_up_timeout)
                conn.send(("warm_up_result", infer_ms))
            except TimeoutError as te:
                try:
                    conn.send(("warm_up_timeout", str(te), warm_up_timeout))
                except Exception:
                    break
            except Exception as e:
                try:
                    conn.send(("warm_up_error", str(e)))
                except Exception:
                    break

        elif cmd == "get_stats":
            conn.send(("stats", engine.inference_count, engine.total_audio_seconds, engine.total_inference_time_seconds, engine.cumulative_rtf))

        elif cmd == "stop":
            try:
                conn.send(("stopped",))
            except Exception:
                pass
            break


class IsolatedSttEngine:
    """
    Process-isolated STT Engine.
    Executes SenseVoice ONNX in a dedicated child process spawned via multiprocessing.
    Guarantees:
      1. Hard termination via SIGTERM/SIGKILL if inference hangs or exceeds deadline.
      2. No zombie worker threads left alive in parent process.
      3. Clean restartability within the same parent process.
      4. macOS spawn safety.
      5. Reuses the model process across utterances without per-utterance reload.
    """

    def __init__(self, config: Optional[SttConfig] = None, uncooperative_hang_sec: float = 0.0):
        self.config = config or SttConfig()
        self.config.validate()
        self.initial_uncooperative_delay = uncooperative_hang_sec
        import multiprocessing
        self._mp_ctx = multiprocessing.get_context("spawn")
        self._proc: Optional[multiprocessing.Process] = None
        self._conn: Optional[Any] = None
        import threading
        self._lock = threading.Lock()
        self.cold_start_load_time_ms = 0.0
        self.inference_count = 0
        self.total_audio_seconds = 0.0
        self.total_inference_time_seconds = 0.0
        self.last_roundtrip_ms = 0.0
        self.last_ipc_overhead_ms = 0.0
        self._req_counter = 0
        self.last_timing_event: Dict[str, Any] = {}

        self.ensure_started()

    def is_child_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.is_alive()

    @property
    def child_pid(self) -> Optional[int]:
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    def ensure_started(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.is_alive():
                return
            self._cleanup_conn_locked()
            parent_conn, child_conn = self._mp_ctx.Pipe(duplex=True)
            self._proc = self._mp_ctx.Process(
                target=_stt_process_worker_loop,
                args=(self.config, child_conn, self.initial_uncooperative_delay),
                name="stt_isolated_worker",
                daemon=True
            )
            self._proc.start()
            child_conn.close()
            self._conn = parent_conn

            if not self._conn.poll(timeout=15.0):
                self._terminate_locked()
                raise TimeoutError("STT worker process failed to initialize within 15.0s")

            try:
                status, val = self._conn.recv()
            except Exception as e:
                self._terminate_locked()
                raise RuntimeError("STT worker process failed during startup handshake") from e

            if status == "error":
                self._terminate_locked()
                raise RuntimeError(f"STT worker process failed to start: {val}")
            self.cold_start_load_time_ms = val

    def inject_uncooperative_delay(self, delay_sec: float) -> None:
        """Inject uncooperative delay in the child process to simulate non-cooperative native hang."""
        self.ensure_started()
        with self._lock:
            if self._conn is not None:
                self._conn.send(("set_uncooperative_delay", delay_sec))
                if self._conn.poll(timeout=2.0):
                    self._conn.recv()

    def clear_uncooperative_delay(self) -> None:
        """Clear any injected uncooperative delay in the child process."""
        self.inject_uncooperative_delay(0.0)

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int = 16000,
        is_warmup: bool = False,
        abort_event: Optional[Any] = None,
        timeout: Optional[float] = None
    ) -> Tuple[str, float]:
        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
            return "", 0.0

        self.ensure_started()

        effective_timeout = timeout if timeout is not None else self.config.request_timeout_sec
        audio_dur = len(samples) / float(sample_rate)

        with self._lock:
            self._req_counter += 1
            req_id = self._req_counter
            if samples.dtype != np.float32:
                samples = samples.astype(np.float32)

            t_req_start = time.perf_counter()
            self.last_timing_event = {
                "req_id": req_id,
                "t_req_issued": t_req_start,
                "effective_timeout": effective_timeout,
                "audio_duration_sec": audio_dur,
            }
            try:
                self._conn.send(("transcribe", req_id, samples, sample_rate, is_warmup, effective_timeout))
            except (BrokenPipeError, EOFError, OSError) as e:
                self._terminate_locked()
                raise RuntimeError("STT worker process connection failed") from e

            poll_interval = 0.02
            deadline = (t_req_start + effective_timeout) if effective_timeout else None

            while True:
                if abort_event is not None and abort_event.is_set():
                    self._terminate_locked()
                    return "", 0.0

                if deadline and time.perf_counter() > deadline:
                    t_timeout_detected = time.perf_counter()
                    self.last_timing_event["t_timeout_detected"] = t_timeout_detected
                    self._terminate_locked()
                    t_reap_completed = time.perf_counter()
                    self.last_timing_event["t_reap_completed"] = t_reap_completed
                    raise TimeoutError(
                        f"STT inference request #{req_id} timed out after {effective_timeout:.2f}s deadline "
                        f"(audio duration: {audio_dur:.2f}s, detected at +{t_timeout_detected - t_req_start:.3f}s)"
                    )

                if self._proc is None or not self._proc.is_alive():
                    self._terminate_locked()
                    raise RuntimeError("STT worker process died unexpectedly during inference")

                if self._conn.poll(timeout=poll_interval):
                    try:
                        msg = self._conn.recv()
                    except (EOFError, BrokenPipeError) as e:
                        self._terminate_locked()
                        raise RuntimeError("STT worker process terminated prematurely") from e

                    if msg[0] == "result":
                        _, res_req_id, text, infer_ms = msg
                        if res_req_id == req_id:
                            t_req_end = time.perf_counter()
                            parent_roundtrip_ms = (t_req_end - t_req_start) * 1000.0
                            ipc_overhead_ms = max(0.0, parent_roundtrip_ms - infer_ms)
                            self.last_roundtrip_ms = parent_roundtrip_ms
                            self.last_ipc_overhead_ms = ipc_overhead_ms
                            self.last_timing_event["t_req_completed"] = t_req_end
                            self.last_timing_event["infer_ms"] = infer_ms
                            self.last_timing_event["roundtrip_ms"] = parent_roundtrip_ms
                            self.last_timing_event["non_inference_overhead_ms"] = ipc_overhead_ms
                            if not is_warmup:
                                self.inference_count += 1
                                self.total_audio_seconds += audio_dur
                                self.total_inference_time_seconds += (infer_ms / 1000.0)
                            return text, infer_ms

                    elif msg[0] == "result_timeout":
                        _, res_req_id, err_msg, req_to = msg
                        t_timeout_detected = time.perf_counter()
                        self.last_timing_event["t_timeout_detected"] = t_timeout_detected
                        self._terminate_locked()
                        t_reap_completed = time.perf_counter()
                        self.last_timing_event["t_reap_completed"] = t_reap_completed
                        raise TimeoutError(
                            f"STT inference request #{res_req_id} timed out after {req_to:.2f}s deadline "
                            f"(child reported: {err_msg})"
                        )

                    elif msg[0] == "result_error":
                        _, res_req_id, err_msg = msg
                        self._terminate_locked()
                        raise RuntimeError(f"STT inference error: {err_msg}")

    def warm_up(self, duration_seconds: float = 1.0, timeout: Optional[float] = None) -> float:
        self.ensure_started()
        effective_timeout = timeout if timeout is not None else self.config.warm_up_timeout_sec
        with self._lock:
            try:
                self._conn.send(("warm_up", duration_seconds, effective_timeout))
            except (BrokenPipeError, EOFError, OSError) as e:
                self._terminate_locked()
                raise RuntimeError("STT worker process connection failed during warm_up") from e

            if self._conn.poll(timeout=effective_timeout):
                try:
                    msg = self._conn.recv()
                except (EOFError, BrokenPipeError) as e:
                    self._terminate_locked()
                    raise RuntimeError("STT worker process terminated during warm_up") from e
                if msg[0] == "warm_up_result":
                    return msg[1]
                elif msg[0] == "warm_up_timeout":
                    self._terminate_locked()
                    raise TimeoutError(f"STT warm_up timed out after {msg[2]:.2f}s deadline: {msg[1]}")
                elif msg[0] == "warm_up_error" or msg[0] == "result_error":
                    self._terminate_locked()
                    raise RuntimeError(f"STT warm_up error: {msg[1]}")

            # Explicit timeout failure: terminate child process and raise TimeoutError
            self._terminate_locked()
            raise TimeoutError(f"STT warm_up timed out after {effective_timeout:.2f}s deadline")


    @property
    def cumulative_rtf(self) -> float:
        if self.total_audio_seconds <= 0:
            return 0.0
        return self.total_inference_time_seconds / self.total_audio_seconds

    def terminate(self) -> None:
        """Forcibly terminate child process and clean up IPC."""
        with self._lock:
            self._terminate_locked()

    def _terminate_locked(self) -> None:
        if self._proc is not None:
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=0.5)
                if self._proc.is_alive():
                    self._proc.kill()
                    self._proc.join(timeout=0.5)
            self._proc = None
        self._cleanup_conn_locked()

    def close(self) -> None:
        """Orderly shutdown of child process."""
        with self._lock:
            if self._proc is not None and self._proc.is_alive() and self._conn is not None:
                try:
                    self._conn.send(("stop",))
                    if self._conn.poll(timeout=1.0):
                        self._conn.recv()
                except Exception:
                    pass
                self._proc.join(timeout=1.0)
                if self._proc.is_alive():
                    self._proc.terminate()
                    self._proc.join(timeout=0.5)
            self._proc = None
            self._cleanup_conn_locked()

    def _cleanup_conn_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __del__(self):
        try:
            self.terminate()
        except Exception:
            pass


class UncooperativeSttEngine(IsolatedSttEngine):
    """
    STT Engine variant that strictly hangs on transcribe without checking any abort token.
    Used for deterministic validation of process-level timeout and parent recovery.
    """
    def __init__(self, config: Optional[SttConfig] = None, hang_seconds: float = 10.0):
        super().__init__(config=config, uncooperative_hang_sec=hang_seconds)

