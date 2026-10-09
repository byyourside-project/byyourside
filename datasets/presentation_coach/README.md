# 발표 코칭 데이터셋 파이프라인 v1

F1 `judge_keypoint_semantics`의 데이터 수집·검수·검증·분할·변환 기반이다. 기존 src/·기존 평가 정답은 변경하지 않는다. 표준 라이브러리만 필요하며 모델 호출·학습·다운로드는 없다.

## Function

Description: 슬라이드 핵심 항목과 발화의 의미적 동등성, 부분 설명, 모순, 숫자/단위, 부정, 최신 정정, 미완성을 판단한다.
Parameters: `slide_title:string`, `keypoints:{"1":text,...}`, `utterances:[{number:1,text:...},...]`.
Returns: `{"1":{"s":1,"e":[1]},...}`; s=1/0/-1, e는 병합 발화 번호. 특수 토큰을 추가하지 않는다. 상세 계약: schemas/functions-v1.json.

## 데이터 계약

canonical-v1.schema.json은 일반 JSON Schema draft 2020-12 도구와 호환된다. 도구 내 validator는 이 스키마에 사용하는 키워드만 구현한 표준 라이브러리 검증기이며 임의의 JSON Schema 실행기는 아니다.

한 줄은 한 요청이다. 필수 필드:

| 필드 | 의미 |
|---|---|
| schema_version / function_version / task | 모두 v1.0 및 judge_keypoint_semantics |
| sample_id | 데이터 전체에서 고유한 가명 ID |
| source | 출처/기준 커밋/세션/대본 계열/화자 그룹/시나리오/부모·파생 ID/동의/PII 상태 |
| snapshot | phase, visit/revision, cutoff_sec, pending, quality_issue_codes, context_trimmed, 원본 segments |
| input | Ollama가 실제 보는 세 필드만; 정답·메타데이터 추가 금지 |
| mapping | 순번 keypoint→원본 ID, 병합 발화 번호→원본 segment ID 목록 |
| target.compact | 사람 검수한 원시 모델 정답; 가드 이후나 누적 상태가 아님 |
| annotation | status, annotator 가명, guideline version, 항목별 relation/rationale, confidence |
| lineage | split_group_id, duplicate_cluster_id(null 가능), transform/prompt version |
| split | unassigned/train/validation/test/legacy_regression/quarantine |

source와 mapping에 실명이나 이메일을 쓰지 않는다. 모든 연결 키는 실제 계보를 나타내야 하며 “unknown” 같은 공유 placeholder는 전체 데이터를 한 그룹으로 묶을 수 있다. 같은 합성 fact template도 scenario_family_id를 공유한다. 원본 segments의 start/end는 유한한 숫자이고 순서대로 겹치지 않아야 하며 cutoff 이전이어야 한다. 입력 text와 mapping은 현재 joined_utterances(<=1.5초 병합)의 결과와 정확히 일치해야 한다. 원본 자체가 잘못 전사되었거나 숨은 미래 내용을 포함하는지는 사람이 검수해야 한다. 코드는 텍스트에서 추론해 미래 사실을 완전히 판별하지 못한다.

v1 schema는 기존 보고서 예시보다 엄격하다: timestamp/원본 병합 검증을 위해 snapshot.segments, 개인정보 검수 상태를 위해 source.pii_status를 추가했다. 대화 전체가 아니라 런타임이 실제 선택한 최근 문맥(최대12 segments)을 기록한다. 선택 문맥 잘림은 context_trimmed에 명시하고 본 입력에 보이는 근거만 라벨링한다. source.kind=legacy는 legacy_regression에만 둔다.

## 실행 순서

저장소 루트에서 실행한다. 실제 데이터는 Git 추적 밖의 로컬 위치를 권장한다. JSONL은 UTF-8, 빈 줄/NaN/중복 JSON 키는 거부한다. 에러는 이유와 레코드 번호만 출력하고 발화 원문은 출력하지 않는다. 잘못된 행을 조용히 건너뛰어 일부만 학습 export하는 동작은 없다.

