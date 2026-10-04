# 발표 전·중·후 공통 계약 v1

작성일: 2026-10-04
구현 기준: `src/presentation.py`, `src/presentation_server.py`.

## 발표 전 → 발표 중: 자료 JSON

예제는 `examples/presentation_deck.json`이다. 최소 형태:

```json
{
  "deck_id": "deck-01",
  "version": 1,
  "title": "발표 제목",
  "total_duration_sec": 300,
  "slides": [
    {
      "slide_id": "slide-01",
      "title": "슬라이드 제목",
      "target_duration_sec": 60,
      "keypoints": [
        {
          "keypoint_id": "point-01",
          "text": "필수 설명 문장",
          "required": true,
          "aliases": ["같은 의미로 허용하는 완전한 설명 표현"]
        }
      ]
    }
  ]
}
```

- slide_id와 keypoint_id는 각각 자료 전체에서 고유하다.
- slides 배열 순서가 발표 순서다. 시간은 양의 유한한 초 단위 숫자다.
- required 기본값은 true, aliases 기본값은 빈 배열이다.
- 최대 100개 슬라이드, 슬라이드당 최대 50개 핵심 항목, 자료 업로드 최대 1MB.
- 자동 추출된 항목은 발표 전 담당자가 사용자 확정 후 전달한다.
- 세션 생성 시 자료를 깊은 복사로 보존하므로 이후 설정 수정이 과거 세션을 바꾸지 않는다.

## 로컬 HTTP API

| 메서드·경로 | 역할 | 요청 |
|---|---|---|
| GET /api/state | 화면 상태 | 없음 |
| POST /api/deck | 자료 교체 | 자료 JSON. 진행 중 교체 금지 |
| POST /api/start | 세션 생성 | `{"microphone":false}` 또는 true |
| POST /api/navigate | 슬라이드 전환 | `{"index":1}`. 0부터 시작 |
| POST /api/utterance | 전사 기능 시험 | `{"text":"...","endpoint_reason":"silence"}` |
| POST /api/stop | 입력 중지·마지막 발화 처리·종료 | `{}` |
| GET /api/export | 전체 세션 JSON | 없음 |

utterance는 선택적으로 segment_id, start_sec, end_sec, status를 받는다. 시간은 세션 시작 기준 초이며 미래 발화는 거부한다. 상태는 OK/UNCERTAIN/ERROR/DROPPED, 동일 구간 ID는 중복 처리하지 않는다. 마이크가 작동 중일 때는 직접 입력을 차단한다.

## 발표 중 → 발표 후: 저장 세션

최상위 필드:

- schema_version: 1.
- session_id, status: running → stopping → ended.
- deck: 당시 발표 자료 스냅샷.
- elapsed_sec, remaining_sec, slide_elapsed_sec: monotonic clock 기반 시간. 종료 후 고정된다.
- visits: slide_id, start_sec, end_sec, version. 슬라이드 재방문은 새 방문 version으로 기록한다.
- segments: 모든 전사와 session-relative start_sec/end_sec, slide_ids, 원래 구간 ID, endpoint_reason, 상태.
- states: keypoint_id별 status, reason, evidence_segment_ids.
- events: 발생 순서의 전체 이벤트. event_id, session_id, type, elapsed_sec 및 유형별 필드.
- alerts: 실제 표시 이력. key, message, priority, 대상 slide_id/version, shown_sec, expires_sec.
- active_alerts: 저장·export 시점에 표시 중인 알림. 상태 조회 API의 alerts는 현재 표시 목록이다.
- issues: 음성·추론·이벤트 전달 품질 문제.

주요 이벤트 유형: session_started, slide_changed, utterance, judgment_deferred, keypoint_judged, coaching_action, slide_review, alert_shown, alert_suppressed, alert_retracted, quality_issue, audio_started, audio_summary, capture_snapshot, session_stopping, session_ended.

로컬 의미 모델 연결 시 coach_configured는 provider, model, 모델 digest·크기·양자화 정보, warm_up_metrics, 요청 제한 시간을 보존한다. coaching_inference는 요청 version/revision, wall_ms 및 모델별 추론 계측을 보존한다. 이 wall_ms는 음성 종료부터 화면까지의 전체 지연이 아니다.

coaching_action은 정상 또는 미언급 상태에서 NO_ACTION, 불확실한 의미 판단에서 UNCERTAIN을 기록한다. slide_review의 missing_candidates는 확인이 필요한 후보이며 확정 누락 판정이 아니다.

## 시간·연결·오류 규칙

- STT sample clock의 구간 시각에 run_start의 monotonic origin과 세션 origin 차이를 더해 세션 시각으로 변환한다.
- 한 슬라이드 방문 안의 발화는 해당 방문에 연결한다. 전환 경계에 걸린 발화는 양쪽 슬라이드를 기록하고 판단을 보류한다.
- 늦은 결과는 발화 당시 슬라이드 상태를 갱신할 수 있으나 현재 슬라이드의 발화로 취급하지 않는다.
- 모델 결과는 session_id·방문 version·요청 revision과 실제 근거 ID를 검증한 뒤 적용한다.
- 입력 손실·미완성 발화·판단 처리 중인 상태는 확정 누락으로 집계하지 않는다.
- 화면의 최근 전사는 30개로 제한하지만 저장·export에는 전체 전사와 이벤트가 포함된다.
- 후처리는 visits와 utterance 시각으로 계산한다. 상태 조회 시점이나 모델 응답 시각을 슬라이드 설명 시각으로 대신하지 않는다.

## 발표 후 담당자에게 남긴 범위

현재 종료 파일 저장과 JSON 내보내기까지 제공한다. 사용자용 결과 요약, 슬라이드별 결과 화면, 개선 제안 생성, 전사 수정, 리허설 비교는 발표 후 작업에서 구현한다.

coaching_inference의 outcome=response_validated는 응답 검증 경로, outcome=error는 실패 경로다. 실패에도 error_type과 wall_ms를 기록한다. 응답 검증 이벤트 자체는 현재 화면 반영을 보장하지 않으며 revision 검증에 따른 judgment_discarded와 함께 해석한다. 현재 요청 실패 시 과거 설명됨도 판단불가로 전환하고, 오래된 요청·다른 세션·종료 이후의 실패는 상태를 바꾸지 않는다.
