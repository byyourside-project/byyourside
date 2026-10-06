"""Local camera-facing proxy from sparse video frames, never an eye-contact score.

The MediaPipe model predicts face/iris landmarks, not a gaze target. The conservative
rules below are transparent engineering defaults awaiting presentation-specific
validation. Unknown observations are excluded from the facing ratio denominator.
"""
import math
from pathlib import Path
import statistics
import subprocess
import tempfile
import threading

MODEL_NAME = 'face_landmarker.task'
REASONS = {
    'camera_facing': ('정면 추정', '얼굴 방향과 눈의 위치가 카메라 정면에 가까운 구간입니다.'),
    'head_away': ('얼굴 방향 확인', '얼굴이 정면에서 벗어난 것으로 추정됩니다. 영상을 확인해 주세요.'),
    'eyes_away': ('눈 방향 확인', '눈의 위치가 중앙에서 벗어난 것으로 추정됩니다. 영상을 확인해 주세요.'),
    'boundary': ('판단 보류', '정면과 벗어남의 경계에 있어 방향을 확정하지 않았습니다.'),
    'no_face': ('얼굴 미검출', '얼굴을 찾지 못해 시선 방향을 판단하지 않았습니다.'),
    'multiple_faces': ('여러 얼굴', '발표자를 구분할 수 없어 판단하지 않았습니다.'),
    'small_face': ('얼굴이 작음', '얼굴이나 눈이 작게 보여 판단하지 않았습니다.'),
    'poor_visibility': ('눈·얼굴 확인 어려움', '눈 감김, 가림, 화면 경계 등으로 판단이 어렵습니다.'),
    'no_geometry': ('방향 추정 어려움', '얼굴 방향 정보를 얻지 못했습니다.'),
    'not_sampled': ('분석 정보 없음', '이 시간대에는 사용할 수 있는 영상 정보가 없습니다.'),
    'calibration': ('정면 기준 설정', '사용자가 지정한 정면 기준 구간입니다. 평가 비율에서는 제외합니다.'),
}
LIMITATIONS = [
    '카메라를 눈높이 정면에 놓고 한 사람을 촬영한 경우를 전제로 합니다.',
    '얼굴·홍채 위치로 추정한 카메라 방향이며, 실제 시선 도착점이나 청중과의 눈맞춤을 측정하지 않습니다.',
    '안경 반사, 가림, 작은 얼굴, 조명, 카메라 각도에 따라 오차가 생길 수 있습니다.',
    '지속적으로 정면에서 벗어난 구간은 다시 볼 후보입니다. 자료나 청중을 보는 행동을 잘못된 발표로 단정하지 않습니다.',
    '샘플 사이의 짧은 변화는 놓칠 수 있으며 구간 시작·끝 시각에는 샘플 간격만큼 오차가 생길 수 있습니다.',
]


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f'{name} 값이 올바르지 않습니다.')
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} 값이 허용 범위를 벗어났습니다.')
    return float(value)


def gaze_options(options=None):
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError('시선 분석 설정이 올바르지 않습니다.')
    return {
        'sample_fps': _number(options.get('sample_fps', 2), '영상 샘플링', 1, 4),
        'sustained_seconds': _number(options.get('sustained_seconds', 2), '지속 시간', 1, 10),
        # Setting this is a user's explicit assertion, never an automatic guess.
        'calibration_seconds': _number(options.get('calibration_seconds', 0), '정면 기준 시간', 0, 5),
    }


def _point(landmark):
    if isinstance(landmark, dict):
        return float(landmark['x']), float(landmark['y'])
    return float(landmark.x), float(landmark.y)


