#!/usr/bin/env python3
"""
Evaluation tool for Korean Presentation 30-Sentence CER Benchmark.
Supports:
  1. Reading custom or human-annotated references (--manifest-json or default P01~P30).
  2. Distinct evaluation for Direct STT vs VAD+STT (to isolate VAD boundary truncation loss).
  3. Execution-specific UUID/timestamp output isolation to prevent overwriting or appending.
  4. Explicit error and missing-file tracking (missing files marked as NOT_RUN and excluded from success count).
  5. SHA-256 file hashing, audio format validation, and category-specific CER breakdowns.
  6. Strict calculation of non-spaced CER (primary metric, target <= 15.0%) and spaced CER.
  7. Robust try/finally lifecycle management for child STT engines.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from typing import Dict, Any, List, Optional
import scipy.io.wavfile as wavfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.metrics import compute_cer, normalize_text, compute_corpus_cer
from src.pipeline import SpeechPipeline, get_git_revision
from src.config import PipelineConfig, SttConfig, VadConfig

# Canonical 30-Sentence Presentation Dataset
STANDARD_DATASET = [
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


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


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

    # 1. Load dataset (either from manifest or default standard dataset)
    dataset = []
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
            dataset = manifest_data.get("items", manifest_data)
        print(f"Loaded {len(dataset)} items from manifest: {manifest_path}")
    else:
        dataset = [dict(item) for item in STANDARD_DATASET]

    print("=================================================================")
    print(" Korean Presentation 30-Sentence CER Evaluation")
    print(f" Execution ID:    {exec_id}")
    print(f" Audio Directory: {audio_dir}")
    print(f" Evaluation Mode: {mode} (direct vs vad comparison)")
    print(f" Target Output:   {output_json}")
    print("=================================================================")

    pipeline = None
    item_results = []
    run_count = 0
    not_run_count = 0
    error_count = 0

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
            item_id = item["id"]
            category = item.get("category", "unclassified")
            ref_text = item["ref"]

            # Possible audio file locations (e.g. P01.wav, p01.wav)
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
                    "reason": f"WAV file not found in {audio_dir} (user recording required)",
                    "expected_paths": candidates[:2],
                    "reference": ref_text,
                    "hypothesis_direct": None,
                    "hypothesis_vad": None,
                    "cer_direct_nospace": None,
                    "cer_vad_nospace": None
                })
                not_run_count += 1
                continue

            # File exists: inspect audio metadata and compute hash
            sha256 = compute_file_sha256(wav_path)
            try:
                sr, audio_data = wavfile.read(wav_path)
                dur_sec = len(audio_data) / float(sr) if sr > 0 else 0.0
                channels = 1 if audio_data.ndim == 1 else audio_data.shape[1]
            except Exception as e:
                sr, dur_sec, channels = 0, 0.0, 0

            rec_info: Dict[str, Any] = {
                "id": item_id,
                "category": category,
                "status": "RUN",
                "wav_path": wav_path,
                "sha256": sha256,
                "sample_rate": sr,
                "duration_seconds": round(dur_sec, 3),
                "channels": channels,
                "reference": ref_text
            }

            try:
                # 1. Direct STT (Acoustic baseline without VAD boundary cut)
                if mode in ["all", "direct"]:
                    run_id_direct = f"cer_direct_{item_id}_{run_uuid}"
                    res_direct = pipeline.run_wav_direct_stt(wav_path, run_id=run_id_direct)
                    hyp_direct = res_direct.segments[0]["text"] if res_direct.segments else ""
                    cer_d_space = compute_cer(ref_text, hyp_direct, remove_punct=True)
                    ref_no = normalize_text(ref_text, remove_punct=True).replace(" ", "")
                    hyp_no = normalize_text(hyp_direct, remove_punct=True).replace(" ", "")
                    cer_d_nospace = compute_cer(ref_no, hyp_no, remove_punct=False)

                    rec_info["hypothesis_direct"] = hyp_direct
                    rec_info["cer_direct_nospace"] = round(cer_d_nospace["cer"], 4)
                    rec_info["cer_direct_spaced"] = round(cer_d_space["cer"], 4)
                    rec_info["distance_direct_nospace"] = cer_d_nospace["distance"]
                    rec_info["infer_ms_direct"] = round(res_direct.segments[0]["infer_ms"], 1)

                    pairs_direct_nospace.append({"ref": ref_no, "hyp": hyp_no})
                    pairs_direct_spaced.append({"ref": ref_text, "hyp": hyp_direct})

                # 2. VAD + STT (Pipeline with speech endpointing)
                if mode in ["all", "vad"]:
                    run_id_vad = f"cer_vad_{item_id}_{run_uuid}"
                    res_vad = pipeline.run_wav_vad(wav_path, run_id=run_id_vad)
                    hyp_vad = " ".join([s["text"] for s in res_vad.segments])
                    cer_v_space = compute_cer(ref_text, hyp_vad, remove_punct=True)
                    ref_no = normalize_text(ref_text, remove_punct=True).replace(" ", "")
                    hyp_no = normalize_text(hyp_vad, remove_punct=True).replace(" ", "")
                    cer_v_nospace = compute_cer(ref_no, hyp_no, remove_punct=False)

                    rec_info["hypothesis_vad"] = hyp_vad
                    rec_info["vad_segment_count"] = len(res_vad.segments)
                    rec_info["cer_vad_nospace"] = round(cer_v_nospace["cer"], 4)
                    rec_info["cer_vad_spaced"] = round(cer_v_space["cer"], 4)
                    rec_info["distance_vad_nospace"] = cer_v_nospace["distance"]

                    pairs_vad_nospace.append({"ref": ref_no, "hyp": hyp_no})
                    pairs_vad_spaced.append({"ref": ref_text, "hyp": hyp_vad})

                # Boundary impact: difference between VAD CER and Direct CER
                if "cer_vad_nospace" in rec_info and "cer_direct_nospace" in rec_info:
                    rec_info["boundary_cer_diff"] = round(rec_info["cer_vad_nospace"] - rec_info["cer_direct_nospace"], 4)

                run_count += 1
            except Exception as exc:
                rec_info["status"] = "ERROR"
                rec_info["error"] = str(exc)
                error_count += 1

            item_results.append(rec_info)

    finally:
        if pipeline is not None:
            pipeline.close()

    # Aggregate Corpus CER Calculation (sum of distances / sum of reference characters)
    aggregate_metrics: Dict[str, Any] = {}
    if run_count > 0:
        if pairs_direct_nospace:
            agg_d_no = compute_corpus_cer(pairs_direct_nospace, remove_punct=False, ignore_space=False)
            agg_d_sp = compute_corpus_cer(pairs_direct_spaced, remove_punct=True, ignore_space=False)
            aggregate_metrics["direct_corpus_cer_nospace"] = round(agg_d_no["corpus_cer"], 4)
            aggregate_metrics["direct_corpus_cer_spaced"] = round(agg_d_sp["corpus_cer"], 4)

        if pairs_vad_nospace:
            agg_v_no = compute_corpus_cer(pairs_vad_nospace, remove_punct=False, ignore_space=False)
            agg_v_sp = compute_corpus_cer(pairs_vad_spaced, remove_punct=True, ignore_space=False)
            aggregate_metrics["vad_corpus_cer_nospace"] = round(agg_v_no["corpus_cer"], 4)
            aggregate_metrics["vad_corpus_cer_spaced"] = round(agg_v_sp["corpus_cer"], 4)
            aggregate_metrics["primary_corpus_cer"] = round(agg_v_no["corpus_cer"], 4)

        # Per-Category Breakdown (for VAD or Direct)
        categories = ["general", "numbers_units", "technical_terms", "fast_boundary"]
        cat_breakdown = {}
        for cat in categories:
            cat_items = [it for it in item_results if it.get("category") == cat and it.get("status") == "RUN"]
            if cat_items:
                cat_pairs = [{"ref": normalize_text(it["reference"], remove_punct=True).replace(" ", ""),
                              "hyp": normalize_text(it.get("hypothesis_vad", it.get("hypothesis_direct", "")), remove_punct=True).replace(" ", "")}
                             for it in cat_items]
                cat_cer = compute_corpus_cer(cat_pairs, remove_punct=False, ignore_space=False)
                cat_breakdown[cat] = {
                    "count": len(cat_items),
                    "corpus_cer_nospace": round(cat_cer["corpus_cer"], 4),
                    "total_ref_chars": cat_cer["total_ref_len"],
                    "total_errors": cat_cer["total_distance"]
                }
        aggregate_metrics["category_breakdown"] = cat_breakdown

    # Overall Judgment Status
    if run_count == 0:
        overall_status = f"NOT_RUN (0/{len(dataset)} recorded files found in {audio_dir})"
    elif run_count < len(dataset):
        overall_status = f"PARTIAL ({run_count}/{len(dataset)} files executed, {not_run_count} missing)"
    else:
        primary_cer = aggregate_metrics.get("primary_corpus_cer", 1.0)
        if primary_cer <= 0.15:
            overall_status = f"PASS (Primary CER: {primary_cer*100:.2f}% <= 15.0%)"
        else:
            overall_status = f"FAIL (Primary CER: {primary_cer*100:.2f}% > 15.0%)"

    report = {
        "benchmark": "Korean Presentation 30-Sentence CER Benchmark",
        "execution_id": exec_id,
        "git_revision": get_git_revision(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audio_directory": audio_dir,
        "manifest_path": manifest_path,
        "total_dataset_items": len(dataset),
        "executed_count": run_count,
        "not_run_count": not_run_count,
        "error_count": error_count,
        "overall_status": overall_status,
        "primary_metric": {
            "name": "vad_corpus_cer_nospace",
            "description": "Sum of Levenshtein edit distances / Sum of reference characters with punctuation and spaces removed",
            "target_threshold": 0.15,
            "measured_value": aggregate_metrics.get("primary_corpus_cer")
        },
        "aggregate_metrics": aggregate_metrics,
        "items": item_results
    }

    os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("-----------------------------------------------------------------")
    print(f" Overall Status: {overall_status}")
    print(f" Executed: {run_count} | Missing/NOT_RUN: {not_run_count} | Errors: {error_count}")
    if aggregate_metrics:
        if "vad_corpus_cer_nospace" in aggregate_metrics:
            print(f" VAD+STT Non-space CER (Primary): {aggregate_metrics['vad_corpus_cer_nospace']*100:.2f}% (Target: <= 15.0%)")
        if "direct_corpus_cer_nospace" in aggregate_metrics:
            print(f" Direct STT Non-space CER:        {aggregate_metrics['direct_corpus_cer_nospace']*100:.2f}%")
    print(f" Report saved to: {output_json}")
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

    evaluate_dataset(
        audio_dir=args.audio_dir,
        manifest_path=args.manifest_json,
        output_json=args.output_json,
        mode=args.mode
    )


if __name__ == "__main__":
    main()
