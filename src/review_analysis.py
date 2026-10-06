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


def _pace_review(segments, comparisons, target_seconds, span):
    """Explain measurable pace evidence without inventing a delivery-quality norm.

    Script groups require close lexical agreement before their duration is used.
    VAD intervals include short pauses and are not word/sentence alignments. A
    whole-talk target is therefore assessed separately from within-talk pace.
    This is an additive report field: schema-v1 history remains readable.
    """
    reference = {
        'kind': 'none', 'label': '비교 기준 부족', 'rate_hangul_per_min': None,
        'details': '한글 전사 길이와 음성 감지 구간을 이용한 참고값입니다. 전달력 점수가 아닙니다.',
        'target': None,
    }
    result = {
        'version': 1, 'status': 'insufficient_evidence', 'label': '속도 판단 보류',
        'summary': '', 'reference': reference, 'intervals': [], 'measurements': [],
        'usable_group_count': 0, 'excluded_group_count': 0,
        'script_coverage': None,
        'thresholds': {'minimum_group_seconds': 3, 'minimum_hangul_count': 15,
                       'minimum_similarity': .85, 'relative_fast_ratio': 1.25,
                       'relative_slow_ratio': .75, 'long_internal_pause_seconds': 1.5},
        'pausing': {'status': 'insufficient_evidence', 'internal_pause_count': 0,
                    'long_internal_pause_count': 0, 'summary': ''},
        'next_step': '',
    }
    if not segments:
        result.update(status='no_speech', label='분석할 발화 없음',
                      summary='말한 구간을 찾지 못해 빠르기와 쉼을 판단하지 않았습니다.',
                      next_step='파일을 재생해 음성이 들어 있는지 확인해 주세요.')
        result['pausing']['summary'] = '발화가 없어 쉼을 분석하지 않았습니다.'
        return result

    by_id = {segment['id']: segment for segment in segments}
    candidates = comparisons or [dict(text=s['text'], status='matched', similarity=None,
                                      start=s['start'], end=s['end'], segment_ids=[s['id']],
                                      internal_pauses=[]) for s in segments]
    for index, row in enumerate(candidates):
        selected = [by_id[sid] for sid in row['segment_ids']]
        spoken = ''.join(s['text'] for s in selected)
        count = len(re.findall('[가-힣]', spoken))
        script_count = len(re.findall('[가-힣]', row['text']))
        seconds = row['end'] - row['start'] if selected else None
        reason = None
        if not selected:
            reason = '대본과 연결되는 발화를 확인하지 못했습니다. 실제 누락을 뜻하지는 않습니다.'
        elif comparisons and (row['status'] != 'matched' or row['similarity'] < .85):
            reason = '대본과 전사의 차이가 커 이 구간의 속도 판단을 보류했습니다.'
        elif re.search('[A-Za-z0-9]', spoken + row['text']):
            reason = '영문·숫자 발음 길이를 한글 글자 수로 셀 수 없어 속도 비교에서 제외했습니다.'
        elif count < 15 or seconds < 3:
            reason = '발화가 짧아 인식·경계 오차가 속도에 크게 영향을 주므로 비교에서 제외했습니다.'
        elif comparisons and (not script_count or not .8 <= count / script_count <= 1.25):
            reason = '대본과 실제 인식된 글자 수 차이가 커 속도 판단을 보류했습니다.'
        result['measurements'].append({
            'group_index': index + 1, 'script_index': index + 1 if comparisons else None,
            'segment_ids': row['segment_ids'], 'start': row['start'], 'end': row['end'],
            'duration': round(seconds, 3) if seconds is not None else None,
            'text': row['text'], 'transcript': spoken, 'hangul_count': count,
            'rate': round(count * 60 / seconds, 1) if seconds else None,
            'similarity': row['similarity'], 'status': 'excluded' if reason else 'usable',
            'reason': reason,
        })

    usable = [row for row in result['measurements'] if row['status'] == 'usable']
    result['usable_group_count'] = len(usable)
    result['excluded_group_count'] = len(candidates) - len(usable)
    median = statistics.median(row['rate'] for row in usable) if len(usable) >= 3 else None
    if median:
        reference.update(kind='recording_median', label='이번 녹음의 비교 가능한 구간 중앙값',
                         rate_hangul_per_min=round(median, 1),
                         details='비교 가능한 구간이 3개 이상일 때 중앙값보다 25% 이상 빠르거나 느린 구간을 표시합니다. '
                                 '25%는 검토 구간을 찾기 위한 설정값이며 좋은 발표의 절대 기준은 아닙니다.')
        for row in usable:
            ratio = row['rate'] / median
            row['relative_ratio'] = round(ratio, 3)
            kind = 'fast' if ratio > 1.25 else 'slow' if ratio < .75 else None
            if kind:
                result['intervals'].append({
                    'start': row['start'], 'end': row['end'], 'kind': kind,
                    'label': '다른 구간보다 빠름' if kind == 'fast' else '다른 구간보다 느림',
                    'detail': f"한글 기준 분당 {row['rate']:g}자, 비교 구간 중앙값 {median:g}자입니다. "
                              + ('핵심어가 지나치게 붙어 들리는지 재생해 확인하세요.' if kind == 'fast' else
                                 '강조나 설명을 위한 의도적인 속도인지 재생해 확인하세요.'),
                    'script_index': row['script_index'], 'segment_ids': row['segment_ids'],
                    'basis': 'recording_median', 'severity': 'review',
                })

    # Assess total duration only after enough of both script and transcript match.
    # Including all script letters despite omitted/unrecognized lines would create
    # an artificially high speaking-rate reference.
    if comparisons:
        script_chars = sum(len(normalized(row['text'])) for row in comparisons)
        close_rows = [row for row in comparisons if row['status'] == 'matched' and row['similarity'] >= .85]
        covered_chars = sum(len(normalized(row['text'])) for row in close_rows)
        matched_ids = {sid for row in close_rows for sid in row['segment_ids']}
        spoken_chars = sum(len(normalized(s['text'])) for s in segments)
        covered_spoken_chars = sum(len(normalized(s['text'])) for s in segments if s['id'] in matched_ids)
        result['script_coverage'] = round(covered_chars / script_chars, 3) if script_chars else 0
        spoken_coverage = covered_spoken_chars / spoken_chars if spoken_chars else 0
    else:
        script_chars, spoken_coverage = 0, 0
    if target_seconds is not None:
        target = {'status': 'unconfirmed', 'planned_seconds': target_seconds,
                  'actual_seconds': round(span, 3) if span is not None else None,
                  'difference_seconds': round(span - target_seconds, 2) if span is not None else None,
                  'tolerance_seconds': round(max(2, target_seconds * .1), 2),
                  'label': '대본 수행 여부 확인 필요',
                  'detail': '전체 시간의 차이만으로 말이 빠르거나 느리다고 단정할 수 없습니다.'}
        if script_chars >= 30 and result['script_coverage'] >= .85 and spoken_coverage >= .85:
            delta = span - target_seconds
            tolerance = target['tolerance_seconds']
            target['status'] = 'longer' if delta > tolerance else 'shorter' if delta < -tolerance else 'near_target'
            target['label'] = {'longer': '목표보다 길게 말함', 'shorter': '목표보다 짧게 말함',
                               'near_target': '목표 시간에 가까움'}[target['status']]
            target['detail'] = ('대본과 전사가 대부분 대응하는 경우에 첫 발화부터 마지막 발화까지 비교합니다. '
                                '목표의 10% 또는 2초 중 큰 값을 검토 범위로 사용합니다. '
                                '이 범위는 제품의 임시 설정값이며 전달력 판정 기준은 아닙니다.')
        reference['target'] = target

    # A newline may contain several sentences: only describe observed gaps and
    # never claim that punctuation received the correct/incorrect breath.
    reliable_indexes = {row['group_index'] for row in usable}
    internal_count = long_count = 0
    for index, row in enumerate(comparisons):
        if index + 1 not in reliable_indexes:
            continue
        for pause in row['internal_pauses']:
            internal_count += 1
            if pause['duration'] >= 1.5:
                long_count += 1
                result['intervals'].append({
                    'start': pause['start'], 'end': pause['end'], 'kind': 'pause',
                    'label': '대본 묶음 안의 긴 쉼',
                    'detail': f"약 {pause['duration']:g}초의 간격입니다. 호흡·강조를 위한 쉼인지 확인하세요. "
                              '한 줄에 여러 문장이 있을 수 있어 잘못 끊었다는 뜻은 아닙니다.',
                    'script_index': index + 1, 'segment_ids': row['segment_ids'],
                    'basis': 'vad_gap', 'severity': 'review',
                })
    result['pausing'].update(
        status='observed' if comparisons and usable else 'insufficient_evidence',
        internal_pause_count=internal_count, long_internal_pause_count=long_count,
        summary=(f'대본 묶음 안의 감지된 쉼 {internal_count}곳 중 1.5초 이상은 {long_count}곳입니다. '
                 '단어·문장 경계를 정밀 정렬한 결과가 아니므로 문장 끊기의 정답 여부는 판단하지 않습니다.'
                 if comparisons and usable else
                 '대본과 충분히 대응하는 발화가 필요합니다. 음성 감지 경계만으로 문장 끊기를 평가하지 않습니다.'))
    result['intervals'].sort(key=lambda item: (item['start'], item['end']))
    target = reference['target']
    timing_known = target and target['status'] != 'unconfirmed'
    timing_review = timing_known and target['status'] != 'near_target'
    if timing_known and not median:
        reference.update(kind='target_duration', label='전체 목표 시간과 비교')
    if result['intervals'] or timing_review:
        result.update(status='needs_review', label='다시 들어볼 구간 있음',
                      summary=f"속도 변화와 쉼을 확인할 구간이 {len(result['intervals'])}곳 있습니다. "
                              '표시된 구간을 재생해 내용 전달이 자연스러운지 확인하세요.')
        if timing_review and not result['intervals']:
            result['label'] = '전체 시간 확인 필요'
            result['summary'] = '목표 시간과 차이가 있습니다. 전체 속도, 쉼, 설명 분량을 함께 확인하세요.'
        result['next_step'] = ('표시 구간을 들어보고 핵심어가 잘 들리는지 확인한 뒤 같은 대본을 다시 녹화해 비교하세요.'
                               if result['intervals'] else '목표 시간을 확인하고 속도·쉼·설명 분량을 조정해 다시 녹화하세요.')
    elif median or timing_known:
        result.update(status='reference_only', label='구간 속도 변화가 크지 않음',
                      summary='비교 가능한 구간에서 큰 속도 차이는 발견하지 못했습니다. '
                              '전체가 너무 빠르거나 느린지는 이 결과만으로 판단할 수 없습니다.',
                      next_step='영상을 들어보며 핵심어가 또렷하게 들리는지 확인하고, 전달력 평가는 사람의 검토로 보완하세요.')
        if not median:
            result['label'] = '목표 시간에 가까움'
            result['summary'] = '전체 발표 시간은 목표에 가깝습니다. 구간별 속도를 비교할 발화가 부족해 전달력 판단은 보류했습니다.'
    else:
        result.update(summary='비교 가능한 발화가 3개 미만이거나 대본 대응이 불확실해 속도 판정을 보류했습니다.',
                      next_step='전사 오류를 수정하고, 대본을 발화 단위로 줄을 나눠 다시 비교해 주세요.')
    if result['excluded_group_count']:
        result['summary'] += f" 짧은 발화·영문·숫자·대본 차이 등으로 {result['excluded_group_count']}개 구간을 속도 비교에서 제외했습니다."
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
    for s in segments:
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
    pace = _pace_review(segments, comparisons, target_seconds, span)
    for interval in pace['intervals']:
        if interval['kind'] in ('fast', 'slow'):
            note = ('이번 녹음의 다른 구간보다 빠른 편입니다. 직접 들어보고 확인하세요.'
                    if interval['kind'] == 'fast' else
                    '이번 녹음의 다른 구간보다 느린 편입니다. 강조를 위한 속도인지 확인하세요.')
            for segment_id in interval['segment_ids']:
                segments[segment_id - 1]['notes'].append(note)
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
            'pace': pace,
            'waveform': raw.get('waveform', []), 'warnings': warnings,
            'method': {'rate': '전사의 한글 글자 수 / 시간. 영문·숫자 발음은 제외된 참고값입니다.',
                       'boundaries': '음성 감지 구간이며 정확한 문장·호흡 경계는 아닙니다.',
                       'matching': '표현의 유사도를 비교합니다. 불일치는 실제 누락이나 모순의 확정 판정이 아닙니다.',
                       'schedule': '첫 발화부터 마지막 발화까지의 시간과 전체 목표만 비교합니다. 실시간 진행 판단은 아닙니다.',
                       'pace': '대본 대응이 충분한 구간의 전사 글자 수를 사용합니다. 상대 속도 차이와 전체 목표 시간은 따로 평가하며 절대 전달력 점수를 매기지 않습니다.',
                       'pausing': '대본 묶음 안의 음성 감지 간격을 표시합니다. 문장·호흡 경계의 정확성은 확정하지 않습니다.'},
            'review_state': 'user_edited_transcript' if edits else 'automatic_unverified',
            'training_eligible': False, 'engine': raw.get('engine', 'SenseVoice')}
