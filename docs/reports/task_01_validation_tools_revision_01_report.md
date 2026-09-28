# Task 01 실사용 평가 도구 Revision 01 결과 보고서

- **작성일**: 2026-09-28
- **대상 커밋**: `d88573e` 이후 수정 작업
- **검수 문서**: `docs/reports/task_01_validation_tools_pm_review.md`
- **지침 문서**: `docs/pm/task_01_validation_tools_revision_01.md`
- **핵심 목표**: STT 및 VAD 핵심 파이프라인 구현(Revision 06 승인 상태)을 유지하면서, 평가 실행기 판정 로직, 지표 집계 정합성, 실패 증거 보존, 사용자 녹음 안내서를 보완하고 작은 회귀 테스트로 검증.

---

## 1. PM 지적 사항(F1 ~ F4) 조치 결과 요약

| 지적 사항 | 분류 | 기존 문제점 | Revision 01 조치 내용 | 검증 결과 |
| :--- | :---: | :--- | :--- | :---: |
| **F1** [P1] | 판정 기준 | 커스텀 매니페스트 1건 전사 성공만으로 전체 PASS 처리 및 미존재 매니페스트 묵인 | P01~P30 30개 고유 ID 전수 커버리지 게이트 고정 (`covers_standard_30`). 1개/29개/중복 ID는 PASS 불가(`PARTIAL`/`ERROR`). `complete` 및 `merge` 모드 지원, 미존재 매니페스트 지정 시 즉시 `ERROR` 보고 및 SHA-256 해시 기록 | **해결 완료** (회귀 테스트 4건 통과) |
| **F2** [P2] | 상태/지표 | 추론 Timeout 시 `NOT_RUN` 출력, Direct 전용 모드에서 VAD 미측정값을 100%로 간주하여 FAIL 처리 | 파일 부재(`NOT_RUN`), 런타임 추론 예외(`ERROR`), 성능 목표 미달(`FAIL`) 명확 분리. Direct 모드 시 VAD CER는 `None`(null) 유지 및 `PARTIAL(VAD gate NOT_RUN)` 분리. 모드별 모집단(`direct_evaluated_count`, `vad_evaluated_count`)과 카테고리 집계 분리 | **해결 완료** (회귀 테스트 4건 통과) |
| **F3** [P1] | 큐/발화 판정 | 큐 계측 필드 누락 시 0 drift 간주, 60초 성긴 발화에 10분 연속 발표 PASS 판정 | `queue_wait_ms` 누락/비수치/NaN 발생 시 즉시 안정성 `FAIL` 처리. 첫값-끝값뿐 아니라 중간 서지(Spike > 300ms) 감지. 60초 발화는 최소 입력 확인으로만 제한하고, 자원 안정성과 발표 연속성(`PARTIAL`)을 엄격 분리. 끝 구간 부분 minute 포함 | **해결 완료** (회귀 테스트 5건 통과) |
| **F4** [P1] | 오프라인 안내 | 차단 규칙 없이 `sudo pfctl -d` 전역 필터 해제 명령 제시 | 시스템 방화벽을 변경/손상시킬 수 있는 `pfctl -d` 절차 전면 제거. 안전한 읽기 전용 네트워크 확인(`ifconfig -u`, `route get default`) 및 표준 인터페이스 해제/복구 절차 안내. 오프라인 실행 성공과 OS egress 차단 증명 명확히 구분 | **해결 완료** (문서 개정 완료) |
| **추가 보완** | 메타데이터 | 재현 메타데이터 및 매니페스트 예시 부재, `boundary_cer_diff` 단정적 표현 | 매니페스트 예시 JSON (`complete`/`merge`) 추가. Git 커밋 및 dirty 상태, 설정, 해시, 장치 정보 기록. `boundary_cer_diff` 해석 주의사항(비교 지표) 명시. 원자적 파일 쓰기(`atomic_write_json`) 도입 | **해결 완료** (코드 및 안내서 반영) |

---

## 2. 변경된 파일 목록 및 주요 변경 내역

### 2.1 [scripts/evaluate_cer.py](file:///Users/jwlee/study1/byyourside/scripts/evaluate_cer.py)
1. **표준 30문항 커버리지 강제**:
   - `STANDARD_IDS = {f"P{i:02d}" for i in range(1, 31)}`를 기준으로 검증하여, 30개 고유 ID가 모두 포함되고 오류 없이 전사되어야만 `PASS` 가능.
   - 1개, 29개 등 부분 데이터셋은 `PARTIAL`로 엄격 판정.
