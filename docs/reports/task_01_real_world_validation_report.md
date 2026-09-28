# Task 01 — 실사용 평가 도구 및 사용자 녹음 안내서 준비 보고서

- 작성일: 2026-09-28
- 작업 ID: Task 01 Real-World Validation Preparation (실사용 평가 도구 정비, 사용자 녹음 가이드 작성, 미수행 항목 경계 명시)
- 대상 커밋: `3d13d85` 및 후속 커밋
- 담당: Gemini 개발자 / 검수: 사용자와 PM
- 관련 문서:
  - 검수 지적서: [docs/reports/task_01_revision_06_pm_review.md](file:///Users/jwlee/study1/byyourside/docs/reports/task_01_revision_06_pm_review.md)
  - 지시서: [docs/pm/task_01_real_world_validation.md](file:///Users/jwlee/study1/byyourside/docs/pm/task_01_real_world_validation.md)
  - 녹음 안내서: [docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md)
  - 이전 보고서:
    - [docs/reports/task_01_revision_06_report.md](file:///Users/jwlee/study1/byyourside/docs/reports/task_01_revision_06_report.md)
    - [docs/reports/task_01_revision_05_report.md](file:///Users/jwlee/study1/byyourside/docs/reports/task_01_revision_05_report.md)

---

## 1. 종합 결과 및 현황 요약

- **판정**: **PARTIAL (평가 도구 및 사용자 가이드 준비 완료, 사용자 실제 녹음 및 마이크 발표 데이터 확보 전까지 실사용 평가는 NOT_RUN 유지)**
- **핵심 완결 사항**:
  1. **사용자 녹음 안내서 작성 완료 ([docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md))**:
     - P01~P30 30개 문장의 텍스트, 카테고리, 권장 낭독 방식, WAV 파일명 규격(`audio/eval_30/P01.wav` ~ `P30.wav`), 16kHz 모노 16-bit PCM 포맷 명시.
     - 평가 전 정답 검수 원칙(오독 시 재녹음 또는 사전 manifest 수정 원칙, 사후 STT 결과 기반 정답 변경 절대 금지) 규정.
     - 10분 연속 발표 마이크 평가 요령 및 오프라인 검증 절차 상세화.
  2. **30문장 CER 평가 도구 정비 (`scripts/evaluate_cer.py`)**:
     - **인식 경로 분리 비교**: 순수 음향 모델 전사(`run_wav_direct_stt`)와 VAD 발화점 검출 전사(`run_wav_vad`)를 동시 실행하여 **VAD 경계 절단에 따른 단어 손실 영향(`boundary_cer_diff`)**을 정밀 분리 측정할 수 있도록 구현.
     - **SHA-256 해시 및 메타데이터 보존**: 모든 입력 오디오의 SHA-256 해시, 샘플 레이트, 채널, 재생 길이를 측정하여 기록.
     - **실행별 고유성 보장**: UUID 및 타임스탬프 기반 독립 결과 JSON 파일(`logs/cer_eval_results_YYYYMMDD_HHMMSS_*.json`) 생성으로 결과 덮어쓰기 방지.
     - **파일 누락 명시 및 상태 제어**: 음성 파일 미존재 시 항목별 `NOT_RUN`을 명확히 표기하고 성공 건수에서 제외(0건: `NOT_RUN`, 1~29건: `PARTIAL`, 30건 완료 시 CER $\le$ 15% 판정).
  3. **10분 마이크 안정성 평가 도구 정비 (`tests/test_mic_10min.py`)**:
     - **구버전 잔여 명칭 갱신**: 과거 `rev3` 고정 명칭을 전면 제거하고 타임스탬프/UUID 기반 고유 출력 체계 적용.
     - **실측 캡처 길이 검증**: 단순 설정값(600s)이 아닌 실제 녹음 캡처 프레임 수 기반 길이(`captured_audio_seconds`) 검증.
     - **분당 발화 분포 분석 (`per_minute_analysis`)**: 10분을 1분 단위(0~60s, 60~120s 등 10개 구간)로 분할하여 분당 발화 시간, 발화 비율, 세그먼트 수를 집계. 1문장 발화 후 9분 무음 방치 등 부정 평가 시 `INVALID_SPEECH_DISTRIBUTION`으로 자동 기각.
     - **메모리 및 큐 드리프트 추적**: 부모 RSS와 자식 RSS를 분리 기록하고 큐 대기 시간 드리프트 추적.
  4. **지연 및 타임스탬프 표기 정정**:
     - `tests/test_regression_rev5.py`의 상수 차감 잔차를 "추정 분해(Estimated Timing Breakdown)"로 명시하고, warm_up 출력 표기를 "더미 추론 시간"으로 정정.
     - `t_req_issued`(부모가 IPC 송신하기 **직전** 시각) 및 `failure_caught_ts`(파이프라인 worker 정리 완료 후 예외 re-raise **직전** 시각)의 엄밀한 의미를 명문화.
  5. **전체 단위/회귀 테스트 51/51 통과 (100% OK, 99.041s)**:
     - 도구 정비 후 전체 회귀 테스트 스위트를 재실행하여 기존 기능과의 무결성을 검증했습니다.
  6. **원칙 준수**:
     - 사용자의 실제 마이크 발표 준비 전 10분 마이크 시험을 임의로 반복하지 않았습니다.
     - **Task 02는 착수하지 않고 PM 검수를 대기합니다.**

---

## 2. 미수행(NOT_RUN) 및 부분 검증(PARTIAL) 항목 현황

실제 사용자 음성 데이터가 아직 투입되지 않은 상태이므로, PM 지시서의 원칙에 따라 실사용 평가 결과는 엄격히 **`NOT_RUN / PARTIAL`** 상태를 유지합니다.

| 검증 항목 | 판정 상태 | 사유 및 실측 조건 |
| :--- | :---: | :--- |
| **사용자 30문장 발표 CER 평가** | **NOT_RUN** | `audio/eval_30/` 디렉터리에 사용자 음성 파일(`P01.wav` ~ `P30.wav`)이 아직 부재함. 평가 도구 실행 결과 `NOT_RUN (0/30 recorded files found)` 반환 확인. |
| **600초(10분) 실제 마이크 연속 발표 안정성** | **NOT_RUN** | 사용자가 실제 발표 자료를 준비하여 10분간 연속 발화하는 마이크 테스트가 아직 수행되지 않음. (과거 수행된 60초 무음/잡음 캡처만 존재함) |
| **사람 수동 청취 발화 종료 시점 (Human Reference End)** | **NOT_RUN** | 음향 분석(Audacity/Praat)을 통한 사람 청취 정답 발화 종료 샘플 라벨이 부재하여 VAD 자동 판정 시점을 기준으로 계측된 상태 유지. |
| **OS 수준 완전 네트워크 egress 차단** | **PARTIAL** | Python 레벨 `socket.connect` 차단 테스트(`Smoke PASS`)는 확인되었으나, OS 커널 수준 패킷 필터링(`pfctl`) 또는 물리적 Wi-Fi 차단 검증은 사용자가 직접 수행해야 함. |

---

## 3. 평가 도구 상세 정비 내용

### 3.1 30문장 CER 평가 도구 (`scripts/evaluate_cer.py`)
- **주요 파라미터**:
  - `--audio-dir`: 녹음 파일 폴더 지정 (기본값: `audio/eval_30`)
  - `--manifest-json`: 사용자가 검수한 커스텀 정답 매니페스트 경로
  - `--mode {all, direct, vad}`: 비교 모드 선택 (기본값: `all`)
  - `--output-json`: 출력 JSON 경로 (미지정 시 `logs/cer_eval_results_YYYYMMDD_HHMMSS_*.json`으로 자동 격리)
- **Direct STT vs VAD+STT 분리 비교**:
  ```python
  # 1. 순수 음향 모델 전사 (Direct STT, VAD 바이패스)
  res_direct = pipeline.run_wav_direct_stt(wav_path, run_id=run_id_direct)
  # 2. VAD 엔드포인팅 파이프라인 전사 (VAD+STT)
  res_vad = pipeline.run_wav_vad(wav_path, run_id=run_id_vad)
  # 3. VAD 경계 절단 영향 분석
  boundary_cer_diff = cer_vad_nospace - cer_direct_nospace
  ```
- **실행 검증**:
  - 오디오 파일 부재 시:
    ```bash
    .venv/bin/python scripts/evaluate_cer.py
    # Output: Overall Status: NOT_RUN (0/30 recorded files found in audio/eval_30)
    # Output file: logs/cer_eval_results_20260928_111818_b1bd0e35.json
    ```
  - 오디오 1건 존재 시 테스트:
    ```bash
    # Output: Overall Status: PARTIAL (1/30 files executed, 29 missing)
    # SHA-256 해시, direct/vad 결과, missing 항목 상세 정상 기록 확인.
    ```

### 3.2 10분 마이크 안정성 평가 도구 (`tests/test_mic_10min.py`)
- **주요 파라미터**:
  - `--duration`: 목표 시간 지정 (기본값: 600.0초)
  - `--output-json`: 출력 JSON 경로 (미지정 시 `logs/task_01_mic_10min_YYYYMMDD_HHMMSS_*.json` 자동 생성)
- **분당 발화 분포 알고리즘 (`analyze_per_minute_speech`)**:
  - 오디오 전체 길이를 1분 단위로 슬라이싱하여, 각 분별 발화 시간과 비율을 산출.
  - 10분 중 최소 5개 이상의 분에서 분당 3초 이상의 활성 발화(`has_active_speech`)가 있어야 정상 발표로 인정.
  - 발화가 1개 분에만 몰려 있고 나머지가 무음인 경우, `PARTIAL (Silence/cheating detected: only X/10 minutes had active speech)`로 판정하여 무음 방치 통과를 차단.
- **메모리 및 큐 드리프트 집계**:
  - `psutil`을 통해 부모 RSS와 격리된 SenseVoice 자식 프로세스 RSS를 각각 측정하여 `memory_metrics`에 기록.

### 3.3 타임스탬프 정의 정정 및 주석 보완
- `tests/test_regression_rev5.py:213`의 상수 차감 잔차를 실측이 아닌 "추정 분해(Estimated Timing Breakdown)"로 명시하고, 엄밀한 monotonic 시계 계측은 `test_regression_rev6.py`를 참조하도록 명문화.
- `src/stt.py` 및 `src/pipeline.py`의 타임스탬프 속성 주석 보완:
  - `t_req_issued`: 부모가 IPC 파이프로 송신하기 **직전** 시각 (`perf_counter`).
  - `failure_caught_ts`: 파이프라인 worker 정리 및 abort 처리 완료 후 호출자에게 예외를 re-raise하기 **직전** 시각 (`perf_counter`).

---

## 4. 사용자 녹음 및 실행 안내 요약

사용자가 음성을 녹음하고 평가를 진행하기 위한 단계는 다음과 같습니다 (상세 내용은 [docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md) 참조):

### 단계 1: 30문장 녹음
1. 디렉터리 생성: `mkdir -p audio/eval_30`
2. P01~P30 문장을 16kHz 모노 WAV로 녹음하여 `audio/eval_30/P01.wav` ~ `P30.wav`로 저장.
3. 오독(말실수)이 있는 경우:
   - 권장: 해당 문장 재녹음.
   - 대안: 평가 실행 전 실제 발화대로 정답 텍스트를 검수한 `manifest.json` 작성.
   - **금지**: STT 결과를 확인한 뒤 정답을 수정하는 행위 금지.

### 단계 2: 30문장 CER 평가 실행
```bash
.venv/bin/python scripts/evaluate_cer.py --audio-dir audio/eval_30
```
- 결과: 공백/문장부호 제거 Corpus CER가 15.0% 이하인지 확인.

### 단계 3: 10분 마이크 연속 발표 평가 실행
```bash
.venv/bin/python tests/test_mic_10min.py --duration 600.0
```
- 준비한 발표 내용으로 10분 동안 마이크에 연속 발화.
- 결과: Overrun 0, Drop 0, p95 RTF $\le$ 0.5, 분당 발화 분포 유효성 확인.

---

## 5. 회귀 테스트 및 검증 결과

- **전체 단위/회귀 테스트 (Discovery)**:
  - **명령**: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
  - **결과**: **51 tests, 99.041초, OK, exit code 0**
  - 포함된 8개 모듈 전체 통과:
    1. `tests/test_cer.py` (4 tests)
    2. `tests/test_regression_r1_r7.py` (7 tests)
    3. `tests/test_regression_rev2.py` (8 tests)
    4. `tests/test_regression_rev3.py` (7 tests)
    5. `tests/test_regression_rev4.py` (5 tests)
    6. `tests/test_regression_rev5.py` (8 tests)
    7. `tests/test_regression_rev6.py` (8 tests)
    8. `tests/test_vad_stt.py` (4 tests)

---

## 6. 결론 및 다음 단계

- **결론**: PM의 Revision 06 승인에 따라, 실사용 검증을 위한 **사용자 녹음 안내서**와 **평가 도구(CER 실행기, 10분 마이크 실행기)**의 정비가 완벽하게 준비되었습니다.
- **현재 상태**: 사용자 실제 음성 파일 및 10분 발표 데이터가 아직 제공되지 않았으므로 실사용 평가는 원칙대로 **`NOT_RUN`**을 유지합니다.
- **다음 단계**:
  - 사용자가 [docs/user/task_01_recording_guide.md](file:///Users/jwlee/study1/byyourside/docs/user/task_01_recording_guide.md)에 따라 녹음 및 마이크 시험을 진행할 수 있도록 안내하고 PM 검수를 대기합니다.
  - **Task 02는 착수하지 않습니다.**
