"""Measure exact function-call match for a local photo-booth LoRA adapter."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="meta-llama/Llama-3.2-1B-Instruct")
    parser.add_argument("--pilot", type=Path, default=ROOT / "outputs/photo_booth/llama_pilot")
    parser.add_argument("--data", type=Path, default=ROOT / "outputs/photo_booth/local_training_v09/valid.jsonl")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--base", action="store_true", help="Evaluate the untrained base with the same added tokens")
    args = parser.parse_args()
    if args.limit <= 0:
        raise ValueError("--limit must be positive")

    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    dtype = torch.float16 if device.type in ("cuda", "mps") else torch.float32
    torch.manual_seed(20261017)
    tokenizer = AutoTokenizer.from_pretrained(args.pilot / "tokenizer")
    base = AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype)
    base.resize_token_embeddings(len(tokenizer))
    model = (base if args.base else PeftModel.from_pretrained(base, args.pilot / "adapter")).to(device).eval()

    examples = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines()]
    if args.limit < len(examples):
        buckets: dict[str, list[dict]] = {}
        for example in examples:
            token = re.match(r"<maum_\d+>", example["completion"])
            buckets.setdefault(token.group() if token else "unknown", []).append(example)
        selected = []
        while len(selected) < args.limit and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(selected) < args.limit:
                    selected.append(bucket.pop(0))
        examples = selected

    results = []
    for example in examples[: args.limit]:
        inputs = tokenizer(example["prompt"], return_tensors="pt").to(device)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=32, do_sample=False, pad_token_id=tokenizer.eos_token_id)
        generated = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
        answer = generated.split("<maum_end>", 1)[0] + ("<maum_end>" if "<maum_end>" in generated else "")
        results.append({"prompt": example["prompt"], "expected": example["completion"], "predicted": answer, "correct": answer == example["completion"]})
        if len(results) % 25 == 0:
            print(f"evaluated {len(results)}/{min(args.limit, len(examples))}", flush=True)

    summary = {"evaluated": len(results), "exact_matches": sum(r["correct"] for r in results), "results": results}
    summary.update({"model": args.model, "base": args.base, "data": str(args.data), "accuracy": summary["exact_matches"] / len(results)})
    filename = "evaluation_base.json" if args.base else "evaluation.json"
    (args.pilot / filename).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics_path = args.pilot / "pilot_metrics.json"
    if not args.base and metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics.update({"evaluated": True, "evaluation_file": filename,
                        "evaluation_summary": {key: summary[key] for key in ("evaluated", "exact_matches", "accuracy", "data")}})
        metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"exact matches: {summary['exact_matches']}/{summary['evaluated']}")
    print(f"saved {args.pilot / filename}")


if __name__ == "__main__":
    main()