2. **매니페스트 관리 및 검증 (`load_manifest`)**:
   - `--manifest-json` 경로가 존재하지 않으면 조용히 기본값으로 대체하지 않고 구조화된 `ERROR` 보고서를 저장하고 종료.
   - 중복 ID 검출 시 즉시 `ERROR` 처리.
   - `mode: "complete"`(30개 전체 목록 필수) 및 `mode: "merge"`(수정 대상 문항만 지정하고 나머지는 표준 30문항 정답 유지) 지원.
   - 매니페스트 파일 SHA-256 및 정답 텍스트 전체 정렬 결합 해시(`reference_text_sha256`) 저장.
3. **상태 및 지표 정합성 확보**:
   - 녹음 파일 부재(`NOT_RUN`), 런타임 추론 예외(`ERROR`), 목표 초과(`FAIL`), 정식 통과(`PASS`)를 분리.
   - `mode: "direct"` 실행 시 `vad_corpus_cer_nospace`를 `None`(null)으로 처리하여 허위 100% FAIL 문제 제거. 상태를 `PARTIAL (Direct STT: X.XX%; Task 01 VAD+STT gate NOT_RUN)`로 분리.
   - Direct 전사 성공 후 VAD 전사 실패 시 아이템 상태를 `PARTIAL`로 기록하고 Direct 모집단에 정상 반영.
   - 카테고리별 집계에서 Direct와 VAD의 모집단(`count`, `total_ref_chars`)을 각각 분리 집계.
4. **재현 메타데이터 및 안전 저장**:
   - Git short revision 및 git dirty 상태 자동 감지.
   - `atomic_write_json`을 적용하여 파일 쓰기 도중 프로세스 중단 시 JSON 파일 깨짐 방지.
5. **결정론적 CLI Exit Code**:
   - `0`: 전체 검증 PASS
   - `1`: FAIL 또는 ERROR
   - `2`: NOT_RUN 또는 PARTIAL

### 2.2 [tests/test_mic_10min.py](file:///Users/jwlee/study1/byyourside/tests/test_mic_10min.py)
1. **큐 계측 무결성 검증**:
   - 모든 세그먼트의 `queue_wait_ms` 존재 여부 및 수치 유효성(NaN, Inf 검사) 검증. 누락/비수치 발생 시 즉시 `FAIL (Queue metrics invalid)` 처리.
   - 첫 세그먼트와 마지막 세그먼트 간의 drift뿐만 아니라, 중간 구간 서지(Spike > 300ms) 발생 시 `RUNAWAY`로 판정.
2. **발화 및 큐 분포 분석 (`analyze_per_minute_speech`)**:
   - 전체 캡처 오디오 길이를 기반으로 올림(`math.ceil`) 처리하여 마지막 부분 구간(예: 605.3초의 11번째 분)까지 누락 없이 분할 분석.
   - 각 분(minute)별 발화 시간, 발화 비율, 세그먼트 수, 큐 대기 시간 통계(`min_ms`, `avg_ms`, `max_ms`, `p95_ms`) 보고.
3. **평가 게이트 및 판정 분리**:
   - `resource_and_streaming_stability`: 무손실 스트리밍, 큐 안정성, 메모리 누수 방지 (기술적 통과 여부 판정).
   - `latency_and_rtf_performance`: RTF p95 $\le$ 0.5, VAD 종료 기준 지연 p95 $\le$ 1.5s (기술적 통과 여부 판정).
   - `speech_input_presence`: 최소 발화 유효성 확인 (10분 중 5분 이상, 60초 이상 발화 감지). *최소 발화 만족은 입력 존재 확인용이며, 10분 연속 발표 완료의 보증이 아님을 명시.*
   - `continuous_presentation_coverage`: 발표 연속성 적합성은 사용자/PM 검토 대상으로 `PARTIAL` 유지.
   - `human_reference_speech_end_latency`: 사람 청취 기준 지연은 정답 주석 부재로 `PARTIAL` 유지.
   - 전체 10분 게이트 상태는 기술적 지표가 통과하더라도 발표 연속성 및 사람 지연이 미검수 상태이므로 `PARTIAL`로 유지하여 허위 PASS 방지.
4. **실패 증거 보존**:
   - 초기화, warm-up, `run_mic` 단계에서 예외(TimeoutError 등) 발생 시, `output_json`에 실패 단계(`failed_phase`), 예외 정보, 경과 시간을 포함한 구조화된 `ERROR` JSON을 원자적으로 기록한 후 예외 재발생.

### 2.3 [tests/test_validation_tools_rev1.py](file:///Users/jwlee/study1/byyourside/tests/test_validation_tools_rev1.py)
- 신규 판정 로직과 예외 처리 경로를 실제 마이크나 모델 가동 없이 Mock으로 정밀 검증하는 독립 단위 테스트 슈트 (14개 테스트 케이스).

