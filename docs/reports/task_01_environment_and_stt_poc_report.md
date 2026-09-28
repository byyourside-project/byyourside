# Task 01 — 노트북 환경 확인 및 한국어 VAD/STT PoC 결과 보고서

- 작성일: 2026-09-28
- 작업 ID: Task 01 (노트북 환경 확인 및 한국어 VAD/STT PoC)
- 담당: Gemini 개발자 / 검수: 사용자와 PM

---

## 1. 작업 ID와 종합 결과

- **작업 ID**: Task 01
- **개발자 자체 평가**: **PARTIAL (PM 검수 대기)**
- **사유**:
  - 오디오 파이프라인(VAD + SenseVoice STT), 10분 마이크 안정성(오버런 0건, 폐기 0건, 큐 지연 누적 없음, 메모리 불변), 단기 발화 처리, 오프라인 로컬 실행, VAD flush 등 핵심 인프라와 벤치마크는 모두 정상 검증되었습니다.
  - 그러나 PM 검수 기준 중 **(1) 실제 한국어 발표 발화 30개 이상의 CER 평가**는 사용자의 실제 음성 녹음본 부재로 인해 `NOT_RUN / PARTIAL`로 보류되었으며, **(2) 30초 이상 연속 발화 시 구간 지연(p95 ≤ 5.5s)** 항목은 sherpa-onnx Silero VAD의 소프트 컷(soft-cut) 특성으로 인해 실측 6.06초로 측정되어 기준을 초과(`FAIL`)하였습니다.
  - 지시서 6절 및 7절 원칙("표본 부족·마이크 미실행·annotation 부재는 NOT_RUN 또는 PARTIAL이다. 기준 미달이면 실패 사례와 개선안을 제시한다. 최종 승인자는 PM이다.")에 따라 성공으로 자의적 변경하지 않고 **PARTIAL**로 보고하며 PM의 검수와 피드백을 요청합니다.

---

## 2. 환경, 의존성, 모델과 설정

### 2.1 하드웨어 및 운영체제 환경
- **OS**: macOS 26.6.2 (Darwin arm64, Build 25G83)
- **CPU**: Apple M5 (Apple Silicon arm64)
- **RAM**: 16 GB (17,179,869,184 Bytes)
- **Python**: 3.12.9 (가상환경: `.venv`, 베이스 바이너리: `/opt/homebrew/bin/python3.12`)
- **오디오 입력 장치**: MacBook Pro 마이크 (기본 입력 장치, 1채널)
- **장치 기본 샘플레이트**: 48,000 Hz
- **내부 처리 샘플레이트**: 16,000 Hz
  - *리샘플링 구현*: `scipy.signal.resample_poly(chunk, up=1, down=3)`을 이용한 안티에일리어싱 3:1 다운샘플링 적용. 단순히 헤더 숫자만 바꾸지 않고 실제 FIR 저역통과 필터 기반 리샘플링 수행.

### 2.2 의존성 패키지 (`requirements.txt`)
- `sherpa-onnx==1.13.8` (sherpa-onnx-core==1.13.8) — Silero VAD 및 SenseVoice STT 추론 엔진
- `numpy==2.5.3` — 오디오 버퍼링 및 배열 연산
- `scipy==1.18.1` — 48kHz -> 16kHz 고품질 리샘플링 (`resample_poly`) 및 WAV I/O
- `sounddevice==0.5.6` (cffi==2.1.1, pycparser==3.0) — CoreAudio 마이크 실시간 스트림 캡처

### 2.3 모델 및 파라미터 설정
- **Silero VAD**:
  - 모델: `models/silero_vad.onnx` (643,854 바이트, SHA-256: `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`)
  - 설정: `threshold=0.5`, `min_silence_duration=0.5s`, `min_speech_duration=0.25s`, `max_speech_duration=4.0s`, `window_size=512`, `num_threads=1`, `provider="cpu"`
- **SenseVoice STT**:
  - 모델: `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx` (239,233,841 바이트, SHA-256: `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51`)
  - 토큰: `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt` (315,894 바이트, SHA-256: `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc`)
  - 설정: `language="ko"`, `use_itn=True`, `num_threads=4`, `provider="cpu"`
