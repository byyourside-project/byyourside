# 평가 도구 Revision 01 PM 검수

- 대상 커밋: `fe48e44`
- 검수일: 2026-09-28
- 판정: **주요 개선 확인, 아래 3개 항목 수정 후 승인**.
- STT 핵심 구현 승인은 유지한다. PM은 제품 코드를 수정하지 않았다.

## 실행 증거

`.venv/bin/python -m unittest tests/test_validation_tools_rev1.py -v`를 재실행해 **14 tests, 0.353초, OK**를 확인했다. 별도 임시 진단으로 정상 30문항 PASS, 공백 정답, CER 초기화 실패, 기존 출력 보존을 확인했다. 전부 mock 기반 평가 판정 검사이며 실제 음성 성능 검증이 아니다.

- `logs/pm_review_validation_rev1_20260928/unittest.txt`
- `logs/pm_review_validation_rev1_20260928/probes.py`
- `logs/pm_review_validation_rev1_20260928/probes.json`

## 승인한 개선

30개 ID coverage, 존재하지 않는 명시적 manifest 오류 처리, Direct/VAD 모집단 분리, direct 모드의 미측정 VAD null, 큐 누락·비수치 감지, 마이크 실패 JSON, 성긴 발화의 전체 PASS 방지, 불완전한 pfctl 지침 제거를 확인했다. 해당 구조를 다시 만들지 않는다.

## R1 [P1] 정규화 후 빈 정답이 30문장 PASS를 만들 수 있음

`scripts/evaluate_cer.py`의 `load_manifest`는 `not it.get('ref')`만 검사한다. 공백 문자열은 통과하며 이후 strip/정규화로 빈 문자열이 된다. `compute_corpus_cer`는 총 정답 문자 수가 0이면 0.0을 반환한다.

PM 재현: P01~P30의 ref를 각각 공백 3개로 하고 빈 전사를 반환했더니 **PASS, measured_value_raw=0.0**이었다. 30개 ID만으로 유효한 정답 집합임을 보장할 수 없다. 문자열 타입 및 실제 평가 정규화 후 비어 있지 않음을 검사하고, 총 분모 0을 성공으로 취급하지 않는다. 문장부호만 있는 정답도 같은 범위다.

## R2 [P2] CER 초기화·파일 처리 실패에 보고서가 남지 않음

`evaluate_dataset`의 pipeline 생성은 try/finally만 있고 보고서 작성은 그 뒤다. 모델 파일 부재 등 생성 예외가 나면 main이 메시지와 exit 1만 남긴다. PM이 constructor에 RuntimeError를 주입한 결과 **report_exists=false**였다. 마이크 경로의 개선이 CER 경로에는 적용되지 않았다. hash/파일 열기 등 개별 전사 try 밖의 예외도 점검한다.

## R3 [P2] atomic write가 기존 결과를 보존하지 않음

두 실행기의 `atomic_write_json`은 `os.replace`로 기존 파일을 덮어쓴다. PM이 prior_evidence가 담긴 JSON에 호출하자 replacement로 내용이 교체됐다. 기본 UUID 경로는 개선됐지만 `--output-json` 재사용은 보호되지 않는다. 원자적 기록과 덮어쓰기 방지는 별개다. 기본 거부 또는 새 고유 경로로 저장하도록 하고 명시적인 정책을 문서화한다.

## 후속 보고 정합성

평가 결과에는 config(ITN 포함), 모델 manifest 연결, 마이크 장치 정보가 여전히 충분히 남지 않는다. 이전 지시서에서 요청한 재현 메타데이터를 실제 JSON에 보존하거나 미확보 상태를 표시한다. complete 모드는 현재 부분 목록을 PARTIAL로 허용하므로 ‘반드시 30개, 아니면 입력 거부’와 혼동되지 않게 설명한다. 마이크 `passed=true`는 전체 PARTIAL과 혼동되지 않도록 technical gate 의미를 명시하거나 필드명을 분리한다.

다음 지시서: `docs/pm/task_01_validation_tools_revision_02.md`.
실제 녹음은 준비할 수 있지만 평가 도구의 최종 승인은 위 보완 후 결정한다. 사용자 30문장·600초·사람 종료 라벨·OS egress 상태는 그대로 유지한다. Task 02는 아직 승인하지 않는다.