### 2.4 [docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md)
1. **불완전/위험 네트워크 명령 제거**:
   - 기존의 `sudo pfctl -d` 명령 및 불완전 절차를 전면 제거.
   - 안전한 읽기 전용 네트워크 확인(`ifconfig -u`, `route get default`) 및 표준 Wi-Fi/이더넷/테더링 해제 및 복구 절차 제시.
   - 로컬 오프라인 실행 성공과 OS 커널 수준의 송신 시도 차단(egress blocking)의 개념적 차이를 명시.
2. **매니페스트 JSON 예시 및 작성 원칙 추가**:
   - `complete` 및 `merge` 모드 예시 JSON 제공.
   - 녹음 도중 실수(오독) 발생 시 재녹음 원칙 및 사전 매니페스트 수정 원칙 명시 (인식 후 사후 조작 엄격 금지).
3. **재현 및 운영 가이드 보완**:
   - 예상 준비 시간 (30문장 녹음: 15~25분, 10분 발표: 15분) 명시.
   - 마이크 입력 장치 설정, 음성 전후 0.5초 패딩(정적), 5초 초과 연속 발화 주의 안내.
   - 스테레오 입력의 산술 평균(`(L + R) / 2.0`) 다운믹스 방식 설명.
   - `boundary_cer_diff`는 비교 참고 지표이며 단독 음향 절단 증거가 아님을 명시.

---

## 3. 검증 결과 및 실행 로그

### 3.1 신규 판정 회귀 테스트 슈트 실행 (`tests/test_validation_tools_rev1.py`)
- **실행 명령**:
  ```bash
  .venv/bin/python -m unittest tests/test_validation_tools_rev1.py -v
  ```
- **실행 결과**:
  ```text
  test_cer_29_items_manifest_is_partial_not_pass ... ok
  test_cer_all_wavs_fail_reports_error_not_not_run ... ok
  test_cer_direct_mode_leaves_vad_cer_none_and_not_100_percent_fail ... ok
  test_cer_direct_success_and_vad_fail_reports_partial_item ... ok
  test_cer_duplicate_id_in_manifest_reports_error ... ok
  test_cer_merge_manifest_mode_succeeds_with_overrides ... ok
  test_cer_missing_explicit_manifest_reports_error_not_fallback ... ok
  test_cer_single_item_manifest_is_partial_not_pass ... ok
  test_mic_exception_preserves_json_report_and_closes_pipeline ... ok
  test_mic_intermediate_queue_surge_detected ... ok
  test_mic_missing_queue_wait_fails_stability ... ok
  test_mic_non_numeric_queue_wait_fails_stability ... ok
  test_mic_sparse_speech_is_not_full_continuous_presentation_pass ... ok
  test_mic_trailing_partial_minute_binning ... ok

  ----------------------------------------------------------------------
  Ran 14 tests in 0.387s

  OK
  ```
- **Exit Code**: `0`

### 3.2 PM 진단 스크립트 재실행 결과 비교 (`logs/pm_review_validation_20260928/probes.py`)
PM이 지적했던 6개 진단 시나리오에 대해 신규 로직을 적용하여 재실행한 결과:

```json
{
  "one_item_all": {
    "status": "PARTIAL (1/1 items executed; standard 30-sentence P01~P30 coverage incomplete (1/30))",
    "count": 1,
    "cer": {
      "mode": "all",
      "direct_evaluated_count": 1,
      "vad_evaluated_count": 1,
      "direct_corpus_cer_nospace": 0.0,
      "vad_corpus_cer_nospace": 0.0,
      "primary_corpus_cer": 0.0
    }
  },
  "one_item_direct": {
    "status": "PARTIAL (1/1 items executed; standard 30-sentence P01~P30 coverage incomplete (1/30))",
    "count": 1,
    "cer": {
      "mode": "direct",
      "direct_evaluated_count": 1,
      "vad_evaluated_count": 0,
      "direct_corpus_cer_nospace": 0.0,
      "vad_corpus_cer_nospace": null,
      "primary_corpus_cer": 0.0
    }
  },
  "all_errors": {
    "overall_status": "ERROR (All 1 found audio files failed with execution errors)",
    "executed_count": 0,
    "error_count": 1,
    "not_run_count": 0
  },
  "missing_explicit_manifest": {
    "dataset_count": 0,
    "status": "ERROR (Manifest error: Specified manifest file not found: /var/folders/.../missing_manifest.json)"
  },
  "sparse_speech_missing_queue": {
    "overall_status": "FAIL (Queue metrics invalid: Segment 0 missing 'queue_wait_ms')",
    "judgment": "FAIL (Queue metrics invalid: Segment 0 missing 'queue_wait_ms')",
    "total_speech_seconds": 60.0,
    "stability_metrics": {
      "queue_stability_status": "INVALID (Segment 0 missing 'queue_wait_ms')",
      "has_runaway_queue": true
    }
  },
  "mic_exception": {
    "type": "TimeoutError",
    "report_exists": true
  }
}
```