- **STT Worker**: 1 worker 스레드, ONNX Runtime 내부 4 스레드

---

## 3. 변경 파일 목록 및 변경 이유

- **기준 Revision**: `1452348a1fa0513583cf8443903c38acd63f6b36` ("docs: Initial task 01 and architecture review documents")

| 파일 경로 | 작업 구분 | 변경 및 추가 이유 |
|---|---|---|
| `.gitignore` | 신규 생성 | 가상환경(`.venv/`), 모델(`models/`), 음성(`audio/`, `*.wav`), 로컬 로그(`logs/`)를 Git에서 제외 |
| `requirements.txt` | 신규 생성 | Task 01 최소 의존성 및 고정 버전 명시 |
| `src/__init__.py` | 신규 생성 | 소스 패키지 초기화 |
| `src/config.py` | 신규 생성 | VAD, STT, 오디오, 큐, 파이프라인 Dataclass 및 유효성 검증 |
| `src/vad.py` | 신규 생성 | Silero VAD 래퍼, 청크 스트리밍 처리, 세그먼트 생성 및 flush 로직 |
| `src/stt.py` | 신규 생성 | SenseVoice INT8 STT 래퍼, cold start / warm-up / warm 추론 시간 계측 |
| `src/pipeline.py` | 신규 생성 | `wav_direct`, `replay`, `mic` 3가지 모드 지원 파이프라인 (스레드 큐, monotonic clock, 오버런/드롭 감지) |
| `src/metrics.py` | 신규 생성 | NFC 정규화, Levenshtein 기반 글자 단위 CER, 백분위수(p50, p95) 및 RSS 메모리 계산 |
| `src/logger.py` | 신규 생성 | JSONL 구조화 로거 (`logs/stt_run_*.jsonl`) 및 터미널 모니터링 출력 |
| `scripts/run_poc.py` | 신규 생성 | CLI 실행 진입점 (`--mode [wav\|replay\|mic]`, 파라미터 제어) |
| `scripts/evaluate_cer.py` | 신규 생성 | 발표 도메인 30개 문장 평가 스크립트 (음성 부재 시 NOT_RUN 처리) |
| `tests/test_vad_stt.py` | 신규 생성 | VAD 및 STT 단위 테스트 (8건) |
| `tests/test_cer.py` | 신규 생성 | CER 및 텍스트 정규화 단위 테스트 |
| `tests/run_scenarios.py` | 신규 생성 | Task 01 필수 시나리오 1, 2, 3, 4, 6 자동화 벤치마크 러너 |
| `tests/test_mic_10min.py` | 신규 생성 | Task 01 필수 시나리오 5 (10분 마이크 연속 안정성 및 메모리 계측) 러너 |
| `docs/reports/task_01_model_manifest.md` | 신규 생성 | 다운로드 모델 파일 크기, 공식 URL, SHA-256 해시, 라이선스 명세 |
| `docs/reports/task_01_environment_and_stt_poc_report.md` | 신규 생성 | Task 01 최종 결과 보고서 (본 문서) |

---

## 4. 실제 실행 명령과 Exit Code 및 로그 경로

| 실행 목적 | 명령줄 | Exit Code | 로그 및 산출물 파일 경로 |
|---|---|---|---|
| 의존성 설치 및 검증 | `.venv/bin/pip install -r requirements.txt` | 0 | `.venv/` |
| 단위 테스트 실행 | `.venv/bin/python3 -m unittest discover tests` | 0 | 터미널 출력 (8/8 통과) |
| WAV 직접 모드 실행 | `.venv/bin/python3 scripts/run_poc.py --mode wav` | 0 | `logs/stt_run_wav_74b575d1.jsonl` |
| Replay 실시간 모드 실행 | `.venv/bin/python3 scripts/run_poc.py --mode replay` | 0 | `logs/stt_run_replay_e8ffd3d8.jsonl` |
| 시나리오 1, 2, 3, 4, 6 벤치마크 | `.venv/bin/python3 tests/run_scenarios.py` | 0 | `logs/task_01_scenario_results.json` |
| 시나리오 5 (10분 마이크 안정성) | `.venv/bin/python3 tests/test_mic_10min.py 600` | 0 | `logs/task_01_mic_10min_result.json`, `logs/stt_run_mic10m_1790568233.jsonl` |
| 30개 발표 문장 CER 평가 | `.venv/bin/python3 scripts/evaluate_cer.py` | 0 | `logs/cer_eval_30.json` |

