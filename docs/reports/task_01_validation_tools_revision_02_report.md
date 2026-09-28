# Task 01 실사용 평가 도구 Revision 02 결과 보고서

- **작성일**: 2026-09-28
- **대상 커밋**: `fe48e44` 이후 수정 작업
- **검수 문서**: `docs/reports/task_01_validation_tools_revision_01_pm_review.md`
- **지침 문서**: `docs/pm/task_01_validation_tools_revision_02.md`
- **핵심 목표**: STT 및 VAD 핵심 파이프라인 구현(Revision 06 승인 상태)을 유지하면서, 정답 유효성 사전 검증(R1), CER 파이프라인 생명주기 실패 증거 보존(R2), 기존 출력 파일 보호 정책(R3), 재현 메타데이터 및 보고서 정합성을 확립.

---

## 1. PM 지적 사항(R1 ~ R3) 및 보고서 정합성 조치 결과 요약

| 지적 사항 | 분류 | 기존 문제점 | Revision 02 조치 내용 | 검증 결과 |
| :--- | :---: | :--- | :--- | :---: |
| **R1** [P1] | 정답 유효성 | 공백 문자열(`"   "`)이나 문장부호만 있는 정답이 0% CER로 전체 PASS를 획득할 수 있음 | `validate_reference_item` 함수를 통해 모델 파이프라인 시작 전에 문자열 타입 검사 및 텍스트 정규화(공백/부호 제거) 후 최소 1글자 이상 여부 검증. 공백/부호만 있거나 비문자열 정답은 모델 추론 전 즉시 구조화 `ERROR` 처리. 분모 0 발생 시 `measured_value_raw=None` 및 PASS 차단 | **해결 완료** (회귀 테스트 4건 통과, PM probe 확인) |
| **R2** [P2] | 실패 증거 | 모델 생성자(constructor) 예외나 파일 읽기 실패 시 CER 보고서 미생성 (`report_exists=false`) | `evaluate_dataset` 최상위 생명주기 try/except 블록 도입. `failed_phase` ("manifest_loading", "pipeline_init", "file_evaluation")를 추적하여 모델 초기화 실패 시에도 `output_json`에 단계, 예외 타입, 메시지, 메타데이터를 저장하고 close 보장 | **해결 완료** (회귀 테스트 1건 통과, PM probe 확인) |
| **R3** [P2] | 출력 보호 | `atomic_write_json`이 기존 `--output-json` 파일을 무조건 덮어써 이전 평가 증거 손실 | `resolve_output_path` 도입: `--overwrite` 플래그가 없는 경우 기존 파일 내용을 100% 보존하고, 새 고유 파일명(`_YYYYMMDD_HHMMSS_<uuid>.json`)으로 자동 분기 저장. 성공 및 예외 실패 경로 모두에서 기존 증거 bytes 보존 | **해결 완료** (회귀 테스트 3건 통과, PM probe 확인) |
| **보고서 정합성** | 메타데이터 | config(ITN 포함), 모델 정보, 마이크 장치 정보 등 재현 메타데이터 부재 및 `passed` 필드 모호성 | `get_reproduction_metadata`로 SenseVoice ONNX 모델, ITN 활성 상태, VAD/STT 설정, 마이크 장치 정보 JSON 기록. 10분 마이크에서 `passed`는 전체 게이트 일치(PARTIAL 시 `False`)로 수정하고 기술 지표 통과는 `technical_stability_passed: True`로 명확히 분리 | **해결 완료** (코드 및 안내서 반영) |

---

## 2. 변경된 파일 목록 및 상세 구현 내역

### 2.1 [scripts/evaluate_cer.py](file:///Users/jwlee/study1/byyourside/scripts/evaluate_cer.py)
1. **사전 정답 유효성 검증 (`validate_reference_item`)**:
   - `ref`가 문자열(`str`)인지 확인.
   - 텍스트 정규화 `normalize_text(ref, remove_punct=True).replace(" ", "")` 후 유효 문자 길이가 1글자 이상인지 검증.
   - 공백만 있는 문자열(`"   "`), 문장부호만 있는 문자열(`".,!? "`), 비문자열(숫자 등)은 모델 초기화(`SpeechPipeline`) 전에 즉시 차단되고 `structured_status: "ERROR"`, `measured_value_raw: None` 보고서를 저장.
   - 표준 30문항(P01~P30) 정상 데이터셋은 0% CER로 전체 `PASS` 달성 유지.
2. **CER 파이프라인 전 생명주기 실패 증거 보존**:
   - 파이프라인 생성자 호출(`SpeechPipeline(...)`) 실패, WAV 읽기 실패 등 예외 발생 시 `failed_phase: "pipeline_init"`, `exception_type`, `exception_message`를 포함한 구조화된 JSON 보고서를 `output_json`에 원자적으로 저장한 뒤 예외를 재발생시킴.
