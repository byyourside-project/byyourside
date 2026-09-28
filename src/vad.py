"""
Voice Activity Detection module using Silero VAD (Revision 02).
Guarantees 100% sample preservation on hard-cut forced splitting (no audio discarded).
Supports stream sample index tracking and gap detection to prevent time-axis compression.
"""
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
    samples: np.ndarray   # 1D float32 array
    start_sample: int     # sample index in overall audio stream
    end_sample: int       # sample index in overall audio stream
    start_ms: float
    end_ms: float
    duration_ms: float
    endpoint_reason: str  # 'silence', 'soft_max_duration', 'hard_max_duration', 'hard_cut_continuation', 'flush', 'gap_forced_flush', 'direct_bypass'
    ready_ts: float       # monotonic timestamp when segment became available

class VadProcessor:
    """Wrapper around sherpa-onnx VoiceActivityDetector with Silero VAD."""

    def __init__(self, config: Optional[VadConfig] = None):
        self.config = config or VadConfig()
        self.config.validate()

        self._init_vad()
        self.segment_counter = 0
        self.samples_fed = 0
        self.vad_stream_offset = 0
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

    def process_chunk(
        self,
        chunk: np.ndarray,
        stream_sample_idx_start: Optional[int] = None
    ) -> List[Segment]:
        """
        Feed 16kHz float32 audio chunk to VAD and return any finalized segments.
        Window size is typically 512 samples (32ms).

        If stream_sample_idx_start is provided, detects any gap from dropped audio,
        flushes prior speech, and resynchronizes samples_fed to preserve true stream time-axis.
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        ready_segments: List[Segment] = []

        # C: Gap Detection and Clock Continuity
        if stream_sample_idx_start is not None and self.samples_fed > 0:
            gap_samples = stream_sample_idx_start - self.samples_fed
            if gap_samples > 0:
                # Audio chunks were dropped before this chunk!
                # 1. Flush any active speech from before the gap
                flushed = self.flush(endpoint_override="gap_forced_flush")
                ready_segments.extend(flushed)
                # 2. Resynchronize samples_fed and vad_stream_offset
                self.samples_fed = stream_sample_idx_start
                self.vad_stream_offset = stream_sample_idx_start
                self.current_speech_start_sample = None
                # Re-initialize VAD internal circular buffer state after gap
                self._init_vad()

        window = self.config.window_size

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
                            # Force cut at hard limit
                            self.vad.flush()
                            while not self.vad.empty():
                                segs = self._pop_segments_with_preservation(endpoint_override="hard_max_duration")
                                ready_segments.extend(segs)
                            self.current_speech_start_sample = None
                            self.vad_stream_offset = self.samples_fed
                            self._init_vad()
                            continue

            else:
                self.current_speech_start_sample = None

            while not self.vad.empty():
                segs = self._pop_segments_with_preservation()
                ready_segments.extend(segs)
                self.current_speech_start_sample = None

        return ready_segments

    def _pop_segments_with_preservation(self, endpoint_override: Optional[str] = None) -> List[Segment]:
        """
        Pop front segment from VAD.
        A [P1] FIX: If segment exceeds hard_max_speech_duration, splits it into
        primary segment + carryover segment(s).
        GUARANTEES 100% SAMPLE PRESERVATION (zero samples discarded).
        """
        raw_seg = self.vad.front
        all_samples = np.array(raw_seg.samples, dtype=np.float32)
        base_start_sample = self.vad_stream_offset + raw_seg.start
        self.vad.pop()

        if len(all_samples) == 0:
            return []

        produced_segments: List[Segment] = []
        max_allowed_samples = int(self.config.hard_max_speech_duration * self.config.sample_rate) if self.config.hard_max_speech_duration else None

        # Check if splitting is necessary
        if max_allowed_samples and len(all_samples) > max_allowed_samples:
            offset = 0
            is_first = True
            while offset < len(all_samples):
                chunk_len = min(max_allowed_samples, len(all_samples) - offset)
                seg_samples = all_samples[offset:offset + chunk_len]
                seg_start_sample = base_start_sample + offset
                seg_end_sample = seg_start_sample + chunk_len

                self.segment_counter += 1
                reason = "hard_max_duration" if is_first else "hard_cut_continuation"
                is_first = False

                start_ms = (seg_start_sample / float(self.config.sample_rate)) * 1000.0
                end_ms = (seg_end_sample / float(self.config.sample_rate)) * 1000.0
                dur_ms = (chunk_len / float(self.config.sample_rate)) * 1000.0

                produced_segments.append(Segment(
                    segment_id=self.segment_counter,
                    samples=seg_samples,
                    start_sample=seg_start_sample,
                    end_sample=seg_end_sample,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=dur_ms,
                    endpoint_reason=reason,
                    ready_ts=time.perf_counter()
                ))
                offset += chunk_len
        else:
            self.segment_counter += 1
            seg_len = len(all_samples)
            seg_end_sample = base_start_sample + seg_len

            start_ms = (base_start_sample / float(self.config.sample_rate)) * 1000.0
            end_ms = (seg_end_sample / float(self.config.sample_rate)) * 1000.0
            dur_ms = (seg_len / float(self.config.sample_rate)) * 1000.0

            if endpoint_override:
                reason = endpoint_override
            else:
                soft_limit_ms = self.config.max_speech_duration * 1000.0
                if dur_ms >= (soft_limit_ms - 100.0):
                    reason = "soft_max_duration"
                else:
                    reason = "silence"

            produced_segments.append(Segment(
                segment_id=self.segment_counter,
                samples=all_samples,
                start_sample=base_start_sample,
                end_sample=seg_end_sample,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=dur_ms,
                endpoint_reason=reason,
                ready_ts=time.perf_counter()
            ))

        return produced_segments

    def flush(self, endpoint_override: str = "flush") -> List[Segment]:
        """Flush buffered samples and finalize any remaining speech segment with 100% sample preservation."""
        self.vad.flush()
        ready_segments: List[Segment] = []
        while not self.vad.empty():
            segs = self._pop_segments_with_preservation(endpoint_override=endpoint_override)
            ready_segments.extend(segs)
        self.current_speech_start_sample = None
        return ready_segments

    def reset(self) -> None:
        """Reset VAD internal state and counters."""
        self.vad.reset()
        self.segment_counter = 0
        self.samples_fed = 0
        self.vad_stream_offset = 0
        self.current_speech_start_sample = None

