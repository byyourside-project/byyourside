"""Validate the reviewed sheet and export project-proposed maum function data.

No training, download, token registration, quantization, or vendor SDK execution.
"""
import argparse
import copy
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.maum_contract import FUNCTIONS, QUERY_PREFIX, encode, decode, validate_target


def import_workbook(path, dataset):
    try:
        import openpyxl
    except ImportError as exc:
        raise ValueError('엑셀 읽기를 위해 openpyxl이 필요합니다. requirements-data.txt를 설치해 주세요.') from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        meta = list(workbook['학습데이터'].values)
        io = list(workbook['입출력JSON'].values)
        if tuple(meta[0][:4]) != ('예시 ID', '기능 ID', '분할', '상황') or tuple(io[0][:4]) != ('예시 ID', '기능 ID', '모델 입력 JSON', '학습 정답 JSON'):
            raise ValueError('곁 학습데이터시트의 열 제목이 변경되었습니다.')
        pairs = {}
        for row in io[1:]:
            if not row[0]: continue
            if row[0] in pairs: raise ValueError('입출력JSON의 예시 ID가 중복되었습니다.')
            pairs[row[0]] = row
        records = []
        for row in meta[1:]:
            if not row[0]: continue
            if row[0] not in pairs or pairs[row[0]][1] != row[1]:
                raise ValueError(f'{row[0]}: 입출력JSON의 ID 또는 기능 ID가 일치하지 않습니다.')
            reviewed_on = row[10]
            if isinstance(reviewed_on, (date, datetime)):
                reviewed_on = reviewed_on.strftime('%Y-%m-%d')
            elif isinstance(reviewed_on, (int, float)) and not isinstance(reviewed_on, bool):
                reviewed_on = (datetime(1899, 12, 30) + timedelta(days=reviewed_on)).strftime('%Y-%m-%d')
            records.append({'sample_id': row[0], 'function_id': row[1], 'split': row[2],
                            'group_id': row[6], 'source_type': row[7],
                            'review_status': 'approved' if row[8] == '검수완료' else 'unreviewed',
                            'reviewer_id': row[9] or '', 'reviewed_on': reviewed_on or '',
                            'adoption': 'use' if row[11] == '사용' else 'exclude' if row[11] == '제외' else 'pending',
                            'input': json.loads(pairs[row[0]][2]), 'target': json.loads(pairs[row[0]][3])})
        if set(pairs) != {record['sample_id'] for record in records}:
            raise ValueError('학습데이터와 입출력JSON의 예시 ID 목록이 다릅니다.')
        result = copy.deepcopy(dataset)
        result['records'] = records
        return result
    finally:
        workbook.close()


def approved(record):
    try:
        when = date.fromisoformat(str(record.get('reviewed_on', '')))
    except ValueError:
        return False
    reviewer = record.get('reviewer_id')
    return (record.get('review_status') == 'approved' and record.get('adoption') == 'use' and
            isinstance(reviewer, str) and bool(reviewer.strip()) and not reviewer.startswith('=') and when <= date.today())


def validate(dataset):
    ids, inputs, groups = set(), {}, {}
    for record in dataset['records']:
        sid = record['sample_id']
        if not isinstance(sid, str) or not sid.strip() or sid in ids:
            raise ValueError('예시 ID가 없거나 중복되었습니다.')
        ids.add(sid)
        split, group = record['split'], record['group_id']
        if split not in ('train', 'validation', 'test') or not isinstance(group, str) or not group.strip():
            raise ValueError(f'{sid}: 분할 또는 그룹 ID를 확인해 주세요.')
        if group in groups and groups[group] != split:
            raise ValueError(f'{sid}: 같은 그룹이 여러 분할에 섞였습니다.')
        groups[group] = split
        encoded_input = json.dumps(record['input'], sort_keys=True, ensure_ascii=False, allow_nan=False)
        fingerprint = hashlib.sha256(encoded_input.encode()).hexdigest()
        if fingerprint in inputs:
            raise ValueError(f'{sid}: {inputs[fingerprint]}와 동일한 입력입니다.')
        inputs[fingerprint] = sid
        function = record['target']['function']
        if function not in FUNCTIONS or record['function_id'] != f'F{FUNCTIONS.index(function) + 1:02d}':
            raise ValueError(f'{sid}: 기능 ID와 정답 함수가 다릅니다.')
        validate_target(record['target'], record['input'])
        if decode(encode(record['target']), record['input']) != record['target']:
            raise ValueError(f'{sid}: 함수 출력 변환 결과가 다릅니다.')


