# Task 01 PM 검수 결과

- 검수일: 2026-09-28
- 대상 revision: `ac9515a` (검수 시작 시 tracked 변경 없음)
- 판정: **CHANGES_REQUESTED — 승인 보류 / Task 02 진행 보류**
- 검수 범위: 지시서, 구현, 기존 보고서·JSONL·JSON, 모델 해시, 테스트 재실행, 분리된 재현 실험.
- 구현 코드는 수정하지 않았다. 이번 검수에서 마이크 10분 녹음이나 OS 네트워크 차단 시험을 새로 수행하지 않았다.

## 확인된 성과

- `.venv/bin/python3 -m unittest discover tests -v`: 8개 통과, exit 0, 3.722초. 공식 WAV 인식 및 replay 실행을 재확인했다.
- VAD/STT/tokens 세 파일의 크기와 SHA-256이 manifest와 일치한다.
- 기존 10분 JSONL에는 60개의 segment_result가 있다. 반올림된 원시 로그에서 지연 p95 78.5965ms, RTF p95 0.05022로 기존 요약과 대체로 일치한다. 숫자 집계의 일치와 측정 정의의 적합성은 별개다.
- 30개 평가 음성이 없고 `cer_eval_30.json`에 30개 NOT_RUN으로 기록한 것은 적절하다.
- STT CPU 실행과 모듈 분리는 후속 작업의 출발점으로 재사용할 수 있다.

## 수정이 필요한 사항

### R1 [P1] 발화 후 지연의 기준점이 잘못되어 PASS 근거로 사용할 수 없음

위치: `src/pipeline.py:265–278, 309–312, 497–513, 544–545`, `tests/test_mic_10min.py:106–116, 140–141`.

구간이 확정된 시점에 처리 중인 청크의 capture timestamp를 그 구간의 실제 발화 끝으로 사용한다. 이 청크에는 종료 판정을 위한 무음이 이미 포함될 수 있다. flush 경로는 아예 현재 시각으로 기준점을 새로 만든다. 따라서 보고서의 78.6ms는 실제 마지막 유성음 이후의 지연이 아니며, VAD 종료 대기를 누락한다. sample_idx_end를 보관하지만 실제 시계 정렬에는 사용하지 않는다.

또 `tests/run_scenarios.py:154–156`의 연속 발화 지연은 duration + inference만 더해 VAD 확정 대기와 큐 대기를 제외한다. 6.06초는 해당 계산식 결과이며 정확한 end-to-end 실측으로 사용할 수 없다.

요구: 오디오 sample clock과 monotonic clock을 연결하고 reference annotation이 있는 경우에만 true post-speech latency를 계산한다. annotation이 없으면 VAD 기준 추정치로 구분한다. 연속 발화는 실제 구간 시작 시각부터 result emit까지 계산한다. WAV direct와 배속 replay를 실시간 지연 평가에서 제외한다.

### R2 [P1] 큐가 가득 차면 발화 구간을 기록 없이 폐기

위치: `src/pipeline.py:268–272, 499–503`, `tests/test_mic_10min.py:108–111`.

segment_queue의 put이 0.5초 내 성공하지 않으면 `except queue.Full: pass`로 구간을 버린다. dropped_audio_chunks는 입력 큐 손실만 집계하므로 이때도 손실 0으로 보고할 수 있다. LLM/TTS 동시 부하나 느린 보드에서 실제 발언이 누락될 수 있다.

요구: 입력 청크와 STT 구간의 손실을 각각 ID·오디오 구간·길이·사유와 함께 기록하고, 유실이 있으면 무손실 판정을 실패시킨다. 처리 지연 주입과 작은 큐로 검증한다. 입력 손실 후 원래 오디오 시간축도 유지해야 한다.

### R3 [P1] 종료 경쟁 조건으로 마지막 발화 유실, worker 오류도 성공으로 보일 수 있음

위치: `src/pipeline.py:289–293, 388–392, 524–528, 601–604`; 마이크 시험에도 같은 구조 존재.

STT worker는 stop_event가 설정되고 audio_queue가 비면 종료한다. 하지만 그 시점에 VAD가 마지막 청크를 처리하거나 flush 중일 수 있다. segment_queue의 종료 sentinel보다 먼저 STT가 종료하여 마지막 결과를 버린다. join timeout 후 worker 생존 여부와 예외도 검사하지 않는다.

재현: 모델을 쓰지 않는 임시 stub으로 VAD.flush가 0.3초 후 1구간을 내도록 하고 replay를 실행했다. **VAD가 1구간 생성했으나 recognized_segments=0**으로 정상 반환했다. 실험 파일은 임시 디렉터리에서만 생성했다.

요구: producer 종료 → VAD 잔여 처리/flush → STT 잔여 처리 순서를 sentinel로 보장한다. worker 예외와 join timeout을 메인 실행의 실패로 전파하고, 아직 실행 중인 worker가 있는 상태에서 logger를 닫거나 성공을 반환하지 않는다.

### R4 [P2] 스테레오 정수 PCM WAV의 정규화 누락

위치: `src/pipeline.py:68–76, 213–220`.

채널 평균을 먼저 계산하면 int16/int32가 float64로 바뀌어 뒤의 정수 dtype 분기를 건너뛴다. WAV/replay가 정상 음량의 수만 배인 값을 모델에 전달한다.

