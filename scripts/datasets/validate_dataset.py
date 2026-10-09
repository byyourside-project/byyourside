"""Fail closed on malformed contracts, split leakage and legacy contamination."""
import argparse
from pathlib import Path
try:
    from .common import ROOT, load_jsonl, require_valid, run_cli
    from .audit_duplicates import require_release_audit
except ImportError:
    from common import ROOT, load_jsonl, require_valid, run_cli
    from audit_duplicates import require_release_audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs', type=Path, nargs='+', help='Pass all split files together to check cross-file leakage')
    p.add_argument('--legacy-dir', type=Path, default=ROOT / 'examples')
    args = p.parse_args()
    rows = [r for path in args.inputs for r in load_jsonl(path)]
    require_valid(rows)
    if any(r['split'] in ('train', 'validation', 'test') for r in rows):
        require_release_audit(rows, legacy_directory=args.legacy_dir)
    print(f'VALID: {len(rows)} records')


if __name__ == '__main__':
    run_cli(main)