---

## 5. 기준별 결과 표

다음은 PM 검수 기준(지시서 6절)에 대한 실측 결과 매핑입니다.

| 검수 항목 | PM 초기 목표 | 실측값 | 표본 수 | 판정 | 근거 파일 |
|---|---|---|---|---|---|
| **한국어 정확도** | 조용한 실제 발화 30개 이상에서 합산 정규화 CER ≤ 15% | • 공식 샘플(ko.wav): Non-space CER **0.0%** (공백 포함 CER 17.02%)<br>• 발표 30개 문장: 미녹음 | 공식 샘플 1건 (발표 문장 30건 미녹음) | **PARTIAL** | `logs/cer_eval_30.json`, `logs/task_01_scenario_results.json` |
| **처리 여유** | warm 구간별 RTF p95 ≤ 0.5 | • 마이크 10분 실측: p95 **0.050** (평균 0.029, 최대 0.071)<br>• Replay 실측: p95 **0.021**<br>• WAV 직접: p95 **0.012** | 60개 구간 (마이크) + 6개 구간 (시나리오) | **PASS** | `logs/task_01_mic_10min_result.json` |
| **발화 후 결과 지연** | 기준 유성음 끝에서 결과 출력까지 p95 ≤ 1.5초 (1,500ms) | • 마이크 10분 실측: p95 **78.6 ms** (평균 52.3ms, 최대 94.2ms)<br>• Replay 실측: p95 **63.8 ms** | 60개 구간 (마이크) | **PASS** | `logs/task_01_mic_10min_result.json` |
| **연속 발화** | 최대 구간 4초 설정 시 구간 시작부터 결과까지 p95 ≤ 5.5초 (5,500ms) | p95 **6,059.1 ms** (6.06초)<br>(세그먼트 길이: 5.85s~5.97s + 추론 0.09s) | 6개 분할 구간 | **FAIL** | `logs/task_01_scenario_results.json` |
| **안정성** | 마이크 10분 동안 crash·캡처 overrun·무기록 폐기 없음, 큐 지연 지속 증가 없음 | • Crash: 0건<br>• Overrun: 0건<br>• Dropped: 0건 (0.00초)<br>• 큐 대기시간: p95 **0.09 ms** (최대 0.10ms)<br>• 메모리 RSS: **901.5 MB 불변** | 600.26초 연속 캡처 (60개 발화) | **PASS** | `logs/task_01_mic_10min_result.json` |
| **재현성** | 실행 절차·고정 버전·모델 manifest·원시 로그로 결과 재계산 가능 | `requirements.txt`, `task_01_model_manifest.md`, JSONL 로그 보존 | 전 항목 | **PASS** | `docs/reports/task_01_model_manifest.md` |
| **오프라인** | 모델 준비 이후 네트워크 차단 조건의 실행 증거 | Python `socket.connect` 차단 환경에서 STT 로컬 추론 exit code 0 정상 완료 (RTF 0.012) | 1회 테스트 | **PASS** | `logs/task_01_scenario_results.json` |

---

## 6. 실패 사례 분석 및 개선안