def extract_geometry(landmarks, matrix, width, height):
    """Return geometry or a reason for withholding judgment. No biometric storage."""
    if len(landmarks) < 478:
        return None, 'no_geometry'
    try:
        points = [_point(p) for p in landmarks]
        if not all(math.isfinite(v) for p in points for v in p):
            return None, 'no_geometry'
        # Use face oval rather than an inferred bounding box containing iris outliers.
        oval = [points[i] for i in (10, 152, 234, 454, 127, 356)]
        if any(not .01 <= v <= .99 for p in oval for v in p):
            return None, 'poor_visibility'
        if (max(p[0] for p in oval) - min(p[0] for p in oval)) * width < 80:
            return None, 'small_face'
        r = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
        if not all(math.isfinite(v) for row in r for v in row):
            return None, 'no_geometry'
        # A canonical forward normal transformed into camera coordinates. Normalize
        # away the uniform scale of MediaPipe's rendering transformation.
        norm = math.sqrt(sum(r[i][2] ** 2 for i in range(3)))
        if norm < 1e-6:
            return None, 'no_geometry'
        nx, ny, nz = (r[i][2] / norm for i in range(3))
        yaw = math.degrees(math.atan2(nx, nz))
        pitch = math.degrees(math.atan2(-ny, math.hypot(nx, nz)))
        eye_values = []
        for a, b, top, bottom, iris in ((33, 133, 159, 145, 468), (362, 263, 386, 374, 473)):
            pa, pb = points[a], points[b]
            dx, dy = (pb[0] - pa[0]) * width, (pb[1] - pa[1]) * height
            length = math.hypot(dx, dy)
            if length < 10:
                return None, 'small_face'
            ex, ey = dx / length, dy / length
            # Keep the image's downward axis under in-plane head rotation.
            vx, vy = -ey, ex
            if vy < 0:
                vx, vy = -vx, -vy
            top_xy, bottom_xy, pupil = points[top], points[bottom], points[iris]
            opening = abs((bottom_xy[0] - top_xy[0]) * width * vx +
                          (bottom_xy[1] - top_xy[1]) * height * vy) / length
            if opening < .10:
                return None, 'poor_visibility'
            horizontal = ((pupil[0] - pa[0]) * width * ex + (pupil[1] - pa[1]) * height * ey) / length
            center_x = (top_xy[0] + bottom_xy[0]) / 2
            center_y = (top_xy[1] + bottom_xy[1]) / 2
            vertical = ((pupil[0] - center_x) * width * vx + (pupil[1] - center_y) * height * vy) / length
            if not -.05 <= horizontal <= 1.05 or abs(vertical) > opening / 2 + .10:
                return None, 'poor_visibility'
            eye_values.append((horizontal - .5, vertical))
        # Strongly inconsistent eye estimates are not useful gaze evidence.
        if abs(eye_values[0][0] - eye_values[1][0]) > .20:
            return None, 'poor_visibility'
        return {'yaw': yaw, 'pitch': pitch,
                'iris_x': statistics.mean(p[0] for p in eye_values),
                'iris_y': statistics.mean(p[1] for p in eye_values)}, None
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return None, 'no_geometry'


def classify_geometry(geometry, baseline=None):
    if not geometry or not all(math.isfinite(geometry.get(k, float('nan')))
                               for k in ('yaw', 'pitch', 'iris_x', 'iris_y')):
        return 'unknown', 'no_geometry'
    baseline = baseline or {'yaw': 0, 'pitch': 0, 'iris_x': 0, 'iris_y': 0}
    yaw, pitch, ix, iy = [abs(geometry[k] - baseline[k]) for k in ('yaw', 'pitch', 'iris_x', 'iris_y')]
    if yaw >= 25 or pitch >= 22:
        return 'away', 'head_away'
    if ix >= .18 or iy >= .12:
        return 'away', 'eyes_away'
    if yaw <= 18 and pitch <= 15 and ix <= .11 and iy <= .07:
        return 'camera_facing', 'camera_facing'
    return 'unknown', 'boundary'


def _interval(start, end, status, reason):
    label, detail = REASONS.get(reason, REASONS['not_sampled'])
    return {'start': round(start, 3), 'end': round(end, 3), 'status': status,
            'reason': reason, 'label': label, 'detail': detail}


