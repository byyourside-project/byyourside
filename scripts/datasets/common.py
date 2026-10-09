"""Shared offline contracts. Uses only the standard library and existing text modules."""
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.ollama_coach import OllamaCoach, SYSTEM_PROMPT, joined_utterances

BASE = ROOT / 'datasets/presentation_coach'
SCHEMA = json.loads((BASE / 'schemas/canonical-v1.schema.json').read_text(encoding='utf-8'))
SPLITS = ('train', 'validation', 'test')


class DatasetError(ValueError):
    pass


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(stable_json(value).encode('utf-8')).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatasetError('duplicate JSON object key')
        result[key] = value
    return result


def parse_json(text):
    def bad_constant(value):
        raise DatasetError('non-finite JSON number')
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=bad_constant)


def load_jsonl(path):
    rows = []
    with Path(path).open(encoding='utf-8') as stream:
        for line, text in enumerate(stream, 1):
            try:
                if not text.strip():
                    raise DatasetError('blank JSONL line')
                rows.append(parse_json(text))
            except (ValueError, TypeError) as exc:
                # Do not echo malformed source text, which may contain personal data.
                raise DatasetError(f'line {line}: invalid JSON ({type(exc).__name__})') from exc
    if not rows:
        raise DatasetError('empty dataset')
    return rows


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def write_jsonl(path, rows):
    text = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def schema_errors(value, schema=SCHEMA, path='$'):
    """Validate the schema keywords used by our checked-in v1 schema (not a general engine)."""
    supported = {'$schema', 'title', 'type', 'properties', 'required', 'additionalProperties',
                 'minProperties', 'maxProperties', 'items', 'minItems', 'maxItems', 'uniqueItems',
                 'minLength', 'maxLength', 'pattern', 'minimum', 'const', 'enum'}
    if set(schema) - supported:
        raise DatasetError('unsupported schema keyword; extend the validator before changing the contract')
    errors = []
    types = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
             'string': lambda v: isinstance(v, str), 'integer': lambda v: type(v) is int,
             'number': lambda v: type(v) in (int, float) and math.isfinite(v),
             'boolean': lambda v: type(v) is bool, 'null': lambda v: v is None}
    expected = schema.get('type')
    if expected:
        expected = [expected] if isinstance(expected, str) else expected
        if not any(types[t](value) for t in expected):
            return [f'{path}: wrong type']
    if 'const' in schema and value != schema['const']:
        errors.append(f'{path}: wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: unsupported value')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                errors.append(f'{path}: required field {key}')
        if len(value) < schema.get('minProperties', 0) or len(value) > schema.get('maxProperties', math.inf):
            errors.append(f'{path}: wrong property count')
        for key, item in value.items():
            child = properties.get(key, schema.get('additionalProperties', True))
            if child is False:
                errors.append(f'{path}: unexpected field')
            elif isinstance(child, dict):
                # Object keys are identifiers, but redact arbitrary user keys from diagnostics.
                errors.extend(schema_errors(item, child, f'{path}.{key}' if key in properties else f'{path}.*'))
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            errors.append(f'{path}: wrong array length')
        if schema.get('uniqueItems') and len({stable_json(v) for v in value}) != len(value):
            errors.append(f'{path}: duplicate array item')
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema.get('items', {}), f'{path}[{index}]'))
    elif isinstance(value, str):
        if len(value.strip()) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', math.inf):
            errors.append(f'{path}: wrong string length')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            errors.append(f'{path}: invalid pattern')
    elif type(value) in (float, int):
        if not math.isfinite(value) or value < schema.get('minimum', -math.inf):
            errors.append(f'{path}: invalid number')
    return errors


def model_input(slide, segments):
    groups = joined_utterances(segments)
    return {'slide_title': slide['title'],
            'keypoints': {str(i): p['text'] for i, p in enumerate(slide['keypoints'], 1)},
            'utterances': [{'number': i, 'text': g['text']} for i, g in enumerate(groups, 1)]}


def safety_reasons(row):
    source = row['source']
    reasons = []
    allowed = source['consent_status'] == 'granted' or (
        source['kind'] in ('synthetic', 'human_authored') and
        source['consent_status'] == 'synthetic_no_personal_data')
    if not allowed:
        reasons.append('consent_not_cleared')
    if source['pii_status'] != 'cleared':
        reasons.append('pii_not_cleared')
    # Conservative hints; human review is still required and these are not a PII guarantee.
    text = stable_json({'input': row['input'], 'segments': row['snapshot']['segments']})
    patterns = (r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', r'(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)',
                r'(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)')
    if any(re.search(pattern, text) for pattern in patterns):
        reasons.append('possible_personal_data')
    return reasons


