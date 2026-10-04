#!/usr/bin/env python3
"""Evaluate baseline or installed Ollama coach against fixed text references."""
import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.coaching_eval import evaluate_cases
from src.presentation import PhraseCoach, positive_number
from src.ollama_coach import OllamaCoach


def main():
    parser = argparse.ArgumentParser(description="발표 내용 코칭 정답 전사 평가")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent.parent / "examples/coaching_eval.json")
    parser.add_argument("--ollama-model")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--latency-budget-ms", type=float, default=2000)
    parser.add_argument("--warm-up", action="store_true", help="별도 더미 추론 후 warm 평가; 로딩 시간도 따로 저장")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    timestamp = datetime.now(timezone.utc)
    target = args.output or Path("logs") / f"coaching_eval_{timestamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json"
    # Reserve an exclusive output before inference; previous results are never overwritten.
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        try:
            latency_budget = positive_number(args.latency_budget_ms, "latency budget")
            coach = OllamaCoach(args.ollama_model, args.ollama_url, args.timeout) if args.ollama_model else PhraseCoach()
            dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
            warm_up = coach.warm_up(args.timeout) if args.warm_up and isinstance(coach, OllamaCoach) else None
            result = evaluate_cases(dataset, coach, latency_budget_ms=latency_budget,
                                    progress=lambda row: print(f"{row['id']}: {'OK' if row['correct'] else 'FAIL'} expected={row.get('expected_status', [p['expected_status'] for p in row['points']])} actual={row.get('actual_status', [p['actual_status'] for p in row['points']])} errors={len(row['errors'])}", flush=True))
            result["warm_up"] = {"requested": args.warm_up, "metrics": warm_up,
                                 "excluded_from_case_latency": warm_up is not None}
            if warm_up is not None:
                result["latency"]["scope"] = "case model requests after a separate dummy warm-up; any per-case model load remains included; not audio-to-screen latency"
        except Exception as exc:
            result = {"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)}
        try:
            git_revision = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
            git_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], stderr=subprocess.DEVNULL).strip())
        except Exception:
            git_revision, git_dirty = None, None
        result.update(timestamp_utc=timestamp.isoformat(), dataset_path=str(args.dataset),
                      git_revision=git_revision, git_dirty=git_dirty)
        json.dump(result, file, ensure_ascii=False, indent=2)
    print(f"Result: {result['status']} | Output: {target}", flush=True)
    if "accuracy" in result:
        print(f"Accuracy: {result['accuracy']:.1%} | valid responses: {result['valid_response_rate']} | request p95: {result['latency']['p95']} ms", flush=True)
    return 0 if result["status"] == "DEVELOPMENT_PASS" else 2 if result["status"] == "CHANGES_REQUIRED" else 1


if __name__ == "__main__":
    sys.exit(main())