def aggregate_samples(samples, duration, sample_fps=2, sustained_seconds=2):
    """Time-weighted sample buckets; missing frames interrupt away events."""
    duration = _number(duration, '영상 길이', .001, 1200)
    sample_fps = _number(sample_fps, '영상 샘플링', 1, 4)
    sustained_seconds = _number(sustained_seconds, '지속 시간', 1, 10)
    period, cursor, intervals = 1 / sample_fps, 0.0, []
    def append(start, end, status, reason):
        if end <= start:
            return
        if intervals and intervals[-1]['status'] == status and intervals[-1]['reason'] == reason and abs(intervals[-1]['end'] - start) < .002:
            intervals[-1]['end'] = round(end, 3)
        else:
            intervals.append(_interval(start, end, status, reason))
    for sample in samples:
        start = _number(sample['time'], '샘플 시간', 0, duration)
        if start < cursor - .002:
            raise ValueError('영상 샘플 시간이 순서에 맞지 않습니다.')
        append(cursor, start, 'unknown', 'not_sampled')
        end = min(duration, start + period)
        status = sample.get('status', 'unknown')
        if status not in ('camera_facing', 'away', 'unknown'):
            raise ValueError('영상 분석 상태가 올바르지 않습니다.')
        append(start, end, status, sample.get('reason', 'not_sampled'))
        cursor = end
    append(cursor, duration, 'unknown', 'not_sampled')
    totals = {state: sum(p['end'] - p['start'] for p in intervals if p['status'] == state)
              for state in ('camera_facing', 'away', 'unknown')}
    # Different away causes may form one sustained event, but unknown never bridges.
    away_runs = []
    for part in intervals:
        if part['status'] != 'away':
            continue
        if away_runs and abs(away_runs[-1]['end'] - part['start']) < .002:
            away_runs[-1]['end'] = part['end']
        else:
            away_runs.append({'start': part['start'], 'end': part['end']})
    events = [dict(p, kind='away', label='정면에서 벗어난 구간',
                   summary=f"약 {p['end'] - p['start']:.1f}초 동안 얼굴 또는 눈이 정면에서 벗어난 것으로 추정됩니다.")
              for p in away_runs if p['end'] - p['start'] >= sustained_seconds - .001]
    analyzed = totals['camera_facing'] + totals['away']
    coverage = analyzed / duration
    enough = coverage >= .5 and analyzed >= min(3, duration)
    if not enough:
        summary = '방향을 판단할 수 있는 영상이 부족합니다. 얼굴과 양쪽 눈이 보이는 촬영 상태를 확인해 주세요.'
    elif events:
        summary = f'정면에서 {sustained_seconds:g}초 이상 벗어난 것으로 보이는 구간 {len(events)}곳을 다시 확인해 주세요.'
    else:
        summary = f'분석 가능한 구간에서 {sustained_seconds:g}초 이상 이어지는 정면 이탈은 발견하지 못했습니다.'
    return {'status': 'complete' if enough else 'insufficient_evidence',
            'label': '시선 방향 검토' if enough else '시선 판단 자료 부족', 'summary': summary,
            'duration': duration, 'sample_fps': sample_fps, 'sample_count': len(samples),
            'coverage_ratio': round(coverage, 4), 'analyzed_seconds': round(analyzed, 3),
            'unknown_seconds': round(totals['unknown'], 3),
            'camera_facing_ratio': round(totals['camera_facing'] / analyzed, 4) if enough else None,
            'away_seconds': round(totals['away'], 3),
            'longest_away_seconds': round(max((p['end'] - p['start'] for p in away_runs), default=0), 3),
            'intervals': intervals, 'events': events, 'limitations': list(LIMITATIONS),
            'methods': [f'초당 {sample_fps:g}개 프레임을 검사하고 각 샘플의 지속 시간으로 비율을 계산했습니다.',
                        '판단 불가·미검출 구간은 정면 비율의 분모에서 제외합니다.',
                        '얼굴 회전과 눈 안의 홍채 위치에 보수적인 경험 기준을 적용합니다. 발표 데이터로 검증된 품질 점수가 아닙니다.'],
            'model': {'name': 'MediaPipe Face Landmarker', 'version': 'float16/1',
                      'runtime': 'MediaPipe CPU', 'board_validated': False}}


def unavailable_gaze(reason, message, duration=0):
    return {'status': 'unavailable', 'reason': reason, 'label': '시선 분석 이용 불가',
            'summary': message, 'duration': duration, 'coverage_ratio': 0,
            'camera_facing_ratio': None, 'analyzed_seconds': 0, 'unknown_seconds': duration,
            'away_seconds': None, 'longest_away_seconds': None, 'intervals': [], 'events': [],
            'limitations': list(LIMITATIONS), 'methods': [], 'calibration': {'status': 'not_applied'}}


