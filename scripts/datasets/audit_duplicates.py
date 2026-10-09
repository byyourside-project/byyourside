"""Report exact/normalized/conflicting and lexical near-duplicate candidates; never delete."""
import argparse
from difflib import SequenceMatcher
from pathlib import Path

try:
    from .common import ROOT, DatasetError, content_view, digest, load_jsonl, model_input, normalize, parse_json, require_valid, run_cli, stable_json, write_json
except ImportError:
    from common import ROOT, DatasetError, content_view, digest, load_jsonl, model_input, normalize, parse_json, require_valid, run_cli, stable_json, write_json


def legacy_inputs(directory=ROOT / 'examples'):
    from src.coaching_eval import reference_points, validate_cases
    from src.presentation import Session
    results, files = [], []
    for path in sorted(Path(directory).glob('coaching*eval*.json')):
        data = parse_json(path.read_text(encoding='utf-8'))
        cases = validate_cases(data)
        files.append({'path': path.name, 'sha256': __import__('hashlib').sha256(path.read_bytes()).hexdigest(), 'cases': len(cases)})
        for case in cases:
            points = [{'keypoint_id': f'k{i}', 'text': p['point'], 'aliases': p.get('aliases', []), 'required': True}
                      for i, p in enumerate(reference_points(case), 1)]
            slide = {'slide_id': 's1', 'title': case.get('title', '핵심 내용'), 'target_duration_sec': 300, 'keypoints': points}
            now = [0.0]
            session = Session({'deck_id': 'legacy', 'title': 'legacy', 'total_duration_sec': 600, 'slides': [slide]}, clock=lambda: now[0])
            for i, utterance in enumerate(case['utterances']):
                now[0] = (i+1) * 2.0
                job = session.ingest({'segment_id': f'{i+1}', 'start_sec': i*2.0+.1, 'end_sec': i*2.0+1,
                                      'text': utterance['text'], 'status': utterance.get('status', 'OK'),
                                      'endpoint_reason': utterance.get('endpoint_reason', 'silence')})
                if job:
                    results.append({'id': path.name + ':' + case['id'] + ':' + str(i+1),
                                    'input': model_input(job['slide'], job['segments'])})
                    session.apply(job, {'judgments': [{'keypoint_id': p['keypoint_id'], 'status': 'unconfirmed',
                                    'reason': 'signature replay only', 'evidence_segment_ids': []} for p in points]})
            # Include full case content as well, even when quality blocks an actual request.
            results.append({'id': path.name + ':' + case['id'] + ':full', 'input': {
                'slide_title': slide['title'], 'keypoints': {str(i): p['text'] for i, p in enumerate(points, 1)},
                'utterances': [{'number': i, 'text': u['text']} for i, u in enumerate(case['utterances'], 1)]}})
    if not files:
        raise DatasetError('no legacy evaluation files; refusing to skip contamination audit')
    return results, files


def similarity(left, right):
    return SequenceMatcher(None, stable_json(content_view(left)), stable_json(content_view(right)), autojunk=False).ratio()


def audit(rows, legacy_directory=ROOT / 'examples', threshold=.85):
    require_valid(rows)
    if not 0 < threshold <= 1:
        raise DatasetError('threshold must be in (0,1]')
    pairs, legacy_matches = [], []
    for i, left in enumerate(rows):
        for right in rows[i+1:]:
            exact = left['input'] == right['input']
            normalized = normalize(left['input']) == normalize(right['input'])
            content_equal = content_view(left['input']) == content_view(right['input'])
            score = similarity(left['input'], right['input'])
            same_target = left['target'] == right['target']
            kind = ('conflict' if normalized and not same_target else
                    'exact_input' if exact else 'normalized_input' if normalized else
                    'content_duplicate' if content_equal else 'near_candidate' if score >= threshold else None)
            if kind:
                pairs.append({'left': left['sample_id'], 'right': right['sample_id'], 'kind': kind,
                              'score': score, 'same_target': same_target,
                              'identical_record': digest(left) == digest(right)})
    legacy, files = legacy_inputs(legacy_directory)
    for row in rows:
        for item in legacy:
            equal = content_view(row['input']) == content_view(item['input'])
            score = similarity(row['input'], item['input'])
            if equal or score >= threshold:
                legacy_matches.append({'sample_id': row['sample_id'], 'legacy_id': item['id'],
                                       'kind': 'content_duplicate' if equal else 'near_candidate', 'score': score})
    return {'audit_version': '1.0', 'method': 'NFC/whitespace + title-independent lexical SequenceMatcher; near matches require human review; no semantic embedding',
            'threshold': threshold, 'records': len(rows), 'input_sha256': digest(sorted(rows, key=lambda r: r['sample_id'])),
            'pairs': pairs, 'legacy_matches': legacy_matches, 'legacy_files': files}


def require_release_audit(rows, **kwargs):
    report = audit(rows, **kwargs)
    split = {r['sample_id']: r['split'] for r in rows}
    problems = []
    for pair in report['pairs']:
        if pair['kind'] == 'conflict':
            problems.append('conflicting targets for identical input')
        if split[pair['left']] != split[pair['right']]:
            problems.append('duplicate/near candidate crosses splits')
    if any(split[m['sample_id']] != 'legacy_regression' for m in report['legacy_matches']):
        problems.append('legacy contamination candidate requires legacy_regression isolation')
    if problems:
        raise DatasetError('; '.join(sorted(set(problems))))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--legacy-dir', type=Path, default=ROOT / 'examples')
    parser.add_argument('--threshold', type=float, default=.85)
    args = parser.parse_args()
    result = audit(load_jsonl(args.input), args.legacy_dir, args.threshold)
    write_json(args.output, result)
    print(f"Audited {result['records']} records; {len(result['pairs'])} pairs, {len(result['legacy_matches'])} legacy matches")
    if any(p['kind'] == 'conflict' for p in result['pairs']):
        raise DatasetError('conflicting labels found; see audit')


if __name__ == '__main__':
    run_cli(main)
