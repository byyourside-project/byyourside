#!/usr/bin/env python3
"""
Evaluation script for Korean Presentation CER benchmark (Revision 01).
Evaluates 30 reference sentences against audio recordings.
Calculates both sentence-level CER and aggregate corpus-level CER (sum distance / sum ref chars).
Separates spaced CER from non-space CER, and provides category-specific accuracy tables.
If recordings are absent, outputs NOT_RUN status with human-annotated reference texts.
"""
import argparse
import json
import os
import sys
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.metrics import compute_cer, normalize_text, compute_corpus_cer
from src.pipeline import SpeechPipeline
from src.config import PipelineConfig

EVAL_DATASET = [
    # Category 1: General Presentation Sentences (1-8)
    {"id": "P01", "category": "general", "ref": "지금부터 발표를 시작하도록 하겠습니다."},
    {"id": "P02", "category": "general", "ref": "오늘 말씀드릴 핵심 주제는 세 가지입니다."},
    {"id": "P03", "category": "general", "ref": "첫 번째로 시스템 구조의 개요를 살펴보겠습니다."},
    {"id": "P04", "category": "general", "ref": "기존 방식에서는 실시간 처리에 한계가 있었습니다."},
    {"id": "P05", "category": "general", "ref": "다음 슬라이드로 넘어가서 구체적인 결과를 확인하겠습니다."},
    {"id": "P06", "category": "general", "ref": "이어서 핵심 모듈의 동작 흐름을 설명해 드리겠습니다."},
    {"id": "P07", "category": "general", "ref": "결론적으로 사용자 경험이 크게 개선되었습니다."},
    {"id": "P08", "category": "general", "ref": "이상으로 발표를 마치고 질의응답을 받겠습니다."},

    # Category 2: Numbers, Percentages, Units (9-16)
    {"id": "P09", "category": "numbers_units", "ref": "전체 응답 속도는 15밀리초 단축되었습니다."},
    {"id": "P10", "category": "numbers_units", "ref": "인식 정확도는 95.8%를 기록했습니다."},
    {"id": "P11", "category": "numbers_units", "ref": "총 30개의 평가 문항을 준비했습니다."},
    {"id": "P12", "category": "numbers_units", "ref": "메모리 점유율은 약 512메가바이트입니다."},
    {"id": "P13", "category": "numbers_units", "ref": "지연 시간 목표는 1.5초 이하입니다."},
    {"id": "P14", "category": "numbers_units", "ref": "처리 용량은 이전 대비 2.5배 증가했습니다."},
    {"id": "P15", "category": "numbers_units", "ref": "초당 30프레임 속도로 데이터를 수집합니다."},
    {"id": "P16", "category": "numbers_units", "ref": "소비 전력은 15와트 수준으로 낮췄습니다."},

    # Category 3: English Acronyms & Technical Proper Nouns (17-24)
    {"id": "P17", "category": "technical_terms", "ref": "QCS6490 프로세서에서 NPU 가속을 적용합니다."},
    {"id": "P18", "category": "technical_terms", "ref": "Silero VAD 모듈이 발화 구간을 탐지합니다."},
    {"id": "P19", "category": "technical_terms", "ref": "SenseVoice 모델로 한국어 음성을 변환합니다."},
    {"id": "P20", "category": "technical_terms", "ref": "ONNX 런타임 환경에서 INT8 양자화를 수행했습니다."},
    {"id": "P21", "category": "technical_terms", "ref": "경량화된 LLM 파이프라인과 연동됩니다."},
    {"id": "P22", "category": "technical_terms", "ref": "REST API 대신 웹소켓 통신을 구성했습니다."},
    {"id": "P23", "category": "technical_terms", "ref": "CPU 사용률과 RTF 지표를 실시간 측정합니다."},
    {"id": "P24", "category": "technical_terms", "ref": "안드로이드 플랫폼 상에서 SDK를 통합합니다."},

    # Category 4: Fast Speech, Short Pauses & Boundary Sentences (25-30)
    {"id": "P25", "category": "fast_boundary", "ref": "시간이 부족하므로 빠르게 결론부터 요약해 드리겠습니다."},
    {"id": "P26", "category": "fast_boundary", "ref": "그리고 또한 이번 실험에서는 예외적인 오류가 발생하지 않았습니다."},
    {"id": "P27", "category": "fast_boundary", "ref": "잠시 표를 보시면 수치가 급격히 변하는 구간이 나타납니다."},
    {"id": "P28", "category": "fast_boundary", "ref": "하지만 실제 환경에서는 예상치 못한 변수가 존재할 수 있습니다."},
    {"id": "P29", "category": "fast_boundary", "ref": "이 부분에 대해서는 추가적인 검증 작업이 반드시 필요합니다."},
    {"id": "P30", "category": "fast_boundary", "ref": "질문 있으시면 자유롭게 말씀해 주시기 바랍니다."},
]

