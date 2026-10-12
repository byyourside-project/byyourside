"""Restore official Korean test audio and the existing forced-cut fixture."""
import argparse
import hashlib
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.download_review_models import ARCHIVE, RELEASE, download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, default=ROOT / 'models' / ARCHIVE)
    archive = parser.parse_args().archive
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        download(RELEASE + ARCHIVE, archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != '7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e':
        raise ValueError('Official archive checksum mismatch')
    target = ROOT / 'models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:bz2') as bundle:
        member = next(m for m in bundle.getmembers() if m.isfile() and m.name.endswith('/test_wavs/ko.wav'))
        target.write_bytes(bundle.extractfile(member).read())
    import numpy as np
    from scipy.io import wavfile
    from src.audio_utils import load_and_normalize_audio
    samples, sr = load_and_normalize_audio(str(target), target_sr=16000)
    speech = samples[int(.8 * sr):int(3.69 * sr)]
    fixture = ROOT / 'logs/test_fixtures/temp_forced_cutoff.wav'
    fixture.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(fixture, sr, (np.concatenate([speech, speech]) * 32767).astype(np.int16))
    print('Official Korean example and forced-cut fixture ready. No personal recordings used.')


if __name__ == '__main__':
    main()
