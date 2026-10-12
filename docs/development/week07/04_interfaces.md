# API와 인터페이스 정의서

## 2026-10-12 추가: 발표 후 API

아래 경로는 발표 중 서버(기본 8766)에 속한다. Host·Origin은 기존 로컬 경계를 유지한다. 없는 세션은 404, 잘못된 입력은 400이다. 질문의 저장 한도는 세션당 200개다.

| 요청 | 입력 | 결과 |
| --- | --- | --- |
| GET /api/after/sessions | 없음 | sessions, unreadable_count |
| GET /api/after/report | session_id 쿼리 | 시간·항목·발화 근거·질문 이력 |
| GET /api/after/export | session_id 쿼리 | report JSON, markdown 문자열 |
| POST /api/after/supplement | session_id, text(20,000자 이내) | saved |
| POST /api/after/ask | session_id, question(1~1,000자) | sources_found 또는 insufficient_evidence, 원문·근거 ID. 생성 답변 아님 |
| POST /api/after/transcribe | 음성 바이너리(12MB·60초 이하) | question, duration_sec, engine. 전사 후 자동 검색하지 않음 |

`/after`는 저장된 세션의 화면이다. 발화 중인 세션의 음성 질문 업로드는 거부한다. 서버별 동시 질문 음성 분석은 하나로 제한한다. 정상·오류 종료 모두 임시 녹음을 정리한다.

작성일: 2026-10-12. 버전: 0.1. 현재 코드의 주요 경로를 정리한 초안이다. 주소는 기본 포트이며 실행 옵션으로 변경할 수 있다.

## 발표 전 서버 127.0.0.1:8765

| ID | 메서드와 경로 | 요청 | 성공 응답 |
| --- | --- | --- | --- |
| A01 | GET /api/status | 없음 | 모델 준비·기능 상태와 presentation_url |
| A02 | POST /api/upload | 미디어 바이너리, X-Audio-Extension 헤더 | 201, id |
| A03 | POST /api/analyze | id, script, 선택적 target_seconds·gaze_options | 202, id·status=running |
| A04 | GET /api/jobs/{id} | 업로드에서 받은 ID | 분석 상태 또는 status=done과 report |
| A05 | GET /api/media/{id} | ID, 선택적 Range 헤더 | 미디어 바이트, 전체 200 또는 부분 206 |
| A06 | POST /api/presentation/prepare | script, target_seconds, 선택적 title | 200, prepared=true·url |

발표 전 `POST /api/script`는 대본 파일의 바이트와 X-Script-Extension 헤더를 받아 텍스트를 추출하는 별도 경로다. 발표 중 서버의 같은 경로는 준비된 대본 문자열로 시간 계획을 만드는 명령이므로 요청 형식을 혼용하지 않는다.

### A06 요청 예시

```json
{"script":"안녕하세요. 발표 연습을 시작합니다.","target_seconds":60,"title":"발표 연습"}
```

```json
{"url":"http://127.0.0.1:8766","prepared":true}
```

script는 문자열이며 발표 중 문장 분리·길이 제한에 맞아야 한다. target_seconds는 양의 유한한 숫자다. 잘못된 입력·진행 중 교체·통합 서버 미연결은 400과 error 메시지를 반환한다. 요청 출처가 허용되지 않으면 403을 반환한다. 기존 준비 자료는 오류 시 유지된다.

## 발표 중 서버 127.0.0.1:8766

| ID | 메서드와 경로 | 요청 | 응답과 역할 |
| --- | --- | --- | --- |
| A07 | GET /api/state | 없음 | deck, session, 음성·입력 상태 등 현재 상태 |
| A08 | POST /api/script | text, duration_sec, 선택적 title | 대본 계획 준비 후 상태 |
| A09 | POST /api/start | microphone, 선택적 voice | 발표 세션 시작 후 상태 |
| A10 | POST /api/utterance | text, 선택적 endpoint_reason 등 | 시험용 직접 전사 처리 후 상태 |
| A11 | POST /api/stop | 빈 JSON 객체 | 입력 종료·처리 상태 |
| A12 | GET /api/export | 없음 | 세션·구간·이벤트·알림 JSON |

명령 입력 오류는 400, 허용되지 않은 출처는 403, 저장할 세션 없음은 export에서 404, 예기치 못한 서버 처리 실패는 POST에서 500을 반환한다. 공통 오류 본문은 `{"error":"설명"}`이다. 정상 시작 요청 후에도 모델 준비와 음성 처리는 상태 조회 결과로 확인한다.

## 자료 형식과 보안

JSON은 UTF-8을 사용하며 시간 단위는 초다. 미디어는 별도의 바이너리 경로로 전송한다. 서비스는 루프백에 바인딩하며 Host·Origin을 검사한다. 현재 구현은 로컬 사용을 전제로 하며 외부 공개 서비스의 사용자 인증·HTTPS 배포를 제공하는 구조로 설명하지 않는다.

발표 세션의 원본 계약과 추가 필드는 `docs/tasks/presentation_contract.md`를 따른다. 마음AI 인계 입력은 `review_handoff.py`의 schema_version, evidence, prompt, integration_status, training_eligible, quantization_performed를 기준으로 한다. 현재는 SDK 호출·네트워크 전송을 수행하지 않는다.
