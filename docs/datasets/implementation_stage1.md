# LLM 파인튜닝 데이터셋 구축 1단계 구현 보고서

기준: 2026-10-09, main `53cce3436355f36aa2edb1f45500f984de07594a`, 작업 브랜치 `feature/llm-dataset-pipeline`.
현재 작업 루트: `C:/Github/byyourside`; origin: `https://github.com/byyourside-project/byyourside.git`.

## 작업 전 확인과 차이

로컬/원격에 analysis/llm-dataset-design 브랜치가 없었다. 이전 별도 분석 폴더의 미커밋 보고서를 읽고 현행 src/ollama_coach.py, presentation.py, script_coaching.py, coaching_eval.py와 기존 tests/examples를 대조했다. 기준 코드 커밋이 동일하므로 F1 계약의 차이는 없다. 실제 실행 코드는 그대로 유지했다.

현재 LLM은 slide_title/keypoints/utterances를 받아 항목별 s/e를 반환한다. e는 <=1.5초 간격으로 병합한 발화의 번호이며 원본 segment ID로 복원한 뒤 Python의 수치/미완성 가드를 거친다. Session은 별도로 최종 상태를 누적한다. 대본 후보 선택/누락/속도/알림 정책은 Python 역할이다. Function Calling 도구나 특수 토큰을 추가하지 않았다.

기존 개발 평가 파일은 20+8+4+32=64 레코드이며 결합 파일이 다른 세 파일을 포함해 고유 case payload는 32개다. 독립 test로 사용하지 않는다. 신규 감사는 파일명 패턴 coaching*eval*.json으로 네 파일을 모두 읽고 실제 평가기의 입력 prefix와 전체 case 내용도 검사한다.

보고서의 일반적 스키마 제안을 구체화하면서 snapshot.segments와 source.pii_status를 필수화했다. timestamp만 적는 것으로는 미래 발화나 병합 매핑을 검증할 수 없기 때문이다. manifests는 분할 도구 실행 시 생기는 release 폴더의 manifest.json에 두어 데이터와 해시의 동시 관리를 단순화했다. 최상위 manifests 폴더는 추후 수집/캘리브레이션 인덱스 용도로 유지했다.

## 파일 목록과 구조

```text
datasets/presentation_coach/
  .gitignore
  README.md
  schemas/canonical-v1.schema.json
  schemas/functions-v1.json
  guidelines/labeling-v1.md
  regression/legacy-index.json
  {manifests,raw,staging,canonical,regression,quarantine,audits}/.gitkeep
  exports/{ollama_sft,coaching_eval}/.gitkeep
scripts/datasets/
  __init__.py
  common.py
  validate_dataset.py
  audit_duplicates.py
  build_splits.py
  export_sft.py
  export_eval.py
tests/test_dataset_contract.py
docs/datasets/implementation_stage1.md
```

common.py는 스키마/병합/개인정보/파일 I/O/그룹 연결 검증의 중복 구현을 줄이기 위해 추가했다. 표준 라이브러리만으로 실행하며 기존 requirements와 루트 .gitignore를 수정하지 않았다. raw/staging/canonical/quarantine/export 내용은 하위 .gitignore로 차단하고 빈 디렉터리 표식만 추적한다. 실제 개인정보/음성/학습 JSONL은 포함하지 않았다.

## Function과 데이터 계약

F1 judge_keypoint_semantics: 현재 항목의 사실 의미 일치, 부분 설명, 모순, 수치·단위, 부정, 정정, 미완성 여부를 입력 발화만으로 판단한다.
Parameters는 현행 모델의 세 필드 그대로, Returns는 `{local_key:{s:-1|0|1,e:[merged_number]}}`이다. 모든 항목의 출력이 필요하고 1/0은 유효한 비어 있지 않은 근거, -1은 빈 근거가 필요하다.

JSONL 한 행은 한 요청이다. schema_version/sample_id/task/function_version/source/snapshot/input/mapping/target/annotation/lineage/split의 필수 필드와 선택 reference를 정의했다. 원본 발화는 snapshot, 모델 입력은 input, 사람 정답은 target.compact, 검수 이력은 annotation에 분리했다. timestamp/cutoff와 실제 joined_utterances 결과를 대조하며 input의 추가 메타데이터 필드를 거부한다. 검수되지 않거나 개인정보·동의·STT 품질이 해결되지 않은 자료는 release/SFT에서 거부한다.

