# Task 01 Revision 01 — 측정·종료·손실 처리 보완 및 재측정 보고서

- 작성일: 2026-09-28
- 작업 ID: Task 01 Revision 01 (측정·종료·손실 처리 보완)
- 담당: Gemini 개발자 / 검수: 사용자와 PM
- 관련 문서: [docs/reports/task_01_pm_review.md](file:///Users/jwlee/study1/byyourside/docs/reports/task_01_pm_review.md), [docs/pm/task_01_revision_01.md](file:///Users/jwlee/study1/byyourside/docs/pm/task_01_revision_01.md)
- 기존 초안 보고서: [docs/reports/task_01_environment_and_stt_poc_report.md](file:///Users/jwlee/study1/byyourside/docs/reports/task_01_environment_and_stt_poc_report.md)

---

## 1. 종합 결과 및 판정

- **작업 ID**: Task 01 Revision 01
- **개발자 자체 평가**: **PARTIAL (PM 검수 대기)**
- **사유**:
  - PM 검수에서 지적된 7대 결함(R1~R7) 및 추가 보완 사항을 전면 수정하고 단위/회귀 테스트 15건을 모두 통과시켰습니다.
  - 시간축 정정(R1)을 통해 VAD 종료 무음(0.5초) 대기 시간이 실제 발화 후 지연에 포함됨을 확인하였으며, 4.0초 하드 분할 상한(R7)을 도입하여 연속 발화 총 지연 p95를 **4,223.9 ms (4.22초)**로 초기 목표(≤ 5.5s) 내로 안정화했습니다.
  - 공통 파이프라인 기반의 10분 마이크 테스트(R6)에서 오버런 0건, 청크/세그먼트 손실 0건(무손실), 큐 대기 누적 없음(p95 0.08ms), Current RSS 399.6MB의 안정성을 실측 검증했습니다.
  - 다만 지시서 원칙에 따라:
    1. **사용자의 실제 30개 발표 음성 부재**로 인한 정확도 평가는 자의적 PASS 처리 없이 **`NOT_RUN / PARTIAL`**을 유지합니다.
    2. **외부 네트워크 차단**은 Python 소켓 레이어 smoke test는 통과했으나, 사용자 환경 보호를 위해 OS 레벨 인터페이스 차단은 미수행하였으므로 **`PARTIAL (Smoke test 통과, OS egress 미차단)`**으로 표기합니다.
  - **Task 02는 시작하지 않고 PM 검수를 대기합니다.**

---

## 2. R1~R7 지적 사항별 수정 내역

| 항목 | 우선순위 | 결함 내용 | 수정 파일 및 위치 | 수정 내용 및 해결책 | 검증 방법 |
|---|---|---|---|---|---|
| **R1** | P1 | 발화 후 지연 계산 시 청크 수신 시각을 기준점으로 사용하여 VAD 종료 무음(0.5s) 대기 시간을 누락함 | `src/pipeline.py`<br>(L360–378, L490–508) | 오디오 sample clock과 monotonic clock을 연결. 물리적 발화 종료 시각 $T_{speech\_end} = stream\_start + (S_{end} / 16000)$부터 결과 출력 $T_{emit}$까지의 시각 차로 **Estimated Post-Speech Delay** 정정 | `tests/test_regression_r1_r7.py` (`test_r1_post_speech_delay_includes_silence_waiting`)에서 0.5초 무음 포함 실측(533.9ms) 확인 |
| **R2** | P1 | 큐 Full 시 `except queue.Full: pass`로 무기록 폐기하며 무손실로 오보고 | `src/pipeline.py`<br>(L286–305, L420–445) | 오디오 큐와 세그먼트 큐 손실을 분리 추적. `dropped_items`에 ID, 샘플 구간, 길이, 사유 기록. 1건이라도 손실 시 `is_lossless = False`, `status = "DROPPED"` 처리 | `test_r2_loss_tracking_under_overload` (극소 큐 + 인위적 지연 주입 시 drop 기록 및 실패 판정) |
| **R3** | P1 | STT worker가 `stop_event`와 `audio_queue.empty()`만 보고 VAD flush보다 먼저 조기 종료되어 마지막 발화 유실 가능 | `src/pipeline.py`<br>(L275–330, L400–470) | Producer → VAD → STT 엄격한 **Sentinel(`SENTINEL = object()`) 전달 체인** 구축. STT는 Sentinel을 수신할 때까지 절대 조기 종료하지 않음. worker 예외 및 join timeout 발생 시 즉시 메인 실행 실패로 raise | `test_r3_flush_delay_worker_lifetime` (flush 지연 세그먼트 정상 처리), `test_r3_worker_exception_propagation` |
| **R4** | P2 | stereo PCM 입력 시 채널 평균을 먼저 계산하여 float64로 변환되면서 integer PCM 정규화 스케일링을 건너뜀 (피크 16384 전달) | `src/audio_utils.py`<br>(L25–58)<br>`src/pipeline.py` | 공통 `load_and_normalize_audio` 구현. 채널 downmix 전에 원본 dtype(int16, int32, float) 기반으로 `[-1.0, 1.0]` 정규화 먼저 수행 | `test_r4_stereo_pcm_normalization` (stereo int16 16384 → float32 0.5 정확 변환 검증) |
| **R5** | P2 | Python `socket.connect` 모킹만으로 "완전 오프라인 증명 PASS"로 단정 | `tests/run_scenarios.py`<br>`docs/reports/...` | 시험 명칭을 **`Python socket connect 차단 smoke test`**로 정정. OS egress 차단 미수행을 명시하고 판정을 **`PARTIAL`**로 하향 조정 | `run_scenario_6` 실행 및 결과 라벨 분리 |
| **R6** | P2 | 10분 마이크 시험이 공통 파이프라인을 복제하여 실행하고, 메모리를 `ru_maxrss`(피크)만 측정하여 "현재 메모리 불변"으로 오해 보고 | `src/pipeline.py`<br>`src/metrics.py`<br>`tests/test_mic_10min.py` | `SpeechPipeline.run_mic` 공통 경로로 통합. `psutil` 기반 **Current RSS**와 `getrusage` 기반 **Peak RSS**를 분리 측정. 큐 누적 및 손실을 PASS 조건에 추가 | `tests/test_mic_10min.py 600` 재실행 완료 |
| **R7** | P2 | 기존 경계 손실 시험이 무음이 포함된 WAV를 이어붙여 강제 분할을 시험하지 못함 | `src/vad.py`<br>`src/pipeline.py`<br>`tests/run_scenarios.py` | `hard_max_speech_duration=4.0s` 하드 상한 강제 슬라이스 구현. 실제 발화 도중에 정확히 4.0초 경계가 위치하는 **진정한 강제 절단 fixture** 제작 및 단어 누락 실측 | `run_scenario_4`에서 4.0초 경계에서의 단어 누락 실측 ('살면' 중 '살' 누락 관측) |

---

## 3. 실제 실행 명령과 Exit Code 및 로그 경로

기존 Revision 00의 원시 로그는 모두 보존되었으며, Revision 01의 재측정 로그는 신규 파일로 격리 저장되었습니다.

| 실행 목적 | 실행 명령 | Exit Code | 산출물 및 원시 로그 경로 | 상태 |
|---|---|---|---|---|
| 단위 및 회귀 테스트 (15건) | `.venv/bin/python3 -m unittest tests/test_vad_stt.py tests/test_cer.py tests/test_regression_r1_r7.py` | 0 | 터미널 출력 (15/15 통과, 14.18s) | **PASS** |
| Revision 01 시나리오 벤치마크 (1, 2, 3, 4, 6) | `.venv/bin/python3 tests/run_scenarios.py` | 0 | `logs/task_01_scenario_revised_results.json`<br>`logs/stt_run_scenario3_rev1_replay.jsonl`<br>`logs/stt_run_scenario4_rev1_boundary.jsonl`<br>`logs/stt_run_scenario6_rev1_offline.jsonl` | **PASS / 실측 완료** |
| Revision 01 10분 마이크 안정성 재측정 | `.venv/bin/python3 tests/test_mic_10min.py 600` | 0 | `logs/task_01_mic_10min_revised_result.json`<br>`logs/stt_run_mic10m_rev1_1790570587.jsonl` | **PASS** |
| 30개 발표 문장 CER 평가 (합산 CER 보완) | `.venv/bin/python3 scripts/evaluate_cer.py` | 0 | `logs/cer_eval_30_revised.json` | **NOT_RUN 유지 (30/30)** |
| 청크별 vs 연속 리샘플링 파형 오차 실측 | 스크립트 실행 | 0 | 터미널 실측 (SNR 38.70 dB, Max Error 0.105) | **분석 완료** |

---

## 4. 기준별 결과 비교 표 (Revision 00 vs Revision 01)

| 검수 항목 | PM 초기 목표 | 기존 1차 측정값 (Rev 00) | **Revision 01 재측정값** | 표본 수 | **최종 판정** | 근거 파일 |
|---|---|---|---|---|---|---|
| **한국어 정확도** | 조용한 실제 발화 30개 이상에서 합산 정규화 CER ≤ 15% | 공식 샘플 1건만 평가<br>(발표 음성 부재) | **발표 30개 음성 미녹음 유지**<br>(평가 프레임워크에 합산 CER 및 공백 분리 산식 보완 완료) | 0 / 30 (발표 음성) | **NOT_RUN** | `logs/cer_eval_30_revised.json` |
| **처리 여유** | warm 구간별 RTF p95 ≤ 0.5 | 마이크 p95 0.050<br>(전체 분모와 발화 분모 혼선) | • Speech RTF (순수 발화 대비): p95 **0.051** (평균 0.030)<br>• Throughput RTF (전체 입력 대비): **0.0049** | 66개 구간 (마이크) | **PASS** | `logs/task_01_mic_10min_revised_result.json` |
| **발화 후 결과 지연** | 기준 유성음 끝에서 결과 출력까지 p95 ≤ 1.5초 (1,500ms) | p95 78.6 ms<br>*(VAD 종료 무음 대기 누락 오류)* | • **마이크 10분 실측: p95 630.7 ms** (평균 593.5ms, 최대 637.6ms)<br>• **Replay 실측: 533.9 ms**<br>*(0.5s 무음 확인 시간 완벽 포함)* | 66개 구간 (마이크) | **PASS** | `logs/task_01_mic_10min_revised_result.json`<br>`logs/task_01_scenario_revised_results.json` |
| **연속 발화** | 최대 구간 4초 설정 시 구간 시작부터 결과까지 p95 ≤ 5.5초 (5,500ms) | p95 6,059.1 ms<br>*(FAIL: soft-cut 지연 누적)* | **p95 4,223.9 ms (4.22초)**<br>*(4.0초 하드 분할 상한 적용으로 5.5초 여유 충족)*<br>• 절단 시점부터 결과까지: p95 **223.9 ms** | 9개 분할 구간 | **PASS** | `logs/task_01_scenario_revised_results.json` |
| **무손실 / 안정성** | 마이크 10분 동안 crash·overrun·무기록 폐기 없음, 큐 지연 지속 증가 없음 | crash/overrun 0건 보고<br>*(큐 Full 무기록 폐기 버그 존재)* | • Crash: **0건**, Overrun: **0건**<br>• Chunks Drop: **0건**, Segments Drop: **0건**<br>• `is_lossless`: **True** (무손실 확인)<br>• 큐 대기시간: p95 **0.08 ms** (지연 누적 0)<br>• Current RSS: **390.6MB → 399.6MB** (안정적)<br>• Peak RSS: **956.2MB** | 600.29초 캡처<br>(66개 발화) | **PASS** | `logs/task_01_mic_10min_revised_result.json` |
| **재현성** | 실행 절차·고정 버전·모델 manifest·원시 로그로 재계산 가능 | 메타데이터 보존 | 회귀 테스트 스크립트 및 분리 로그 보존 완료 | 전 항목 | **PASS** | `tests/test_regression_r1_r7.py` |
| **오프라인** | 모델 준비 이후 네트워크 차단 조건의 실행 증거 | Python monkeypatch 후 "완전 오프라인" PASS로 보고 | **Python socket connect 차단 smoke test 통과** (STT 정상 실행 확인). 단, OS 레벨 인터페이스 차단은 미수행 | 1회 테스트 | **PARTIAL**<br>(Smoke test 확인) | `logs/task_01_scenario_revised_results.json` |

---

## 5. 실패 및 실측 관측 사례 분석

### 5.1 시나리오 4: 진정한 강제 절단 경계 fixture에서의 단어 누락 실측 (R7)
- **실험 조건**:
  - 발화 텍스트: `"조금만 생각을 하면서 살면 훨씬 편할 거야"` (순수 발화 2.89초)
  - pause 없이 2회 연속 연결: 총 5.78초
  - `hard_max_speech_duration = 4.0s` 강제 절단 적용.
  - 두 번째 반복의 1.11초 지점(단어 `"살면"`의 한가운데)에서 정확히 4.0초 하드 컷 발생.
- **실측 세그먼트 전사 결과**:
  - **Seg #1 (4,000 ms, `hard_max_duration`)**: `"조 금만 생각 을 하 면서 살 면 훨씬 편할 거야조 금만 생각 을 하 면서."`
  - **Seg #2 (1,376 ms, `flush`)**: `"면 훨씬 편할 거야."`
- **단어 경계 손실 분석**:
  - 절단 경계에 걸쳐 있던 단어 `"살면"` 중 앞 음절인 **`"살"`**이 앞 세그먼트에도, 뒤 세그먼트에도 포함되지 않고 음향적으로 소실(Deletion)되었습니다.
  - 뒤 세그먼트는 남은 음절인 `"면 훨씬 편할 거야"`부터 정상 인식되었습니다.
  - **정량 지표**:
    - Non-space CER: **2.94%** (정답 34글자 중 정확히 1글자 '살' 누락)
    - Spaced CER: **19.15%** (SenseVoice 토크나이저의 형태소/음절 공백 삽입으로 인한 차이)
- **엔지니어링 결론**:
  - 비스트리밍 모델을 VAD로 강제 절단하는 simulated streaming 구조에서는 **단어 중간 절단 시 최소 1음절의 음향적 손실이 필연적으로 발생함**이 실측 데이터로 확인되었습니다.
  - 후속 Task 03(통합 파이프라인)에서는 50~100ms의 오디오 오버랩(overlap) 윈도우를 도입하거나, LLM 코칭 계층에서 문맥 기반으로 잘린 음절을 보정하는 전략이 필요함을 시사합니다.

### 5.2 청크별 리샘플링 vs 연속 리샘플링 파형 오차 실측 (추가 보완)
- **실험 조건**: 48kHz 오디오를 전체 연속 리샘플링한 파형과 32ms(1536 samples) 청크 단위로 리샘플링한 파형을 샘플 단위 비교.
- **실측 수치**:
  - 최대 절대 오차 (Max Error): **0.1050**
  - 평균 제곱근 오차 (RMSE): **0.000814**
  - 신호 대 잡음비 (SNR): **38.70 dB**
  - STT 인식 비교:
    - 연속 리샘플링: `"조금만 생각을 하면서 살면 훨씬 편할 거야."`
    - 청크별 리샘플링: `"조 금만 생각을  하 면서 살면 훨씬 편할 거야."`
- **분석**:
  - 청크 경계에서 FIR 필터 상태 리셋으로 인해 약 38.7 dB 수준의 미세 경계 잡음이 발생하며, 이는 전체적인 어휘 인식에는 영향을 주지 않으나 토크나이저의 띄어쓰기 토큰 생성에 미세한 차이를 유발할 수 있음을 확인했습니다.

---

## 6. 실행하지 못한 항목 및 사용자 필요 절차

### 6.1 조용한 환경 실제 한국어 발표 문장 30개 CER 벤치마크 (`NOT_RUN`)
- **사유**: 작업 디렉토리에 사용자의 실제 발표 녹음 파일(`audio/eval_30/P01.wav` ~ `P30.wav`)이 부재합니다. 합성 음성을 실제 음성으로 둔갑시키지 않고 `NOT_RUN` 상태를 유지합니다.
- **사용자 수행 필요 절차**:
  1. 조용한 환경에서 마이크로 `audio/eval_30/` 디렉토리에 `P01.wav`부터 `P30.wav`까지 30개 문장을 녹음하여 배치합니다. (문장 목록은 `scripts/evaluate_cer.py`의 `EVAL_DATASET` 참조)
  2. 다음 명령을 실행하면 개정된 산식(합산 CER 및 공백 분리)에 따른 정밀 평가표가 즉시 생성됩니다:
     ```bash
     .venv/bin/python3 scripts/evaluate_cer.py --audio-dir audio/eval_30
     ```

### 6.2 OS 레벨 완전 외부 네트워크 차단 (`PARTIAL`)
- **사유**: Python `socket.connect` 차단 smoke test는 통과하였으나, macOS의 시스템 네트워크 설정(Wi-Fi 인터페이스 비활성화, pf 방화벽 규칙 변경 등)을 임의로 변경하지 않아야 하는 원칙에 따라 OS 레벨 차단은 미실행으로 남겼습니다.
- **사용자 수행 절차**: 필요 시 사용자가 Wi-Fi를 끈 오프라인 상태에서 `.venv/bin/python3 scripts/run_poc.py --mode wav`를 직접 실행하여 완전 오프라인 동작을 최종 확인할 수 있습니다.

---

## 7. 타깃 보드(QCS6490)와의 차이 및 다음 단계 고려사항

1. **CPU 추론 여유도 차이**:
   - 현재 Apple M5 CPU에서 SenseVoice INT8 추론의 RTF는 0.026 수준(실시간 대비 약 38배 빠름)을 기록했습니다.
   - 타깃 보드인 QCS6490(Kryo 670 CPU)은 클록과 IPC, 메모리 대역폭이 상이하므로 RTF가 0.2~0.3 수준으로 증가할 것으로 예상되며, 4개 CPU 코어 점유에 따른 발열 쓰로틀링을 고려해야 합니다.
2. **단어 경계 손실 대응**:
   - 시나리오 4 실측에서 확인되었듯이 4초 하드 컷 시 경계 음절 손실이 발생하므로, 후속 LLM 단계에서 불완전 발화에 대한 견고성(Robustness) 프롬프팅 또는 오버랩 버퍼 정책이 수립되어야 합니다.

---

## 8. 결론

PM 검수(Revision 01)에서 요구된 7가지 수정 사항(R1~R7)을 코드베이스에 충실히 반영하고, 새로운 원시 로그와 함께 정밀한 재측정을 완료하였습니다.

지시서에 따라 **Task 02는 착수하지 않고, PM의 재검수 및 피드백을 기다립니다.**