### 6.1 실패 사례 1: 시나리오 3 — 연속 발화 총 지연 기준 초과 (6.06초 > 5.50초)
- **조건**: 쉼(pause)이 0.2초로 `min_silence_duration`(0.5초)보다 짧은 34초 연속 발표 음성. `max_speech_duration`을 4.0초로 설정함.
- **측정값**: 세그먼트 길이가 5.85초 ~ 5.97초에 절단되었으며, 구간 시작부터 결과 출력까지의 지연이 p95 **6,059.1 ms (6.06초)**로 측정되어 초기 목표(5.5초 이하)를 0.56초 초과.
- **원인 분석 (C++ 소스코드 확인)**:
  - `sherpa-onnx`의 Silero VAD 구현체(`csrc/voice-activity-detector.cc`)를 확인한 결과, `max_speech_duration`은 하드 타임아웃(hard-cut) 방식이 아닙니다.
  - 버퍼 크기가 `max_utterance_length_`를 초과하면, 내부적으로 `new_min_silence_duration_s = 0.1s`, `new_threshold_ = 0.90`으로 음성 임계값을 일시적으로 올려 "발화 중 자연스러운 극소 무음(0.1초) 구간"을 찾아 자르는 **소프트 컷(soft-cut)** 알고리즘으로 동작합니다.
  - 따라서 발화자가 0.1초 이상의 틈도 전혀 없이 쉼 없이 계속 발화하는 경우, 버퍼가 5.8~6.0초까지 누적된 후 다음 단락에서 잘리게 됩니다.
- **개선안**:
  1. *파이프라인 레이어 강제 분할(Hard-cut slicing)*: VAD가 4.0초 이상 세그먼트를 묶어두는 경우, 상위 오디오 버퍼 레이어에서 물리적으로 4.0초 시점에 청크를 분할하여 VAD에 flush를 유도하거나 강제 슬라이스를 주입.
  2. *`max_speech_duration` 기본 파라미터 하향 조정*: 소프트 컷으로 인해 1~1.8초 추가 누적되는 여유를 고려하여 설정값을 `max_speech_duration = 3.0s` 또는 `3.2s`로 조정하면, 실제 분할 길이가 4.0~4.8초에 형성되어 총 지연이 4.9초 이내(p95 ≤ 5.5s)로 목표를 만족하게 됨.

### 6.2 실패 사례 2: 시나리오 4 — 음절 간 공백 토큰에 의한 Spaced CER 상승 (17.02% > 15%)
- **조건**: 동일 문장 반복 발화 ("조금만 생각을 하면서 살면 훨씬 편할 거야")
- **정답 (Reference)**: `조금만 생각을 하면서 살면 훨씬 편할 거야 조금만 생각을 하면서 살면 훨씬 편할 거야`
- **인식문 (Hypothesis)**: `조 금만 생각 을 하 면서 살 면 훨씬 편할 거야. 조 금만 생각 을 하 면서 살 면 훨씬 편할 거야.`
- **측정값**:
  - 공백 포함 정규화 CER: **17.02%** (거리 8 / 글자수 47)
  - 공백 제거 정규화 CER (Non-space CER): **0.00%** (완벽 일치)
- **원인 분석**:
  - SenseVoice 모델의 토크나이저(`tokens.txt`)가 한국어 음절을 조합할 때 `조`, `금만`, `생각`, `을`, `하`, `면서`, `살`, `면` 형태로 형태소/음절 단위 공백 토큰을 생성합니다.
  - 음성 인식 음향 모델 자체의 한국어 음소/어휘 인식은 100% 완벽히 정확하였으나, 공백을 포함한 CER 평가 방식에서는 불필요한 공백 삽입(Insertions) 8글자가 오류 거리로 누적되어 17.02%가 산출되었습니다.
- **개선안**:
  1. *한국어 평가 지표 명문화*: 음성인식 정확도 평가 시 형태소 공백 노이즈를 배제하는 **Non-space CER**을 병기하거나 주 지표로 채택.
  2. *한국어 띄어쓰기 교정기(Spacing Corrector)* 도입: Task 03 이후 텍스트 정제 파이프라인에서 한국어 띄어쓰기 규칙 또는 경량 교정기를 적용.

---

## 7. 실행하지 못한 항목과 사유 및 사용자 필요 절차

