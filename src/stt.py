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

    def warm_up(self, duration_seconds: float = 1.0) -> float:
        """Run a warm-up inference with dummy audio. Returns inference time in ms."""
        dummy_audio = np.zeros(int(16000 * duration_seconds), dtype=np.float32)
        _, infer_ms = self.transcribe(dummy_audio, 16000, is_warmup=True)
        return infer_ms

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int = 16000,
        is_warmup: bool = False,
        abort_event: Optional[Any] = None
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

        t_start = time.perf_counter()
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self.recognizer.decode_stream(stream)
        t_end = time.perf_counter()

        infer_ms = (t_end - t_start) * 1000.0
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
