# Revision 02 PM 검수

- 대상 revision: c411020 / 검수일 2026-09-28
- 판정: CHANGES_REQUESTED. Task 02 진입 보류.
- 구현 수정 없음. 마이크 신규 녹음 없음. 실제 파일·로그, 테스트 재실행 및 별도 임시 재현을 확인했다.
- `.venv/bin/python3 -m unittest discover -s tests -p 'test_*.py'`: 23개 통과, exit 0, 25.832초. 검수 출력 `/tmp/byyourside-rev2-tests.txt`.

## 인정되는 개선

이전의 첫 flush 내 4512 samples 폐기는 해결됐다. 일반적인 VAD/STT 예외 전달과 지연 flush 회귀검증, 입력 gap offset 보정, callback 밖 resampling, 실제 발화 종료 기준 지연 PARTIAL 표기는 개선됐다. 30문장 NOT_RUN과 OS egress PARTIAL 유지도 적절하다. 다만 아래 재현 결과 때문에 '전체 무손실' 및 '완전한 worker 정리'는 승인할 수 없다.

## F1 [P1] 하드 컷 후 VAD 재초기화로 이어지는 음성 166ms 누락

위치: src/vad.py:114–116; tests/test_regression_rev2.py:55–77.

첫 두 조각은 보존되지만 그 직후 VAD를 새로 만들어 연속 발화를 다시 탐지한다. 기존 fixture를 실제 VAD로 실행한 결과:

- hard=4초: [2144,66144), [66144,70656), [73312,92672).
- hard 없음(soft limit 60초): [2144,92672) 한 구간.
- **[70656,73312)의 2656 samples = 166ms 누락**. 해당 원본 구간 RMS 0.05591로 0으로 채운 구간도 아니다.

기존 테스트는 seg1+seg2만 원본과 비교하여 seg2→seg3 사이 손실을 검사하지 않는다. 이 상태에서 282ms 조각이 '.'로 인식됐다는 사실만으로 이후 단어 누락 전체를 STT 특성으로 귀속할 수 없다.

수정: VAD의 발화 지속 상태와 ASR용 분할 버퍼를 분리하거나 동등한 방법으로, 인위적인 하드 컷 때문에 진행 중인 음성이 다시 탐지 단계에서 제외되지 않도록 한다. 전체 발화 구간을 합쳐 원본 sample coverage·파형을 비교한다. 특정 4512 샘플 수에 고정된 테스트 대신 여러 절단 위치·3회 이상 연속 절단도 검사한다. 짧은 continuation을 독립 추론하지 않고 다음 구간과 병합하는 방안도 평가한다.

## F2 [P1] timeout 후 worker가 살아 있는데 logger를 닫고 반환

위치: src/pipeline.py:606–613, 662–663; run_mic에도 유사 구조.

예외가 즉시 발생하는 경우는 개선됐지만 추론이 오래 멈춘 경우는 해결되지 않았다. STT stub을 Event.wait로 정지시키고 replay를 실행하면 **2.05초 뒤 TimeoutError 반환 시 stt_worker 1개가 살아 있음**을 확인했다. 바깥 finally는 이미 logger.close를 실행한다. 검수에서는 gate를 풀고 thread join하여 잔여 worker를 정리했다.

수정: timeout을 실제 취소/정리로 연결한다. Python thread의 native 추론을 강제로 중단할 수 없다는 점을 반영해 협력적 취소, 완료 대기, 필요한 경우 별도 프로세스 중 실제 요구에 맞는 정책을 선택한다. worker가 살아 있는 동안 모델/로그를 재사용하거나 완료됐다고 보고하지 않는다. deadline 경과와 마이크 시작 실패·KeyboardInterrupt 경로도 검증한다. 테스트는 예외 발생만이 아니라 정리 후 worker 수와 닫힌 logger 접근 여부를 assert해야 한다.

## F3 [P2] 큐 지연 추세가 없는 필드의 기본값 0으로 계산됨

위치: tests/test_mic_10min.py의 queue_waits 계산; src/pipeline.py의 segment_results 저장.

`s.get('queue_wait_ms', 0.0)`을 사용하지만 segment_results에는 해당 키가 없다. 새 마이크 요약의 18개 detected_segments 모두 그 키가 없음을 확인했다. 따라서 trend_drift는 실제 밀림과 무관하게 항상 0이다.

수정: 실제 per-segment queue_wait_ms를 저장하고 누락 시 검증 실패/NOT_RUN으로 처리한다. 증가하는 queue wait 사례가 실패하는 테스트를 추가한다. 단순 첫 값과 마지막 값의 차이만 쓸 경우 그 제한도 명시한다.

## F4 [P2] 60초 시험을 10분 안정성 검증과 구분해야 함

`logs/task_01_mic_10min_rev2_result.json`의 target_duration_seconds=60, total_audio_captured_seconds=60.06, passed=true다. 보고서 본문은 60초라고 밝혔으나 제목·파일명·PASS는 10분 요구사항과 혼동된다.

수정: 60초 smoke test로 표시하고 600초 기준은 NOT_RUN/PARTIAL로 남긴다. benchmark는 요청된 duration뿐 아니라 기준 duration 충족 여부를 판정해야 한다. 위 결함 수정 전 재녹음은 불필요하다.

## 잔여 검증 범위

- 마이크 ADC clock 정렬은 아직 구현되지 않았으며 InputStream 시작 전 perf_counter를 원점으로 쓴다. 지연은 계속 추정치로 한정한다.
- 보고서에 실제 저장소와 다른 테스트 이름/파일명(test_sherpa_onnx.py 등)이 나온다. 실제 명령·테스트 ID·로그로 다시 작성한다.
- 23개 테스트 통과는 확인했으나 전체 무손실, 오류 경로 정리, 10분 안정성 완료까지 증명하지 않는다.

다음 작업: docs/pm/task_01_revision_03.md. 이번에는 F1–F4를 집중 수정하고, 짧은 재현이 모두 통과한 후 장시간 시험을 수행한다.
