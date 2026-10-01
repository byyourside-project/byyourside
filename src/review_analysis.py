"""Offline rehearsal review. No learned coaching labels or quality scores."""
import math
import re
import statistics
import unicodedata
from difflib import SequenceMatcher


def normalized(text):
    return re.sub(r"[^가-힣a-z0-9]", "", unicodedata.normalize("NFC", text).lower())


def script_lines(script):
    # Respect the presenter's explicit groups (e.g. an introduction with two sentences).
    paragraphs = [s.strip() for s in script.splitlines() if s.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+", script) if s.strip()]


def align_script(lines, segments):
    """Order-preserving, non-reusing lexical alignment; never proves omission."""
    n, m = len(lines), len(segments)
    scores = [[-math.inf] * (m + 1) for _ in range(n + 1)]
    previous = {}
    scores[0][0] = 0
    refs = [normalized(line) for line in lines]
    spoken = [normalized(s['text']) for s in segments]

    def update(i, j, score, origin, action):
        if score > scores[i][j]:
            scores[i][j] = score
            previous[i, j] = (origin, action)

    for i in range(n + 1):
        for j in range(m + 1):
            current = scores[i][j]
            if i < n:
                update(i + 1, j, current - .6, (i, j), ('skip', i))
            if j < m:
                update(i, j + 1, current - .2, (i, j), ('extra', j))
            if i < n and refs[i]:
                for size in range(1, min(3, m - j) + 1):
                    hyp = ''.join(spoken[j:j + size])
                    ratio = SequenceMatcher(None, refs[i], hyp, autojunk=False).ratio()
                    if ratio >= .45:
                        update(i + 1, j + size, current + 2 * ratio - .9 - .025 * (size - 1),
                               (i, j), ('match', i, j, size, ratio))
    matches = {}
    cursor = (n, m)
    while cursor != (0, 0):
        cursor, action = previous[cursor]
        if action[0] == 'match':
            _, i, j, size, ratio = action
            matches[i] = (j, size, ratio)
    result = []
    for i, line in enumerate(lines):
        row = {'text': line, 'status': 'unconfirmed', 'similarity': None,
               'start': None, 'end': None, 'segment_ids': []}
        if i in matches:
            j, size, ratio = matches[i]
            row.update(status='matched' if ratio >= .78 else 'review',
                       similarity=round(ratio, 3), start=segments[j]['start'],
                       end=segments[j + size - 1]['end'],
                       segment_ids=[s['id'] for s in segments[j:j + size]])
        result.append(row)
    return result


def build_review(raw, script='', target_seconds=None, edits=None):
    duration = float(raw['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 1200:
        raise ValueError('녹음 길이는 0초 초과, 20분 이하여야 합니다.')
    if not isinstance(script, str) or len(script) > 12000:
        raise ValueError('대본은 12,000자 이하로 입력해 주세요.')
    lines = script_lines(script)
    if len(lines) > 100:
        raise ValueError('대본은 100문장 이하로 나눠 주세요.')
    if target_seconds is not None:
        if isinstance(target_seconds, bool):
            raise ValueError('목표 시간은 초 단위 숫자로 입력해 주세요.')
        target_seconds = float(target_seconds)
        if not math.isfinite(target_seconds) or not 1 <= target_seconds <= 7200:
            raise ValueError('목표 시간은 1초~120분 사이로 입력해 주세요.')
    edits = {} if edits is None else edits
    if not isinstance(edits, dict):
        raise ValueError('전사 수정 형식이 올바르지 않습니다.')
    segments = []
    last_end = 0
    for i, source in enumerate(raw['segments']):
        start, end = float(source['start']), float(source['end'])
        if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration + .001:
            raise ValueError('발화 구간의 시간이 올바르지 않습니다.')
        if start < last_end - .001:
            raise ValueError('발화 구간이 서로 겹칩니다.')
        text = edits.get(str(i + 1), source['text'])
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError('구간 전사는 2,000자 이하로 입력해 주세요.')
        count = len(re.findall('[가-힣]', text))
        segments.append({'id': i + 1, 'start': start, 'end': end,
                         'duration': round(end - start, 3), 'text': text,
                         'asr_text': source['text'], 'edited': str(i + 1) in edits,
                         'hangul_count': count, 'rate': round(60 * count / (end - start), 1),
                         'pause_before': round(start - last_end, 3) if i else None,
                         'notes': []})
        last_end = end
    if set(edits) - {str(s['id']) for s in segments}:
        raise ValueError('존재하지 않는 발화 구간입니다.')
    eligible = [s['rate'] for s in segments if s['duration'] >= 3 and s['hangul_count'] >= 15
                and not re.search('[A-Za-z0-9]', s['text'])]
    median = statistics.median(eligible) if len(eligible) >= 3 else None
    for s in segments:
        reliable_rate = s['duration'] >= 3 and s['hangul_count'] >= 15 and not re.search('[A-Za-z0-9]', s['text'])
        if median and reliable_rate:
            if s['rate'] > median * 1.25:
                s['notes'].append('이번 녹음의 다른 구간보다 빠른 편입니다. 직접 들어보고 확인하세요.')
            elif s['rate'] < median * .75:
                s['notes'].append('이번 녹음의 다른 구간보다 느린 편입니다. 강조를 위한 속도인지 확인하세요.')
        if s['pause_before'] is not None and s['pause_before'] >= 1.5:
            s['notes'].append('앞 구간 뒤에 긴 간격이 감지됐습니다. 의도한 쉼인지 확인하세요.')
        if not normalized(s['text']):
            s['notes'].append('발화가 감지됐지만 내용은 인식하지 못했습니다.')
    pauses = [{'start': a['end'], 'end': b['start'], 'duration': round(b['start'] - a['end'], 3)}
              for a, b in zip(segments, segments[1:]) if b['start'] - a['end'] >= .35]
    comparisons = align_script(lines, segments) if lines else []
    for row in comparisons:
        ids = row['segment_ids']
        row['internal_pauses'] = [p for p in pauses if ids and row['start'] < p['start'] < p['end'] < row['end']]
    span = segments[-1]['end'] - segments[0]['start'] if segments else None
    schedule = {'status': 'no_plan' if target_seconds is None else 'no_speech',
                'target_seconds': target_seconds, 'actual_seconds': span, 'difference_seconds': None}
    if target_seconds is not None and span is not None:
        difference = span - target_seconds
        schedule.update(status='over_target' if difference > 0 else 'within_target',
                        difference_seconds=round(difference, 2))
    warnings = []
    if raw.get('peak_dbfs', 0) < -18:
        warnings.append('녹음 음량이 작게 저장되었습니다. 다음 녹음에서는 마이크 위치나 입력 음량을 확인해 보세요.')
    if not segments:
        warnings.append('말한 구간을 찾지 못했습니다. 음성이 들어 있는 파일인지 확인해 주세요.')
    return {'schema_version': 1, 'title': raw.get('title', '연습 발표'), 'duration': duration, 'segments': segments,
            'summary': {'segment_count': len(segments), 'speech_span_seconds': span,
                        'rate': round(sum(s['hangul_count'] for s in segments) * 60 / span, 1) if span else None,
                        'pause_count': len(pauses)},
            'pauses': pauses, 'script': script, 'comparison': comparisons, 'schedule': schedule,
            'waveform': raw.get('waveform', []), 'warnings': warnings,
            'method': {'rate': '전사의 한글 글자 수 / 시간. 영문·숫자 발음은 제외된 참고값입니다.',
                       'boundaries': '음성 감지 구간이며 정확한 문장·호흡 경계는 아닙니다.',
                       'matching': '표현의 유사도를 비교합니다. 불일치는 실제 누락이나 모순의 확정 판정이 아닙니다.',
                       'schedule': '첫 발화부터 마지막 발화까지의 시간과 전체 목표만 비교합니다. 실시간 진행 판단은 아닙니다.'},
            'review_state': 'user_edited_transcript' if edits else 'automatic_unverified',
            'training_eligible': False, 'engine': raw.get('engine', 'SenseVoice')}
