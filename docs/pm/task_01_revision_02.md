# Task 01 Revision 02 — 샘플 보존 및 오류 종료 보완

`docs/reports/task_01_revision_01_pm_review.md`를 먼저 읽는다. Task 02는 진행하지 않는다. 사용자 변경과 기존 로그를 보존한다.

## 우선 수정

1. **하드 컷 샘플 보존(A)**: 초과 샘플을 버리지 말고 다음 구간에 이월한다. VAD 원본 구간→출력 구간의 sample mapping을 유지한다. 시간 상한과 샘플 보존을 함께 검증한다. 무음 제거는 별도 정책으로 구분한다.
2. **오류 종료(B)**: VAD/STT 예외가 발생하면 producer와 모든 worker에 취소를 전달한다. sentinel put을 포함한 모든 blocking 경로에 제한시간/취소 처리를 둔다. 실패 시 프로세스가 제한시간 내 종료되고 worker와 logger가 정리되어야 한다.
3. **drop 이후 시간축(C)**: 원본 AudioChunk sample index의 gap을 감지한다. 비연속 음성을 몰래 연결하지 말고 이전 구간 종료/offset 보존 등 일관된 전략을 적용한다. 구간/청크 손실 원시 기록을 남긴다.
4. **지연 지표(D)**: VAD 추정치와 reference annotation 기반 지표를 분리한다. 마이크 ADC 시각과 monotonic clock을 매핑하고, replay 입력이 해당 sample 가용 시각보다 앞서 공급되지 않도록 한다. annotation 부재는 정확한 발화 종료 지연 PARTIAL로 남긴다.
5. resampling을 마이크 callback에서 worker로 옮긴다. 정상 종료뿐 아니라 마이크 시작 실패와 사용자 중단도 정리한다.

## 반드시 실패를 재현한 뒤 통과시킬 테스트

- 기존 forced-cutoff fixture에서 282ms가 폐기되는 사례. 수정 후 hard-cut 전후 샘플 연결에 누락/비의도적 중복이 없어야 한다. 길이만 비교하는 테스트는 불충분하다.
- 큐 크기 1 + VAD 첫 청크 예외. 정해진 제한시간 내 오류 종료, hang 없음.
- 큐 크기 1 + STT 예외, 느린 worker/timeout. 종료 중 추가 hang 또는 살아 있는 worker 없음.
- flush에 실제 0.3초 이상 지연을 주입하고 마지막 구간이 정확히 한 번 인식되는지 확인.
- 입력 큐/구간 큐 과부하를 각각 확실히 유발하고 drop 수·sample 범위·길이·DROPPED·is_lossless=False를 assert.
- 원래 sample index에 인위적 gap을 넣어 gap 이후 구간 시각이 압축되지 않는지 확인.
- 입력 시작 지연 및 reference 발화 끝이 알려진 fixture로 시계 매핑과 종료 대기 포함 여부 검증. synthetic/stub 검증을 실제 한국어 발표 지연 평가로 취급하지 않는다.
- 기존 15개 테스트를 계속 실행하되 느슨한 assertion은 강화한다.

## 보고와 실행 순서

먼저 짧은 결정적 회귀검증을 완료한 뒤 기존 음성으로 지연·경계 CER을 재측정한다. 위 결함이 남아 있으면 10분 마이크 시험을 반복하지 않는다. 결함이 해결된 뒤 마이크 시험을 실행할 수 있으며 접근 불가/실제 발화 부재는 NOT_RUN으로 둔다.

경계 누락을 비스트리밍 모델의 필연적 음절 손실이라고 단정하지 않는다. 샘플 보존을 먼저 확인하고 그 다음 STT 인식 오류를 분석한다. LLM을 사용해 오디오 누락을 보정하지 않는다.

사용자 30문장과 OS egress 차단 미실행은 기존대로 NOT_RUN/PARTIAL 유지한다. 무근거 보드 RTF 예측은 제거한다. current RSS 변화와 큐 추세에 대해 실제 확인한 범위만 쓴다.

산출물:
- 수정 코드와 회귀 테스트.
- `docs/reports/task_01_revision_02_report.md`: A–D별 수정 위치, 수정 전 재현/수정 후 결과, 명령·exit code, raw log, 남은 제한.
- 고유 run_id의 새 로그. revision/config/model hash/clock 원점을 기록하고 이전 로그와 섞지 않는다.
- 이전 보고서는 보존하고 정정 링크를 추가한다.

최종 승인은 PM이 한다. 완료 후 Task 02로 넘어가지 말고 검수를 기다린다.