#### PM 진단 대비 변화 분석:
1. `one_item_all`: 기존 `PASS (0.00%)` $\to$ **`PARTIAL (1/30)`로 정상 변경**.
2. `one_item_direct`: 기존 `FAIL (100.00%)` $\to$ **`vad_corpus_cer_nospace: null`, `PARTIAL`로 정상 변경**.
3. `all_errors`: 기존 `NOT_RUN (0/1 recorded files found)` $\to$ **`ERROR (All 1 found audio files failed)`로 정상 분리**.
4. `missing_explicit_manifest`: 기존 30개 기본값 암묵 대체 $\to$ **`ERROR (Manifest error: ...)`로 명시적 차단**.
5. `sparse_speech_missing_queue`: 기존 `PASS (drift 0)` $\to$ **`FAIL (Queue metrics invalid)`로 정상 차단**.
6. `mic_exception`: 기존 `report_exists: false` $\to$ **`report_exists: true` (예외 발생 시에도 JSON 증거 보존)**.

### 3.3 CLI 도구 인터페이스 및 상태 코드 검증
1. **평가 오디오 미존재 시 CLI 실행 (`scripts/evaluate_cer.py`)**:
   - 명령: `.venv/bin/python scripts/evaluate_cer.py --audio-dir audio/eval_30`
   - 출력:
     ```text
     Overall Status:    NOT_RUN (0/30 recorded files found in audio/eval_30)
     Structured Status: NOT_RUN
     Executed: 0 | Missing/NOT_RUN: 30 | Errors: 0
     Report saved to:   logs/cer_eval_results_20260928_114151_89af909d.json
     ```
   - **Exit Code**: `2` (`NOT_RUN` 정상 반환)
2. **존재하지 않는 매니페스트 지정 시 CLI 실행**:
   - 명령: `.venv/bin/python scripts/evaluate_cer.py --manifest-json nonexistent.json`
   - 출력: `Error loading manifest: Specified manifest file not found: nonexistent.json`
   - **Exit Code**: `1` (`ERROR` 정상 반환)

---

## 4. 미실행 및 부분 실행 항목 현황 (NOT_RUN / PARTIAL 유지)

본 Revision 01 작업은 평가 하네스 및 안내서의 신뢰성 보완 작업이며, 실제 사용자의 오디오 녹음이 투입되기 전까지 아래 항목들은 지침에 따라 `NOT_RUN` 또는 `PARTIAL` 상태를 엄격히 유지합니다:

1. **사용자 30문장 발표 전사 CER 평가**:
   - 상태: **`NOT_RUN`**
   - 사유: `audio/eval_30/` 디렉터리에 사용자 실제 낭독 WAV 파일(`P01.wav` ~ `P30.wav`)이 부재함 (0/30).
2. **사용자 600초(10분) 연속 발표 마이크 실사용 안정성**:
   - 상태: **`NOT_RUN / PARTIAL`**
   - 사유: 파이프라인의 10분 마이크 무손실 스트리밍 기능 및 큐 안정성 판정 로직은 준비되었으나, 실제 사용자의 10분 라이브 발표 오디오 투입 전이므로 공식 게이트는 `PARTIAL` 상태 유지.
3. **사람 청취 기준 발화 종료 정답 라벨링 (Human Reference Annotation)**:
   - 상태: **`NOT_RUN / PARTIAL`**
   - 사유: 음향학적 정밀 발화 경계 사람 라벨링 데이터 미작성 상태.
4. **OS 커널 수준 완전 송신 차단 (OS Egress Blocking)**:
   - 상태: **`PARTIAL`**
   - 사유: 파이프라인 프로세스의 외부 통신 부재 및 소켓 차단 smoke test는 입증되었으나, 커널 방화벽 및 패킷 캡처 수준의 완전 차단 증거는 사용자의 물리적 오프라인 확인 시 검증 대상임.

---

## 5. 결론 및 향후 계획

- `docs/reports/task_01_validation_tools_pm_review.md`에서 제기된 P1/P2 결함 4건(F1~F4)과 추가 메타데이터 요구사항이 모두 완결되었습니다.
- 기존의 승인된 STT 엔진 및 VAD 파이프라인 구조는 100% 온전히 유지되었으며, 모델/파이프라인 재작성 없이 평가 실행기와 안내서의 정합성을 확립했습니다.
- **Task 02(Android 통합 및 최적화)는 시작하지 않았으며, PM 검수를 대기합니다.**
