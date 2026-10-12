"""Saved-session reports and local, extractive Q&A support.

Retrieval returns original material excerpts, not generated or verified answers.
No inference service, executable function text, or network access is used here.
"""
import json
import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.presentation import validate_deck

MAX_RECORD_BYTES = 16 * 1024 * 1024


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('발표 시간 기록이 올바르지 않습니다.')
    return float(value)


def _read(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError('기록 파일이 너무 큽니다.')
    return json.loads(data)


def build_report(record):
    deck = validate_deck(record['deck'])
    elapsed = _number(record['elapsed_sec'])
    segments = record.get('segments', [])
    states = record.get('states', {})
    if not isinstance(segments, list) or not isinstance(states, dict):
        raise ValueError('발화 기록이 올바르지 않습니다.')
    evidence = {}
    for segment in segments:
        start, end = _number(segment['start_sec']), _number(segment['end_sec'])
        if end < start or not isinstance(segment.get('text'), str):
            raise ValueError('발화 구간이 올바르지 않습니다.')
        evidence[segment['segment_id']] = {
            'segment_id': segment['segment_id'], 'start_sec': start, 'end_sec': end,
            'text': segment['text'], 'status': segment.get('status', 'OK')}
    issues = [str(item) for item in record.get('issues', [])]
    if record.get('status') != 'ended':
        issues.append('종료 처리가 완료되지 않은 저장본입니다. 실행 중이면 종료 후 새로고침해 주세요.')
    if not segments:
        issues.append('저장된 발화가 없어 실제 설명 내용과 발화 속도를 판단할 수 없습니다.')
    if any(item['status'] != 'OK' for item in evidence.values()):
        issues.append('인식이 불확실하거나 손실된 발화가 있습니다.')
    slides, sources = [], []
    counts = {'explained': 0, 'uncertain': 0, 'unconfirmed': 0}
    for slide in deck['slides']:
        visits = []
        for visit in record.get('visits', []):
            if visit['slide_id'] != slide['slide_id']:
                continue
            start = _number(visit['start_sec'])
            end = elapsed if visit.get('end_sec') is None else _number(visit['end_sec'])
            if end < start or start > elapsed + .1:
                raise ValueError('슬라이드 방문 시간이 올바르지 않습니다.')
            visits.append({'start_sec': start, 'end_sec': min(end, elapsed)})
        actual = sum(v['end_sec'] - v['start_sec'] for v in visits)
        points = []
        for point in slide['keypoints']:
            state = states.get(point['keypoint_id'], {})
            status = state.get('status', 'unconfirmed')
            if status not in counts:
                status = 'uncertain'
            ids = state.get('evidence_segment_ids', [])
            found = [evidence[key] for key in ids if key in evidence]
            # Never preserve a positive judgment whose evidence is missing or damaged.
            if status == 'explained' and (not found or len(found) != len(ids) or any(e['status'] != 'OK' for e in found)):
                status = 'uncertain'
                issues.append('설명 확인의 발화 근거가 없거나 불확실한 항목이 있습니다.')
            counts[status] += 1
            points.append({'keypoint_id': point['keypoint_id'], 'text': point['text'],
                           'required': point['required'], 'status': status,
                           'reason': str(state.get('reason', '')), 'evidence': found})
            sources.append({'source_id': f'S{len(sources) + 1}', 'title': slide['title'], 'text': point['text']})
        slides.append({'slide_id': slide['slide_id'], 'title': slide['title'],
                       'target_sec': slide['target_duration_sec'], 'actual_sec': round(actual, 2),
                       'difference_sec': round(actual - slide['target_duration_sec'], 2),
                       'visited': bool(visits), 'visits': visits, 'points': points})
    target = deck['total_duration_sec']
    suggestions = []
    if issues:
        suggestions.append('먼저 아래 기록 상태와 전사를 확인하세요. 인식 실패를 발표 내용 누락으로 해석하지 마세요.')
    if elapsed > target * 1.1:
        suggestions.append('목표 시간을 초과했습니다. 오래 머문 구간을 확인하고 핵심 설명 뒤의 반복을 줄여 다시 연습하세요.')
    elif elapsed < target * .9:
        suggestions.append('목표보다 일찍 끝났습니다. 빠르게 말한 것인지, 일찍 종료한 것인지 전사와 함께 확인하세요.')
    if counts['uncertain'] + counts['unconfirmed']:
        suggestions.append('확인되지 않은 항목의 발화를 찾아보세요. 실제 설명이 부족한 항목만 다음 연습에 추가하세요.')
    if not suggestions:
        suggestions.append('확인된 설명 순서와 시간 배분을 다음 연습에서도 유지해 보세요.')
    return {'schema_version': 1, 'session_id': record['session_id'], 'title': deck['title'],
            'status': record.get('status'), 'actual_sec': round(elapsed, 2), 'target_sec': target,
            'difference_sec': round(elapsed - target, 2), 'counts': counts,
            'slides': slides, 'transcript': list(evidence.values()), 'sources': sources,
            'issues': list(dict.fromkeys(issues)), 'suggestions': suggestions[:3],
            'limitations': ['목표 대비 전체 소요 시간은 실제 말의 빠르기나 전달력 점수가 아닙니다.',
                            '미확인은 설명 누락 확정이 아닙니다. 당시 코칭 방식과 음성 인식의 영향을 받습니다.',
                            '발표 중 기록에는 영상이 없어 시선을 평가하지 않습니다. 영상 리뷰는 발표 전 화면에서 확인하세요.']}


def _terms(text):
    words = re.findall(r'[가-힣A-Za-z0-9]+', text.casefold())
    stop = {'발표', '자료', '질문', '답변', '우리', '이거', '그거', '무엇', '어떤', '어떻게', '알려', '주세요',
            '되나요', '인가요', '있나요', '하나요', 'the', 'what', 'how', 'this'}
    result = set()
    for word in words:
        word = re.sub(r'(인가요|은요|는요|에서는|에서|으로|이고|이며|을|를|은|는|이|가|의|도)$', '', word)
        if len(word) >= 2 and word not in stop:
            result.add(word)
    return result


def retrieve(question, sources):
    query = _terms(question)
    ranked = []
    for source in sources:
        text = source['text'].casefold()
        matched = {word for word in query if word in text}
        if matched:
            ranked.append((len(matched) / max(1, len(query)), source))
    ranked.sort(key=lambda item: item[0], reverse=True)
    found = [source for _, source in ranked[:3]]
    return {'status': 'sources_found' if found else 'insufficient_evidence',
            'mode': 'local_excerpts', 'question': question,
            'message': '관련 원문입니다. 질문의 답이 포함되어 있는지 직접 확인하세요.' if found else
                       '관련 내용을 찾지 못했습니다. 질문의 핵심어를 바꾸거나 보충 자료를 추가해 주세요.',
            'sources': found, 'evidence_ids': [source['source_id'] for source in found],
            'limitation': '현재는 자료 원문 검색입니다. 마음AI 답변 생성 모델은 아직 연결되지 않았습니다.'}


class AfterStore:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.lock = threading.RLock()

    def _path(self, session_id, sidecar=False):
        if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session_id):
            raise ValueError('잘못된 발표 기록 번호입니다.')
        path = self.root / ('qa' if sidecar else '') / f'{session_id}.json'
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('기록 경로를 확인해 주세요.')
        return path

    def report(self, session_id):
        path = self._path(session_id)
        record = _read(path)
        if record.get('session_id') != session_id:
            raise ValueError('발표 기록 번호가 일치하지 않습니다.')
        result = build_report(record)
        result['saved_at'] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return result

    def listing(self):
        sessions, unreadable = [], 0
        for path in self.root.glob('*.json'):
            try:
                report = self.report(path.stem)
                sessions.append({key: report[key] for key in
                                 ('session_id', 'title', 'status', 'actual_sec', 'target_sec', 'saved_at')})
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                unreadable += 1
        sessions.sort(key=lambda item: item['saved_at'], reverse=True)
        return {'sessions': sessions, 'unreadable_count': unreadable}

    def _notes(self, session_id):
        path = self._path(session_id, sidecar=True)
        if not path.exists():
            return {'supplement': '', 'questions': []}
        notes = _read(path)
        if not isinstance(notes, dict) or not isinstance(notes.get('questions'), list) or not isinstance(notes.get('supplement'), str):
            raise ValueError('질문 기록을 읽을 수 없습니다. 원본 파일을 확인해 주세요.')
        return notes

    def _save(self, session_id, notes):
        payload = json.dumps(notes, ensure_ascii=False, indent=2).encode('utf-8')
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError('질문 기록의 저장 한도에 도달했습니다. 현재 리뷰를 내려받아 보관해 주세요.')
        path = self._path(session_id, sidecar=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        if not temp.resolve().is_relative_to(self.root):
            raise ValueError('기록 경로를 확인해 주세요.')
        temp.write_bytes(payload)
        temp.replace(path)

    def detail(self, session_id):
        with self.lock:
            return {**self.report(session_id), 'qa': self._notes(session_id)}

    def supplement(self, session_id, text):
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError('보충 자료는 20,000자 이내로 입력해 주세요.')
        with self.lock:
            self.report(session_id)
            notes = self._notes(session_id)
            notes['supplement'] = text.strip()
            self._save(session_id, notes)
        return {'saved': True}

    def ask(self, session_id, question):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError('질문은 1~1,000자로 입력해 주세요.')
        with self.lock:
            report, notes = self.report(session_id), self._notes(session_id)
            if len(notes['questions']) >= 200:
                raise ValueError('한 발표에는 질문을 최대 200개 저장할 수 있습니다.')
            sources = report['sources'] + [
                {'source_id': f'A{i + 1}', 'title': '직접 추가한 보충 자료', 'text': line.strip()}
                for i, line in enumerate(notes['supplement'].splitlines()) if line.strip()]
            result = retrieve(question.strip(), sources)
            result['created_at'] = datetime.now(timezone.utc).isoformat()
            # Store original excerpts, so later edits do not rewrite an old question's evidence.
            notes['questions'].append(result)
            self._save(session_id, notes)
            return result


def markdown_report(report):
    status_names = {'explained': '설명 확인', 'uncertain': '판단 보류', 'unconfirmed': '미확인'}
    def plain(value):
        # User text must remain text in the downloadable Markdown as well as in the UI.
        return re.sub(r'([\\`*_{}\[\]<>#!|])', r'\\\1', str(value)).replace('\n', ' ')
    lines = [f"# {plain(report['title'])}", '',
             f"목표 {report['target_sec']:g}초 / 실제 {report['actual_sec']:g}초", '', '## 다음 연습', '']
    lines += ['- ' + plain(item) for item in report['suggestions']]
    for slide in report['slides']:
        lines += ['', f"## {plain(slide['title'])}", '',
                  f"목표 {slide['target_sec']:g}초 / 실제 {slide['actual_sec']:g}초 / 방문 {len(slide['visits'])}회", '']
        for point in slide['points']:
            lines.append(f"- {status_names[point['status']]}: {plain(point['text'])}")
            for ev in point['evidence']:
                lines.append(f"  - {ev['start_sec']:g}–{ev['end_sec']:g}초: {plain(ev['text'])}")
    lines += ['', '## 기록 상태와 해석 범위', '']
    lines += ['- ' + plain(item) for item in report['issues'] + report['limitations']]
    lines += ['', '## 질의응답 원문 검색 기록', '']
    for question in report.get('qa', {}).get('questions', []):
        lines += [f"- 질문: {plain(question['question'])}", f"  - {plain(question['message'])}"]
        lines += [f"  - [{plain(source['source_id'])}] {plain(source['text'])}" for source in question['sources']]
    return '\n'.join(lines) + '\n'
