# 평가 도구 Revision 02 — 정답 유효성 및 결과 보존 마무리

`docs/reports/task_01_validation_tools_revision_01_pm_review.md`와 PM probes 결과를 읽는다. 이미 통과한 구조는 유지하고 다음 세 항목에 집중한다. STT 엔진은 재작성하지 않는다.

1. **정답 유효성**: ref가 문자열이며 실제 CER 정규화 후 1문자 이상인지 모델 시작 전에 검증한다. 공백·문장부호만 있는 정답, 비문자열, 총 정답 분모 0은 구조화 ERROR/INVALID이며 PASS 금지다. ID도 검증·정규화 결과를 일관되게 사용한다. 기본 30문항 정상 PASS 경로를 유지한다.
2. **CER 실패 기록**: 모델 초기화·파일 해시/읽기·추론·정리 실패의 단계와 오류를 가능한 결과 JSON에 기록한다. 시작부터 실행 ID/기본 metadata를 확보한다. 초기화 전 실패도 보고서가 남아야 하며, 생성된 pipeline은 close한다. 출력 자체가 불가능하면 stderr와 exit code로 원인을 명시한다. 마이크 예외 기록은 유지한다.
3. **기존 출력 보호**: CER/마이크 공통으로 기존 `--output-json` 파일은 기본 거부하거나 새 고유 이름으로 보존한다. 원자적 교체만으로 보호됐다고 설명하지 않는다. 거부/실패 기록 때문에 이전 파일을 덮어쓰지 않게 한다. 정책과 실제 CLI 동작을 문서에 적는다.

보고서 정합성도 함께 마무리한다. 실제 적용 config/ITN/모델 manifest 연결·장치 정보를 결과에 기록하고 미확보 정보는 명시한다. 전체 structured_status와 기술 gate의 passed 의미를 구분한다. complete 부분 목록 처리 설명을 실제 정책에 맞춘다. 추가 기능은 만들지 않는다.

## 필요한 검증

- 기존 14개 테스트 + 정상 30문항 PASS.
- 30개 공백 ref, 문장부호 ref, 비문자열 ref는 PASS 불가 및 모델 미시작.
- CER constructor/hash/read 실패 시 JSON/단계/오류 보존, 가능한 close 확인.
- 기존 출력 파일을 지정한 성공·실패 경로 모두에서 기존 bytes 보존.
- 기본 출력 경로로 연속 실행하면 서로 다른 산출물이 생성됨.

mock 기반 작은 테스트로 충분하다. 모델 전체 회귀, 실제 10분 마이크, 네트워크 설정 변경은 수행하지 않는다. PM probe는 mock 논리 검증이며 실제 CER 성능으로 보고하지 않는다.

`docs/reports/task_01_validation_tools_revision_02_report.md`에 실제 명령·test ID·exit code·고유 증거 경로를 기록한다. 이전 보고서에는 보완 안내를 추가하고 PM 검수를 기다린다. Task 02 미착수 및 실사용 NOT_RUN/PARTIAL은 유지한다.