def export(dataset, output, preview=False):
    validate(dataset)
    records = [record for record in dataset['records'] if
               (preview and record.get('adoption') != 'exclude') or approved(record)]
    if not preview and not any(record['split'] == 'train' for record in records):
        raise ValueError('사람이 검수·승인한 train 행이 없습니다. 형식 점검만 하려면 --preview를 사용하세요.')
    output = Path(output)
    # Use a new output directory, so an old approved export cannot survive under new preview files.
    output.mkdir(parents=True, exist_ok=False)
    prefix = 'preview_' if preview else ''
    description = {function['name']: function for function in dataset['functions']}
    prompt_functions = [{key: function[key] for key in ('name', 'token', 'description', 'parameters', 'boundaries')}
                        for function in dataset['functions']]
    policy = dataset['system_prompt'].replace('function과 arguments만 가진 JSON을 출력한다.',
                                             '정의된 함수 토큰·인자·종료 토큰만 출력한다.')
    max_prompt_chars = 0
    counts = {}
    for split in ('train', 'validation', 'test'):
        rows = [record for record in records if record['split'] == split]
        counts[split] = len(rows)
        with (output / f'{prefix}{split}.jsonl').open('w', encoding='utf-8') as messages, \
                (output / f'{prefix}{split}.csv').open('w', encoding='utf-8-sig', newline='') as table:
            writer = csv.writer(table)
            writer.writerow(['No', '기능명', 'LLM Output', 'Query(국문)', 'sample_id', 'group_id', '검수 상태'])
            for number, record in enumerate(rows, 1):
                query = policy + '\n입력 자료:\n' + json.dumps(record['input'], ensure_ascii=False, separators=(',', ':'))
                query += '\n출력 형식: 정의된 <maum_N>(인자=값)<maum_end> 함수 호출 한 개만 반환한다.\n함수 정의:\n'
                query += json.dumps(prompt_functions, ensure_ascii=False, separators=(',', ':'))
                max_prompt_chars = max(max_prompt_chars, len(QUERY_PREFIX + query + ' Response:'))
                answer = encode(record['target'])
                item = {'sample_id': record['sample_id'], 'group_id': record['group_id'], 'split': split,
                        'human_approved': approved(record), 'training_eligible': not preview and split == 'train' and approved(record),
                        'messages': [{'role': 'user', 'content': query}, {'role': 'assistant', 'content': answer}],
                        'formatted_prompt': QUERY_PREFIX + query + ' Response:',
                        'function_description': description[record['target']['function']]}
                messages.write(json.dumps(item, ensure_ascii=False) + '\n')
                writer.writerow([number, record['target']['function'], answer, query, record['sample_id'], record['group_id'], '초안 미리보기' if preview else '검수완료'])
    manifest = {'mode': 'preview_not_for_training' if preview else 'reviewed_export', 'counts': counts,
                'human_approved_count': sum(approved(record) for record in records),
                'function_map': {f'<maum_{i}>': name for i, name in enumerate(FUNCTIONS)},
                'end_token': '<maum_end>', 'vendor_loader_verified': False, 'model_training_performed': False,
                'max_prompt_characters': max_prompt_chars, 'token_length_checked': False,
                'quantization_performed': False, 'note': '공급사 토크나이저·로더·문맥 길이 확인 전의 프로젝트 변환 형식. test는 학습에 사용하지 않는다.'}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description='곁 마음AI 데이터시트 검사·변환 (학습 실행 없음)')
    parser.add_argument('--source', type=Path, default=ROOT / 'docs/data/maumai/seed_dataset.json')
    parser.add_argument('--workbook', type=Path, help='수정한 곁 데이터시트 xlsx. 입력·정답과 검수 정보를 다시 읽습니다.')
    parser.add_argument('--output', type=Path, required=True, help='새로 만들 출력 폴더')
    parser.add_argument('--preview', action='store_true', help='미검수 초안 형식 점검. 학습에 사용하지 않음')
    args = parser.parse_args()
    try:
        dataset = json.loads(args.source.read_text(encoding='utf-8'))
        if args.workbook: dataset = import_workbook(args.workbook, dataset)
        manifest = export(dataset, args.output, args.preview)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'변환하지 못했습니다: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
