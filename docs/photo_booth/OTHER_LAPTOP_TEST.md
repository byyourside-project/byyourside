# 다른 노트북에서 학습 모델로 사진관 시험하기

**Built with Llama**

브랜치: `codex/photo-booth-training-handoff`.

이 브랜치에는 사진관 앱, 명령 가이드, 학습·평가 코드, 결과가 반영된 데이터셋 엑셀, 작은 학습 어댑터와 토크나이저가 포함된다. Hugging Face 토큰, 원본 Llama 가중치, 재개용 대용량 체크포인트는 포함하지 않는다. 다시 학습할 필요는 없다.

## 1. 브랜치 받기

```text
git clone --branch codex/photo-booth-training-handoff https://github.com/byyourside-project/byyourside.git
cd byyourside
python -m venv .venv
```

Windows PowerShell에서는 `.\.venv\Scripts\Activate.ps1`, macOS/Linux에서는 `source .venv/bin/activate`로 가상환경을 활성화한다. 시스템에 따라 `python` 대신 `python3`를 사용한다.

```text
python -m pip install -r requirements-photo-train.txt
```

NVIDIA GPU를 사용할 때는 해당 장비에 맞는 CUDA용 PyTorch 빌드를 먼저 설치한다. CUDA, Apple MPS, CPU 순서로 사용 가능한 장치를 선택한다. 이 실행에는 마이크/STT용 Python 패키지를 추가로 설치할 필요가 없으며, 음성 인식은 기존 브라우저 기능을 사용한다.

## 2. 원본 모델 받기

Meta Llama 3.2 접근 권한이 승인된 Hugging Face 계정으로 로그인한다. 읽기 토큰은 터미널의 숨김 입력에 넣고 채팅·문서·저장소에 적지 않는다.

```text
hf auth login
hf download meta-llama/Llama-3.2-1B-Instruct --revision 9213176726f574b556790deb65791e0c5aa438b6 --include config.json generation_config.json model.safetensors
```

이 다운로드는 인터넷이 필요하다. 앱은 로그인한 같은 환경의 로컬 모델 캐시를 읽는다. `HF_HOME`을 따로 지정한다면 다운로드와 실행에서 같은 경로를 사용한다. 학습된 추가 토큰을 포함한 토크나이저는 이미 브랜치의 `artifacts/photo_booth_llama_v09/tokenizer/`에 있다.

## 3. 학습 모델로 실행

```text
python scripts/run_photo_booth.py --trained
```

준비 완료 메시지가 나오면 `http://127.0.0.1:8770/`을 연다. 화면에 **학습 모델 연결됨**이 표시되는지 확인한다. Windows에서는 가상환경을 만든 뒤 `start_photo_booth.cmd`로도 실행할 수 있다.

`--trained`를 빼고 실행하면 기존 키워드 시연만 사용한다. 학습 모델로 실행한 화면에서는 ‘명령 해석’ 선택으로 두 방식을 비교할 수 있다. 모델 오류나 실행 보류가 발생해도 자동으로 키워드 방식에 넘겨 명령을 실행하지 않는다.

## 4. 노트북에서 확인할 것

1. 카메라를 연결하고 브라우저의 해당 사이트 카메라 권한을 허용한다. 장치가 없으면 미리보기가 나오지 않는다.
2. 먼저 문장 입력으로 “타이머를 삼 초로 설정해 줘”, “화면 확대해 줘”를 시험한다.
3. 실제 카메라 미리보기가 보이면 배치·프레임·타이머 버튼과 명령을 비교한다.
4. “사진 찍어줘”는 모델의 촬영 제안을 확인한 뒤 현재 설정으로 시작한다. 시험에 사용한 GPU PC에는 카메라가 연결되지 않아 실제 촬영은 다른 노트북에서 확인해야 한다.
5. ‘말하기’로 실제 STT 결과와 최종 동작을 확인한다. 브라우저가 음성 인식을 지원하지 않으면 문장 입력과 화면 버튼으로 시험한다. 브라우저 STT는 네트워크를 사용할 수 있으므로 전체 음성 흐름이 오프라인이라고 가정하지 않는다.
6. 상단 ‘명령 가이드’에서 기능별 문장과 한 번에 한 가지씩 요청하는 방법을 확인한다.

창이나 터미널에서 Ctrl+C를 누르면 서버를 종료한다. 메모리가 부족해 모델을 로드하지 못하면 오류를 기록하고, `--trained` 없이 화면·카메라를 먼저 점검한다. 이는 학습 모델 시험을 통과했다는 뜻은 아니다.

## 결과와 기록

- 전체 1회 학습: 1,280개 / 1,280단계.
- 검증: 193/256(75.39%). 최종 시험 256개는 미평가.
- 오답의 주된 유형은 정상 명령을 실행 보류로 분류하거나 배치와 촬영 화면을 혼동하는 것이다.
- [학습 결과와 오답](GPU_PC_RESULT.md), [앱 연결 상세](model_integration.md).
- 결과 반영 엑셀: `outputs/photo_booth/음성포토존_마음AI형식_검수전_v09.xlsx`. 학습 순서·손실, 검증 실제 출력·정답/오답·오류 유형, ‘학습 결과’ 시트를 포함한다. 사람 검수는 여전히 완료되지 않았다.

별도 노트북에서의 텍스트 명령, STT, 카메라 촬영 결과는 각각 구분해서 기록한다. 새 시험 결과로 기존 검증 정답을 고치거나 최종 시험을 반복 사용하지 않는다.
