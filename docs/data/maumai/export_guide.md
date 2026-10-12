# 마음AI 데이터시트 검사·내보내기

2026-10-12. 학습 실행 도구가 아니라 검수한 데이터의 형식 변환 도구다. 모델 다운로드·양자화·SDK 실행을 하지 않는다.

## 사용 순서

1. `outputs/week07_maumai/곁_마음AI_학습데이터시트_v1.xlsx`를 열고 `학습데이터`에서 입력 요약·정답 요약을 읽는다.
2. 정확한 학습 입력·정답은 `입출력JSON` 시트의 같은 ID를 수정한다. 요약만 고치면 학습 정답은 변경되지 않는다. 어렵다면 수정 메모를 남긴다.
3. 검수를 마친 행은 검수 상태 `검수완료`, 검수자, 날짜, 사용 결정 `사용`을 입력한다. 검수하지 않은 행은 그대로 둔다.
4. 엑셀을 저장한다. 변환기는 저장된 셀을 다시 읽는다. 수식인 ‘학습 후보 상태’를 신뢰하지 않고 검수 정보를 직접 확인한다.
5. 아래 명령으로 변환한다. 출력 폴더는 매번 새 이름을 사용한다. `validation`과 `test`는 학습 가능 상태로 바꾸지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt
.\.venv\Scripts\python.exe -X utf8 scripts/export_maum_dataset.py --workbook "outputs/week07_maumai/곁_마음AI_학습데이터시트_v1.xlsx" --output outputs/maumai_reviewed_v1
```

아직 승인된 train 행이 없으면 내보내기를 중단한다. 현재 80행은 모두 미검수 초안이다. 검수 상태를 임의로 바꿔 학습 완료로 처리하지 않는다.

형식만 보려면 아래 명령을 쓴다. 만들어지는 파일명은 `preview_`로 시작하고 모든 행은 `training_eligible=false`다. 이 결과를 실제 학습에 넣지 않는다.

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/export_maum_dataset.py --output outputs/maumai_preview_v2 --preview
```

## 공급 자료와의 연결

HomeIoT와 Jeju bus 예제의 공통 열인 `No`, `기능명`, `LLM Output`, `Query(국문)`을 CSV로 내보낸다. 공급사 예제의 시트·열 이름이 서로 다르므로 실제 학습 로더에서 열 매핑을 확인해야 한다. 동명의 외부 예제를 우리 데이터에 합쳐 학습하지 않는다.

`src/maum_contract.py`는 프로젝트 제안 토큰 `<maum_0>`~`<maum_3>`, `<maum_end>`와 JSON 규약 사이를 변환한다. 모델 응답은 제한된 데이터 문법으로 해석하고 실행하지 않는다. 근거 ID·리뷰 시각·속도 알림 정책도 입력과 대조한다. 이 검사는 문장의 의미가 정답임을 보장하지 않는다.

JSONL에는 user/assistant 메시지와 특강2의 Octopus 스타일 `formatted_prompt`를 함께 넣는다. 특강의 실제 학습 로더는 제공받지 못했으므로 완전 호환을 확인한 것은 아니다. `function_description`은 별도 필드다. 설명을 assistant 정답 뒤에 붙이는지, 구분자·BOS·chat template·종료 토큰·문맥 길이를 어떻게 쓰는지는 받은 학습 코드와 토크나이저에 맞춰 최종 조정한다. 현재 출력에 토큰 ID를 등록하거나 보드용 모델을 만드는 작업은 포함되지 않는다.

새 데이터는 같은 화자·대본·유사 주제의 변형을 같은 group_id에 두고 분리한다. 동일 입력 중복, 그룹의 분할 교차, 출력 함수 불일치, 존재하지 않는 발화·자료 근거를 검사한다. ‘실제 자료’의 동의 범위와 정답 의미는 사람이 별도로 확인해야 한다. 원래 seed JSON의 검수 메타데이터와 Excel 수정본은 자동 동기화되지 않으므로 최종 내보내기는 `--workbook`을 지정한다.
