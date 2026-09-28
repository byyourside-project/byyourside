# Revision 03 PM 검수

- 대상: db02686 / 2026-09-28
- 판정: CHANGES_REQUESTED. 이번 보완 범위는 비협력적 추론의 종료 처리 및 보고서 정정으로 제한한다.
- 구현 코드 수정 없음. 새 마이크 녹음 없음.
- 전체 테스트 재실행: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`, 30개 통과, 38.736초, exit 0. 검수 출력: `/tmp/byyourside-rev3-tests.txt`.

## 인정되는 개선

- 기존 forced-cutoff fixture에서 전체 두 구간 연결과 샘플 보존 검증이 추가됐다. 기존 166ms gap은 해당 fixture에서 해결됐고, 저장된 시나리오4 결과는 공백 제외 CER 0%다. 이 결과를 다른 발표 음성 전체의 정확도로 일반화하지 않는다.
- 4.5초 음성 + 2초 무음 + 2.89초 음성을 추가로 구성하여 실제 VAD 출력 각 구간이 원래 위치의 파형과 일치함을 확인했다.
- queue_wait_ms 저장과 누락 감지, smoke/600초 gate 분리를 확인했다.
- 새 마이크 로그는 실제 수집 60.06초, 발화 0개, passed=false, gate_10min_passed=false, 판단 NOT_RUN이다. 무음 캡처 기록이며 실제 발화 안정성 PASS가 아니다.

## 남은 차단 사항 [P1]: 비협력적 native 추론의 종료 처리

위치: src/pipeline.py:630–663 및 logger 정리 finally; src/stt.py의 decode_stream 호출; tests/test_regression_rev3.py:214–244.

협력적 stub은 취소 신호를 읽지만 실제 SttEngine.transcribe는 decode_stream 호출 전에만 취소 여부를 검사한다. 실행 중인 native 추론에는 취소 신호를 전달하지 못한다. 두 번의 제한된 join 이후 살아 있는 thread를 남겨둔 채 TimeoutError를 반환하고 logger도 열린 채 남는다.

재현: 실제 모델을 초기화한 SpeechPipeline에서 transcribe만 Event.wait로 대체해 취소 신호를 무시하도록 했다. replay 1000배속 실행 결과 **3.06초 뒤 TimeoutError, has_running_workers()=True**. 검수 후 gate를 해제하고 worker를 join해 잔여 thread를 정리했다.

현재 subprocess 테스트는 전체 프로그램을 자식 프로세스로 실행하고 TimeoutError 발생 시 sys.exit(42)를 호출한다. daemon thread는 프로그램 종료와 함께 사라지므로 통과한다. 이는 실제 앱이 같은 프로세스에서 계속 실행될 때의 worker 정리를 검증하지 않는다. 실제 파이프라인에 프로세스 격리가 구현된 것도 아니다.

요구: 실제 추론을 수명 관리 가능한 프로세스로 격리하고 deadline 경과 시 부모가 terminate/필요 시 kill/join으로 회수한 뒤 오류를 반환하도록 한다. 또는 동등하게 검증 가능한 실제 실행 정책을 구현한다. 단순히 테스트에서 프로그램을 종료하거나 취소 신호를 읽는 stub만 검사해서는 충분하지 않다. logger를 닫힌 뒤 조용히 무시하는 방어 코드도 자원 정리를 대신하지 못한다.

재검증: 같은 부모 프로세스에서 비협력적 추론 timeout → 남은 worker/child 없음 → 모델과 로그 정리 → 새 정상 실행 성공을 확인한다. 모델 프로세스는 매 발화마다 다시 로딩하지 않고 실행 동안 재사용하도록 한다.

## 보고서 정정

- 보고서의 모듈 목록(test_regression_rev1.py, test_audio.py 등)은 실제 저장소 파일명과 다르다. 실제 unittest test ID와 명령 출력으로 대체한다.
- subprocess 테스트 설명의 multiprocessing/0.5초 강제회수 등도 현재 코드(subprocess.Popen, communicate 6초, exit 42)와 다르므로 정정한다.
- 실제 발화 0개인 60초 결과를 표에서 SMOKE PASS로 요약하지 않는다. 캡처 성공과 음성 처리 성능 NOT_RUN을 나눈다.
- 30문장 CER, 600초 실제 발화 안정성, OS egress, reference annotation 지연은 여전히 별도 미완료 항목이다.

다음 지시서는 docs/pm/task_01_revision_04.md이다. 이미 확인한 F1/F3/F4 구현을 다시 전면 수정하지 않는다.
