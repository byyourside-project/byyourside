from dataclasses import dataclass, field
from typing import Optional
import os

@dataclass
class VadConfig:
    """Silero VAD configuration."""
    model_path: str = "models/silero_vad.onnx"
    sample_rate: int = 16000
    threshold: float = 0.5
    min_silence_duration: float = 0.5    # seconds
    min_speech_duration: float = 0.25    # seconds
    max_speech_duration: float = 4.0     # seconds (VAD internal soft-cut trigger)
    hard_max_speech_duration: Optional[float] = 4.0  # seconds (enforced upper bound)
    window_size: int = 512               # samples (32ms at 16kHz)
    num_threads: int = 1
    provider: str = "cpu"

    def validate(self) -> None:
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"VAD model not found: {self.model_path}")
        if self.sample_rate != 16000:
            raise ValueError(f"Silero VAD expects 16000Hz, got {self.sample_rate}")
        if self.min_silence_duration <= 0:
            raise ValueError("min_silence_duration must be positive")
        if self.max_speech_duration <= 0:
            raise ValueError("max_speech_duration must be positive")

@dataclass
class SttConfig:
    """SenseVoice INT8 STT configuration."""
    model_path: str = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx"
    tokens_path: str = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt"
    language: str = "ko"
    use_itn: bool = True
    num_threads: int = 4
    provider: str = "cpu"
    request_timeout_sec: float = 2.0         # Streaming inference deadline per segment request (default 2.0s)
    warm_up_timeout_sec: float = 10.0        # Warm-up inference deadline (default 10.0s)
    batch_timeout_base_sec: float = 5.0      # Base timeout for direct/batch WAV inference
    batch_timeout_per_second: float = 1.0    # Additional timeout per second of audio in batch mode

    def validate(self) -> None:
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"STT model not found: {self.model_path}")
        if not os.path.exists(self.tokens_path):
            raise FileNotFoundError(f"STT tokens not found: {self.tokens_path}")
        if self.request_timeout_sec <= 0:
            raise ValueError("request_timeout_sec must be positive")
        if self.warm_up_timeout_sec <= 0:
            raise ValueError("warm_up_timeout_sec must be positive")

@dataclass
class AudioConfig:
    """Audio input & hardware configuration."""
    device_sample_rate: int = 48000
    target_sample_rate: int = 16000
    channels: int = 1
    chunk_size_samples: int = 1536       # 32ms at 48kHz (corresponds to 512 at 16kHz)
    device_index: Optional[int] = None   # None for default input device

@dataclass
class QueueConfig:
    """Audio & segment queue configuration."""
    max_audio_queue_size: int = 300      # audio chunks (~9.6 seconds buffer)
    max_segment_queue_size: int = 100    # segments
    put_timeout: float = 0.5             # timeout when putting to queues

@dataclass
class PipelineConfig:
    """Unified pipeline configuration."""
    vad: VadConfig = field(default_factory=VadConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    log_dir: str = "logs"
    worker_join_timeout_sec: float = 2.0     # Final worker thread join deadline after feeder finishes

    def validate(self) -> None:
        self.vad.validate()
        self.stt.validate()

