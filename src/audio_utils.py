"""
Audio loading, normalization, and resampling utilities for Task 01.
Addresses R4: Ensures proper PCM integer normalization before channel downmix.
"""
from typing import Tuple, Union
import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as signal

def load_and_normalize_audio(
    source: Union[str, Tuple[int, np.ndarray]],
    target_sr: int = 16000
) -> Tuple[np.ndarray, int]:
    """
    Load and normalize audio data to mono float32 in range [-1.0, 1.0] at target_sr.

    Ensures that integer PCM (int16, int32) is normalized to float32 BEFORE downmixing channels,
    avoiding the bug where ndarray.mean() converts integer PCM to unnormalized float64.

    Args:
        source: File path (str) or a tuple of (sample_rate: int, raw_data: np.ndarray).
        target_sr: Desired output sample rate in Hz (default: 16000).

    Returns:
        (samples, sample_rate): 1-D np.ndarray of float32, and the sample rate (int).

    Raises:
        ValueError: If audio dtype is unsupported or dimensions exceed 2.
    """
    if isinstance(source, str):
        sr, raw_data = wavfile.read(source)
    elif isinstance(source, tuple) and len(source) == 2:
        sr, raw_data = source
    else:
        raise ValueError("source must be a file path string or (sample_rate, np.ndarray) tuple")

    if raw_data.ndim > 2:
        raise ValueError(f"Audio has {raw_data.ndim} dimensions; only 1D mono or 2D stereo supported")

    # Step 1: Normalize based on original PCM dtype BEFORE channel downmixing
    if raw_data.dtype == np.int16:
        float_data = raw_data.astype(np.float32) / 32768.0
    elif raw_data.dtype == np.int32:
        float_data = raw_data.astype(np.float32) / 2147483648.0
    elif raw_data.dtype == np.float32:
        float_data = np.clip(raw_data, -1.0, 1.0).astype(np.float32)
    elif raw_data.dtype == np.float64:
        float_data = np.clip(raw_data, -1.0, 1.0).astype(np.float32)
    elif raw_data.dtype == np.uint8:
        # 8-bit unsigned PCM: centered at 128
        float_data = (raw_data.astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported audio dtype: {raw_data.dtype}. Only int16, int32, float32, float64 supported.")

    # Step 2: Downmix to mono if multi-channel
    if float_data.ndim > 1:
        float_data = np.mean(float_data, axis=-1, dtype=np.float32)

    # Step 3: Resample if sample rate does not match target_sr
    if sr != target_sr:
        gcd = np.gcd(sr, target_sr)
        up = target_sr // gcd
        down = sr // gcd
        float_data = signal.resample_poly(float_data, up, down).astype(np.float32)
        sr = target_sr

    return float_data, sr