def record_errors(row):
    errors = schema_errors(row)
    if errors:
        return errors
    inp, mapping, snap = row['input'], row['mapping'], row['snapshot']
    keys = list(inp['keypoints'])
    expected_keys = [str(i) for i in range(1, len(keys) + 1)]
    if keys != expected_keys:
        errors.append('input.keypoints: keys/order must be 1..N')
    if set(keys) != set(row['target']['compact']) or set(keys) != set(mapping['keypoint_ids']):
        errors.append('keypoint input/target/mapping mismatch')
    if len(set(mapping['keypoint_ids'].values())) != len(keys):
        errors.append('mapping: duplicate original keypoint ID')
    if set(row['annotation']['point_details']) != set(keys):
        errors.append('annotation: point keys mismatch')
    numbers = [u['number'] for u in inp['utterances']]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append('input.utterances: numbers/order must be 1..M')
    for label in row['target']['compact'].values():
        if any(e not in numbers for e in label['e']):
            errors.append('target: unknown evidence number')
        if (label['s'] == -1) != (not label['e']):
            errors.append('target: inconsistent status/evidence')
    segments = snap['segments']
    ids = [s['segment_id'] for s in segments]
    if len(set(ids)) != len(ids):
        errors.append('snapshot: duplicate segment ID')
    previous_end = -1
    for seg in segments:
        if seg['start_sec'] < previous_end or seg['end_sec'] <= seg['start_sec']:
            errors.append('snapshot: invalid or overlapping timestamp')
        if seg['end_sec'] > snap['cutoff_sec']:
            errors.append('snapshot: future utterance after cutoff')
        previous_end = seg['end_sec']
    groups = joined_utterances(segments)
    wanted_map = {str(i): [ids[j-1] for j in g['numbers']] for i, g in enumerate(groups, 1)}
    wanted_utterances = [{'number': i, 'text': g['text']} for i, g in enumerate(groups, 1)]
    if wanted_map != mapping['utterance_segment_ids'] or wanted_utterances != inp['utterances']:
        errors.append('mapping/input: not the runtime merge of snapshot.segments')
    if row['lineage']['prompt_version'] != OllamaCoach.prompt_version:
        errors.append('lineage: prompt version differs from current runtime')
    if row['split'] not in ('quarantine', 'legacy_regression'):
        errors.extend(safety_reasons(row))
    if row['split'] in SPLITS:
        if row['annotation']['status'] != 'adjudicated' or row['annotation']['confidence'] == 'low':
            errors.append('annotation: release data requires adjudicated, non-low confidence')
        if snap['pending'] or snap['quality_issue_codes'] or any(s['status'] != 'OK' for s in segments):
            errors.append('snapshot: unusable F1 request; quality/pending policy cases must be quarantined')
    if row['source']['kind'] == 'legacy' and row['split'] not in ('legacy_regression', 'quarantine'):
        errors.append('legacy source must remain legacy_regression')
    ref = row.get('reference')
    if ref:
        if set(ref['points']) != set(keys):
            errors.append('reference: final-state point keys mismatch')
        if ref['phase'] != snap['phase']:
            errors.append('reference: replay phase mismatch')
    return errors


def group_tokens(row):
    source = row['source']
    tokens = [('group', row['lineage']['split_group_id'])]
    for field in ('source_id', 'session_id', 'deck_family_id', 'speaker_group_id', 'scenario_family_id'):
        tokens.append((field, source[field]))
    if row['lineage']['duplicate_cluster_id']:
        tokens.append(('duplicate', row['lineage']['duplicate_cluster_id']))
    tokens += [('sample', row['sample_id'])]
    tokens += [('sample', p) for p in source['derived_from_ids']]
    if source['parent_sample_id']:
        tokens.append(('sample', source['parent_sample_id']))
    return tokens


def validate_rows(rows):
    errors, seen, owners = [], set(), {}
    if not rows:
        return ['empty dataset']
    for i, row in enumerate(rows, 1):
        local = record_errors(row)
        errors.extend(f'record {i}: {e}' for e in local)
        if local:
            continue
        sid = row['sample_id']
        if sid in seen:
            errors.append(f'record {i}: duplicate sample_id')
        seen.add(sid)
        if row['split'] != 'unassigned':
            tokens = group_tokens(row) + [('input', digest(normalize(row['input'])))]
            for token in tokens:
                old = owners.setdefault(token, row['split'])
                if old != row['split']:
                    errors.append(f'record {i}: cross-split lineage/input leakage')
    return errors


def require_valid(rows):
    errors = validate_rows(rows)
    if errors:
        raise DatasetError('\n'.join(errors))
    return rows


def normalize(value):
    if isinstance(value, str):
        return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', value)).strip()
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    return value


def content_view(inp):
    # Ignore presentation title and local IDs; preserve numbers, negation and order.
    return normalize({'keypoints': list(inp['keypoints'].values()),
                      'utterances': [u['text'] for u in inp['utterances']]})


def run_cli(operation):
    try:
        operation()
    except (DatasetError, OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