재현: 양 채널 값이 16384인 int16 WAV를 fake STT로 검사했다. 기대 peak는 0.5인데 실제 peak는 **16384.0**이었다.

요구: 원본 PCM dtype을 기준으로 먼저 정규화한 뒤 채널을 합친다. mono/stereo int16·int32·float 입력을 검증하고 지원하지 않는 형식은 명시적으로 거부한다.

### R5 [P2] 오프라인 PASS가 실제 외부 네트워크 차단을 입증하지 않음

위치: `tests/run_scenarios.py:225–235`, 기존 보고서 5절.

Python socket.connect 하나만 교체한 시험은 native 라이브러리, 다른 연결 API, 자식 프로세스 등의 외부 통신을 차단하지 않는다. 로컬 모델 추론 smoke test로는 유용하지만 지시서의 네트워크 차단 검증과는 다르다.

요구: 현재 시험을 Python connect 차단 smoke test로 명명하고, OS/격리 환경에서 egress 차단 검증 전에는 완전 오프라인 항목을 PARTIAL/NOT_RUN으로 표시한다. 사용자 네트워크를 임의로 끊지 않는다.

### R6 [P2] 10분 시험이 실제 사용 경로를 검증하지 않고 PASS 조건도 불충분

위치: `tests/test_mic_10min.py` 전체, 특히 217–229; `src/metrics.py:117–126`.

10분 시험은 SpeechPipeline.run_mic을 호출하지 않고 별도 캡처/VAD/STT 파이프라인을 복제했다. 한쪽만 수정하면 검증 대상과 앱 동작이 달라진다. PASS 조건은 overrun과 입력 drop만 확인하여 worker 실패·구간 손실·지속적 밀림을 놓친다.

메모리는 현재 RSS가 아닌 ru_maxrss(프로세스 시작 이후 최고치)이다. 최고치 901.5MB가 일정하다는 사실로 현재 RSS 불변이나 누수 없음까지 결론낼 수 없다.

요구: 공통 파이프라인으로 시험하고 worker 오류/종료/손실/큐 지연 조건을 판정한다. 현재 RSS와 peak RSS를 구분한다. 수정 후에만 10분 시험을 재실행하고 이전 로그는 보존한다.

### R7 [P2] 경계 손실 시험이 실제 강제 분할 경계를 시험하지 않음

위치: `tests/run_scenarios.py:175–213`, `logs/stt_run_scenario4_boundary.jsonl`.

기존 공식 WAV를 두 번 잇는 방식은 파일 안의 무음도 유지한다. 저장된 로그의 두 구간은 806–3692ms, 5414–8268ms이며 둘 다 endpoint_reason=silence다. 단어 중간의 강제 절단을 확인한 근거가 아니다. 따라서 공백 제거 CER 0%만으로 강제 분할에서 손실이 없다고 볼 수 없다.

요구: 사람이 확인한 단어 경계와 실제 강제 분할 위치를 포함한 fixture로 누락·중복을 평가한다. 종료 무음보다 짧은 쉼과 장시간 발화를 별도 시험한다. max_speech_duration을 3초로 내리면 반드시 통과한다는 보고서의 문장은 미측정 가설로 정정한다.

## 추가 보완

- 구간별 resample_poly 호출은 필터 상태를 매번 초기화한다. 연속 resampling 또는 overlap/state 관리로 청크 경계 파형 차이를 검증한다. 현재 STT 품질에 미친 영향은 미측정이다.
- `scripts/evaluate_cer.py`는 문장별 값만 저장한다. 합산 CER = 합산 편집거리 / 합산 reference 길이를 추가하고 공백 포함/제외를 구분한다. 수치·단위·약어 보존 평가는 별도 필요하다.
- WAV direct의 VAD 결과가 없으면 전체 파일을 강제 decode하는 fallback이 있다. VAD가 무음을 거른 경우와 VAD를 우회하는 순수 인식 모드를 구분해야 한다.
- 전체 입력 길이(무음 포함)를 분모로 쓰는 cumulative_rtf와 처리한 발화 길이를 분모로 쓰는 RTF를 구분한다. warm-up은 본 파이프라인에서 일관되게 적용되지 않는다.
- run_start에 실제 config/model hash/revision, 입력 식별자, warm-up 규칙을 남긴다. 고정 run_id로 append하면 재실행 로그가 섞인다.
- endpoint_reason은 길이만으로 추정 중이다. 실제 이벤트와 추정 사유를 구분한다.

## 기준별 PM 판정

| 항목 | 판정 |
|---|---|
| 로컬 모델 실행·기본 테스트 | 확인됨 |
| 30개 실제 발표 CER | NOT_RUN 유지 |
| 처리 속도 | 기존 로그상 빠름. warm 기준과 분모 정리 필요 |
| 발화 후 지연 | 기존 PASS 철회, 측정 수정 필요 |
| 연속 발화 | 미달 상태 유지, 정확한 지연 측정·분할 정책 검증 필요 |
| 무손실/안정성 | 기존 10분 실행 기록은 있음. 누락 검출·종료 오류 때문에 승인 보류 |
| 오프라인 | 부분 smoke test만 확인, 완전 차단 검증 미완료 |

재검수 작업은 `docs/pm/task_01_revision_01.md`를 따른다. 녹음 부재 자체를 구현 결함으로 취급하지 않는다. 먼저 개발자 혼자 수정·검증할 수 있는 문제부터 해결한다.