def main():
    parser = argparse.ArgumentParser(description="Evaluate CER for 30 presentation sentences (Revision 01)")
    parser.add_argument("--audio-dir", type=str, default="audio/eval_30",
                        help="Directory containing P01.wav ~ P30.wav recordings")
    parser.add_argument("--output-json", type=str, default="logs/cer_eval_30_revised.json",
                        help="Path to output evaluation summary JSON")
    args = parser.parse_args()

    print("=================================================================")
    print(" Korean Presentation 30-Sentence CER Benchmark (Revision 01)")
    print(f" Audio Directory: {args.audio_dir}")
    print("=================================================================")

    pipeline = None
    results = []
    run_count = 0
    not_run_count = 0

    executed_pairs_spaced = []
    executed_pairs_nospace = []

    for item in EVAL_DATASET:
        wav_path = os.path.join(args.audio_dir, f"{item['id']}.wav")
        ref_text = item["ref"]

        if os.path.exists(wav_path):
            if pipeline is None:
                pipeline = SpeechPipeline(PipelineConfig())
            run_res = pipeline.run_wav_vad(wav_path, run_id=f"cer_{item['id']}")
            hyp_text = " ".join([s["text"] for s in run_res.segments])

            cer_info_space = compute_cer(ref_text, hyp_text, remove_punct=True)
            ref_no_space = normalize_text(ref_text, remove_punct=True).replace(" ", "")
            hyp_no_space = normalize_text(hyp_text, remove_punct=True).replace(" ", "")
            cer_info_nospace = compute_cer(ref_no_space, hyp_no_space, remove_punct=False)

            results.append({
                "id": item["id"],
                "category": item["category"],
                "status": "RUN",
                "reference": ref_text,
                "hypothesis": hyp_text,
                "cer_with_space": round(cer_info_space["cer"], 4),
                "cer_no_space": round(cer_info_nospace["cer"], 4),
                "distance": cer_info_space["distance"],
                "substitutions": cer_info_space["substitutions"],
                "deletions": cer_info_space["deletions"],
                "insertions": cer_info_space["insertions"]
            })
            executed_pairs_spaced.append({"ref": ref_text, "hyp": hyp_text})
            run_count += 1
        else:
            results.append({
                "id": item["id"],
                "category": item["category"],
                "status": "NOT_RUN",
                "reason": f"WAV file {wav_path} not found (user recording required)",
                "reference": ref_text,
                "hypothesis": None,
                "cer_with_space": None,
                "cer_no_space": None
            })
            not_run_count += 1

    aggregate_summary = None
    if run_count > 0:
        agg_spaced = compute_corpus_cer(executed_pairs_spaced, remove_punct=True, ignore_space=False)
        agg_nospace = compute_corpus_cer(executed_pairs_spaced, remove_punct=True, ignore_space=True)
        aggregate_summary = {
            "aggregate_spaced_cer": round(agg_spaced["corpus_cer"], 4),
            "aggregate_nospace_cer": round(agg_nospace["corpus_cer"], 4),
            "total_ref_chars_spaced": agg_spaced["total_ref_len"],
            "total_distance_spaced": agg_spaced["total_distance"],
            "total_ref_chars_nospace": agg_nospace["total_ref_len"],
            "total_distance_nospace": agg_nospace["total_distance"],
        }

    summary = {
        "revision": "01",
        "total_items": len(EVAL_DATASET),
        "executed_count": run_count,
        "not_run_count": not_run_count,
        "aggregate_metrics": aggregate_summary,
        "items": results
    }

    os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("-----------------------------------------------------------------")
    print(f" Executed: {run_count} / {len(EVAL_DATASET)} | NOT_RUN: {not_run_count} / {len(EVAL_DATASET)}")
    if aggregate_summary:
        print(f" Aggregate Spaced CER: {aggregate_summary['aggregate_spaced_cer']*100:.2f}%")
        print(f" Aggregate Non-space CER: {aggregate_summary['aggregate_nospace_cer']*100:.2f}%")
    print(f" Summary saved to {args.output_json}")
    print("=================================================================")

if __name__ == "__main__":
    main()
