"""Export validated release requests to chat messages; no training or downloads."""
import argparse
import hashlib
import json
from pathlib import Path
try:
    from .common import ROOT, SCHEMA, DatasetError, OllamaCoach, SYSTEM_PROMPT, digest, load_jsonl, parse_json, require_valid, run_cli, write_json, write_jsonl
    from .audit_duplicates import require_release_audit
except ImportError:
    from common import ROOT, SCHEMA, DatasetError, OllamaCoach, SYSTEM_PROMPT, digest, load_jsonl, parse_json, require_valid, run_cli, write_json, write_jsonl
    from audit_duplicates import require_release_audit


def load_release(directory):
    directory = Path(directory)
    manifest = parse_json((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_sha256') != digest(SCHEMA) or manifest.get('system_prompt_sha256') != digest(SYSTEM_PROMPT):
        raise DatasetError('release contract/prompt changed; review and rebuild the release')
    if manifest.get('runtime_adapter_sha256') != hashlib.sha256((ROOT / 'src/ollama_coach.py').read_bytes()).hexdigest():
        raise DatasetError('runtime adapter changed since release')
    rows = []
    for split in ('train', 'validation', 'test', 'legacy_regression', 'quarantine'):
        path = directory / (split + '.jsonl')
        if path.stat().st_size:
            part = load_jsonl(path)
            if any(r.get('split') != split for r in part):
                raise DatasetError('file/split mismatch')
            rows.extend(part)
    require_valid(rows)
    actual = sorted((r['sample_id'], r['split'], digest(r)) for r in rows)
    expected = sorted((r['sample_id'], r['split'], r['record_sha256']) for r in manifest['samples'])
    if actual != expected:
        raise DatasetError('release manifest checksum/membership mismatch')
    return rows, manifest


def export_sft(rows, split='train', legacy_directory=ROOT / 'examples'):
    if split not in ('train', 'validation'):
        raise DatasetError('SFT export only permits train or validation')
    require_valid(rows)
    release = [r for r in rows if r['split'] in ('train', 'validation', 'test')]
    require_release_audit(release, legacy_directory=legacy_directory)
    selected = [r for r in release if r['split'] == split]
    if not selected:
        raise DatasetError('no eligible samples for requested split')
    return [{'messages': [{'role': 'system', 'content': SYSTEM_PROMPT},
                          {'role': 'user', 'content': json.dumps(r['input'], ensure_ascii=False)},
                          {'role': 'assistant', 'content': json.dumps(r['target']['compact'], ensure_ascii=False)}]}
            for r in selected]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('release_dir', type=Path, help='build_splits output, including manifest and all split files')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--split', choices=('train', 'validation'), default='train')
    p.add_argument('--legacy-dir', type=Path, default=ROOT / 'examples')
    p.add_argument('--tokenizer-dir', type=Path, help='Optional already-local HF tokenizer; never downloads')
    args = p.parse_args()
    rows, release_manifest = load_release(args.release_dir)
    result = export_sft(rows, args.split, args.legacy_dir)
    token_info = {'status': 'NOT_CHECKED', 'requirement': 'Verify the chosen model chat template, assistant-only loss and EOS before training; do not train directly on role JSON text.'}
    if args.tokenizer_dir:
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise DatasetError('optional tokenizer check requires an already installed transformers package') from exc
        tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer_dir), local_files_only=True, trust_remote_code=False)
        if not tokenizer.chat_template:
            raise DatasetError('local tokenizer has no chat template')
        lengths = []
        for row in result:
            prompt = tokenizer.apply_chat_template(row['messages'][:2], tokenize=True, add_generation_prompt=True)
            full = tokenizer.apply_chat_template(row['messages'], tokenize=True, add_generation_prompt=False)
            if len(prompt) + 1024 > 4096 or len(full) > 4096:
                raise DatasetError('token budget exceeded; never truncate labels')
            lengths.append(len(full))
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.tokenizer_dir.iterdir() if p.is_file()}
        token_info = {'status': 'CHECKED_LOCAL', 'max_tokens': max(lengths), 'file_sha256': files,
                      'chat_template_sha256': hashlib.sha256(str(tokenizer.chat_template).encode()).hexdigest(),
                      'assistant_only_loss': 'must be configured and verified in trainer; no trainer executed'}
    manifest = {'format_version': '1.0', 'split': args.split, 'records': len(result),
                'prompt_version': OllamaCoach.prompt_version, 'prompt_sha256': hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                'release_manifest_sha256': digest(release_manifest), 'messages_sha256': digest(result), 'tokenizer': token_info,
                'runtime_options': {'num_ctx': 4096, 'num_predict': 1024, 'think': False, 'temperature': 0}}
    write_jsonl(args.output, result)
    write_json(str(args.output) + '.manifest.json', manifest)
    print(f'Exported {len(result)} {args.split} requests; tokenizer={token_info["status"]}')


if __name__ == '__main__':
    run_cli(main)
