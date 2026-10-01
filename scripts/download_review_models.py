"""Download published models; verify checksums. Performs no quantization."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import urllib.request

RELEASE = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
ARCHIVE = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
HASHES = {'model.int8.onnx': 'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51',
          'tokens.txt': 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc',
          'silero_vad.onnx': '9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6'}


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, path):
    partial = path.with_suffix(path.suffix + '.part')
    with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as output:
        shutil.copyfileobj(response, output)
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'models')
    root = parser.parse_args().models_dir
    folder = root / 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
    folder.mkdir(parents=True, exist_ok=True)
    if any(not (folder / name).is_file() or checksum(folder / name) != HASHES[name]
           for name in ('model.int8.onnx', 'tokens.txt')):
        archive = root / ARCHIVE
        print('Downloading SenseVoice INT8 (about 163 MB)...', flush=True)
        download(RELEASE + ARCHIVE, archive)
        if checksum(archive) != '7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e':
            raise ValueError('Archive checksum mismatch')
        with tarfile.open(archive, 'r:bz2') as bundle:
            for item in bundle.getmembers():
                name = Path(item.name).name
                if item.isfile() and name in ('model.int8.onnx', 'tokens.txt'):
                    with bundle.extractfile(item) as source, (folder / name).open('wb') as dest:
                        shutil.copyfileobj(source, dest)
    vad = root / 'silero_vad.onnx'
    if not vad.is_file() or checksum(vad) != HASHES['silero_vad.onnx']:
        download(RELEASE + 'silero_vad.onnx', vad)
    for name, expected in HASHES.items():
        file = vad if name == vad.name else folder / name
        if checksum(file) != expected:
            raise ValueError(f'Model checksum mismatch: {name}')
    print('Models ready:', root.resolve())


if __name__ == '__main__':
    main()
