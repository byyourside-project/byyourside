"""One isolated file-analysis process; parent enforces a wall-clock deadline."""
import json
import sys
from pathlib import Path

from src.review_audio import analyze_file
from src.review_media import prepare_media, write_silent_wav


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def progress(job, phase, message):
    write_json(job / 'progress.json', {'phase': phase, 'message': message})


def main():
    job = Path(sys.argv[1])
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    try:
        progress(job, 'preparing', '재생할 영상·음성을 준비하고 있어요.')
        media = prepare_media(job / request['source'], job)
        progress(job, 'audio', '말한 구간과 말하기 속도를 분석하고 있어요.')
        if media['has_audio']:
            raw = analyze_file(media['analysis_source'], job / 'audio.wav', request['models_dir'])
        else:
            write_silent_wav(job / 'audio.wav', media['duration'])
            raw = {'duration': media['duration'], 'segments': [], 'engine': 'no_audio',
                   'media_warnings': ['영상에 음성 트랙이 없어 말하기 속도는 분석하지 않았습니다.']}
        raw['media'] = {key: value for key, value in media.items() if key not in {'analysis_source', 'playback_file'}}
        if media['kind'] == 'video':
            # Audio packet rounding can differ by a few milliseconds from the
            # container duration. One report clock must cover both analyses.
            raw['audio_duration'] = raw['duration']
            raw['duration'] = max(raw['duration'], media['duration'])
            progress(job, 'gaze', '영상에서 얼굴과 카메라 방향을 확인하고 있어요.')
            from src.review_gaze import analyze_gaze
            raw['gaze'] = analyze_gaze(job / 'video.mp4', request['models_dir'], media['duration'], request.get('gaze_options'))
        raw['title'] = request['title']
        progress(job, 'finishing', '분석 결과와 다시 볼 구간을 정리하고 있어요.')
        write_json(job / 'raw.json', raw)
    except Exception as exc:
        write_json(job / 'error.json', {'error': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
