# 실사용 평가 도구 PM 검수

- 대상: `d88573e`, 2026-09-28
- 판정: **수정 요청. 사용자 실측 결과의 자동 PASS 판정에 아직 사용하지 않는다.**
- Revision 06 STT 핵심 구현 승인은 유지한다. 이번 문제는 평가 실행기와 안내서에 있다.
- PM은 구현 코드를 변경하지 않고 문서와 격리된 진단 증거만 작성했다.

## 검증 방법

새 평가 분기의 판정을 확인하기 위해 임시 WAV와 mock pipeline 반환값/예외를 주입했다. 실제 마이크를 열거나 모델 성능을 측정한 시험이 아니다. 6개 진단 결과가 재현됐고 실행 exit code는 0이다.

- 재현 스크립트: `logs/pm_review_validation_20260928/probes.py`
- 결과: `logs/pm_review_validation_20260928/probes.json`
- 이번에 기존 51개 전체 회귀를 반복하지 않았다. 기존 목록에는 신규 평가 실행기 판정을 직접 검증하는 테스트가 없다. `--help` 실행도 발화 분포 함수나 평가 분기의 검증이 아니다.

## F1 [P1] 30문장 충족 조건이 manifest 길이에 따라 축소됨

위치: `scripts/evaluate_cer.py:281`.

커스텀 manifest가 P01 한 건뿐일 때 1건의 정확한 전사만으로 `PASS (Primary CER: 0.00% <= 15.0%)`를 반환했다. `run_count == len(dataset)`가 30개 기준을 대체한다. 표준 P01~P30 coverage 및 중복 ID 검증이 필요하다. 부분 manifest가 수정분만 뜻하는지 전체 목록인지도 정의해야 한다.

또한 명시한 manifest 경로가 없어도 기본 30문장으로 조용히 대체된다. 사람이 고친 정답이 누락된 것을 숨길 수 있으므로 명시적 입력 오류로 처리해야 한다.

## F2 [P2] 실행 상태와 실제 측정 지표 불일치

위치: `scripts/evaluate_cer.py:282-291`, `tests/test_mic_10min.py:181`, `run_mic_benchmark` 예외 경로.

- WAV가 존재하지만 모든 추론에 TimeoutError를 주입하면 `error_count=1`인데 전체는 `NOT_RUN (0/1 recorded files found)`라고 출력한다. 파일 없음과 실행 실패를 구분해야 한다.
- direct 전용 모드에서 실제 Direct CER 0%인데 없는 VAD 값을 1.0으로 대체하여 `FAIL (Primary CER: 100.00%)`라고 출력한다. 미측정 지표를 100%로 만들면 안 된다. Direct 결과와 Task 01 VAD gate의 미실행 상태를 분리한다.
- Direct 성공 후 VAD 실패 시 Direct aggregate에는 포함되지만 항목은 ERROR가 돼 category 집계에서 제외된다. 모드별 성공 수와 집계 모집단을 명시해야 한다.
- 마이크 run_mic에 TimeoutError를 주입하면 finally의 close는 실행되지만 결과 JSON은 생성되지 않는다. 실패 단계·예외·run_id를 보존해야 한다.
- 마이크 무음/분포 부족에서 judgment의 NOT_RUN/PARTIAL과 overall_status의 FAIL이 충돌한다.

## F3 [P1] 큐 계측 누락이 정상으로 처리되고 성긴 발화도 연속 발표 PASS

위치: `tests/test_mic_10min.py:135-152`, `:170-173`.

600초 반환값에 5개 분 동안 각각 12초(총 60초)만 발화한 세그먼트를 넣고 모든 `queue_wait_ms`를 생략했다. 결과는 `PASS (10-minute continuous presentation stability validated)`였다. 큐 필드 누락을 제외한 뒤 0 drift로 대체하는 로직은 이전 누락 지표 문제를 다시 만든다.

큐 필드는 전부 유효해야 하며 누락/비수치/표본 부족은 유효한 0과 구분한다. 첫값-끝값만으로 중간 적체와 지속 증가를 판단할 수 없으므로 시간 구간별 분포도 보고한다. 최소 발화 감지 조건(5개 분/총 60초)은 10분 발표 지속의 증거로 충분하지 않다. 자원 안정성, 발화 coverage, 실제 발표 검증을 분리하고 성긴 입력을 전체 연속 발표 PASS로 단정하지 않는다. VAD 구간 길이는 실제 유성음 길이의 근사치라는 한계도 남긴다.

## F4 [P1] 오프라인 안내에 차단 설정 없이 전역 필터 해제 명령만 존재

위치: `docs/user/task_01_recording_guide.md:176-180`.

pfctl 절차에는 차단 설정이나 기존 상태 보존 없이 `sudo pfctl -d`만 제시된다. 로컬 `man pfctl`에서 `-d`는 **Disable the packet filter**임을 확인했다. 기존 필터가 켜져 있던 사용자에게 무조건 비활성화를 권하면 기존 상태도 보존하지 못한다. 이 불완전한 절차는 제거한다. PM은 네트워크/필터 변경 명령을 실행하지 않았다.

Wi-Fi 또는 Ethernet 중 하나만 끄는 것으로 다른 활성 네트워크까지 차단됐다고 단정할 수 없다. 모든 활성 외부 경로를 확인하고, 오프라인에서 실행됐다는 사실과 외부 송신 시도 자체가 없었다는 주장은 구분한다.

## 추가 문서 보완

manifest JSON 예시가 없어 사용자가 작성하기 어렵다. 장치·ITN·모델 및 정답 manifest 해시·dirty 상태 등 재현 메타데이터도 부족하다. `boundary_cer_diff`는 두 경로의 CER 차이이지 경계 단어 소실의 원인별 실측이 아니다. 숫자/약어의 표기 차이는 별도 분석하고 원인 단정을 피한다. 안내서의 스테레오 처리는 실제 평균 downmix와 일치시킨다.

다음 작업: `docs/pm/task_01_validation_tools_revision_01.md`. 모델·파이프라인 재작성 없이 평가 판정과 안내만 보완한다. 녹음 파일을 만들 수는 있지만 현재 자동 판정으로 최종 합격을 결정하지 않는다. 실제 음성·600초·reference annotation·OS egress는 NOT_RUN/PARTIAL 유지, Task 02 미착수.
