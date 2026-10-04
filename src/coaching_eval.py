"""Text-only quality evaluation, independent of audio and of model output labels."""
import copy
import hashlib
import json
import math
import time

from src.presentation import Session


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    offset = (len(values) - 1) * fraction
    lower = math.floor(offset)
    return values[lower] + (values[math.ceil(offset)] - values[lower]) * (offset - lower)


def validate_cases(dataset):
    cases = dataset.get("cases") if isinstance(dataset, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("비어 있지 않은 cases 배열이 필요합니다.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or case["id"] in seen:
            raise ValueError("각 평가 항목에는 고유한 id가 필요합니다.")
        seen.add(case["id"])
        if case.get("expected_status") not in ("explained", "uncertain", "unconfirmed") or not isinstance(case.get("expected_missing"), bool):
            raise ValueError("평가 정답 상태와 expected_missing bool이 필요합니다.")
        if case.get("phase", "ongoing") not in ("ongoing", "transition", "end"):
            raise ValueError("phase는 ongoing/transition/end여야 합니다.")
        if not isinstance(case.get("point"), str) or not case["point"].strip():
            raise ValueError("핵심 항목 문장이 필요합니다.")
        if not isinstance(case.get("utterances"), list) or not case["utterances"]:
            raise ValueError("발화 배열이 필요합니다.")
        for utterance in case["utterances"]:
            if not isinstance(utterance, dict) or not isinstance(utterance.get("text"), str) or not utterance["text"].strip():
                raise ValueError("평가 발화는 비어 있지 않은 텍스트여야 합니다.")
    return cases


def evaluate_cases(dataset, coach, progress=None, latency_budget_ms=2000.0):
    cases = validate_cases(dataset)
    rows, latency, calls, valid_calls = [], [], 0, 0
    for case in cases:
        current_time = [0.0]
        deck = {"deck_id": "evaluation", "title": "정답 전사 평가", "total_duration_sec": 600,
                "slides": [{"slide_id": "s1", "title": case.get("title", "핵심 내용"), "target_duration_sec": 300,
                            "keypoints": [{"keypoint_id": "k1", "text": case["point"], "required": True,
                                           "aliases": case.get("aliases", [])}]},
                           {"slide_id": "s2", "title": "다음", "target_duration_sec": 300, "keypoints": []}]}
        session = Session(deck, clock=lambda: current_time[0])
        responses, errors, timings = [], [], []
        for index, utterance in enumerate(case["utterances"]):
            current_time[0] = (index + 1) * 2.0
            job = session.ingest({"segment_id": f"{case['id']}:{index + 1}", "text": utterance["text"],
                                  "start_sec": index * 2.0 + .1, "end_sec": index * 2.0 + 1,
                                  "endpoint_reason": utterance.get("endpoint_reason", "silence"),
                                  "status": utterance.get("status", "OK")})
            if job is None:
                continue
            calls += 1
            began = time.perf_counter()
            try:
                # Expected labels and category are never sent to the model.
                response = coach.evaluate(job)
                session.apply(job, response)
                valid_calls += 1
                responses.append(copy.deepcopy(response))
            except Exception as exc:
                errors.append({"type": type(exc).__name__, "message": str(exc)})
                session.fail_job(job, f"평가 추론 실패: {exc}")
            finally:
                wall_ms = (time.perf_counter() - began) * 1000
                latency.append(wall_ms)
                timings.append({"wall_ms": wall_ms, "provider_metrics": copy.deepcopy(getattr(coach, "last_metrics", {}))})
        if case.get("phase") == "transition":
            session.navigate(1)
        elif case.get("phase") == "end":
            session.stop()
            session.finish()
        actual = session.states["k1"]["status"]
        missing = any(event["type"] == "slide_review" and "k1" in event["missing_candidates"] for event in session.events)
        row = {"id": case["id"], "category": case.get("category", "uncategorized"),
               "expected_status": case["expected_status"], "actual_status": actual,
               "expected_missing": case["expected_missing"], "actual_missing": missing,
               "correct": not errors and actual == case["expected_status"] and missing == case["expected_missing"],
               "errors": errors, "model_responses": responses, "timings": timings,
               "evidence_segment_ids": session.states["k1"]["evidence_segment_ids"],
               "decision_reason": session.states["k1"]["reason"]}
        rows.append(row)
        if progress:
            progress(row)
    correct = sum(row["correct"] for row in rows)
    safe_cases = [row for row in rows if not row["expected_missing"]]
    not_explained = [row for row in rows if row["expected_status"] != "explained"]
    false_missing = sum(row["actual_missing"] for row in safe_cases)
    false_explained = sum(row["actual_status"] == "explained" for row in not_explained)
    by_category = {}
    for row in rows:
        group = by_category.setdefault(row["category"], {"count": 0, "correct": 0})
        group["count"] += 1
        group["correct"] += int(row["correct"])
    accuracy = correct / len(rows)
    p95 = percentile(latency, .95)
    gates = {"development_accuracy": accuracy >= .9,
             "response_validity": valid_calls == calls and calls > 0,
             "false_missing_rate": false_missing / len(safe_cases) <= .05 if safe_cases else None,
             "false_explained_rate": false_explained / len(not_explained) <= .05 if not_explained else None,
             "request_latency": p95 <= latency_budget_ms if p95 is not None else None}
    return {"schema_version": 1, "provider": coach.name,
            "prompt_version": getattr(coach, "prompt_version", None),
            "model": getattr(coach, "model", None), "model_info": getattr(coach, "model_info", None),
            "dataset_sha256": hashlib.sha256(json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "dataset_scope": dataset.get("purpose", "development cases"), "total_cases": len(rows),
            "correct_cases": correct, "accuracy": accuracy, "inference_calls": calls,
            "valid_response_count": valid_calls, "valid_response_rate": valid_calls / calls if calls else None,
            "error_cases": sum(bool(row["errors"]) for row in rows),
            "false_missing": {"count": false_missing, "eligible_cases": len(safe_cases)},
            "false_explained": {"count": false_explained, "eligible_cases": len(not_explained)},
            "latency": {"unit": "ms", "scope": "all model calls, including any initial load; not audio-to-screen latency",
                        "count": len(latency), "p50": percentile(latency, .5), "p95": p95,
                        "max": max(latency) if latency else None,
                        "first_call": latency[0] if latency else None,
                        "subsequent_p95": percentile(latency[1:], .95), "budget_ms": latency_budget_ms},
            "category_breakdown": by_category, "gate_breakdown": gates,
            "status": "DEVELOPMENT_PASS" if all(value is not False for value in gates.values()) else "CHANGES_REQUIRED",
            "generalization_status": "NOT_RUN", "real_audio_status": "NOT_RUN", "cases": rows}
