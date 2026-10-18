# 로컬 학습 시험 기록

작성일: 2026-10-17. 목표 모델은 **Meta Llama 3.2 1B Instruct**다. 마음AI의 학습 코드가 없어 독립적인 작은 시험 경로를 만들었다. 이것은 마음AI의 최종 토큰·학습·보드 변환 절차와 동일하다고 보장하지 않는다.

이 문서는 초기 Mac 시험 기록이다. 이후 GPU PC에서 전체 1회 학습과 검증을 완료했다. 현재 결과는 [GPU PC 학습·검증 결과](GPU_PC_RESULT.md)를 기준으로 본다.

## 현재 결과

- [v09 데이터 초안](dataset_v09_draft.json)에서 학습 1,280개, 검증 256개, 시험 256개의 `prompt`/`completion` JSONL을 생성했다. 아직 사람 검수 전이다.
- 새 기능 토큰 `<maum_0>`부터 `<maum_6>`, 종료 토큰 `<maum_end>`를 각 한 토큰으로 등록하고, 새 토큰의 임베딩과 LoRA 가중치를 함께 학습하는 코드를 준비했다.
- 이 Mac은 Apple M1, 통합 메모리 8GB다. 공개된 작은 **SmolLM2 135M** 모델로 2단계 시험을 실행해 어댑터와 토크나이저가 저장되는 것을 확인했다. 이 시험은 Llama 3.2의 학습이나 성능 평가가 아니다.
- 시험 모델의 검증 문장 3개에 대한 완전 일치 결과는 **0/3**이다. 2단계만 학습했으므로 기능 정확도를 판단할 자료가 아니다.
- 공식 Llama 3.2 1B Instruct 저장소 접근 신청은 2026-10-17에 **ACCEPTED**로 확인했다. 설정 파일, 토크나이저, 모델 가중치를 `/Users/ds/.cache/photo-booth/Llama-3.2-1B-Instruct/`에 보관했고, 토크나이저 128,256개와 대화 템플릿을 읽는 시험은 성공했다. 로컬 환경이 Hugging Face 계정 `ilyeong34`로 로그인된 것을 확인했다.
- **실제 Llama 3.2 1B Instruct 1단계 학습 성공:** Apple M1의 MPS에서 학습 손실 8.0721을 기록하고 LoRA 어댑터와 토크나이저를 `outputs/photo_booth/llama_1step/`에 저장했다. 검증 문장 3개 완전 일치 평가는 **0/3**이다. 1단계 학습은 실행 가능성을 확인한 것일 뿐 성능 개선의 증거가 아니다.

## 계정 연결 진행 기록

- 기존 학습 환경의 `huggingface_hub` 0.36.2는 `hf auth login` 실행 시 사용자 토큰 입력만 지원한다.
- 최신 CLI를 별도 임시 환경에 설치해 브라우저 기기 연결을 시험했다. 승인 화면에서 모델 읽기 외에 저장소 쓰기·관리 등 넓은 권한을 요청하는 것을 확인해 승인하지 않고 절차를 중단했다.
- 승인된 브라우저로 모델의 작은 파일을 직접 받았다. 가중치 파일은 브라우저 다운로드 시 제한 시간 안에 저장되지 않았다. 사용자가 읽기 전용 토큰을 로컬 터미널에 직접 입력한 뒤 `hf auth whoami`로 로그인 계정을 확인했다. 토큰 문자열은 이 문서·명령 기록·채팅에 남기지 않는다.

## 전체 1회 학습 시험: 중단

- 2026-10-17에 학습 데이터 1,280개를 각각 한 번 쓰는 1,280단계 실행을 시작했다. 진행 로그는 `outputs/photo_booth/llama_full_v09/training.log`다. 3단계까지 진행한 뒤 디스크 공간 부족 위험으로 중단했다. 전체 학습 결과물은 없다.
- 초기 3단계는 실행됐고 손실이 8.0713 → 7.5749 → 7.0850이었다. 이것만으로 성능 향상을 판단할 수 없다. 이 Mac의 단계당 시간은 수십 초로 관찰돼 전체 학습은 여러 시간이 걸릴 수 있다.
- 시작 당시 디스크 여유가 약 5.4GiB였으나 몇 분 만에 3.4GiB로 감소했다. macOS 스왑이 약 5.5GiB 사용 중이었다. 실행 중단 후 여유 공간은 약 4.4GiB로 회복됐다. 현재 8GB M1 Mac에서 이 방식의 1,280단계 학습을 계속하는 것은 디스크 공간 위험이 크다. 더 여유 있는 GPU 환경 또는 메모리 사용을 줄이는 학습 방식이 필요하다.

## 재현 및 다음 단계

1. 모델 본체 다운로드와 1단계 학습 시험은 완료했다. 인증 정보는 채팅이나 저장소 파일에 적지 않는다.
2. 별도 가상환경에 `requirements-photo-train.txt`를 설치한다.
3. `python3 scripts/prepare_photo_training_data.py`로 데이터를 다시 내보낸다.
4. `python3 scripts/train_photo_local.py --model /Users/ds/.cache/photo-booth/Llama-3.2-1B-Instruct --steps 4`로 단계를 늘리며 메모리와 속도를 확인한다. 8GB 메모리에서 부족하면 학습 설정 또는 GPU 환경을 바꾼다. 완전 학습을 바로 시작하지 않는다.
5. 성공하면 학습 데이터 검수 후 단계를 늘리고 `python3 scripts/evaluate_photo_local.py --pilot outputs/photo_booth/llama_pilot --limit 256`으로 검증 문장을 평가한다. 기본 모델과 비교하고, 최종 시험 데이터는 마지막에만 사용한다.

학습 코드: [데이터 변환](../../scripts/prepare_photo_training_data.py), [토큰 추가·학습](../../scripts/train_photo_local.py), [평가](../../scripts/evaluate_photo_local.py). 임시 토큰 번호는 [대응표](maum_format_mapping.md)에 있다.
