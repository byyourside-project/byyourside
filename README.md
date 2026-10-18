# 음성 조작 무인 포토존

촬영 위치에서 화면을 터치하지 않고 배치, 프레임, 타이머, 촬영 화면 구도를 음성으로 바꾼 뒤 사진을 찍는 PC 시연 앱입니다. 행사장처럼 카메라·조작 화면과 촬영 위치가 떨어진 공간을 가정합니다.

**PC 시연 영상**

https://github.com/user-attachments/assets/c7effb1f-1730-46ce-8f6c-a51e0414538c

## 실행

Python 3으로 프로젝트 폴더에서 실행합니다.

```bash
python scripts/run_photo_booth.py
```

브라우저에서 `http://127.0.0.1:8770/`을 엽니다. 이 방식은 앱과 기존 키워드 해석기를 사용하며 모델 설치가 필요 없습니다. 카메라 접근을 허용하고, 브라우저에서 음성 인식이 지원되면 **말하기**를 누릅니다. 문장 입력과 화면 버튼도 사용할 수 있습니다.

학습된 Llama 3.2 1B 어댑터로 명령을 해석하려면 [다른 PC 설치 안내](docs/photo_booth/OTHER_LAPTOP_TEST.md)에 따라 원본 모델과 Python 패키지를 준비한 뒤 실행합니다.

```bash
python scripts/run_photo_booth.py --trained
```

Windows에서는 같은 준비를 마친 뒤 `start_photo_booth.cmd`를 사용할 수 있습니다. 원본 모델의 가중치와 계정 토큰은 이 브랜치에 포함하지 않았습니다.

## 기능과 현재 결과

- 1장 또는 4컷 배치, 프레임 4종, 3·10·15·20초 타이머
- 4컷 촬영 시 네 칸을 보면서 찍거나 한 장씩 큰 화면으로 찍는 방식
- 화면 확대·축소·상하좌우 이동과 구도 초기화, 현재 설정으로 촬영
- 한국어 음성 명령 또는 문장 입력. 학습 모델 사용 시 기능과 인자를 해석하고 앱이 실행

학습용 문장 1,280개로 Llama 3.2 1B를 한 차례 학습했습니다. 합성 검증 문장 256개 중 193개(75.39%)의 출력이 정답과 완전히 일치했습니다. 최종 시험 256개와 실제 행사 소음·원거리 음성 인식의 정확도는 아직 측정하지 않았습니다. [학습 결과와 오류 분석](docs/photo_booth/GPU_PC_RESULT.md)을 참고하세요.

## 자료

- [기능·사용 안내](docs/photo_booth/README.md)
- [요구사항](docs/photo_booth/requirements.md) · [프로젝트 계획](docs/photo_booth/project_plan.md)
- [데이터 설계](docs/photo_booth/data_design.md) · [v09 데이터 점검](docs/photo_booth/dataset_v09_review.md)
- [마음AI 예시 형식 데이터시트](outputs/photo_booth/음성포토존_마음AI형식_검수전_v09.xlsx)
- [모델 연결](docs/photo_booth/model_integration.md) · [다른 PC 설치 안내](docs/photo_booth/OTHER_LAPTOP_TEST.md)

모델의 출력 토큰 번호와 인자값은 팀의 임시 사양입니다. 마음AI 최종 코드와 QCS6490 보드 적용, 양자화는 아직 이 앱에 연결되지 않았습니다.
