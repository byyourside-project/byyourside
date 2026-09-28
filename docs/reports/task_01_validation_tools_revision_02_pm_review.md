# 평가 도구 Revision 02 PM 검수

- 대상: `b051286`
- 검수일: 2026-09-28
- 판정: **실사용 데이터 수집·평가에 사용 승인. 이번 평가 도구 보완 단계 종료.**
- Task 01 전체 성능 판정은 PARTIAL이며 Task 02 구현 승인을 뜻하지 않는다.
- PM은 구현 코드를 수정하지 않았다.

## PM 재실행

명령: `.venv/bin/python -m unittest tests/test_validation_tools_rev1.py tests/test_validation_tools_rev2.py -v`

결과: **23 tests, 1.107초, OK, exit 0**.

이전 PM 진단도 재실행하여 정상 30문항 PASS, 초기화 실패 JSON 보존, 빈 정답 ERROR/측정값 null, 기존 출력 bytes 보존을 확인했다. 추가 진단에서는 CER 초기화·CER 파일 해시·마이크 초기화 실패 각각에 대해 기존 출력 보존 및 새 ERROR JSON 생성과 단계 기록을 확인했다. CER 해시 실패에서 생성된 pipeline의 close 호출도 확인했다.

증거:

- `logs/pm_review_validation_rev2_20260928/unittest.txt`
- `logs/pm_review_validation_rev2_20260928/probes.json`
- `logs/pm_review_validation_rev2_20260928/failure_probes.py`
- `logs/pm_review_validation_rev2_20260928/failure_probes.json`

모두 mock 기반 평가 판정 검증이다. 정상 30문항 PASS는 실제 음성 정확도가 0% 오류라는 뜻이 아니다. 실제 마이크·장시간 시험·네트워크 설정 변경은 수행하지 않았다.

## 승인 근거

1. ref 문자열 및 정규화 후 길이를 모델 생성 전에 검사한다. 공백/문장부호/비문자열의 허위 PASS가 차단됐다.
2. CER 모델 생성 및 파일 평가 예외가 구조화 JSON으로 남는다. 기존 마이크 실패 기록도 유지된다.
3. 기존 명시 출력 파일은 기본 보존하고 고유 경로를 생성한다. 명시적 overwrite만 교체를 허용한다.
4. 전체 PARTIAL과 기술 지표 통과를 `passed`/`technical_stability_passed`로 구분한다.

## 검증 범위와 해석

- 메타데이터의 `model_manifest_status=verified_task_01`는 고정 문자열이다. 이번 실행에서 모델 파일 해시를 다시 확인했다는 증거로 사용하지 않는다. 실사용 결과 검수 때 기존 모델 manifest와 실제 사용 파일을 연결해 확인한다.
- 모든 예외 상황의 완전 보존을 보증하지 않는다. 예를 들어 close 자체의 예외나 결과 저장 매체 장애까지 이번 테스트가 다루지는 않는다. 정상 사용 및 이번 지적 재현 조건에 대한 승인이다.
- 단일 노트북의 순차 평가를 대상으로 출력 보호를 확인했다. 동시 프로세스의 같은 출력 경로 경쟁까지 검증한 것은 아니다.
- 실제 평가 결과는 JSON의 structured_status와 gate_breakdown을 기준으로 검토한다. 기술 지표 통과만으로 전체 성능 PASS로 올리지 않는다.

## 다음 실행 순서

1. `docs/user/task_01_recording_guide.md`에 따라 P01~P30 실제 음성을 녹음하고 결과를 보기 전에 정답을 확정한다.
2. 프로젝트 루트에서 `.venv/bin/python scripts/evaluate_cer.py --audio-dir audio/eval_30` 실행. 사전 검수 manifest가 있으면 `--manifest-json audio/eval_30/manifest.json` 추가.
3. 사용자 준비가 된 시점에 `.venv/bin/python tests/test_mic_10min.py --duration 600.0`을 직접 시작하고 실제 발표한다. 첫 실제 실패가 생기면 증거를 보존하고 원인부터 검토한다.
4. 각 실행에서 출력된 고유 JSON 경로를 PM에게 전달한다. PARTIAL/NOT_RUN의 exit 2는 명시된 정책이며 그 자체가 프로그램 crash를 뜻하지 않는다.

새 구현 지시서를 발행하지 않는다. 다음 PM 작업은 실제 결과 검수다. 30문장 CER·600초 실제 발표·사람 종료 라벨·OS egress 증거가 없으면 각 상태를 NOT_RUN/PARTIAL로 유지한다.
