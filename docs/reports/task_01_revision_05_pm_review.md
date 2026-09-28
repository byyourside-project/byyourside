# Task 01 Revision 05 PM 검수

- 검수일: 2026-09-28
- 검수 대상: `e7e9dc3`
- 판정: **수정 요청 — 기존 streaming deadline 누락은 해결 확인, 전체 승인 보류**
- 역할: PM은 코드 열람·테스트·임시 재현을 수행하고 이 검수서와 다음 지시서만 작성했다. 애플리케이션 구현은 수정하지 않았다.

## 확인된 개선

요청 timeout과 worker join 설정이 분리됐고, replay/mic/direct/batch 호출부에 timeout이 전달된다. 부모는 요청 대기 중 deadline을 감시하며 timeout 시 자식을 회수한다. 기존 EOF 전 실패, 같은 부모 재실행, 정상 PID 재사용, warm-up 실패 테스트를 포함한 전체 테스트를 PM이 재실행했다.

- 명령: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- 결과: **43 tests, 83.985초, OK, exit 0**
- 콘솔 증거: `logs/pm_review_rev05_20260928/unittest.txt`
- 시나리오 전체 및 실제 마이크 장시간 시험은 이번 검수에서 재실행하지 않았다. 개발자의 시나리오 PASS와 PM의 재실행 결과를 구분한다.
- 기존 테스트는 고정 run_id 로그에 append한다. 이번 재실행에서도 기존 파일에 레코드가 추가될 수 있으므로 독립 콘솔 증거를 별도 보존했다.

## F1 [P1] 부모 요청 예산이 자식 기본 예산과 충돌

위치: `src/stt.py:123` (자식 호출), `src/stt.py:62` (별도 기본 예산 검사).

부모가 받은 timeout은 Pipe 요청에 포함되지 않는다. 자식 `SttEngine.transcribe()`는 `config.request_timeout_sec`를 기본값으로 사용하고 추론 완료 후 이를 초과하면 실패한다. 따라서 default 2초보다 긴 WAV/override 예산을 부모가 허용해도 순수 추론이 2초를 넘으면 정상 완료 결과가 오류로 바뀐다. 현재 지연 주입은 이 내부 타이머 밖에서 sleep하므로 기존 테스트는 충돌을 놓친다.

PM은 실제 spawn 자식과 실제 한국어 모델로 아래 축소 조건을 재현했다.

- 설정: `SttConfig(request_timeout_sec=0.001)` (유효한 양수).
- 호출: 공식 `ko.wav`를 `IsolatedSttEngine.transcribe(samples, sr, timeout=5.0)`로 전사.
- 결과: 약 **0.0573초**에 `RuntimeError: STT inference error: SttEngine inference timed out after 0.00s`.
- 해당 시점 자식 생존: True. 재현 종료 시 close로 정리했다.
- 재현 스크립트: `logs/pm_review_rev05_20260928/budget_probe.py`.

이는 기본 2초 조건에서 2초 이상 모델 실행을 실측했다는 뜻은 아니다. 요청 override가 자식에 반영되지 않는 결함을 작은 시간 단위로 확인한 것이다. warm-up 역시 호출별 override를 자식에 전달하지 않아 같은 정책 불일치가 있다. 부모를 강제 회수 주체로 유지하면서 예산을 일관되게 적용해야 한다.

## F2 [P2] 시간 분해와 보고서 목록이 실제 증거와 불일치

`tests/test_regression_rev5.py:213`에서 수집 시간 4.13초와 요청-실패 시간 2초는 상수다. 정리 시간은 총 시간에서 두 상수를 뺀 값이다. 따라서 총 소요 시간과 EOF 전 실패는 실측이지만, 세 단계 각각을 정밀 실측했다고 승인할 수 없다. 요청 발행·timeout 감지·회수 완료·상위 실패 수신 시각을 기록해야 한다.

또한 다음 표기를 정정한다.

- warm_up 반환 18.6ms는 추론 시간이며, 자식 spawn/모델 로딩을 포함한 재초기화 총 시간이 아니다.
- 왕복 시간에서 추론 시간을 뺀 값에는 IPC 외 스케줄링 등의 비용도 들어간다. 단일 샘플로 실시간 영향 없음을 일반화하지 않는다.
- 보고서 6.2의 test_audio_utils.py, test_metrics.py, test_regression_rev1.py, test_stt_poc.py는 현재 tests 디렉터리에 없다. 실제 discovery ID로 목록을 작성한다.
- `tests/run_scenarios.py`는 --rev5에서 요약 파일명만 바꾸고 일부 원시 로그에는 scenario*_rev3 ID를 재사용한다. revision/실행별 원시 증거가 섞이지 않도록 고유 ID를 사용한다.

## PM 결정

기존 구조를 다시 만들 필요는 없다. Revision 06은 F1과 F2만 해결하는 제한된 수정이다. 테스트가 43개 통과했다는 사실은 인정하지만 요청 override 정상 성공 조건이 빠졌으므로 전체 구현 승인을 대체하지 않는다.

Task 01 실사용 평가는 여전히 PARTIAL이다. 실제 30문장 CER, 실제 발화 600초 마이크, OS egress 차단, 사람 발화 종료 기준은 미검증 상태를 유지한다. Task 02는 이번 승인에 포함하지 않는다.

다음 지시서: `docs/pm/task_01_revision_06.md`.
