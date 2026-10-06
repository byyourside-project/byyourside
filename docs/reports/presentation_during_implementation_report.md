# 발표 중 1차 기능 구현 보고서

작성일: 2026-10-04
상태: 로컬 기능 구현 및 자동 검증 완료. 실제 발표 성능·코칭 품질 검증은 별도.

## 결과

기존 VAD/STT에 발표 세션, 수동 슬라이드 전환, monotonic 타이머, 확정 발화 이벤트 전달, 문맥 유지, 핵심 항목 상태, 시간·내용 확인 알림, 로컬 진행 화면을 연결했다. 종료 결과는 발표 후 담당자가 읽을 수 있는 JSON으로 저장한다.

| 1차 작업 | 구현 |
|---|---|
| 진행 제어 | 시작·종료 및 수동 슬라이드 전환. 마지막 음성·판단 처리 후 종료 상태 저장 |
| 타이머 | 전체·슬라이드 경과 시간, 남은 시간, 전환 시 슬라이드 타이머 초기화 |
| STT 연결 | 로그 이벤트 소비자 인터페이스, 제한된 큐, 초기화·추론 오류와 전달 손실 기록 |
| 발화 문맥 | 방문별 최근 문맥, 강제 분할 시 후속 발화 대기, 전환 경계 판단 유보 |
| 핵심 항목 | 미확인·설명됨·판단불가, 발화 근거 ID, 늦은 전사의 원래 슬라이드 연결 |
| 시간 알림 | 규칙 기반 전체 잔여·초과, 슬라이드 목표 시간 초과 |
| 내용 확인 | 전환·종료 시 필수 항목 확인 후보. 기록 오류·처리 중·미완성은 판단 유보 |
| 알림 정책 | 중복 억제, 간격, 우선순위, 만료, 오래된 판단 응답 차단, 늦은 근거 확인 시 안내 철회 |
| 진행 화면 | 입력 방식 선택, 슬라이드·타이머·항목·최근 전사·알림·품질 문제·기록 다운로드 |

## 파일 구성

- `src/presentation.py`: 자료 검증, 세션 상태, 시간·발화·판단·알림·저장.
- `src/presentation_server.py`: 로컬 HTTP, 음성·내용 판단 작업 분리, 이벤트 큐와 오류 처리.
- `src/logger.py`, `src/pipeline.py`: 선택적 이벤트 소비자, 마이크 정상 중지 신호, 캡처 상태 이벤트.
- `scripts/run_presentation.py`: 실행 진입점.
- `web/presentation/`: 외부 CDN·빌드 도구 없는 HTML/CSS/JavaScript 화면.
- `examples/presentation_deck.json`: 발표 전 담당자용 자료 예제.
- `tests/test_presentation*.py`: 신규 기능 검증.
- `docs/user/presentation_during_guide.md`: 실행 안내와 모델 연결 계약.
- `docs/tasks/presentation_contract.md`: 발표 전·후 담당자 연동 규격.

## 검증 증거

1. 전체 테스트 discovery: **96 tests, 107.553초, OK, exit 0**. 기존 STT 실제 모델 회귀 포함.
2. 이후 보완한 종료 후 알림 만료·전체 알림 이력 저장 및 실제 이벤트·중지 신호 시험을 포함한 최종 발표 기능 테스트: **25 tests, 4.597초, OK, exit 0**.
3. JavaScript 문법 검사 `node --check web/presentation/app.js` 및 `git diff --check` 통과.
4. 로컬 브라우저에서 시작, 허용 표현으로 2개 항목 설명 확인, 강제 분할 조각의 판단 보류·후속 병합, 이전/다음 전환, 타이머 초기화, 종료 상태를 확인했다.

실행 명령:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m unittest tests.test_presentation tests.test_presentation_server tests.test_presentation_pipeline -v
```

두 테스트 실행은 일부 항목이 중복된다. 최종 신규 기능 테스트에는 실제 로컬 모델의 WAV 전사 이벤트와 합성 InputStream의 중지 신호 시험이 포함된다. 실제 마이크를 켜거나 600초 발표를 진행한 결과가 아니다.

### Git 업로드 전 최종 검증

2026-10-04 최종 코드로 전체 discovery를 다시 실행했다. **99 tests, 106.813초, OK, exit 0**. JavaScript 문법 검사, Python 컴파일 검사, staged diff 공백 검사도 통과했다. 이번 99개는 기존 74개와 신규 발표 기능 25개를 한 번에 실행한 결과다.

원시 로그: `logs/presentation_implementation_20261004_kk8e5hbu/prepush_regression.txt`. 로그·모델·실행 세션은 기존 Git 제외 정책을 유지하고 검증 요약과 구현 파일을 커밋한다.

원시 콘솔 로그:

- `logs/presentation_implementation_20261004_kk8e5hbu/full_regression.txt`
- `logs/presentation_implementation_20261004_kk8e5hbu/presentation_final.txt`

## 내용 판단과 미검증 범위

기본 코치는 핵심 문장·등록된 허용 표현을 확인하는 보수적 기준선이다. 일반적인 바꿔 말하기 의미 판단을 수행하는 학습 모델을 제공하지 않는다. 로컬 모델 서버의 입출력 어댑터 계약과 응답 검증을 구현했으며, 실제 모델 서버 연결과 코칭 품질 평가는 남아 있다.

STT가 놓친 발화는 내용 누락으로 단정할 수 없다. 기본 알림은 확인 요청으로 표현한다. 부정·수치 처리는 휴리스틱이며 임의의 표현에서 정확성을 보장하지 않는다.

잔여 검증: 실제 30문장 CER, 최신 버전 600초 발표, 사람 기준 발화 종료 라벨, 전체 화면 피드백 p95 목표, OS 수준 오프라인 증거, 실제 QCS6490 실행. 자동 테스트의 성공을 이러한 실사용 성능의 PASS로 해석하지 않는다.

## 후속 작업

발표 전 담당자는 공통 자료 스키마로 핵심 항목·목표 시간·허용 표현을 제공한다. 발표 후 담당자는 저장 세션의 자료 스냅샷·방문·발화 근거·이벤트를 소비한다. 이후 실제 전사와 슬라이드 평가 세트를 구축하고 로컬 모델의 무알림·누락·부정·수치·불확실 판단을 평가한다.
