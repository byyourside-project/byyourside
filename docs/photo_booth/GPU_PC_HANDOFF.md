# 다른 컴퓨터에서 사진관 음성 명령 학습 이어하기

2026-10-18 기준. GitHub의 `codex/photo-booth-training-handoff` 브랜치에는 기존 프로젝트와 사진관 시연 앱, 학습 데이터 및 코드가 함께 있다. 채팅 기록 없이 이 브랜치에서 작업을 이어갈 수 있다.

## 현재 상태

- 목표: Meta `meta-llama/Llama-3.2-1B-Instruct`에 사진관 음성 명령을 학습한다. 현재 8개 사용자 정의 토큰(`<maum_0>`~`<maum_6>`, `<maum_end>`)을 추가하고 LoRA를 학습하는 자체 시험 코드다. 마음AI의 최종 코드와 같다고 보장하지 않는다.
- 데이터: `outputs/photo_booth/local_training_v09/`에 학습 1,280개, 검증 256개, 최종 시험 256개가 있다. 합성 초안이므로 본격 학습 전 사람 검수가 필요하다.
- 현재 Mac에서 모델 다운로드와 1단계 학습은 성공했다. 검증 3개 완전 일치 0개다. 이는 성능 결과가 아니라 실행 확인이다.
- 1,280단계 전체 1회 학습은 3단계 후 중단했다. 8GB 통합 메모리와 낮은 디스크 여유 때문에 스왑 파일이 늘어 디스크 여유가 5.4GiB에서 3.4GiB로 줄었다. 전체 학습 결과물은 없다.
- 자세한 실행 근거와 제한은 `docs/photo_booth/local_training.md`에 있다.

## 새 컴퓨터에서 먼저 확인할 것

1. 운영체제, GPU 이름과 GPU 메모리(VRAM), 시스템 RAM, 빈 저장 공간을 확인한다. 권장 여유는 전용 GPU VRAM 12~16GB 이상, RAM 16GB 이상, 빈 저장 공간 20GB 이상이다. 이는 현재 코드의 보수적 추정치이며 최소 사양은 아니다.
2. `codex/photo-booth-training-handoff` 브랜치를 내려받는다. 모델 본체와 계정 토큰은 포함하지 않았다. 사진관 시연 앱은 `web/photo_booth/`와 `scripts/run_photo_booth.py`에 있고, 학습된 모델을 연결한 버전은 아직 아니다.
3. Hugging Face 계정에서 Meta Llama 3.2 접근이 승인된 상태인지 확인한다. 토큰은 새 컴퓨터에서 직접 로그인하며 채팅, 문서, 코드에 기록하지 않는다.
4. 새 컴퓨터의 Python 및 GPU용 PyTorch가 정상 설치되는지 확인한다. `requirements-photo-train.txt`의 PyTorch 버전은 기존 Mac 시험 버전이다. CUDA 컴퓨터에서는 공식 PyTorch 설치 안내에 맞는 GPU 빌드를 먼저 설치하고, 나머지 패키지 버전을 맞춘다.

## 실행 순서

프로젝트 최상위 폴더에서:

```text
hf auth login
hf auth whoami
python scripts/prepare_photo_training_data.py
python scripts/train_photo_local.py --model meta-llama/Llama-3.2-1B-Instruct --steps 4 --output outputs/photo_booth/gpu_pilot
python scripts/evaluate_photo_local.py --model meta-llama/Llama-3.2-1B-Instruct --pilot outputs/photo_booth/gpu_pilot --limit 10
```

4단계 시험이 통과하고 메모리 여유가 확인되면, 검수된 데이터로 1,280단계 한 번을 실행한다. 현재 코드는 한 단계에 학습 예시 하나를 사용하고 1,280단계가 끝난 뒤 결과를 저장한다. 긴 실행 전 중간 저장·재개 기능을 추가하는 것이 좋다.

```text
python scripts/train_photo_local.py --model meta-llama/Llama-3.2-1B-Instruct --steps 1280 --output outputs/photo_booth/gpu_full_v09
python scripts/evaluate_photo_local.py --model meta-llama/Llama-3.2-1B-Instruct --pilot outputs/photo_booth/gpu_full_v09 --limit 256
```

학습 완료 여부는 `pilot_metrics.json`, `adapter/`, `tokenizer/` 생성으로 확인한다. 평가 결과는 `evaluation.json`의 `exact_matches`와 개별 오답을 함께 본다. 최종 시험 데이터는 설정을 확정한 뒤 한 번만 평가한다.

## 새 채팅에 전달할 설명

> 사진관 음성 명령 프로젝트를 이어서 도와줘. `https://github.com/byyourside-project/byyourside`의 `codex/photo-booth-training-handoff` 브랜치를 내려받고 `docs/photo_booth/GPU_PC_HANDOFF.md`와 `docs/photo_booth/local_training.md`를 먼저 읽어줘. 사진관 앱은 `web/photo_booth/`와 `scripts/run_photo_booth.py`에 있어. 아직 규칙 기반 시연이며 학습 모델은 연결되지 않았다. Llama 3.2 1B Instruct에 `<maum_0>`~`<maum_6>`, `<maum_end>` 토큰을 추가해 1,280개 학습 데이터로 LoRA를 학습할 계획이야. 우선 이 컴퓨터의 GPU와 메모리, 디스크 공간을 확인하고 4단계 시험과 평가를 실행해줘. 성공하면 중간 저장·재개를 준비한 다음 검수된 데이터로 전체 1회 학습을 진행하고 결과를 기록해줘. 인증 토큰은 채팅에 출력하지 마.
