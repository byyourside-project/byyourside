"""Small local LoRA experiment with trainable photo-booth function tokens.

The default model is the Meta Llama model named in the course material.
Requires access to its gated Hugging Face repository and torch/transformers/peft.
This pilot does not replace the Maum AI training or board conversion scripts.
"""

from __future__ import annotations

import argparse
import json
import random
import hashlib
import time
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
    parser.add_argument("--save-every", type=int, default=100)
    parser.add_argument("--resume", type=Path)
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
    started = time.perf_counter()
    args = arguments()
    if args.steps <= 0:
        raise ValueError("--steps must be positive")
    train_file = args.data / "train.jsonl"
    examples = [json.loads(line) for line in train_file.read_text(encoding="utf-8").splitlines()]
    fingerprint = hashlib.sha256(train_file.read_bytes()).hexdigest()
    torch.manual_seed(20261017)
    random.Random(20261017).shuffle(examples)
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    dtype = torch.float16 if device.type in ("cuda", "mps") else torch.float32

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
    # Keep AdamW moments and small trainable weights in float32 on half-precision bases.
    for parameter in model.parameters():
        if parameter.requires_grad:
            parameter.data = parameter.data.float()
    model.config.use_cache = False
    if device.type == "cuda":
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.train()
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.learning_rate)

    losses: list[float] = []
    start = 0
    def save_checkpoint(step):
        folder = args.output / f"checkpoint-{step}"
        folder.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(folder / "adapter")
        tokenizer.save_pretrained(folder / "tokenizer")
        torch.save({"step": step, "losses": losses, "optimizer": optimizer.state_dict(),
                    "trainable": {n: p.detach().cpu() for n, p in model.named_parameters() if p.requires_grad},
                    "rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state_all() if device.type == "cuda" else None,
                    "fingerprint": fingerprint, "model": args.model, "max_length": args.max_length}, folder / "state.tmp")
        (folder / "state.tmp").replace(folder / "state.pt")
    if args.resume:
        state = torch.load(args.resume / "state.pt", map_location="cpu", weights_only=True)
        if (state["fingerprint"], state["model"], state["max_length"]) != (fingerprint, args.model, args.max_length):
            raise ValueError("checkpoint data/model/max-length mismatch")
        with torch.no_grad():
            for name, param in model.named_parameters():
                if param.requires_grad:
                    param.copy_(state["trainable"][name])
        optimizer.load_state_dict(state["optimizer"])
        torch.set_rng_state(state["rng"])
        if device.type == "cuda":
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        start, losses = state["step"], state["losses"]
    for step in range(start, args.steps):
        input_ids, labels = encoded_example(tokenizer, examples[step % len(examples)], args.max_length)
        optimizer.zero_grad(set_to_none=True)
        result = model(input_ids=input_ids.to(device), labels=labels.to(device))
        if not torch.isfinite(result.loss).item():
            raise RuntimeError("non-finite training loss")
        result.loss.backward()
        if any(p.grad is not None and not torch.isfinite(p.grad).all().item() for p in model.parameters() if p.requires_grad):
            raise RuntimeError("non-finite training gradients")
        optimizer.step()
        loss = float(result.loss.detach().cpu())
        losses.append(loss)
        print(f"step {step + 1}/{args.steps}: loss={loss:.4f}", flush=True)
        if args.save_every > 0 and ((step + 1) % args.save_every == 0 or step + 1 == args.steps):
            save_checkpoint(step + 1)
            if device.type == "cuda":
                print(f"checkpoint {step + 1}: peak CUDA allocated={torch.cuda.max_memory_allocated() / 2**30:.2f} GiB, reserved={torch.cuda.max_memory_reserved() / 2**30:.2f} GiB; elapsed={time.perf_counter() - started:.1f}s", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output / "adapter")
    tokenizer.save_pretrained(args.output / "tokenizer")
    (args.output / "pilot_metrics.json").write_text(
        json.dumps({"model": args.model, "device": str(device), "steps": args.steps, "losses": losses,
                    "special_token_ids": dict(zip(TOKENS, ids)), "evaluated": False,
                    "resumed_from_step": start, "examples": len(examples), "data_sha256": fingerprint,
                    "elapsed_seconds": time.perf_counter() - started, "torch_version": torch.__version__,
                    "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if device.type == "cuda" else None,
                    "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved() if device.type == "cuda" else None}, indent=2) + "\n"
    )
    print(f"saved pilot to {args.output}")


if __name__ == "__main__":
    main()