def _frames(video_path, duration, fps, ffmpeg):
    """Decode bounded RGB frames in a pipe; preserve aspect/rotation, never save faces."""
    width, height = 640, 480
    filters = (f'fps={fps:g}:start_time=0,scale={width}:{height}:force_original_aspect_ratio=decrease,'
               f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1')
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2',
               '-i', str(video_path), '-map', '0:v:0', '-t', str(duration), '-an', '-sn',
               '-vf', filters, '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '2', 'pipe:1']
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL)
        timer = threading.Timer(180, proc.kill)
        timer.daemon = True
        timer.start()
        try:
            size = width * height * 3
            for index in range(math.ceil(duration * fps)):
                data = bytearray()
                while len(data) < size:
                    chunk = proc.stdout.read(size - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if not data:
                    break
                if len(data) != size:
                    raise ValueError('불완전한 영상 프레임입니다.')
                yield index / fps, bytes(data), width, height
            # The pipe may contain one final rounded frame; stopping here is intended.
            stopped_at_limit = index + 1 >= math.ceil(duration * fps) and len(data) == size
            if stopped_at_limit and proc.poll() is None:
                proc.terminate()
            code = proc.wait(timeout=5)
            if code != 0 and not stopped_at_limit:
                raise ValueError('영상 프레임을 읽지 못했습니다.')
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()


def analyze_gaze(video_path, models_dir, duration, options=None):
    duration = _number(duration, '영상 길이', .001, 1200)
    options = gaze_options(options)
    model = Path(models_dir) / MODEL_NAME
    if not model.is_file():
        return unavailable_gaze('model_missing', '얼굴 모델이 준비되지 않았습니다. 얼굴 모델 준비 후 다시 분석해 주세요.', duration)
    try:
        import mediapipe as mp
        import numpy as np
        import imageio_ffmpeg
    except (ImportError, OSError):
        return unavailable_gaze('dependency_missing', '영상 분석 프로그램이 준비되지 않았습니다. 영상 분석 설치 안내를 확인해 주세요.', duration)
    samples = []
    calibration_seconds = options['calibration_seconds']
    baseline = None
    calibration = {'status': 'not_requested', 'seconds': calibration_seconds}
    try:
        task_options = mp.tasks.vision.FaceLandmarkerOptions(
            # Buffer also supports Windows paths containing Korean characters.
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model.read_bytes()),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=2,
            min_face_detection_confidence=.6, min_face_presence_confidence=.6,
            min_tracking_confidence=.6, output_facial_transformation_matrixes=True)
        with mp.tasks.vision.FaceLandmarker.create_from_options(task_options) as landmarker:
            for time, data, width, height in _frames(video_path, duration, options['sample_fps'], imageio_ffmpeg.get_ffmpeg_exe()):
                rgb = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), round(time * 1000))
                geometry, reason = None, 'no_face'
                if len(result.face_landmarks) > 1:
                    reason = 'multiple_faces'
                elif len(result.face_landmarks) == 1:
                    matrix = result.facial_transformation_matrixes[0] if len(result.facial_transformation_matrixes) else None
                    geometry, reason = extract_geometry(result.face_landmarks[0], matrix, width, height)
                samples.append({'time': time, 'geometry': geometry, 'reason': reason})
        if not samples:
            return unavailable_gaze('video_decode_failed', '영상을 읽지 못했습니다. 재생 가능한 영상 파일인지 확인해 주세요.', duration)
        if calibration_seconds:
            candidates = [p['geometry'] for p in samples if p['time'] < calibration_seconds and p['geometry']]
            # Reject unreliable/extreme reference poses even if the user selected them.
            enough = len(candidates) >= max(3, math.ceil(calibration_seconds * options['sample_fps'] * .7))
            stable = enough and all(max(p[k] for p in candidates) - min(p[k] for p in candidates) <= spread
                                    for k, spread in (('yaw', 10), ('pitch', 10), ('iris_x', .1), ('iris_y', .08)))
            centered = stable and all(abs(p['yaw']) <= 20 and abs(p['pitch']) <= 18 and
                                      abs(p['iris_x']) <= .15 and abs(p['iris_y']) <= .10 for p in candidates)
            if centered:
                baseline = {k: statistics.median(p[k] for p in candidates) for k in ('yaw', 'pitch', 'iris_x', 'iris_y')}
                calibration['status'] = 'applied'
            else:
                calibration['status'] = 'rejected'
        classified = []
        for sample in samples:
            if calibration_seconds and sample['time'] < calibration_seconds:
                status, reason = 'unknown', 'calibration'
            elif sample['geometry']:
                status, reason = classify_geometry(sample['geometry'], baseline)
            else:
                status, reason = 'unknown', sample['reason']
            classified.append({'time': sample['time'], 'status': status, 'reason': reason})
        report = aggregate_samples(classified, duration, options['sample_fps'], options['sustained_seconds'])
        report['calibration'] = calibration
        if calibration['status'] == 'rejected':
            report['limitations'].append('지정한 정면 기준 구간이 불안정하거나 정면에서 크게 벗어나 있어 기본 정면 기준을 사용했습니다.')
        return report
    except Exception:
        # Optional gaze failure must never masquerade as favorable gaze or discard audio.
        return unavailable_gaze('inference_failed', '시선 분석을 완료하지 못했습니다. 얼굴 모델과 영상 상태를 확인해 주세요.', duration)