3. **기존 출력 파일 보호 정책 및 `--overwrite` 플래그**:
   - `resolve_output_path(filepath, allow_overwrite)` 함수 추가.
   - `--output-json`으로 지정된 파일이 이미 존재할 경우, `allow_overwrite=False`(기본값)이면 기존 파일의 내용을 일체 수정하지 않고 새 고유 경로(`_YYYYMMDD_HHMMSS_<uuid>.json`)로 자동 분기하여 저장.
   - 사용자가 명시적으로 덮어쓰기를 원하는 경우에만 `--overwrite` 플래그를 통해 기존 파일 교체 허용.
4. **재현 메타데이터 (`reproduction_metadata`) 추가**:
   - Git short revision, git dirty 상태, Python 버전, OS 플랫폼.
   - 모델 명칭, ONNX INT8 파일 경로, 토큰 파일 경로, ITN 활성화 상태 (`builtin_sensevoice_normalization_enabled`).
   - 파이프라인 상세 설정 (VAD hard max duration, STT request timeout, 스레드 수, 다운믹스 방식).

### 2.2 [tests/test_mic_10min.py](file:///Users/jwlee/study1/byyourside/tests/test_mic_10min.py)
1. **기존 출력 파일 보호 적용**:
   - CER과 동일하게 `resolve_output_path` 및 `atomic_write_json(..., allow_overwrite=allow_overwrite)` 적용.
   - CLI에 `--overwrite` 인자 추가.
2. **`passed` 필드와 `technical_stability_passed` 필드 분리**:
   - 기존의 `passed: true`가 `overall_status: PARTIAL`과 상충되던 문제를 해결.
   - 전체 종합 게이트 통과 여부를 나타내는 `passed`는 `overall_status == "PASS"`일 때만 `True`(10분 게이트가 PARTIAL인 경우 `False`).
   - 스트리밍 무손실, 큐 안정성, RTF, 지연 지표 통과 여부는 `technical_stability_passed: True`로 명확히 분리 기록.
3. **마이크 장치 재현 메타데이터 추가**:
   - `sounddevice.query_devices(kind="input")`를 통해 기본 입력 마이크 명칭, 기본 샘플 레이트, 최대 입력 채널 수를 캡처하여 메타데이터에 기록.

### 2.3 [tests/test_validation_tools_rev2.py](file:///Users/jwlee/study1/byyourside/tests/test_validation_tools_rev2.py)
- Revision 02 신규 요구사항을 집중 검증하는 9개 테스트 케이스 구현:
  1. `test_normal_30_sentences_achieves_pass`: 정상 30문항 전체 PASS 검증.
  2. `test_blank_reference_rejected_before_model_init`: 30개 공백 ref 모델 미시작 및 ERROR 검증.
  3. `test_punctuation_only_reference_rejected_before_model_init`: 문장부호 ref 모델 미시작 및 ERROR 검증.
  4. `test_non_string_reference_rejected_before_model_init`: 비문자열 ref 사전 거부 검증.
  5. `test_cer_constructor_failure_preserves_json_and_closes_pipeline`: 생성자 예외 시 JSON 보존 검증.
  6. `test_existing_output_file_bytes_preserved_without_overwrite`: 기존 파일 bytes 100% 보존 검증.
  7. `test_existing_output_file_overwritten_when_overwrite_true`: `--overwrite` 시 교체 검증.
  8. `test_consecutive_default_runs_generate_distinct_files`: 연속 실행 시 고유 파일 생성 검증.
  9. `test_mic_passed_field_matches_overall_gate_status`: 마이크 `passed`와 `technical_stability_passed` 분리 검증.

### 2.4 [docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md)
1. **매니페스트 모드 동작 정책 상세화**:
   - `complete` 모드: 매니페스트에 나열된 항목만 평가하며, 30개 미만(예: 1개, 29개) 기재 시 커버리지 미충족으로 `PARTIAL`로 기록됨(결코 전체 `PASS`가 될 수 없으며, 입력을 무조건 거부하지 않고 부분 실행으로 취급).
   - `merge` 모드: 수정한 문항만 기재하고 나머지는 표준 30문장 기본 정답을 유지하여 전수 평가 완결.
2. **정답 텍스트 유효성 요건 명시**:
   - 공백 및 문장부호만 있는 정답은 모델 추론 전 즉시 에러로 거부됨을 명시.
3. **기존 파일 보호 정책 및 `--overwrite` 사용법 안내**:
   - 결과 파일 덮어쓰기 방지 정책 및 `--overwrite` 옵션 사용법 추가.
4. **마이크 평가 게이트 분리 안내**:
   - `technical_stability_passed`와 종합 `passed`(`PARTIAL`)의 의미 구분 설명.

---

## 3. 검증 결과 및 실행 로그

### 3.1 신규 회귀 테스트 슈트 실행 (`tests/test_validation_tools_rev2.py`)
- **실행 명령**:
  ```bash
  .venv/bin/python -m unittest tests/test_validation_tools_rev2.py -v
  ```
