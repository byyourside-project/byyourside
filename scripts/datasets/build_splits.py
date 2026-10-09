"""Deterministic connected-component split; near candidates stay together, never deleted."""
import argparse
import copy
import random
from collections import Counter
from pathlib import Path
try:
    from .common import ROOT, SCHEMA, SYSTEM_PROMPT, DatasetError, SPLITS, digest, group_tokens, load_jsonl, require_valid, run_cli, write_json, write_jsonl
    from .audit_duplicates import audit, require_release_audit
except ImportError:
    from common import ROOT, SCHEMA, SYSTEM_PROMPT, DatasetError, SPLITS, digest, group_tokens, load_jsonl, require_valid, run_cli, write_json, write_jsonl
    from audit_duplicates import audit, require_release_audit


def build_splits(rows, seed=42, legacy_directory=ROOT / 'examples'):
    require_valid(rows)
    if any(r['split'] not in ('unassigned', 'legacy_regression', 'quarantine') for r in rows):
        raise DatasetError('input must be unassigned/legacy_regression/quarantine; frozen releases cannot be re-split')
    rows = sorted(copy.deepcopy(rows), key=lambda r: r['sample_id'])
    report = audit(rows, legacy_directory)
    parents = {r['sample_id']: r['sample_id'] for r in rows}
    def find(x):
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)
    tokens = {}
    for row in rows:
        for token in group_tokens(row):
            old = tokens.setdefault(token, row['sample_id'])
            union(old, row['sample_id'])
    for pair in report['pairs']:
        union(pair['left'], pair['right'])
    conflicts = {p[k] for p in report['pairs'] if p['kind'] == 'conflict' for k in ('left', 'right')}
    legacy = {m['sample_id'] for m in report['legacy_matches']}
    groups = {}
    for row in rows:
        groups.setdefault(find(row['sample_id']), []).append(row)
    assignable = []
    for group, members in sorted(groups.items()):
        ids = {r['sample_id'] for r in members}
        isolate = ('quarantine' if ids & conflicts or any(r['split'] == 'quarantine' for r in members) else
                   'legacy_regression' if ids & legacy or any(r['split'] == 'legacy_regression' for r in members) else None)
        for row in members:
            row['lineage']['split_group_id'] = 'component-' + digest(sorted(ids))[:16]
        if isolate:
            for row in members:
                row['split'] = isolate
        else:
            assignable.append(members)
    random.Random(seed).shuffle(assignable)
    # Assign large groups first; shuffled order breaks size ties deterministically.
    assignable.sort(key=len, reverse=True)
    total = sum(map(len, assignable))
    targets = dict(zip(SPLITS, (total*.8, total*.1, total*.1)))
    counts = Counter()
    for members in assignable:
        chosen = max(SPLITS, key=lambda s: targets[s] - counts[s])
        for row in members:
            row['split'] = chosen
        counts[chosen] += len(members)
    require_valid(rows)
    release = [r for r in rows if r['split'] in SPLITS]
    if release:
        require_release_audit(release, legacy_directory=legacy_directory)
    summary = {s: {'records': sum(r['split'] == s for r in rows),
                   'groups': len({r['lineage']['split_group_id'] for r in rows if r['split'] == s}),
                   'labels': dict(Counter(str(v['s']) for r in rows if r['split'] == s for v in r['target']['compact'].values()))}
               for s in (*SPLITS, 'legacy_regression', 'quarantine')}
    manifest = {'dataset_version': '1.0', 'algorithm': 'connected-groups-largest-deficit-v1',
                'seed': seed, 'ratios': dict(zip(SPLITS, (.8, .1, .1))),
                'input_sha256': report['input_sha256'], 'audit_sha256': digest(report),
                'schema_sha256': digest(SCHEMA), 'system_prompt_sha256': digest(SYSTEM_PROMPT),
                'runtime_adapter_sha256': __import__('hashlib').sha256((ROOT / 'src/ollama_coach.py').read_bytes()).hexdigest(),
                'summary': summary, 'warnings': ['Ratios are targets; groups are indivisible. Empty/small test sets do not establish independent quality.'],
                'samples': [{'sample_id': r['sample_id'], 'split': r['split'], 'group': r['lineage']['split_group_id'],
                             'input_sha256': digest(r['input']), 'record_sha256': digest(r)} for r in rows]}
    return rows, manifest, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--legacy-dir', type=Path, default=ROOT / 'examples')
    args = p.parse_args()
    rows, manifest, report = build_splits(load_jsonl(args.input), args.seed, args.legacy_dir)
    # Write only after every record and all release gates pass.
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise DatasetError('output directory must be empty; use a new release directory')
    for split in (*SPLITS, 'legacy_regression', 'quarantine'):
        write_jsonl(args.output_dir / (split + '.jsonl'), [r for r in rows if r['split'] == split])
    write_json(args.output_dir / 'manifest.json', manifest)
    write_json(args.output_dir / 'audit.json', report)
    print(manifest['summary'])


if __name__ == '__main__':
    run_cli(main)
