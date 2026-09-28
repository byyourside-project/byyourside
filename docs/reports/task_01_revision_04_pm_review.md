# Revision 04 PM 검수

- 대상: b28e718 / 2026-09-28
- 판정: **CHANGES_REQUESTED — 프로세스 격리·회수는 확인, 요청별 deadline 연결 필요**.
- 구현 코드 수정 및 신규 마이크 녹음 없음.
- 전체 테스트 재실행: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`.
- 결과: 35개 통과, 50.800초, exit 0. 검수 출력 `/tmp/byyourside-rev4-tests.txt`.

## 확인된 개선

- 실제 기본 STT가 spawn 자식 프로세스에서 모델을 초기화하고 IPC로 추론을 수행한다.
- 비협력적 추론을 주입한 종료 시험, 프로세스 회수, 같은 부모에서 정상 재실행, PID 재사용 테스트가 통과했다.
- 이전의 '테스트 프로그램을 종료해 daemon thread를 숨기는 방식'에서 실제 실행 경로의 프로세스 격리로 개선됐다.
- 30문장 CER, 600초 실제 발화, OS egress, reference annotation 미실행 구분을 유지한 것은 적절하다.

## 남은 차단 사항 [P1]: 추론 요청 deadline이 실제 호출부에서 사용되지 않음

위치: `src/stt.py:228–263`, `src/pipeline.py:127,232,484,930,635–636,1065–1066`.

IsolatedSttEngine.transcribe는 timeout 인자를 지원하지만 기본값이 None이다. 실제 WAV/replay/mic 호출부는 timeout을 전달하지 않는다. 현재 2초는 입력을 모두 공급한 뒤의 worker join 제한이다. 계속 입력되는 마이크/긴 replay 중에는 STT가 멈춰도 해당 제한이 시작되지 않으며, WAV 직접 실행은 worker join 경로도 없다.

### 실제 재현

공식 ko.wav 뒤에 4초 무음을 붙인 약 8.61초 입력을 1배속 replay로 실행했다. 실제 IsolatedSttEngine 자식에 `inject_uncooperative_delay(3.0)`을 주입했다. 임시 파일과 로그는 별도 임시 디렉터리에 생성했고 마지막에 pipeline.close로 정리했다.

- 결과: **TimeoutError 없이 status=OK**.
- VAD 기준 추정 발화 후 지연: **3587.45ms**.
- 자식에 주입한 정지 시간: 3초. 2초 제한보다 길지만, 입력 종료 전에 추론이 끝나므로 기존 테스트와 달리 회수되지 않았다.

이것은 성능 미달 수치 자체보다 '2초 추론 deadline을 보장한다'는 구현/보고서 설명과 실제 동작의 차이다. 기존 100배속 시험은 입력이 즉시 끝나므로 종료 join이 요청 timeout처럼 보인다.

### 필요한 수정

- STT 요청별 deadline과 세션 종료 join 제한을 별도 설정으로 분리한다.
- replay/mic의 실제 요청에 유효한 timeout을 전달해 입력 진행 중에도 회수되도록 한다. 지연 후 입력 producer와 VAD도 취소·정리한다.
- WAV 직접 및 VAD batch 모드도 명시적인 timeout 정책을 갖게 한다. 전체 파일 길이를 고려한 별도 예산을 둘 수 있으며, 짧은 구간용 2초를 무조건 모든 길이에 적용할 필요는 없다.
- warm_up timeout도 실패를 0.0ms 성공처럼 반환하지 말고 자식 정리와 명시적 오류로 처리한다.
- 기존 프로세스 재사용, 샘플 보존, 같은 부모 재시작 구조는 유지한다.

## 검증 요구

1. 입력이 계속되는 1배속 replay에서 요청 deadline을 초과한 비협력적 STT를 주입한다. EOF 전에 timeout이 발생해야 한다.
2. 제한시간은 최초 STT 요청 시각부터 측정하고 초기 입력 수집 시간과 모델 시작 시간을 분리한다.
3. 실제 마이크 없이 mock 입력으로 같은 run_mic 경로를 확인한다. 이 테스트를 실제 마이크 성능 검증으로 표시하지 않는다.
4. direct WAV, batch WAV에서도 비협력적 추론이 설정 deadline 내 실패하고 회수되는지 검증한다.
5. 같은 부모에서 정상 재실행과 worker/child 정리, 정상 느린 요청의 예산 내 성공을 확인한다.

## 보고서의 표현 범위

- 'IPC 0.15ms, 영향 없음'은 측정 절차·원시 근거를 함께 제시하지 않으면 미검증 추정으로 둔다. child infer_ms와 부모의 요청 왕복시간은 다른 지표다.
- 메모리를 부모 RSS만 측정하면 모델을 보유한 자식 메모리를 제외하게 된다. 이후 성능 표에는 부모/자식 RSS와 집계 기준을 명시한다. 합산 RSS는 공유 페이지를 중복 계산할 수 있다.
- Python socket monkeypatch는 spawn 자식에 자동 적용되지 않는다. 현 시나리오6은 부모 범위의 부분 smoke test이며 자식이나 OS egress 차단을 입증하지 않는다. 전체 오프라인 상태는 PARTIAL 유지.
- 짧은 회귀 테스트 통과와 Task 01 실사용 검증 완료는 구분한다. 이미 확인한 프로세스 격리를 다시 전면 재작성할 필요는 없다.

다음 작업: `docs/pm/task_01_revision_05.md`. 이번 수정은 요청 timeout 정책과 직접 관련된 계측/보고에 한정한다.