라벨 지침에는 요청한 12종 사례 각각 input/compact 정답/근거가 있다. 예: 항목 “최대 50분 녹음”에 “최대 삼십 분 녹음”은 s=0/e=[1], 무관한 날씨 발언은 -1/[], 동일한 사실의 바꿔 말하기는 1과 실제 근거다. STT status가 오류인 실제 구간은 현재 추론이 보류되므로 F1 release에 섞지 않는다. 지침은 인간 이중 검수와 불일치 조정을 요구한다.

## 검증·중복·분할·변환

구체적인 실행 명령과 입력 계약은 [데이터셋 README](../../datasets/presentation_coach/README.md)에 있다.

- validate: 엄격 JSON/타입/키/근거/시간/병합/검수/동의/분할 누출. 전체 release 파일을 함께 검사한다.
- audit: exact/NFC·공백/같은 입력의 다른 정답/내용 중복/lexical near 후보/legacy contamination. 숫자·부정을 지우지 않고 자동 삭제하지 않는다.
- split: 세션·대본·시나리오·화자·부모·파생·유사 후보의 연결 요소별 80/10/10 목표 배정. 입력 순서와 무관하게 seed 재현. 충돌은 quarantine, legacy 관련 그룹은 legacy_regression. 비율·그룹·라벨·레코드/프롬프트/스키마/adapter 해시를 manifest에 남긴다.
- SFT: 전체 release manifest와 멤버십/체크섬 검증 후 train/validation만 messages로 변환. 현재 SYSTEM_PROMPT와 정확한 input/compact만 사용. test는 학습 export에서 금지.
- eval: 기본 single-request compact reference 형식. 기존 coaching_eval.py용 session 모드는 retimed replay 최종 상태에 대한 별도 adjudicated reference가 필수이며 compact에서 자동 추정하지 않는다. 기존 validate_cases와 실제 PhraseCoach evaluate_cases 왕복을 시험했다.

## 검증 결과

번들 Python의 UTF-8 모드(-X utf8)에서:

- 신규 데이터셋 테스트 23개 통과(마지막 전체 신규 실행 10.445초).
- 신규+기존 Ollama/presentation/semantic_guards/coaching_eval 81개 통과(16.123초).
- script_coaching의 오디오 의존 HTTP 테스트를 제외한 11개 통과(0.311초).
- 따라서 신규23 + 기존69 = 92개 성공. 이후 session export와 현행 evaluate_cases 왕복 검증을 추가해 해당 테스트도 다시 성공했다.
- CLI JSONL → 분할 → manifest 검증 → SFT/eval export를 임시 합성 fixture로 실행했다. 잘못된 session reference는 실패하고 출력 파일을 생성하지 않는 것을 확인했다.
- 기존 추적 파일 git diff는 비어 있고, ignore 규칙 및 UTF-8 소스/공백 검사를 확인했다.

넓은 109개 테스트 초기 실행에서는 21개 오류가 발생했다. 일부는 Windows cp949의 기존 테스트 read_text 문제로 -X utf8에서 해결했다. 나머지 마이크/서버 경로는 sounddevice 미설치, pause_recovery는 scipy 미설치로 확인됐다. 기존 .venv는 원래 Python312 실행 경로가 사라져 실행되지 않았다. 오디오 의존성을 임의로 설치하거나 runtime 코드를 수정하지 않았다. 전체 오디오/장치/UI 회귀 완료나 실기기 성능을 주장하지 않는다.

## 한계와 다음 단계

near 검사는 lexical 후보 탐색이라 어휘가 크게 다른 패러프레이즈를 완전히 찾지 못한다. 계보 기록과 사람 검수로 보완하고 대량 수집 전에 인덱스를 확장해야 한다. PII 패턴도 인간 비식별 검수를 대체하지 않는다. 문장 내부의 숨은 미래 사실은 timestamp·매핑만으로 완전 검출할 수 없다. 큰 그룹/소량 데이터의 split 비율은 목표와 다를 수 있으며 빈 test로 독립 품질을 주장하지 않는다.

tokenizer 검사는 선택적 local-only 옵션이며 이번 환경에서 실제 모델 tokenizer 검증은 하지 않았다. 기본 SFT export manifest에 NOT_CHECKED를 남긴다. 학습 전 동일 모델의 chat template/EOS/assistant-only loss와 4096/1024 예산을 확인해야 한다. 모델 다운로드·파인튜닝·양자화·배포는 실행하지 않았다.

다음 단계는 승인된 소량 실제 세션/대본·합성 최소쌍 수집, 동의·비식별 검수, 독립 이중 라벨링, 충돌 및 legacy/near 감사, 그룹 분할/manifest 잠금이다. 새 Q&A·표정/시선·진동·종합 점수·QCS6490 배포는 계획서상 목표로 유지하며 이번 F1 구현 완료에 포함하지 않는다.
