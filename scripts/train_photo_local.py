"""Small local LoRA experiment with trainable photo-booth function tokens.

The default model is the Meta Llama model named in the course material.
Requires access to its gated Hugging Face repository and torch/transformers/peft.
This pilot does not replace the Maum AI training or board conversion scripts.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "outputs/photo_booth/local_training_v09"
TOKENS = [*(f"<maum_{i}>" for i in range(7)), "<maum_end>"]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="meta-llama/Llama-3.2-1B-Instruct")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/photo_booth/llama_pilot")
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    return parser.parse_args()


def encoded_example(tokenizer, example: dict, max_length: int) -> tuple[torch.Tensor, torch.Tensor]:
    prompt = tokenizer(example["prompt"], add_special_tokens=True)["input_ids"]
    completion = tokenizer(example["completion"], add_special_tokens=False)["input_ids"]
    if tokenizer.eos_token_id is not None:
        completion.append(tokenizer.eos_token_id)
    if len(prompt) + len(completion) > max_length:
        raise ValueError(
            f"example has {len(prompt) + len(completion)} tokens; increase --max-length "
            "or shorten the prompt without truncating the answer"
        )
    ids = torch.tensor([prompt + completion], dtype=torch.long)
    labels = torch.tensor([[-100] * len(prompt) + completion], dtype=torch.long)
    return ids, labels


def main() -> None:
    args = arguments()
    if args.steps <= 0:
        raise ValueError("--steps must be positive")
    train_file = args.data / "train.jsonl"
    examples = [json.loads(line) for line in train_file.read_text().splitlines()]
    random.Random(20261017).shuffle(examples)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    dtype = torch.float16 if device.type == "mps" else torch.float32

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    added = tokenizer.add_special_tokens({"additional_special_tokens": TOKENS})
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype)
    model.resize_token_embeddings(len(tokenizer))
    ids = tokenizer.convert_tokens_to_ids(TOKENS)
    if added != len(TOKENS) or len(set(ids)) != len(TOKENS):
        raise ValueError("special tokens were not added as eight distinct token IDs")
    if any(len(tokenizer.encode(token, add_special_tokens=False)) != 1 for token in TOKENS):
        raise ValueError("a custom function token is not encoded as one token")

    config = LoraConfig(
        r=4,
        lora_alpha=8,
        target_modules=["q_proj", "v_proj"],
        trainable_token_indices={"embed_tokens": ids},
        task_type="CAUSAL_LM",
        ensure_weight_tying=True,
    )
    model = get_peft_model(model, config).to(device)
    model.train()
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.learning_rate)

    losses: list[float] = []
    for step in range(args.steps):
        input_ids, labels = encoded_example(tokenizer, examples[step % len(examples)], args.max_length)
        optimizer.zero_grad(set_to_none=True)
        result = model(input_ids=input_ids.to(device), labels=labels.to(device))
        result.loss.backward()
        optimizer.step()
        loss = float(result.loss.detach().cpu())
        losses.append(loss)
        print(f"step {step + 1}/{args.steps}: loss={loss:.4f}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output / "adapter")
    tokenizer.save_pretrained(args.output / "tokenizer")
    (args.output / "pilot_metrics.json").write_text(
        json.dumps({"model": args.model, "device": str(device), "steps": args.steps, "losses": losses,
                    "special_token_ids": dict(zip(TOKENS, ids)), "evaluated": False}, indent=2) + "\n"
    )
    print(f"saved pilot to {args.output}")


if __name__ == "__main__":
    main()
