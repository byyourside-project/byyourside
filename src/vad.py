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
    start_sample: int
    end_sample: int
    start_ms: float
    end_ms: float
    duration_ms: float
    endpoint_reason: str  # 'silence', 'max_duration', 'flush'
    ready_ts: float       # monotonic timestamp when segment became available

class VadProcessor:
    """Wrapper around sherpa-onnx VoiceActivityDetector with Silero VAD."""

    def __init__(self, config: Optional[VadConfig] = None):
        self.config = config or VadConfig()
        self.config.validate()

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
        self.segment_counter = 0
        self.samples_processed = 0

    def process_chunk(self, chunk: np.ndarray) -> List[Segment]:
        """
        Feed 16kHz float32 audio chunk to VAD and return any finalized segments.
        Chunk should typically be a multiple of window_size (e.g. 512).
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        window = self.config.window_size
        ready_segments = []

        # Feed in window_size increments
        for i in range(0, len(chunk), window):
            sub_chunk = chunk[i:i + window]
            if len(sub_chunk) < window:
                sub_chunk = np.pad(sub_chunk, (0, window - len(sub_chunk)))

            self.vad.accept_waveform(sub_chunk)
            self.samples_processed += len(sub_chunk)

            while not self.vad.empty():
                seg = self.vad.front
                seg_samples = np.array(seg.samples, dtype=np.float32)
                self.vad.pop()

                if len(seg_samples) == 0:
                    continue

                self.segment_counter += 1
                seg_start_sample = seg.start
                seg_len = len(seg_samples)
                seg_end_sample = seg_start_sample + seg_len

                start_ms = (seg_start_sample / self.config.sample_rate) * 1000.0
                end_ms = (seg_end_sample / self.config.sample_rate) * 1000.0
                dur_ms = (seg_len / self.config.sample_rate) * 1000.0

                # Determine endpoint reason: if duration is near max_speech_duration, it's max_duration
                max_dur_ms = self.config.max_speech_duration * 1000.0
                if dur_ms >= (max_dur_ms - 100.0):
                    reason = "max_duration"
                else:
                    reason = "silence"

                segment = Segment(
                    segment_id=self.segment_counter,
                    samples=seg_samples,
                    start_sample=seg_start_sample,
                    end_sample=seg_end_sample,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=dur_ms,
                    endpoint_reason=reason,
                    ready_ts=time.perf_counter()
                )
                ready_segments.append(segment)

        return ready_segments

    def flush(self) -> List[Segment]:
        """Flush buffered samples and finalize remaining speech segment."""
        self.vad.flush()
        ready_segments = []
        while not self.vad.empty():
            seg = self.vad.front
            seg_samples = np.array(seg.samples, dtype=np.float32)
            self.vad.pop()

            if len(seg_samples) == 0:
                continue

            self.segment_counter += 1
            seg_start_sample = seg.start
            seg_len = len(seg_samples)
            seg_end_sample = seg_start_sample + seg_len

            start_ms = (seg_start_sample / self.config.sample_rate) * 1000.0
            end_ms = (seg_end_sample / self.config.sample_rate) * 1000.0
            dur_ms = (seg_len / self.config.sample_rate) * 1000.0

            segment = Segment(
                segment_id=self.segment_counter,
                samples=seg_samples,
                start_sample=seg_start_sample,
                end_sample=seg_end_sample,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=dur_ms,
                endpoint_reason="flush",
                ready_ts=time.perf_counter()
            )
            ready_segments.append(segment)
        return ready_segments

    def reset(self) -> None:
        """Reset VAD internal state and counters."""
        self.vad.reset()
        self.segment_counter = 0
        self.samples_processed = 0
