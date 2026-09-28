# Task 01 Revision 01 재검수

- 대상: `a1b4c20`, 2026-09-28
- 판정: **CHANGES_REQUESTED — Task 02 진입 보류**
- 구현 수정 없음. 기존 원시 로그를 읽고 테스트와 별도 임시 재현을 수행했다. 이번 검수에서 새 마이크 녹음은 하지 않았다.

## 인정되는 개선

- 기존 테스트 및 회귀 테스트 15개 재실행 통과: 14.716초. 출력은 `/tmp/byyourside-rev1-tests.txt`.
- PCM 정규화 순서 수정, VAD 우회/적용 모드 분리, 오프라인 시험 PARTIAL 정정은 적절하다.
- 마이크 장시간 시험이 공통 run_mic을 사용하고 current/peak RSS를 분리한다.
- 정상 종료의 sentinel 전달과 큐 손실 집계는 이전보다 개선되었다. 다만 오류·과부하 경로는 아래 문제가 남아 있다.
- 30문장 실제 발화 NOT_RUN 유지도 적절하다.

## A [P1] 하드 컷이 초과 오디오를 폐기함

위치: `src/vad.py:107–112`.

flush로 받은 구간을 pop한 뒤 `seg_samples[:max_allowed_samples]`로 자르고 남은 샘플은 보존하지 않는다. 이는 비스트리밍 모델의 인식 오류가 아니라 파이프라인의 입력 데이터 손실이다. lossless 판정에는 반영되지 않는다.

기존 `logs/test_fixtures/temp_forced_cutoff.wav`에 실제 VAD를 실행하고 `_pop_segment` 전후 샘플 수를 관찰했다.

- 입력: 92,480 samples.
- 첫 flush의 원본: start 2,144, length 68,512 samples.
- 반환: length 64,000 samples. **4,512 samples = 282ms 폐기**.
- 반환 구간: [2,144, 66,144), 다음 구간 [70,656, 92,672).
- 기존 시나리오4 JSONL도 134–4134ms, 4416–5792ms로 같은 282ms 간격을 보인다.

따라서 '살' 누락을 모든 simulated streaming에서 필연적인 현상으로 결론내릴 수 없다. 또한 절단 위치는 입력 시작 기준 정확히 4.000초가 아니라 첫 구간 끝 4.134초다. 음절별 위치 annotation 없이 어느 음절의 한가운데인지 확정할 수도 없다.

요구: 초과분을 다음 구간으로 이월하는 방식으로 샘플 보존부터 보장한다. 길이 상한만 검사하지 말고 강제 분할 전후의 원본 sample index와 파형 연결을 검증한다. VAD가 의도적으로 제외한 무음과 hard-cut 폐기를 구분한다. 모델의 경계 인식 품질은 이 버그를 고친 후 별도로 측정한다. LLM 보정으로 덮지 않는다.

## B [P1] worker 실패 시 sentinel put에서 무기한 정지

위치: `src/pipeline.py:519` 및 `832`; segment sentinel 전달 `384/387/715/718`도 검토 필요.

VAD worker가 실패해 입력을 소비하지 않으면 audio_queue에 남은 항목 때문에 메인 스레드의 `put(SENTINEL)`이 영원히 대기한다. 뒤에 있는 join timeout까지 도달하지 않는다.

재현: 실제 모델 없이 VAD.process_chunk에 ValueError 주입, 입력 큐 크기 1, put_timeout 0.001, replay 10000배속. 자식 프로세스가 3초 제한을 넘겨 강제 종료됐다. faulthandler stack은 `queue.py:140 put` → `pipeline.py:519`를 가리킨다.

요구: 공유 취소/오류 신호와 제한시간이 있는 종료 절차를 구현하고 모든 blocking put/get을 오류 상황에 해제한다. 예외 전달, worker 정리, logger close를 finally 경로로 보장한다. 마이크 시작 실패·중단도 다룬다. 단순히 join timeout을 늘리지 않는다.

## C [P1] 입력 drop 이후 오디오 시간축이 압축됨

위치: `src/pipeline.py:348–349, 681–682`.

AudioChunk에는 원래 stream_sample_idx_start/end가 있지만 VAD에는 samples만 전달한다. 입력 큐에서 빠진 청크는 VAD의 내부 sample counter에 반영되지 않는다. 따라서 손실 이후 seg.start/end가 원본보다 앞당겨져 서로 떨어진 음성이 붙고, 지연 수치와 구간 위치가 잘못된다.

요구: gap을 감지해 이전 구간을 종료하고 원래 시간축의 offset을 유지하거나, 그에 준하는 명시적 전략을 적용한다. drop 전후의 sample mapping과 gap crossing 여부를 회귀 테스트로 확인한다. 단순히 is_lossless=False만 반환해서는 시간축 문제가 해결되지 않는다.

## D [P2] 지연 수치를 실제 발화 기준 PASS로 승인할 수 없음

위치: `src/pipeline.py:630, 745–751` 및 보고서 지연 결과 표.

종료 무음을 포함하도록 바꾼 방향은 맞다. 그러나 seg.end_sample은 VAD가 추정한 경계이지 사람이 확인한 마지막 유성음이 아니다. 마이크 시계 원점도 InputStream 시작 전 perf_counter이며 callback의 ADC 시각과 정렬하지 않는다. 따라서 630.7ms는 현 구현의 추정치이고 정확한 기준 유성음 종료 지연 PASS 근거는 아니다.

요구: VAD 기반 추정치와 annotation 기반 기준 지표를 분리한다. ADC/callback 시계와 monotonic clock 매핑을 명시한다. replay는 청크를 공급한 다음 기다려 약 한 청크를 앞서 공급하므로 입력 가용 시각도 정리한다. 기준 annotation이 없으면 실제 발화 종료 기준 검수 항목은 PARTIAL로 둔다.

## 회귀 테스트 보완

- `test_r2_loss_tracking_under_overload`는 status가 None이 아닌지, dropped_items가 list인지 확인할 뿐 실제 drop, 손실 길이, DROPPED, is_lossless=False를 검증하지 않는다.
- `test_r3_flush_delay_worker_lifetime`는 이름과 달리 flush 지연을 주입하지 않는다.
- R7 테스트는 각 구간 길이만 검사해 잘라 버리는 구현도 통과한다.
- VAD 오류+가득 찬 입력 큐, STT 오류+가득 찬 구간 큐, timeout 후 worker 정리를 검증해야 한다.

## 보고서 표현 정정

- 4초 구간 길이 보장은 오디오 보존 또는 실제 4초 시점 전달 보장이 아니다.
- '최소 1음절 손실이 필연적', 'QCS6490 RTF 0.2–0.3 예상'은 해당 근거가 없으므로 삭제하거나 미검증 가설로 표시한다.
- 마이크 callback에 resample_poly를 옮겼다. 캡처 callback은 수집·큐 전달로 제한하고 resampling은 worker에서 수행한다.
- 장시간 시험의 PASS 식에는 큐 지연 추세 검사가 없다. 추세 미검증을 '누적 0'으로 단정하지 않는다. 발화가 0개인 경우 성능 검증은 NOT_RUN이어야 한다.
- config/hash/revision/clock 원점을 run_start에 충분히 남기고 고정 run_id append로 측정이 섞이지 않도록 한다.

다음 작업은 `docs/pm/task_01_revision_02.md`의 작은 수정·회귀검증부터 진행한다. 이 단계가 끝나기 전에 10분 마이크 시험을 반복할 필요는 없다.
