# Task 01 Revision 06 — timeout 예산 충돌 및 검증 증거 정정

먼저 `docs/reports/task_01_revision_05_pm_review.md`를 읽는다. PM은 구현을 수정하지 않았다. 기존 프로세스 격리와 EOF 전 회수 구조는 유지한다. 이번 범위는 아래 두 항목이며 Task 02는 시작하지 않는다.

## 1. 요청별 timeout의 단일 기준 확립

현재 부모 `IsolatedSttEngine`의 timeout은 IPC에 포함되지 않는다. 자식 `SttEngine.transcribe()`는 기본 `config.request_timeout_sec`를 별도로 적용하므로 긴 WAV/명시적 override가 충돌한다. 부모 5초, 기본 0.001초로 정상 한국어 파일을 전사하면 약 57ms에 RuntimeError가 발생한다.

- 부모가 결정한 요청 예산을 일관되게 적용한다. 부모가 강제 회수를 책임지는 구조를 유지하며, 자식에 별도의 더 짧은 기본 예산이 숨어 있지 않도록 한다. 구현 방식은 개발자가 선택하되 in-process API의 동작과 보장 범위를 명시한다.
- streaming override, direct/batch의 길이 기반 예산, warm-up override를 모두 확인한다. warm-up도 현재 IPC에 override가 전달되지 않는 같은 구조다.
- timeout 발생 시 요청 ID/예산/오류 종류를 보존한다. 자식의 TimeoutError가 문자열 RuntimeError로 바뀌는 경우도 정책을 명확히 한다.
- `transcribe()` 내부 TypeError를 잡아 timeout 없이 다시 호출하는 광범위 fallback은 제거하거나 명시적 어댑터로 제한한다. 실제 추론 오류를 인자 호환성 문제로 간주해 재호출하지 않는다.

### 필요한 회귀 증거

1. 실제 spawn 자식에서 기본 예산보다 오래 걸리지만 override 안에 완료되는 정상 추론이 성공한다. 짧은 양수 기본값을 이용한 축소 재현도 허용한다. 현재 지연 주입은 자식 엔진 내부 타이머 밖에 있으므로 그것만으로 이 결함이 검증되지는 않는다.
2. 기본값보다 짧은 override를 초과하면 명시적 timeout, 회수, 동일 부모 재실행을 확인한다.
3. WAV direct/batch의 자동 산출 예산 및 warm-up override를 확인한다. 각 API를 호출만 했다는 사실 대신 실제 적용 예산을 검증한다.
4. 기존 EOF 전 실패와 정상 PID 재사용 회귀는 유지한다. 전면 구조 개편은 하지 않는다.

## 2. 계측과 보고서의 증거 일치

- `collection_time=4.13`, `request_to_fail=2.0`은 측정값이 아니다. 요청 발행, timeout 감지(회수 전), 자식 회수 완료, 호출자 실패 수신 시각을 동일 부모 monotonic clock에서 기록한다. 총 시간과 각 차이를 원시 이벤트로 재계산할 수 있게 한다. 시작/초기화의 포함 범위도 적는다.
- 정확한 계측이 없는 과거 표는 추정으로 정정한다. 총 시간에서 고정값을 뺀 잔차를 정리 시간 실측으로 부르지 않는다.
- warm_up 반환값은 추론 시간이다. 18.6ms를 자식 재생성/모델 로딩까지 포함한 재초기화 시간으로 쓰지 않는다. 필요하면 전체 호출을 별도 계측한다.
- `parent_roundtrip_ms - child_infer_ms`는 IPC·직렬화·스케줄링 등 비추론 왕복 오버헤드 추정이다. 단일 샘플로 실시간성에 영향 없다고 일반화하지 않는다.
- 보고서의 테스트 목록은 실제 discovery 결과에서 작성한다. 존재하지 않는 test_audio_utils.py, test_metrics.py, test_regression_rev1.py, test_stt_poc.py를 나열하지 않는다.
- 실행별 고유 run_id 또는 고유 로그 디렉터리를 사용한다. 시나리오 실행기의 --rev5가 결과 JSON 이름만 바꾸고 원시 JSONL에는 scenario*_rev3 ID를 재사용하는 문제도 정정한다. 과거 로그는 삭제하거나 다시 생성하지 않는다.

## 실행 및 제출

짧은 신규 회귀 → 전체 테스트 1회 → 관련 시나리오 순으로 실행한다. 테스트 실행기는 외부의 유한 시간 제한으로 감시하고 hang 시 자식까지 회수한다. 반복 실행은 실패나 추가 변경이 있을 때만 한다.

`docs/reports/task_01_revision_06_report.md`에 커밋, 명령, 실제 test ID, exit code, 고유 원시 로그, 위 항목별 통과/미실행 근거를 기록한다. 과거 Rev05 보고서에는 정정 안내를 추가한다. 모든 수치는 실측·설정·추정을 구분한다.

30문장 CER, 실제 발화 600초, OS egress, 사람 발화 종료 annotation은 자료가 없으면 NOT_RUN/PARTIAL 유지한다. 이번에 새 기능이나 장시간 마이크 시험을 추가하지 않는다. 두 항목 완료 후 PM 검수를 기다린다.
