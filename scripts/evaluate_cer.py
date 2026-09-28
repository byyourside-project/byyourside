#!/usr/bin/env python3
"""
Evaluation tool for Korean Presentation 30-Sentence CER Benchmark.
Supports:
  1. Strict P01~P30 coverage verification (1, 29, duplicate, or missing manifest items cannot achieve PASS).
  2. Manifest loading with explicit mode ('complete' vs 'merge') and SHA-256 integrity hashing.
  3. Direct STT vs VAD+STT evaluation: VAD CER is reported as null/NOT_RUN in direct mode (no fake 100%).
  4. Population-aware aggregate CER and category breakdowns for both Direct and VAD pipelines.
  5. Distinction between missing audio files (NOT_RUN), runtime failures (ERROR), and target misses (FAIL).
  6. Atomic JSON output writing and reproducible metadata (git commit, dirty status, config, hashes).
  7. Deterministic CLI exit codes (0: PASS, 1: FAIL/ERROR, 2: NOT_RUN/PARTIAL).
"""

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
import scipy.io.wavfile as wavfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.metrics import compute_cer, normalize_text, compute_corpus_cer
from src.pipeline import SpeechPipeline, get_git_revision
from src.config import PipelineConfig, SttConfig, VadConfig

# Canonical 30-Sentence Presentation Dataset
STANDARD_DATASET: List[Dict[str, str]] = [
    # Category 1: General Presentation Sentences (P01~P08)
    {"id": "P01", "category": "general", "ref": "지금부터 발표를 시작하도록 하겠습니다."},
    {"id": "P02", "category": "general", "ref": "오늘 말씀드릴 핵심 주제는 세 가지입니다."},
    {"id": "P03", "category": "general", "ref": "첫 번째로 시스템 구조의 개요를 살펴보겠습니다."},
    {"id": "P04", "category": "general", "ref": "기존 방식에서는 실시간 처리에 한계가 있었습니다."},
    {"id": "P05", "category": "general", "ref": "다음 슬라이드로 넘어가서 구체적인 결과를 확인하겠습니다."},
    {"id": "P06", "category": "general", "ref": "이어서 핵심 모듈의 동작 흐름을 설명해 드리겠습니다."},
    {"id": "P07", "category": "general", "ref": "결론적으로 사용자 경험이 크게 개선되었습니다."},
    {"id": "P08", "category": "general", "ref": "이상으로 발표를 마치고 질의응답을 받겠습니다."},

    # Category 2: Numbers, Percentages, Units (P09~P16)
    {"id": "P09", "category": "numbers_units", "ref": "전체 응답 속도는 15밀리초 단축되었습니다."},
    {"id": "P10", "category": "numbers_units", "ref": "인식 정확도는 95.8%를 기록했습니다."},
    {"id": "P11", "category": "numbers_units", "ref": "총 30개의 평가 문항을 준비했습니다."},
    {"id": "P12", "category": "numbers_units", "ref": "메모리 점유율은 약 512메가바이트입니다."},
    {"id": "P13", "category": "numbers_units", "ref": "지연 시간 목표는 1.5초 이하입니다."},
    {"id": "P14", "category": "numbers_units", "ref": "처리 용량은 이전 대비 2.5배 증가했습니다."},
    {"id": "P15", "category": "numbers_units", "ref": "초당 30프레임 속도로 데이터를 수집합니다."},
    {"id": "P16", "category": "numbers_units", "ref": "소비 전력은 15와트 수준으로 낮췄습니다."},

    # Category 3: English Acronyms & Technical Proper Nouns (P17~P24)
    {"id": "P17", "category": "technical_terms", "ref": "QCS6490 프로세서에서 NPU 가속을 적용합니다."},
    {"id": "P18", "category": "technical_terms", "ref": "Silero VAD 모듈이 발화 구간을 탐지합니다."},
    {"id": "P19", "category": "technical_terms", "ref": "SenseVoice 모델로 한국어 음성을 변환합니다."},
    {"id": "P20", "category": "technical_terms", "ref": "ONNX 런타임 환경에서 INT8 양자화를 수행했습니다."},
    {"id": "P21", "category": "technical_terms", "ref": "경량화된 LLM 파이프라인과 연동됩니다."},
    {"id": "P22", "category": "technical_terms", "ref": "REST API 대신 웹소켓 통신을 구성했습니다."},
    {"id": "P23", "category": "technical_terms", "ref": "CPU 사용률과 RTF 지표를 실시간 측정합니다."},
    {"id": "P24", "category": "technical_terms", "ref": "안드로이드 플랫폼 상에서 SDK를 통합합니다."},

    # Category 4: Fast Speech, Short Pauses & Boundary Sentences (P25~P30)
    {"id": "P25", "category": "fast_boundary", "ref": "시간이 부족하므로 빠르게 결론부터 요약해 드리겠습니다."},
    {"id": "P26", "category": "fast_boundary", "ref": "그리고 또한 이번 실험에서는 예외적인 오류가 발생하지 않았습니다."},
    {"id": "P27", "category": "fast_boundary", "ref": "잠시 표를 보시면 수치가 급격히 변하는 구간이 나타납니다."},
    {"id": "P28", "category": "fast_boundary", "ref": "하지만 실제 환경에서는 예상치 못한 변수가 존재할 수 있습니다."},
    {"id": "P29", "category": "fast_boundary", "ref": "이 부분에 대해서는 추가적인 검증 작업이 반드시 필요합니다."},
    {"id": "P30", "category": "fast_boundary", "ref": "질문 있으시면 자유롭게 말씀해 주시기 바랍니다."},
]