- **실행 결과**:
  ```text
  test_blank_reference_rejected_before_model_init ... ok
  test_cer_constructor_failure_preserves_json_and_closes_pipeline ... ok
  test_consecutive_default_runs_generate_distinct_files ... ok
  test_existing_output_file_bytes_preserved_without_overwrite ... ok
  test_existing_output_file_overwritten_when_overwrite_true ... ok
  test_mic_passed_field_matches_overall_gate_status ... ok
  test_non_string_reference_rejected_before_model_init ... ok
  test_normal_30_sentences_achieves_pass ... ok
  test_punctuation_only_reference_rejected_before_model_init ... ok

  ----------------------------------------------------------------------
  Ran 9 tests in 0.517s

  OK
  ```
- **Exit Code**: `0`

### 3.2 전체 평가 도구 회귀 테스트 슈트 실행 (`rev1` + `rev2`)
- **실행 명령**:
  ```bash
  .venv/bin/python -m unittest tests/test_validation_tools_rev1.py tests/test_validation_tools_rev2.py -v
  ```
- **실행 결과**:
  ```text
  Ran 23 tests in 1.364s

  OK
  ```
- **Exit Code**: `0`

### 3.3 PM 진단 스크립트(`probes.py`) 재실행 결과 비교 (`logs/pm_review_validation_rev1_20260928/probes.py`)
PM이 진단했던 Revision 01의 4가지 검증 시나리오를 신규 코드로 재실행한 결과:

```json
{
  "normal_30": "PASS",
  "init_error": {
    "exception": "model init failure",
    "report_exists": true
  },
  "blank_reference_30": {
    "status": "ERROR",
    "metric": {
      "name": "vad_corpus_cer_nospace",
      "description": "Sum of Levenshtein edit distances / Sum of reference characters with punctuation and spaces removed",
      "target_threshold": 0.15,
      "measured_value": null,
      "measured_value_raw": null
    }
  },
  "existing_output": {
    "prior_evidence": true
  }
}
```

#### 진단 결과 대조 분석:
1. `normal_30`: **`PASS` 유지** (정상 30문항은 0% CER로 완전 통과).
2. `init_error`: 기존 `report_exists: false` $\to$ **`report_exists: true`** (초기화 예외 시에도 실패 보고서가 안전하게 보존됨).
3. `blank_reference_30`: 기존 `status: PASS, measured_value_raw: 0.0` $\to$ **`status: ERROR, measured_value_raw: null`** (공백 정답으로 인한 허위 0% PASS 원천 차단 및 모델 미시작).
4. `existing_output`: 기존 `{'replacement': True}` (기존 파일 교체 손실) $\to$ **`{'prior_evidence': True}`** (기존 증거 파일 내용 100% 보존).

---

## 4. 미실행 항목 현황 (NOT_RUN / PARTIAL 유지)

본 작업은 평가 도구와 판정 하네스의 무결성을 보완한 작업이며, 실제 실사용 음성 데이터 투입 전까지 다음 항목들은 지침에 따라 `NOT_RUN` 또는 `PARTIAL` 상태를 엄격히 유지합니다:

1. **사용자 30문장 발표 전사 CER 평가**:
   - 상태: **`NOT_RUN`**
   - 사유: `audio/eval_30/` 디렉터리에 실제 낭독 파일 부재 (0/30).
2. **사용자 600초(10분) 연속 발표 마이크 실사용 안정성**:
   - 상태: **`NOT_RUN / PARTIAL`**
   - 사유: 파이프라인의 10분 마이크 무손실 스트리밍 및 판정 하네스는 검증되었으나, 실제 10분 연속 사용자 음성 입력 전이므로 공식 게이트는 `PARTIAL` 유지.
3. **사람 청취 기준 발화 종료 정답 라벨링 (Human Reference Annotation)**:
   - 상태: **`NOT_RUN / PARTIAL`**
   - 사유: 음향학적 정밀 발화 경계 사람 라벨링 데이터 미작성 상태.
4. **OS 커널 수준 완전 송신 차단 (OS Egress Blocking)**:
   - 상태: **`PARTIAL`**
   - 사유: 파이프라인의 오프라인 실행 및 소켓 차단 smoke test는 입증되었으나, 커널 방화벽 및 패킷 캡처 수준의 완전 차단 증거는 사용자의 물리적 오프라인 확인 시 검증 대상임.

---

## 5. 결론 및 향후 계획

- `docs/reports/task_01_validation_tools_revision_01_pm_review.md`에서 제기된 P1/P2 결함 3건(R1 정답 유효성, R2 CER 실패 기록, R3 기존 출력 보호)과 보고서 정합성 과제가 모두 완결되었습니다.
- 기존 승인된 STT 엔진 및 VAD 파이프라인 코드는 일체 수정하지 않고 온전히 보존되었습니다.
- **Task 02(Android 통합 및 최적화)는 시작하지 않았으며, PM 검수를 대기합니다.**
