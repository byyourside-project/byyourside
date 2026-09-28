# Task 01 Revision 05 — 요청별 STT deadline 연결

먼저 `docs/reports/task_01_revision_04_pm_review.md`를 읽는다. 프로세스 격리·회수와 같은 부모 재실행은 확인됐다. 구조를 다시 전면 작성하지 말고, 실제 호출부의 deadline 누락에 집중한다. Task 02는 시작하지 않는다.

## 수정 범위

1. STT 요청 deadline과 종료 join timeout을 별도 설정으로 둔다. streaming 구간은 초기 기본 2초를 사용할 수 있으나 설정값과 적용 범위를 문서화한다.
2. `run_replay`/`run_mic`의 transcribe 호출에 deadline을 연결한다. 아직 오디오 입력이 계속되고 있어도 timeout 즉시 실패를 전파하고 입력·worker·child·IPC·logger를 정리한다.
3. WAV direct/batch에도 명시적인 유한 요청 timeout 정책을 적용한다. 길이가 긴 파일은 streaming 구간과 다른 예산을 허용하되 무제한 대기를 기본 성공 경로로 남기지 않는다.
4. warm_up timeout 시 0.0 반환 대신 명시적 실패와 자식 정리를 수행한다. 이후 같은 부모에서 정상 시작이 가능해야 한다.
5. timeout 예외가 다른 RuntimeError로 감싸지면 원인과 요청 ID, deadline 초과를 보고서/로그에서 식별할 수 있게 한다.

## 핵심 회귀 테스트

- 실제 자식 프로세스에 취소 신호를 확인하지 않는 지연을 주입한다. 1배속으로 입력이 계속되는 동안 deadline을 넘기게 하고 EOF 전에 timeout·회수가 일어나는지 assert한다.
- 검수 재현은 ko.wav + 뒤쪽 무음 4초, 지연 3초, 요청 예산 2초다. 입력 종료 시간을 측정해 종료 join에 의존한 통과를 방지한다.
- 요청 발행부터 실패까지의 시간, 초기화/발화 수집 시간, 정리 시간을 분리한다.
- mock 마이크로 동일 동작을 검증한다. 실제 마이크 성능으로 표기하지 않는다.
- WAV direct/batch, warm_up timeout도 확인한다.
- 모든 timeout 이후 worker/child 정리와 같은 부모에서 정상 재실행을 검증한다.
- 예산 안에서 완료되는 정상 요청 및 연속 요청의 PID 재사용을 확인한다.
- 기존 테스트를 재실행한다. 새로운 테스트 실행기에도 외부 deadline을 둬 hang을 회수한다.

## 보고

`docs/reports/task_01_revision_05_report.md`에 실제 명령·test ID·exit code, 요청별 시간 예산, EOF 전 실패 증거, 자원 정리, 같은 부모 재실행, 고유 run_id 원시 로그를 기록한다. 기존 로그 보존.

IPC overhead 수치는 parent round-trip과 child inference를 구분해 측정한 경우에만 단정한다. 메모리는 부모와 자식을 구분하고 현재 부모 RSS를 전체 모델 메모리로 보고하지 않는다. Python socket monkeypatch는 spawn 자식에 자동 적용되지 않으므로 부분 검증 범위를 정확히 적는다.

30문장 CER, 실제 발화 600초, OS egress, reference annotation은 미확보 시 그대로 NOT_RUN/PARTIAL이다. 위 짧은 요청 timeout 검증 전 장시간 마이크 시험을 반복하지 않는다. 완료 후 PM 검수를 기다린다.
