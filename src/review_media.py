"""Normalize uploaded media to one playback/analysis clock; keep originals intact."""
import math
import re
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg

MAX_DURATION = 1200
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}
INPUT_RESTRICTIONS = ['-protocol_whitelist', 'file,pipe', '-format_whitelist',
                      'mov,matroska,webm,wav,mp3,flac,ogg,aac,avi']


def probe_media(path):
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-nostdin', *INPUT_RESTRICTIONS, '-i', str(path)],
        capture_output=True, timeout=30,
    )
    info = result.stderr.decode('utf-8', errors='replace')
    duration_match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', info)
    if not duration_match:
        raise ValueError('파일 길이를 읽지 못했습니다. 정상적인 영상·음성 파일을 선택해 주세요.')
    hours, minutes, seconds = map(float, duration_match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError('20분 이하의 영상·녹음을 선택해 주세요.')
    streams = [line for line in info.splitlines() if re.search(r'Stream #\d+:\d+', line)]
    has_video = any('Video:' in line and 'attached pic' not in line for line in streams)
    has_audio = any('Audio:' in line for line in streams)
    if not has_video and not has_audio:
        raise ValueError('파일에서 영상이나 음성을 찾지 못했습니다.')
    return {'duration': duration, 'has_video': has_video, 'has_audio': has_audio}


def prepare_media(source, folder):
    """Create H.264/AAC MP4 for videos. Analyze that same file to keep seek times aligned."""
    source, folder = Path(source), Path(folder)
    original = probe_media(source)
    if not original['has_video']:
        return {**original, 'kind': 'audio', 'analysis_source': str(source), 'playback_file': 'audio.wav'}
    output = folder / 'video.mp4'
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-copyts', '-start_at_zero', *INPUT_RESTRICTIONS, '-i', str(source), '-map', '0:V:0', '-map', '0:a:0?',
               '-t', str(MAX_DURATION), '-vf',
               "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
               '-r', '24', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '2',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k']
    if original['has_audio']:
        # Pad a short audio track so the video can still be reviewed until its final frame.
        command += ['-af', 'aresample=async=1:first_pts=0,apad', '-shortest']
    command += ['-movflags', '+faststart', '-map_metadata', '-1', str(output)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError('영상 준비에 실패했습니다. 손상되지 않은 MP4·MOV 파일로 다시 시도해 주세요.') from exc
    prepared = probe_media(output)
    return {**prepared, 'kind': 'video', 'analysis_source': str(output), 'playback_file': 'video.mp4',
            'original_duration': original['duration'], 'normalized': True}


def write_silent_wav(path, duration):
    """Keep audio compatibility for a video which truly has no audio track."""
    frames = round(duration * 16000)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        while frames:
            size = min(frames, 16000)
            output.writeframesraw(bytes(size * 2))
            frames -= size
