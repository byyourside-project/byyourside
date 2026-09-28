import time
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
import sherpa_onnx
from src.config import VadConfig

@dataclass
class Segment:
    """Represents a speech segment detected by VAD."""
    segment_id: int
    samples: np.ndarray
    start_sample: int     # sample index in overall audio stream
    end_sample: int       # sample index in overall audio stream
    start_ms: float
    end_ms: float
    duration_ms: float
    endpoint_reason: str  # 'silence', 'soft_max_duration', 'hard_max_duration', 'flush', 'direct_bypass'
    ready_ts: float       # monotonic timestamp when segment became available

class VadProcessor:
    """Wrapper around sherpa-onnx VoiceActivityDetector with Silero VAD."""

    def __init__(self, config: Optional[VadConfig] = None):
        self.config = config or VadConfig()
        self.config.validate()

        self._init_vad()
        self.segment_counter = 0
        self.samples_fed = 0
        self.current_speech_start_sample: Optional[int] = None

    def _init_vad(self) -> None:
        vad_model_config = sherpa_onnx.VadModelConfig()
        vad_model_config.silero_vad.model = self.config.model_path
        vad_model_config.silero_vad.threshold = self.config.threshold
        vad_model_config.silero_vad.min_silence_duration = self.config.min_silence_duration
        vad_model_config.silero_vad.min_speech_duration = self.config.min_speech_duration
        vad_model_config.silero_vad.max_speech_duration = self.config.max_speech_duration
        vad_model_config.silero_vad.window_size = self.config.window_size

        vad_model_config.sample_rate = self.config.sample_rate
        vad_model_config.num_threads = self.config.num_threads
        vad_model_config.provider = self.config.provider

        self.vad = sherpa_onnx.VoiceActivityDetector(
            vad_model_config,
            buffer_size_in_seconds=60.0
        )

    def process_chunk(self, chunk: np.ndarray) -> List[Segment]:
        """
        Feed 16kHz float32 audio chunk to VAD and return any finalized segments.
        Window size is typically 512 samples (32ms).
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        window = self.config.window_size
        ready_segments: List[Segment] = []

        for i in range(0, len(chunk), window):
            sub_chunk = chunk[i:i + window]
            if len(sub_chunk) < window:
                sub_chunk = np.pad(sub_chunk, (0, window - len(sub_chunk)))

            chunk_start_sample = self.samples_fed
            self.samples_fed += len(sub_chunk)

            self.vad.accept_waveform(sub_chunk)

            # Check if speech is currently active
            if self.vad.is_speech_detected():
                if self.current_speech_start_sample is None:
                    self.current_speech_start_sample = chunk_start_sample
                else:
                    if self.config.hard_max_speech_duration is not None:
                        curr_dur_sec = (self.samples_fed - self.current_speech_start_sample) / float(self.config.sample_rate)
                        if curr_dur_sec >= self.config.hard_max_speech_duration:
                            self.vad.flush()
                            while not self.vad.empty():
                                seg = self._pop_segment(endpoint_override="hard_max_duration")
                                if seg is not None:
                                    ready_segments.append(seg)
                            self.current_speech_start_sample = None
                            continue
            else:
                self.current_speech_start_sample = None

            while not self.vad.empty():
                seg = self._pop_segment()
                if seg is not None:
                    ready_segments.append(seg)
                    self.current_speech_start_sample = None

        return ready_segments

    def _pop_segment(self, endpoint_override: Optional[str] = None) -> Optional[Segment]:
        """Pop front segment from VAD and wrap in Segment dataclass."""
        raw_seg = self.vad.front
        seg_samples = np.array(raw_seg.samples, dtype=np.float32)
        start_sample = raw_seg.start
        self.vad.pop()

        if len(seg_samples) == 0:
            return None

        # Enforce hard upper bound if requested
        if endpoint_override == "hard_max_duration" and self.config.hard_max_speech_duration is not None:
            max_allowed_samples = int(self.config.hard_max_speech_duration * self.config.sample_rate)
            if len(seg_samples) > max_allowed_samples:
                seg_samples = seg_samples[:max_allowed_samples]

        self.segment_counter += 1
        seg_len = len(seg_samples)
        end_sample = start_sample + seg_len

        start_ms = (start_sample / float(self.config.sample_rate)) * 1000.0
        end_ms = (end_sample / float(self.config.sample_rate)) * 1000.0
        dur_ms = (seg_len / float(self.config.sample_rate)) * 1000.0

        if endpoint_override:
            reason = endpoint_override
        else:
            soft_limit_ms = self.config.max_speech_duration * 1000.0
            if dur_ms >= (soft_limit_ms - 100.0):
                reason = "soft_max_duration"
            else:
                reason = "silence"

        return Segment(
            segment_id=self.segment_counter,
            samples=seg_samples,
            start_sample=start_sample,
            end_sample=end_sample,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=dur_ms,
            endpoint_reason=reason,
            ready_ts=time.perf_counter()
        )

    def flush(self) -> List[Segment]:
        """Flush buffered samples and finalize any remaining speech segment."""
        self.vad.flush()
        ready_segments: List[Segment] = []
        while not self.vad.empty():
            seg = self._pop_segment(endpoint_override="flush")
            if seg is not None:
                ready_segments.append(seg)
        self.current_speech_start_sample = None
        return ready_segments

    def reset(self) -> None:
        """Reset VAD internal state and counters."""
        self.vad.reset()
        self.segment_counter = 0
        self.samples_fed = 0
        self.current_speech_start_sample = None