```powershell
python scripts/datasets/validate_dataset.py datasets/presentation_coach/staging/reviewed.jsonl
python scripts/datasets/audit_duplicates.py datasets/presentation_coach/staging/reviewed.jsonl --output datasets/presentation_coach/audits/reviewed.json
python scripts/datasets/build_splits.py datasets/presentation_coach/staging/reviewed.jsonl --output-dir datasets/presentation_coach/canonical/release-v1 --seed 42
python scripts/datasets/validate_dataset.py datasets/presentation_coach/canonical/release-v1/train.jsonl datasets/presentation_coach/canonical/release-v1/validation.jsonl datasets/presentation_coach/canonical/release-v1/test.jsonl
python scripts/datasets/export_sft.py datasets/presentation_coach/canonical/release-v1 --split train --output datasets/presentation_coach/exports/ollama_sft/train.jsonl
python scripts/datasets/export_sft.py datasets/presentation_coach/canonical/release-v1 --split validation --output datasets/presentation_coach/exports/ollama_sft/validation.jsonl
python scripts/datasets/export_eval.py datasets/presentation_coach/canonical/release-v1 --mode single --output datasets/presentation_coach/exports/coaching_eval/test-single.json
python -m unittest tests.test_dataset_contract -v
```

분할 결과가 빈 split이면 빈 JSONL 파일을 남기고 manifest에 0건을 기록한다. validate_dataset은 비어 있는 파일을 데이터셋으로 인정하지 않으므로 위 다중 파일 검증 명령에서 빈 파일은 제외한다. export는 전체 release와 manifest를 읽으며 빈 split을 선택하면 실패한다. 기존 release 폴더 재사용은 거부한다.

## 검증·격리·분할

필수필드/타입/라벨/근거/ID/시간·미래 참조/실제 병합/프롬프트 버전/검수·동의를 검사한다. consent_status는 granted 또는 개인정보 없는 synthetic/human_authored의 synthetic_no_personal_data만 허용한다. pii_status=cleared가 필요하다. 연락처·이메일·주민번호 패턴이 잡히면 인간 검수를 위해 quarantine으로 분류한다. 미동의 자료는 사용자가 먼저 split=quarantine으로 기록해야 하며 자동으로 동의를 추정하지 않는다. STT status!=OK/pending/quality_issue가 있는 사례는 F1 release에서 거부한다.

중복 감사는 입력 전체 exact, NFC·공백 정규화, title/ID를 제외한 내용 비교, lexical SequenceMatcher >=0.85 후보를 보고한다. 동일 정규화 입력·다른 정답은 conflict다. 의미 임베딩을 사용하지 않으므로 어휘가 크게 다른 패러프레이즈는 놓칠 수 있다. 이를 보완하도록 부모·시나리오 계보를 기록하고 후보를 사람 검수한다. 숫자/부정은 삭제하지 않는다. 근접 검사는 O(N² + N×legacy)로 1단계 소량 수집용이며 대량화 전에 MinHash/임베딩 인덱스를 별도 검토한다.

세션/대본/시나리오/화자/부모/파생/duplicate_cluster 및 입력 유사 후보의 연결 요소를 하나로 묶는다. 그룹 전체를 seed=42의 결정적 순서와 큰 그룹 우선 부족량 배정으로 목표 80/10/10 분할한다. 입력 행 순서에도 독립적이다. 큰 그룹으로 비율이 어긋날 수 있으며 manifest에 실제 건수·그룹·라벨을 기록한다. conflict 그룹은 quarantine. 기존 네 coaching*eval*.json의 실제 요청 prefix 및 전체 내용과 중복/근접하면 그룹 전체가 legacy_regression이 된다. 근접 legacy 후보도 보수적으로 격리하며 자동 삭제하지 않는다. 동의나 검수 실패는 release 이전에 수정/격리해야 한다.

