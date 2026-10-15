# Llama 3.1 로컬 모델 적용

2026-10-16 · `codex/script-slide-tracking`

기본 발표 모델을 `llama3.1:8b`로 변경하고 로컬 Ollama에 설치했다. 설치된 모델은 8B Q4_K_M, 4,920,753,328바이트이며 digest는 `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`다. 모델 배포 정보는 [Ollama 공식 모델 페이지](https://ollama.com/library/llama3.1:8b)에서 확인할 수 있다. 가중치는 Ollama 로컬 저장소에 보관하며 Git에는 코드·설정·사용법·검증 기록을 포함한다.

기존 STT → 로컬 LLM, 현재 문장과 다음 3문장 탐색, 내용 판단과 위치 판단의 분리, 슬라이드별 시간 안내 및 실시간 속도 영역을 유지한다. 실행기와 세 검증 스크립트는 같은 기본 모델 상수를 사용한다. `--ollama-model qwen3:8b` 또는 검증 스크립트의 `--model qwen3:8b`로 기존 모델을 선택할 수 있다.

기존 v17 지시문을 그대로 적용하면 Llama의 문장 추적 개발 사례는 6개 중 2개만 통과했다. Llama 3.1의 대본 추적 요청에는 영어 지시문과 별도의 매장 예시 3개를 제공하도록 조정했다. 예시는 숫자가 다른 문장의 위치, 미완성 문장, 무관한 발화를 다루며 실제 검증 대본의 문장이나 정답은 포함하지 않는다. 실제 요청의 후보·근거 번호로만 응답을 검증한다. 다른 모델의 지시문과 일반 슬라이드 모드의 입력은 유지했다. 프롬프트 버전은 `semantic_v18_llama31_fewshot_position_and_facts`다.

## 검증 결과

| 범위 | 결과 |
|---|---|
| 모델·문장 추적·진행 중 요청·대본·준비 흐름 회귀 | 102개 통과 |
| 실제 Llama 문장 추적 | 정상·건너뛰기·숫자 오류·부분 발화·무관한 발화·슬라이드 전환 6/6 |
| 실제 Llama 연속 대본 확인 | 첫 3문장 순차 확인 통과 |
| 예제 WAV → SenseVoice → Llama | 전사 및 내용 판단 연결 통과 |
| 슬라이드 시간과 음성 합성 | 빠름·느림·계획 범위 통과, 음성은 파일로만 합성 |
| 서버 재시작 | 기존 입력 대본 유지, 기본 Llama 모델 로딩, `/`·`/api/state` HTTP 200 |
| 변경 공백 검사 | 통과 |

문장 추적 6회에서 모델 요청은 평균 **3.74초**, 범위 **3.52~3.80초**였다. 모델 준비 이후 요청 시간이며 마이크·STT·요청 대기·화면 갱신 시간은 포함하지 않는다. 모델 교체만으로 응답 속도가 개선됐다고 볼 수 없다. 프롬프트 조정에 사용한 개발 사례이므로 독립 정확도 평가도 아니다. 실제 사람의 장시간 발표, 일반 슬라이드 모드의 새 모델 정확도, 이어폰 청취는 별도 검증 대상이다.

## 재현

```bash
ollama pull llama3.1:8b
.venv/bin/python scripts/run_presentation.py --port 8771
.venv/bin/python scripts/validate_script_tracking.py --output logs/llama31_tracking.json
.venv/bin/python scripts/validate_pace_voice.py --output-dir logs/llama31_pace_voice
.venv/bin/python -m unittest tests.test_ollama_coach tests.test_script_tracking tests.test_inflight_judgments tests.test_script_coaching tests.test_coach_streaming
```

측정값·모델 식별자·소스 해시는 [검증 JSON](presentation_llama31_validation.json)에 저장했다. 상세 로컬 기록은 `logs/llama31_final_tracking_validation.json`, `logs/llama31_pace_voice_validation/37380710acd3/validation.json`이다.