### 7.1 조용한 환경 실제 한국어 발표 문장 30개 CER 벤치마크
- **상태**: `NOT_RUN / PARTIAL`
- **정확한 사유**:
  - 저장소 내에 사용자가 직접 녹음한 30개 발표 문장 오디오 파일(`audio/eval_30/P01.wav` ~ `P30.wav`)이 부재합니다.
  - 지시서 4절 및 6절에 명시된 원칙("사람이 확인한 reference와 hypothesis를 같이 남긴다. 인식 결과를 정답으로 재사용하지 않는다. 녹음 확보가 불가능하면 측정 가능한 것만 실행하고 나머지를 NOT_RUN으로 남긴다. 코드가 실행된다는 이유만으로 위 성능 기준을 PASS 처리하지 않는다.")에 따라, 합성 음성이나 비공식 음성을 임의로 정답으로 둔갑시키지 않고 솔직하게 미실행(`NOT_RUN`)으로 남겼습니다.
- **구축 완료된 자산**:
  - 30개 문장(일반 문장 8개, 수치/단위 문장 8개, 기술 고유명사/영문 약어 8개, 빠른 말/문장 경계 6개) 데이터셋 구축 완료.
  - 원클릭 평가 스크립트 `scripts/evaluate_cer.py` 작성 완료.
- **사용자 수행 필요 절차**:
  1. 사용자가 조용한 장소에서 `audio/eval_30/` 디렉토리에 `P01.wav`부터 `P30.wav`까지 30개 문장을 마이크로 녹음하여 배치합니다. (문장 목록은 `scripts/evaluate_cer.py`의 `EVAL_DATASET` 참조)
  2. 다음 명령을 1회 실행하면 즉시 실제 CER 측정표가 `logs/cer_eval_30.json`에 자동 생성됩니다:
     ```bash
     .venv/bin/python3 scripts/evaluate_cer.py --audio-dir audio/eval_30
     ```

---

## 8. 보드와의 차이 및 다음 단계 전 해결해야 할 사항

1. **하드웨어 및 가속기 차이**:
   - 본 테스트는 **Apple M5 CPU (ARM64)**에서 실행되었습니다.
   - 대상 타깃 보드인 **QCS6490 (Kryo CPU + Hexagon NPU)**는 CPU 코어 수, 클록 주파수, 메모리 대역폭(LPDDR4x/LPDDR5), L3 캐시 구조가 노트북과 상이합니다.
   - 노트북 CPU에서 RTF가 0.029(여유도 17배)가 나왔다고 해서 QCS6490 CPU에서도 동일한 여유를 보장하지 않습니다. 특강3 자료에 따르면 QCS6490에서 SenseVoice INT8 CPU 실행 시 4 thread를 사용하므로, 보드 환경에서의 실제 RTF와 발열 쓰로틀링을 조기에 검증해야 합니다.
2. **CoreAudio vs ALSA / Android AudioRecord**:
   - macOS에서는 sounddevice(CoreAudio)를 통해 48kHz 입력을 받아 3:1 다운샘플링을 거쳤으며 10분간 오버런 0건을 달성했습니다.
   - 보드 Android/Linux 환경에서는 AudioRecord 또는 ALSA/TinyALSA 버퍼 크기와 쓰레드 스케줄링 우선순위(SCHED_FIFO/RR)를 적절히 설정하지 않으면 캡처 오버런이 발생할 수 있습니다.
3. **Task 02 진입 전 해결 권장사항**:
   - Silero VAD의 `max_speech_duration`을 3.0s로 조정하여 연속 발화 총 지연을 5.5s 이내로 안정화하는 설정값 승인 필요.
   - 사용자의 실제 30개 발표 녹음본 확보 여부 확인 및 PM의 Task 01 검수 승인.

---

## 9. 결론 및 PM 검수 대기

Task 01의 기술적 구현 범위(가상환경, 최소 의존성, Silero VAD 구간화, SenseVoice INT8 추론, Direct/Replay/Mic 모드, JSONL 구조화 로깅, 10분 마이크 안정성 실측, 오프라인 동작 증명)를 성공적으로 완료하였습니다.

지시서의 명시적 지침에 따라 **Task 02는 착수하지 않고, PM의 검수와 피드백을 대기합니다.**