기존 데이터는 개발 회귀 64 레코드/32 고유 케이스이며 독립 test가 아니다. regression/legacy-index.json에는 기존 파일 checksum만 참조한다. 모든 CLI는 현재 examples를 다시 읽어 오염을 검사한다. 결과 release에 legacy 및 quarantine 파일도 포함되므로 release 자체도 비공개 로컬 자료로 취급한다. calibration/최종 test 잠금 도구는 이번 범위가 아니다. calibration을 만들면 train ID만 참조해야 한다.

## SFT와 평가 변환

SFT는 현재 SYSTEM_PROMPT를 import하고 user=input만, assistant=target.compact만 저장한다. test/legacy/quarantine은 SFT에 내보내지 않는다. CLI는 manifest의 전체 멤버십/레코드 해시와 split 간 누출을 확인한다. 별도 .manifest.json에 prompt checksum, release checksum, runtime 예산을 기록한다.

기본 export는 모델에 중립적인 messages 형식이고 tokenizer 검증 상태를 NOT_CHECKED로 표시한다. 이미 로컬에 있는 HF tokenizer와 transformers가 있으면 `--tokenizer-dir PATH`로 다운로드 없이 chat template와 토큰 예산(4096 context, 1024 생성 여유)을 확인할 수 있다. Ollama GGUF만 있는 경우 이 옵션을 억지로 쓰지 말고 해당 모델과 동일 tokenizer/template를 학습 전에 확보한다. assistant-only loss/EOS/모델 thinking template는 학습 도구에서 추가 확인해야 한다. 이번 단계에서 학습은 실행하지 않는다.

`export_eval --mode single`은 requests와 expected_compact를 출력한다. 현재 coaching_eval.py의 cases 포맷과 다르며 단일 추론 정답 분석용이다. 세션 도구에 직접 입력하지 않는다.

`--mode session`은 별도 reference가 반드시 필요하다:

```json
{"scope":"legacy_evaluator_replay","review_status":"adjudicated","phase":"ongoing","points":{"1":{"expected_status":"explained","expected_missing":false}}}
```

reference는 canonical의 선택 필드다. 인간 검수자는 **현행 evaluator가 재구성하는 2초 간격(0.9초 발화, 1.1초 쉼)의 재생**, 호출별 문맥, Python 가드, Session 누적 상태를 기준으로 최종 정답을 별도로 검수한다. 원본 녹음의 타이밍을 보존하는 형식이 아니므로 실제 세션 성능이라고 보고하지 않는다. exporter는 raw compact에서 expected_status/expected_missing를 추정하지 않는다. 출력은 src.coaching_eval.validate_cases를 통과하며 기존 scripts/evaluate_coaching.py --dataset OUTPUT으로 사용한다. ScriptSession 속도/흐름·실제 timestamp 재생은 후속 evaluator가 필요하다.

## 개인정보와 Git

원본 음성/개인정보/실제 JSONL은 커밋하지 않는다. 기존 전역 *.jsonl ignore를 완화하지 않고 데이터 하위 .gitignore로 raw/staging/canonical/quarantine/exports의 내용도 제외한다. 빈 폴더용 .gitkeep만 추적한다. 공개 가능한 실제 데이터가 생기면 별도 검수 후 추적 정책을 명시적으로 바꾼다. 소량 fixture는 tests/test_dataset_contract.py에서 런타임 생성하고 임시 디렉터리에서 자동 제거한다.

다음 단계: 합의된 소량 세션과 합성 시나리오를 수집 → 동의/비식별 검수 → 독립 이중 라벨링 → conflict/legacy/near 감사 → 그룹 분할·manifest 잠금 → validation 기반 모델 선택. test 사례의 실패를 재학습에 쓰면 다음 release에서 test를 교체하고 개발 전환 이력을 남긴다.
