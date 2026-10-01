"""One isolated file-analysis process; parent enforces a wall-clock deadline."""
import json
import sys
from pathlib import Path

from src.review_audio import analyze_file


def main():
    job = Path(sys.argv[1])
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    try:
        raw = analyze_file(job / request['source'], job / 'audio.wav', request['models_dir'])
        raw['title'] = request['title']
        (job / 'raw.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    except Exception as exc:
        (job / 'error.json').write_text(json.dumps({'error': str(exc)}, ensure_ascii=False), encoding='utf-8')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
