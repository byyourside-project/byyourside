"""Download Google's versioned Face Landmarker bundle; no conversion/quantization."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
             'face_landmarker/float16/1/face_landmarker.task')
# Verified against the official HTTPS artifact, 2026-10-05 (3,758,596 bytes).
MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'


def download(models_dir):
    destination = Path(models_dir) / 'face_landmarker.task'
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == MODEL_SHA256:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.task.part')
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, temporary.open('wb') as output:
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise ValueError('얼굴 모델 다운로드 크기가 예상 범위를 벗어났습니다.')
                output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('얼굴 모델 체크섬이 일치하지 않습니다. 파일을 사용하지 않습니다.')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'models')
    args = parser.parse_args()
    print(f'Face Landmarker ready: {download(args.models_dir)}')
