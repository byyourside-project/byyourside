"""File-based adapter reusing the project's VAD and STT engines."""
import math
import subprocess
import wave
from pathlib import Path

import numpy as np


def model_paths(root):
    root = Path(root)
    folder = root / 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
    return folder / 'model.int8.onnx', folder / 'tokens.txt', root / 'silero_vad.onnx'


def analyze_file(source, wav_path, models_dir):
    import imageio_ffmpeg
    from src.config import SttConfig, VadConfig
    from src.stt import SttEngine
    from src.vad import VadProcessor

    model, tokens, vad_path = model_paths(models_dir)
    for file in (model, tokens, vad_path):
        if not file.is_file():
            raise ValueError('음성 모델이 준비되지 않았습니다. 모델 준비 안내를 확인해 주세요.')
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
               '-nostdin', '-y', '-i', str(source), '-map', '0:a:0', '-t', '1201',
               '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav_path)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError('오디오를 읽지 못했습니다. WAV, M4A, MP3 등의 음성 파일을 선택해 주세요.') from exc
    with wave.open(str(wav_path), 'rb') as reader:
        x = np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2').astype(np.float32) / 32768
    duration = len(x) / 16000
    if not 0 < duration <= 1200:
        raise ValueError('20분 이하의 녹음을 선택해 주세요.')
    peak = float(np.max(np.abs(x)))
    peak_dbfs = 20 * math.log10(max(peak, 1e-12))
    # Uniform gain for inference only. Playback retains the decoded original level.
    gain = min(10 ** (18 / 20), .8 / max(peak, 1e-9)) if peak_dbfs < -18 else 1.0
    samples = (x * gain).astype(np.float32)
    vad = VadProcessor(VadConfig(model_path=str(vad_path), min_silence_duration=.35,
                                 min_speech_duration=.2, max_speech_duration=15,
                                 hard_max_speech_duration=15))
    detected = []
    for start in range(0, len(samples), 512):
        detected.extend(vad.process_chunk(samples[start:start + 512], stream_sample_idx_start=start))
    detected.extend(vad.flush())
    if len(detected) > 240:
        raise ValueError('발화 구간이 너무 많습니다. 녹음을 짧게 나눠 주세요.')
    stt = SttEngine(SttConfig(model_path=str(model), tokens_path=str(tokens), num_threads=2,
                             request_timeout_sec=30)) if detected else None
    segments = []
    for segment in detected:
        start = max(0, segment.start_sample)
        end = min(len(samples), segment.end_sample)
        if start >= end:
            continue
        padded = samples[max(0, start - 2400):min(len(samples), end + 2400)]
        text, _ = stt.transcribe(padded, 16000, timeout=30)
        segments.append({'start': start / 16000, 'end': end / 16000, 'text': text})
    blocks = np.array_split(x, min(360, len(x)))
    waveform = [float(np.sqrt(np.mean(block * block))) for block in blocks]
    maximum = max(waveform, default=0)
    waveform = [round(value / maximum, 4) if maximum else 0 for value in waveform]
    return {'duration': duration, 'peak_dbfs': peak_dbfs, 'segments': segments,
            'waveform': waveform, 'inference_gain_db': round(20 * math.log10(gain), 2),
            'engine': 'Silero VAD + SenseVoice 2024-07-17 / CPU'}