STANDARD_SET_SIZE = 30
STANDARD_IDS = {f"P{i:02d}" for i in range(1, STANDARD_SET_SIZE + 1)}


def get_git_info() -> Dict[str, Any]:
    """Retrieve git revision and dirty status for provenance tracking."""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return {"revision": rev, "is_dirty": len(status_out) > 0}
    except Exception:
        return {"revision": "unknown", "is_dirty": False}


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(filepath: str, data: Dict[str, Any]) -> None:
    """Safely write JSON to disk using atomic rename."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
    tmp_path = f"{filepath}.tmp_{uuid.uuid4().hex[:8]}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)


def load_manifest(manifest_path: Optional[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Load dataset from a manifest file or default standard dataset.
    Raises:
        FileNotFoundError: If manifest_path is specified but does not exist.
        ValueError: If manifest format is invalid or contains duplicate IDs.
    """
    if manifest_path is None:
        dataset = [dict(item) for item in STANDARD_DATASET]
        ref_concat = "".join(f"{it['id']}:{it['ref']}\n" for it in sorted(dataset, key=lambda x: str(x['id'])))
        ref_sha256 = hashlib.sha256(ref_concat.encode("utf-8")).hexdigest()
        manifest_meta = {
            "source": "default_standard",
            "path": None,
            "sha256": None,
            "mode": "complete",
            "reference_text_sha256": ref_sha256,
            "item_count": len(dataset),
            "unique_ids": sorted(list({it["id"] for it in dataset}))
        }
        return dataset, manifest_meta

    # Explicit manifest path provided
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Specified manifest file not found: {manifest_path}")

    manifest_sha256 = compute_file_sha256(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    if isinstance(manifest_data, dict):
        mode = manifest_data.get("mode", "complete")
        raw_items = manifest_data.get("items", [])
    elif isinstance(manifest_data, list):
        mode = "complete"
        raw_items = manifest_data
    else:
        raise ValueError("Invalid manifest JSON: root must be an object or an array of items")

    if not isinstance(raw_items, list):
        raise ValueError("Manifest 'items' must be a list of item objects")

    # Check validity and duplicate IDs
    seen_ids = set()
    duplicate_ids = []
    for it in raw_items:
        if not isinstance(it, dict) or "id" not in it or not it.get("ref"):
            raise ValueError(f"Invalid manifest item (must have 'id' and non-empty 'ref'): {it}")
        item_id = str(it["id"]).strip()
        if item_id in seen_ids:
            duplicate_ids.append(item_id)
        seen_ids.add(item_id)

    if duplicate_ids:
        raise ValueError(f"Duplicate item IDs detected in manifest: {sorted(list(set(duplicate_ids)))}")

    if mode == "merge":
        # Merge specified items into standard dataset
        std_map = {item["id"]: dict(item) for item in STANDARD_DATASET}
        for it in raw_items:
            item_id = str(it["id"]).strip()
            if item_id in std_map:
                std_map[item_id].update(it)
            else:
                std_map[item_id] = dict(it)
        dataset = list(std_map.values())
    elif mode == "complete":
        dataset = [dict(it) for it in raw_items]
    else:
        raise ValueError(f"Unsupported manifest mode: '{mode}'. Supported modes: 'complete', 'merge'")

    ref_concat = "".join(f"{it['id']}:{it['ref']}\n" for it in sorted(dataset, key=lambda x: str(x['id'])))
    ref_sha256 = hashlib.sha256(ref_concat.encode("utf-8")).hexdigest()
    manifest_meta = {
        "source": "custom_manifest",
        "path": manifest_path,
        "sha256": manifest_sha256,
        "mode": mode,
        "reference_text_sha256": ref_sha256,
        "item_count": len(dataset),
        "unique_ids": sorted(list({it["id"] for it in dataset}))
    }
    return dataset, manifest_meta


def evaluate_dataset(
    audio_dir: str = "audio/eval_30",
    manifest_path: Optional[str] = None,
    output_json: Optional[str] = None,
    mode: str = "all"
) -> Dict[str, Any]:
    """
    Executes the CER evaluation workflow against recorded audio files.
    """
    run_timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    run_uuid = uuid.uuid4().hex[:8]
    exec_id = f"{run_timestamp}_{run_uuid}"

    if output_json is None:
        output_json = f"logs/cer_eval_results_{exec_id}.json"

    git_info = get_git_info()

    # 1. Load dataset & validate manifest
    dataset = []
    manifest_meta = {}
    try:
        dataset, manifest_meta = load_manifest(manifest_path)
    except Exception as exc:
        err_report = {
            "benchmark": "Korean Presentation 30-Sentence CER Benchmark",
            "execution_id": exec_id,
            "git_revision": git_info["revision"],
            "git_dirty": git_info["is_dirty"],
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "audio_directory": audio_dir,
            "manifest_path": manifest_path,
            "total_dataset_items": 0,
            "executed_count": 0,
            "not_run_count": 0,
            "error_count": 1,
            "overall_status": f"ERROR (Manifest error: {exc})",
            "structured_status": "ERROR",
            "error": str(exc),
            "manifest_metadata": {"path": manifest_path, "error": str(exc)}
        }
        atomic_write_json(output_json, err_report)
        print(f"Error loading manifest: {exc}", file=sys.stderr)
        return err_report

    dataset_ids = {it["id"] for it in dataset}
    covers_standard_30 = (STANDARD_IDS == dataset_ids)
    covered_std_count = len(dataset_ids & STANDARD_IDS)

    print("=================================================================")
    print(" Korean Presentation 30-Sentence CER Evaluation")
    print(f" Execution ID:      {exec_id}")
    print(f" Git Revision:      {git_info['revision']} (dirty: {git_info['is_dirty']})")
    print(f" Audio Directory:   {audio_dir}")
    print(f" Evaluation Mode:   {mode}")
    print(f" Dataset Items:     {len(dataset)} (Standard 30 coverage: {covered_std_count}/30)")
    print(f" Manifest Source:   {manifest_meta['source']} (mode: {manifest_meta['mode']})")
    print(f" Target Output:     {output_json}")
    print("=================================================================")

    pipeline = None
    item_results = []
    found_audio_count = 0
    missing_audio_count = 0

    direct_success_count = 0
    direct_error_count = 0
    vad_success_count = 0
    vad_error_count = 0

    pairs_direct_nospace = []
    pairs_direct_spaced = []
    pairs_vad_nospace = []
    pairs_vad_spaced = []

    try:
        pipeline = SpeechPipeline(
            PipelineConfig(
                vad=VadConfig(min_silence_duration=0.5, max_speech_duration=4.0, hard_max_speech_duration=4.0),
                stt=SttConfig(request_timeout_sec=5.0, num_threads=4)
            )
        )

        for item in dataset:
            item_id = str(item["id"]).strip()
            category = item.get("category", "unclassified")
            ref_text = str(item["ref"]).strip()

            candidates = [
                os.path.join(audio_dir, f"{item_id}.wav"),
                os.path.join(audio_dir, f"{item_id.lower()}.wav"),
                os.path.join(audio_dir, f"{item_id.upper()}.wav"),
            ]
            if "audio_path" in item and os.path.exists(item["audio_path"]):
                candidates.insert(0, item["audio_path"])

            wav_path = None
            for cand in candidates:
                if os.path.exists(cand):
                    wav_path = cand
                    break

            if wav_path is None:
                item_results.append({
                    "id": item_id,
                    "category": category,
                    "status": "NOT_RUN",
                    "status_direct": "NOT_RUN",
                    "status_vad": "NOT_RUN",
                    "reason": f"WAV file not found in {audio_dir} (user recording required)",
                    "expected_paths": candidates[:2],
                    "reference": ref_text,
                    "hypothesis_direct": None,
                    "hypothesis_vad": None,
                    "cer_direct_nospace": None,
                    "cer_vad_nospace": None
                })
                missing_audio_count += 1
                continue

            # WAV file found
            found_audio_count += 1
            sha256 = compute_file_sha256(wav_path)
            try:
                sr, audio_data = wavfile.read(wav_path)
                dur_sec = len(audio_data) / float(sr) if sr > 0 else 0.0
                channels = 1 if audio_data.ndim == 1 else audio_data.shape[1]
            except Exception:
                sr, dur_sec, channels = 0, 0.0, 0

            rec_info: Dict[str, Any] = {
                "id": item_id,
                "category": category,
                "wav_path": wav_path,
                "sha256": sha256,
                "sample_rate": sr,
                "duration_seconds": round(dur_sec, 3),
                "channels": channels,
                "reference": ref_text,
                "status_direct": "NOT_RUN",
                "status_vad": "NOT_RUN"
            }

            # 1. Direct STT
            if mode in ["all", "direct"]:
                try:
                    run_id_direct = f"cer_direct_{item_id}_{run_uuid}"
                    res_direct = pipeline.run_wav_direct_stt(wav_path, run_id=run_id_direct)
                    hyp_direct = res_direct.segments[0]["text"] if res_direct.segments else ""
                    cer_d_space = compute_cer(ref_text, hyp_direct, remove_punct=True)
                    ref_no = normalize_text(ref_text, remove_punct=True).replace(" ", "")
                    hyp_no = normalize_text(hyp_direct, remove_punct=True).replace(" ", "")
                    cer_d_nospace = compute_cer(ref_no, hyp_no, remove_punct=False)

                    rec_info["status_direct"] = "SUCCESS"
                    rec_info["hypothesis_direct"] = hyp_direct
                    rec_info["cer_direct_nospace"] = round(cer_d_nospace["cer"], 4)
                    rec_info["cer_direct_spaced"] = round(cer_d_space["cer"], 4)
                    rec_info["distance_direct_nospace"] = cer_d_nospace["distance"]
                    rec_info["infer_ms_direct"] = round(res_direct.segments[0]["infer_ms"], 1) if res_direct.segments else 0.0

                    pairs_direct_nospace.append({"ref": ref_no, "hyp": hyp_no})
                    pairs_direct_spaced.append({"ref": ref_text, "hyp": hyp_direct})
                    direct_success_count += 1
                except Exception as exc:
                    rec_info["status_direct"] = "ERROR"
                    rec_info["error_direct"] = str(exc)
                    direct_error_count += 1

            # 2. VAD + STT
            if mode in ["all", "vad"]:
                try:
                    run_id_vad = f"cer_vad_{item_id}_{run_uuid}"
                    res_vad = pipeline.run_wav_vad(wav_path, run_id=run_id_vad)
                    hyp_vad = " ".join([s["text"] for s in res_vad.segments])
                    cer_v_space = compute_cer(ref_text, hyp_vad, remove_punct=True)
                    ref_no = normalize_text(ref_text, remove_punct=True).replace(" ", "")
                    hyp_no = normalize_text(hyp_vad, remove_punct=True).replace(" ", "")
                    cer_v_nospace = compute_cer(ref_no, hyp_no, remove_punct=False)

                    rec_info["status_vad"] = "SUCCESS"
                    rec_info["hypothesis_vad"] = hyp_vad
                    rec_info["vad_segment_count"] = len(res_vad.segments)
                    rec_info["cer_vad_nospace"] = round(cer_v_nospace["cer"], 4)
                    rec_info["cer_vad_spaced"] = round(cer_v_space["cer"], 4)
                    rec_info["distance_vad_nospace"] = cer_v_nospace["distance"]

                    pairs_vad_nospace.append({"ref": ref_no, "hyp": hyp_no})
                    pairs_vad_spaced.append({"ref": ref_text, "hyp": hyp_vad})
                    vad_success_count += 1
                except Exception as exc:
                    rec_info["status_vad"] = "ERROR"
                    rec_info["error_vad"] = str(exc)
                    vad_error_count += 1

            # Determine composite item status
            attempted_modes = []
            if mode in ["all", "direct"]:
                attempted_modes.append(rec_info["status_direct"])
            if mode in ["all", "vad"]:
                attempted_modes.append(rec_info["status_vad"])

            if all(s == "SUCCESS" for s in attempted_modes):
                rec_info["status"] = "SUCCESS"
            elif any(s == "SUCCESS" for s in attempted_modes):
                rec_info["status"] = "PARTIAL"
            else:
                rec_info["status"] = "ERROR"

            # Difference between VAD CER and Direct CER
            if rec_info.get("status_direct") == "SUCCESS" and rec_info.get("status_vad") == "SUCCESS":
                rec_info["boundary_cer_diff"] = round(rec_info["cer_vad_nospace"] - rec_info["cer_direct_nospace"], 4)

            item_results.append(rec_info)

    finally:
        if pipeline is not None:
            pipeline.close()

    # Aggregate Corpus CER Calculation
    aggregate_metrics: Dict[str, Any] = {
        "mode": mode,
        "direct_evaluated_count": direct_success_count,
        "vad_evaluated_count": vad_success_count,
    }

    if pairs_direct_nospace:
        agg_d_no = compute_corpus_cer(pairs_direct_nospace, remove_punct=False, ignore_space=False)
        agg_d_sp = compute_corpus_cer(pairs_direct_spaced, remove_punct=True, ignore_space=False)
        aggregate_metrics["direct_corpus_cer_nospace"] = round(agg_d_no["corpus_cer"], 4)
        aggregate_metrics["direct_corpus_cer_spaced"] = round(agg_d_sp["corpus_cer"], 4)
        aggregate_metrics["direct_raw_cer_nospace"] = agg_d_no["corpus_cer"]
    else:
        aggregate_metrics["direct_corpus_cer_nospace"] = None
        aggregate_metrics["direct_corpus_cer_spaced"] = None
        aggregate_metrics["direct_raw_cer_nospace"] = None

    if pairs_vad_nospace:
        agg_v_no = compute_corpus_cer(pairs_vad_nospace, remove_punct=False, ignore_space=False)
        agg_v_sp = compute_corpus_cer(pairs_vad_spaced, remove_punct=True, ignore_space=False)
        aggregate_metrics["vad_corpus_cer_nospace"] = round(agg_v_no["corpus_cer"], 4)
        aggregate_metrics["vad_corpus_cer_spaced"] = round(agg_v_sp["corpus_cer"], 4)
        aggregate_metrics["vad_raw_cer_nospace"] = agg_v_no["corpus_cer"]
    else:
        aggregate_metrics["vad_corpus_cer_nospace"] = None
        aggregate_metrics["vad_corpus_cer_spaced"] = None
        aggregate_metrics["vad_raw_cer_nospace"] = None

    # Primary corpus CER assignment
    if mode in ["all", "vad"]:
        primary_raw_cer = aggregate_metrics.get("vad_raw_cer_nospace")
        aggregate_metrics["primary_corpus_cer"] = aggregate_metrics.get("vad_corpus_cer_nospace")
    else:  # mode == "direct"
        primary_raw_cer = aggregate_metrics.get("direct_raw_cer_nospace")
        aggregate_metrics["primary_corpus_cer"] = aggregate_metrics.get("direct_corpus_cer_nospace")

    # Category breakdown by mode
    categories = ["general", "numbers_units", "technical_terms", "fast_boundary"]
    cat_breakdown: Dict[str, Any] = {}
    for cat in categories:
        cat_items = [it for it in item_results if it.get("category") == cat]
        d_items = [it for it in cat_items if it.get("status_direct") == "SUCCESS"]
        v_items = [it for it in cat_items if it.get("status_vad") == "SUCCESS"]

        cat_data: Dict[str, Any] = {
            "total_items": len(cat_items),
        }
        if d_items:
            d_pairs = [{"ref": normalize_text(it["reference"], remove_punct=True).replace(" ", ""),
                        "hyp": normalize_text(it["hypothesis_direct"], remove_punct=True).replace(" ", "")}
                       for it in d_items]
            d_cer = compute_corpus_cer(d_pairs, remove_punct=False, ignore_space=False)
            cat_data["direct"] = {
                "count": len(d_items),
                "corpus_cer_nospace": round(d_cer["corpus_cer"], 4),
                "total_ref_chars": d_cer["total_ref_len"],
                "total_errors": d_cer["total_distance"]
            }
        else:
            cat_data["direct"] = None

        if v_items:
            v_pairs = [{"ref": normalize_text(it["reference"], remove_punct=True).replace(" ", ""),
                        "hyp": normalize_text(it["hypothesis_vad"], remove_punct=True).replace(" ", "")}
                       for it in v_items]
            v_cer = compute_corpus_cer(v_pairs, remove_punct=False, ignore_space=False)
            cat_data["vad"] = {
                "count": len(v_items),
                "corpus_cer_nospace": round(v_cer["corpus_cer"], 4),
                "total_ref_chars": v_cer["total_ref_len"],
                "total_errors": v_cer["total_distance"]
            }
        else:
            cat_data["vad"] = None

        cat_breakdown[cat] = cat_data

    aggregate_metrics["category_breakdown"] = cat_breakdown

    # Overall Judgment Status Determination
    fully_executed_count = sum(1 for it in item_results if it.get("status") == "SUCCESS")
    any_error_count = sum(1 for it in item_results if it.get("status") == "ERROR")
    partial_item_count = sum(1 for it in item_results if it.get("status") == "PARTIAL")

    # 1. No audio files found on disk
    if found_audio_count == 0:
        structured_status = "NOT_RUN"
        overall_status = f"NOT_RUN (0/{len(dataset)} recorded files found in {audio_dir})"

    # 2. Files exist, but all attempted executions failed
    elif (direct_success_count == 0 and vad_success_count == 0) and (direct_error_count > 0 or vad_error_count > 0):
        structured_status = "ERROR"
        overall_status = f"ERROR (All {found_audio_count} found audio files failed with execution errors)"

    # 3. Custom manifest does not meet the standard 30-sentence requirement
    elif not covers_standard_30:
        structured_status = "PARTIAL"
        overall_status = f"PARTIAL ({fully_executed_count}/{len(dataset)} items executed; standard 30-sentence P01~P30 coverage incomplete ({covered_std_count}/30))"

    # 4. Standard 30 items present, but some are missing or failed
    elif missing_audio_count > 0 or any_error_count > 0 or partial_item_count > 0:
        structured_status = "PARTIAL"
        overall_status = f"PARTIAL ({fully_executed_count}/30 executed successfully, {missing_audio_count} missing, {any_error_count} errors)"

    # 5. Full 30 items executed successfully without errors
    else:
        if mode == "all":
            if primary_raw_cer is not None and primary_raw_cer <= 0.15:
                structured_status = "PASS"
                overall_status = f"PASS (Primary CER: {primary_raw_cer*100:.2f}% <= 15.0%)"
            else:
                structured_status = "FAIL"
                val_s = f"{primary_raw_cer*100:.2f}%" if primary_raw_cer is not None else "N/A"
                overall_status = f"FAIL (Primary CER: {val_s} > 15.0%)"
        elif mode == "direct":
            if primary_raw_cer is not None and primary_raw_cer <= 0.15:
                structured_status = "PARTIAL"
                overall_status = f"PARTIAL (Direct STT: {primary_raw_cer*100:.2f}% <= 15.0%; Task 01 VAD+STT gate NOT_RUN)"
            else:
                structured_status = "FAIL"
                val_s = f"{primary_raw_cer*100:.2f}%" if primary_raw_cer is not None else "N/A"
                overall_status = f"FAIL (Direct CER: {val_s} > 15.0%; Task 01 VAD+STT gate NOT_RUN)"
        elif mode == "vad":
            if primary_raw_cer is not None and primary_raw_cer <= 0.15:
                structured_status = "PASS"
                overall_status = f"PASS (Primary CER: {primary_raw_cer*100:.2f}% <= 15.0%)"
            else:
                structured_status = "FAIL"
                val_s = f"{primary_raw_cer*100:.2f}%" if primary_raw_cer is not None else "N/A"
                overall_status = f"FAIL (Primary CER: {val_s} > 15.0%)"
        else:
            structured_status = "UNKNOWN"
            overall_status = f"UNKNOWN (Unknown mode: {mode})"

    report = {
        "benchmark": "Korean Presentation 30-Sentence CER Benchmark",
        "execution_id": exec_id,
        "git_revision": git_info["revision"],
        "git_dirty": git_info["is_dirty"],
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audio_directory": audio_dir,
        "manifest_path": manifest_path,
        "manifest_metadata": manifest_meta,
        "total_dataset_items": len(dataset),
        "standard_30_covered": covers_standard_30,
        "standard_30_covered_count": covered_std_count,
        "executed_count": fully_executed_count,
        "not_run_count": missing_audio_count,
        "error_count": any_error_count,
        "partial_item_count": partial_item_count,
        "overall_status": overall_status,
        "structured_status": structured_status,
        "primary_metric": {
            "name": "vad_corpus_cer_nospace" if mode in ["all", "vad"] else "direct_corpus_cer_nospace",
            "description": "Sum of Levenshtein edit distances / Sum of reference characters with punctuation and spaces removed",
            "target_threshold": 0.15,
            "measured_value": aggregate_metrics.get("primary_corpus_cer"),
            "measured_value_raw": primary_raw_cer
        },
        "boundary_analysis_note": (
            "boundary_cer_diff reflects the comparative difference between Direct STT and VAD+STT pipelines "
            "(segment boundary context, pause cuts). It is not an isolated causal proof of speech truncation without acoustic phoneme alignment."
        ),
        "aggregate_metrics": aggregate_metrics,
        "items": item_results
    }

    atomic_write_json(output_json, report)

    print("-----------------------------------------------------------------")
    print(f" Overall Status:    {overall_status}")
    print(f" Structured Status: {structured_status}")
    print(f" Executed: {fully_executed_count} | Missing/NOT_RUN: {missing_audio_count} | Errors: {any_error_count}")
    if aggregate_metrics:
        if aggregate_metrics.get("vad_corpus_cer_nospace") is not None:
            print(f" VAD+STT Non-space CER: {aggregate_metrics['vad_corpus_cer_nospace']*100:.2f}% (Target: <= 15.0%)")
        if aggregate_metrics.get("direct_corpus_cer_nospace") is not None:
            print(f" Direct STT Non-space CER: {aggregate_metrics['direct_corpus_cer_nospace']*100:.2f}%")
    print(f" Report saved to:   {output_json}")
    print("=================================================================")
    return report


def main():
    parser = argparse.ArgumentParser(description="Korean Presentation 30-Sentence CER Evaluation Tool")
    parser.add_argument("--audio-dir", type=str, default="audio/eval_30",
                        help="Directory containing P01.wav ~ P30.wav recordings")
    parser.add_argument("--manifest-json", type=str, default=None,
                        help="Optional path to human-verified reference manifest JSON")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Path to output evaluation summary JSON (defaults to isolated timestamped file)")
    parser.add_argument("--mode", type=str, choices=["all", "direct", "vad"], default="all",
                        help="Evaluation mode: 'all' (compare direct vs vad), 'direct', or 'vad'")
    args = parser.parse_args()

    try:
        report = evaluate_dataset(
            audio_dir=args.audio_dir,
            manifest_path=args.manifest_json,
            output_json=args.output_json,
            mode=args.mode
        )
        status = report.get("structured_status", "UNKNOWN")
        if status == "PASS":
            sys.exit(0)
        elif status in ["NOT_RUN", "PARTIAL"]:
            sys.exit(2)
        else:  # FAIL, ERROR, UNKNOWN
            sys.exit(1)
    except Exception as exc:
        print(f"Fatal error during CER evaluation: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
