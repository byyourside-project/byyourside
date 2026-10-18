"""Export only learned LoRA/token weights, excluding the frozen base embeddings."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-revision', required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new output directory to avoid replacing an adapter.')
    source = args.source / 'adapter/adapter_model.safetensors'
    learned = {}
    with safe_open(source, framework='pt', device='cpu') as saved:
        for key in saved.keys():
            if 'lora_' in key or 'trainable_tokens_delta' in key:
                learned[key] = saved.get_tensor(key).clone()
            elif not key.endswith('.token_adapter.base_layer.weight'):
                raise ValueError(f'Unexpected weight would be omitted: {key}')
    if not learned or not any('trainable_tokens_delta' in key for key in learned):
        raise ValueError('Missing learned photo tokens')
    args.output.mkdir(parents=True)
    (args.output / 'adapter').mkdir()
    target = args.output / 'adapter/adapter_model.safetensors'
    save_file(learned, target, metadata={'format': 'pt'})
    shutil.copyfile(args.source / 'adapter/adapter_config.json', args.output / 'adapter/adapter_config.json')
    shutil.copytree(args.source / 'tokenizer', args.output / 'tokenizer')
    info = {
        'base_model': 'meta-llama/Llama-3.2-1B-Instruct', 'base_revision': args.base_revision,
        'training_examples': 1280, 'training_steps': 1280, 'epochs': 1,
        'validation_correct': 193, 'validation_total': 256, 'test_evaluated': False,
        'tensor_count': len(learned), 'adapter_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'contains_base_weights': False,
    }
    (args.output / 'MODEL_INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(learned)} learned tensors, {target.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
