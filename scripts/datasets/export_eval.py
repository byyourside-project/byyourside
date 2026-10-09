"""Export single-request references or explicitly adjudicated legacy-session replay cases."""
import argparse
import copy
from pathlib import Path
try:
    from .common import ROOT, DatasetError, require_valid, run_cli, write_json
    from .audit_duplicates import require_release_audit
    from .export_sft import load_release
except ImportError:
    from common import ROOT, DatasetError, require_valid, run_cli, write_json
    from audit_duplicates import require_release_audit
    from export_sft import load_release


def export_eval(rows, mode='single', split='test', legacy_directory=ROOT / 'examples'):
    require_valid(rows)
    require_release_audit([r for r in rows if r['split'] in ('train', 'validation', 'test')], legacy_directory=legacy_directory)
    selected = [r for r in rows if r['split'] == split]
    if not selected:
        raise DatasetError('no evaluation samples')
    if mode == 'single':
        return {'schema_version': '1.0', 'purpose': 'single_request_compact_judgment; NOT a coaching_eval session dataset',
                'requests': [{'id': r['sample_id'], 'input': copy.deepcopy(r['input']),
                              'expected_compact': copy.deepcopy(r['target']['compact'])} for r in selected]}
    if mode != 'session':
        raise DatasetError('unknown evaluation mode')
    cases = []
    for row in selected:
        ref = row.get('reference')
        if ref is None:
            raise DatasetError('session export requires separately adjudicated reference, never inferred from compact')
        # Legacy evaluator reconstructs segments with 0.9s duration and 1.1s gaps.
        # Export a deliberately retimed replay, not an assertion of original timing equivalence.
        segments = row['snapshot']['segments']
        if len(segments) * 2 > 300 or any(not s['text'].strip() for s in segments):
            raise DatasetError('source cannot be represented by legacy evaluator')
        cases.append({'id': row['sample_id'], 'title': row['input']['slide_title'], 'phase': ref['phase'],
                      'category': 'adjudicated_retimed_replay',
                      'points': [{'point': text, 'aliases': [], **ref['points'][key]} for key, text in row['input']['keypoints'].items()],
                      'utterances': [{k: s[k] for k in ('text', 'status', 'endpoint_reason')} for s in segments]})
    from src.coaching_eval import validate_cases
    output = {'purpose': 'Separately labeled legacy_evaluator_replay; retimed, not original-session accuracy', 'cases': cases}
    validate_cases(output)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('release_dir', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--split', choices=('validation', 'test'), default='test')
    p.add_argument('--mode', choices=('single', 'session'), default='single')
    p.add_argument('--legacy-dir', type=Path, default=ROOT / 'examples')
    args = p.parse_args()
    rows, _ = load_release(args.release_dir)
    result = export_eval(rows, args.mode, args.split, args.legacy_dir)
    write_json(args.output, result)
    print(f'Exported {args.mode} evaluation references')


if __name__ == '__main__':
    run_cli(main)
