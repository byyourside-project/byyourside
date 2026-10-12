"""Project-owned proposed function map. This is not the vendor SDK or tokenizer.

The parser accepts data literals only and never evaluates generated function calls.
"""
import ast
import json
import math
import re

FUNCTIONS = ('display_review', 'update_keypoints', 'display_pace_hint', 'suggest_answer')
FIELDS = ({'summary', 'intervals', 'suggestion', 'limitation'}, {'states'},
          {'state', 'text'}, {'status', 'answer', 'evidence_ids'})
END_TOKEN = '<maum_end>'
QUERY_PREFIX = 'Below is the query from the users, please choose the correct function and generate the parameters to call the function. Query: '


def validate_target(target, context=None):
    if not isinstance(target, dict) or set(target) != {'function', 'arguments'} or target.get('function') not in FUNCTIONS:
        raise ValueError('등록된 function과 arguments만 허용합니다.')
    name, args = target['function'], target['arguments']
    index = FUNCTIONS.index(name)
    if not isinstance(args, dict) or set(args) != FIELDS[index]:
        raise ValueError('함수 인자가 정의와 다릅니다.')
    json.dumps(args, allow_nan=False)
    if index == 0:
        if not all(isinstance(args[key], str) for key in ('summary', 'suggestion', 'limitation')) or not isinstance(args['intervals'], list):
            raise ValueError('리뷰 문장과 구간 배열이 필요합니다.')
        for item in args['intervals']:
            if not isinstance(item, dict) or set(item) != {'kind', 'start', 'end'} or item['kind'] not in ('fast', 'slow', 'pause', 'away'):
                raise ValueError('리뷰 구간 형식을 확인해 주세요.')
            if not all(isinstance(item[key], (int, float)) and not isinstance(item[key], bool) and math.isfinite(item[key]) for key in ('start', 'end')) or not 0 <= item['start'] < item['end']:
                raise ValueError('리뷰 구간의 시간이 올바르지 않습니다.')
        if context is not None:
            evidence = context['evidence']
            available = [(item['kind'], item['start'], item['end']) for item in evidence.get('pace', {}).get('intervals', [])]
            available += [('away', item['start'], item['end']) for item in evidence.get('gaze', {}).get('events', []) if item.get('kind', 'away') == 'away']
            for item in args['intervals']:
                if (item['kind'], item['start'], item['end']) not in available:
                    raise ValueError('입력에 없는 리뷰 구간을 출력했습니다.')
    elif index == 1:
        if not isinstance(args['states'], dict):
            raise ValueError('항목별 상태 객체가 필요합니다.')
        for value in args['states'].values():
            if not isinstance(value, dict) or set(value) != {'s', 'e'} or type(value['s']) is not int or value['s'] not in (-1, 0, 1) or not isinstance(value['e'], list):
                raise ValueError('핵심 항목 상태 형식을 확인해 주세요.')
            if any(type(number) is not int or number < 1 for number in value['e']) or (value['s'] == 1 and not value['e']):
                raise ValueError('설명 확인에는 실제 발화 번호가 필요합니다.')
        if context is not None:
            if set(args['states']) != set(context['keypoints']):
                raise ValueError('핵심 항목 ID가 입력과 다릅니다.')
            ids = {utterance['number'] for utterance in context['utterances']}
            if any(set(value['e']) - ids for value in args['states'].values()):
                raise ValueError('입력에 없는 발화 번호입니다.')
    elif index == 2:
        if args['state'] not in ('fast', 'slow', 'on_track', 'waiting', 'unknown') or not isinstance(args['text'], str) or len(args['text']) > 80:
            raise ValueError('속도 상태와 짧은 안내 문구가 필요합니다.')
        if context is not None and (args['state'] != context['pace_state'] or
                ((not context['alert_allowed'] or args['state'] in ('waiting', 'unknown')) and args['text'])):
            raise ValueError('입력의 속도 상태 또는 알림 허용 정책과 다릅니다.')
    else:
        if args['status'] not in ('answered', 'insufficient_evidence', 'clarification_needed') or not isinstance(args['answer'], str) or not isinstance(args['evidence_ids'], list) or not all(isinstance(item, str) for item in args['evidence_ids']):
            raise ValueError('Q&A 출력 형식을 확인해 주세요.')
        if args['status'] == 'answered' and not args['evidence_ids']:
            raise ValueError('답변에는 자료 근거가 필요합니다.')
        if context is not None and set(args['evidence_ids']) - {source['source_id'] for source in context['sources']}:
            raise ValueError('입력에 없는 자료 ID입니다.')
    return target


def encode(target):
    validate_target(target)
    index = FUNCTIONS.index(target['function'])
    args = ', '.join(key + '=' + json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                     for key, value in target['arguments'].items())
    return f'<maum_{index}>({args}){END_TOKEN}'


def decode(text, context=None):
    if not isinstance(text, str) or len(text) > 64000:
        raise ValueError('모델 응답 길이가 올바르지 않습니다.')
    match = re.fullmatch(r'\s*<maum_([0-3])>\((.*)\)<maum_end>\s*', text, re.S)
    if not match:
        raise ValueError('모델 응답의 함수 토큰 또는 종료 토큰이 올바르지 않습니다.')
    try:
        tree = ast.parse('function(' + match[2] + ')', mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 8000:
            raise ValueError('모델 응답이 너무 복잡합니다.')
        call = tree.body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != 'function' or call.args:
            raise ValueError('이름 있는 인자만 허용합니다.')
        def literal(item):
            if isinstance(item, ast.Name) and item.id in ('null', 'true', 'false'):
                return {'null': None, 'true': True, 'false': False}[item.id]
            if isinstance(item, ast.List):
                return [literal(value) for value in item.elts]
            if isinstance(item, ast.Dict):
                keys = [literal(key) for key in item.keys]
                if not all(isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
                    raise ValueError('문자열 키가 중복되었습니다.')
                return dict(zip(keys, [literal(value) for value in item.values]))
            if isinstance(item, ast.Constant) and isinstance(item.value, (str, int, float, bool, type(None))):
                return item.value
            if isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.USub) and isinstance(item.operand, ast.Constant) and type(item.operand.value) in (int, float):
                return -item.operand.value
            raise ValueError('모델 출력에는 데이터만 허용합니다.')
        keys = [kw.arg for kw in call.keywords]
        if None in keys or len(set(keys)) != len(keys):
            raise ValueError('인자 이름이 없거나 중복되었습니다.')
        args = {kw.arg: literal(kw.value) for kw in call.keywords}
        return validate_target({'function': FUNCTIONS[int(match[1])], 'arguments': args}, context)
    except (SyntaxError, RecursionError, TypeError, AttributeError) as exc:
        raise ValueError('모델 응답을 해석할 수 없습니다.') from exc
