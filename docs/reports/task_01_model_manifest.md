# Task 01 Model Manifest

- 작성일: 2026-09-28
- 작업 ID: Task 01 (노트북 환경 확인 및 한국어 VAD/STT PoC)
- 담당: Gemini 개발자 / 검수: 사용자와 PM

---

## 1. 개요

본 문서는 Task 01 수행을 위해 공식 sherpa-onnx 릴리스 배포처에서 다운로드하여 검증한 음성 활동 감지(VAD) 및 음성 인식(STT) 모델 파일의 메타데이터와 체크섬을 기록한다. 다운로드 URL은 공식 문서(`https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html`)에 명시된 공식 배포처를 사용하였다.

---

## 2. 모델 명세 및 체크섬

### 2.1 Silero VAD

- **모델명**: Silero VAD (ONNX)
- **용도**: 음성 활동 감지 및 발화 구간화 (Voice Activity Detection & Segmentation)
- **공식 다운로드 URL**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx`
- **릴리스 태그**: `asr-models`
- **파일명**: `silero_vad.onnx`
- **파일 크기**: 643,854 바이트 (628.76 KB)
- **SHA-256 Checksum**: `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`
- **입력 규격**:
  - 샘플레이트: 16,000 Hz (mono float32)
  - 윈도우 크기: 512 samples (32 ms)
- **라이선스**: MIT License (Silero VAD / Snakers4)

---

### 2.2 SenseVoice INT8 (Small)

- **모델명**: SenseVoice Small (sherpa-onnx INT8 ONNX 양자화 버전)
- **용도**: 한국어 비스트리밍 음성 인식 (STT)
- **원본 출처**: Alibaba FunAudioLLM / ModelScope (`https://www.modelscope.cn/models/iic/SenseVoiceSmall`)
- **공식 다운로드 URL**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`
- **릴리스 태그**: `asr-models`
- **아카이브 파일**: `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`
- **포함 파일별 상세**:

| 파일명 | 용도 | 크기 (Bytes) | 크기 (MB/KB) | SHA-256 Checksum |
|---|---|---|---|---|
| `model.int8.onnx` | INT8 양자화 음향 모델 | 239,233,841 | 228.15 MB | `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51` |
| `tokens.txt` | 음소/문자/특수 토큰 매핑 테이블 | 315,894 | 308.49 KB | `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc` |

- **입력 규격**:
  - 샘플레이트: 16,000 Hz (mono float32)
  - 특징 추출: 80-dim filter bank, 25ms window, 10ms hop
  - 지원 언어: `ko` (한국어), `zh` (중국어), `en` (영어), `ja` (일본어), `yue` (광둥어)
  - 언어 설정: `language="ko"`
  - 역 텍스트 정규화: `use_itn=True`
- **라이선스**: MIT License (`https://github.com/modelscope/FunASR?tab=readme-ov-file#license`)

---

## 3. 부속 테스트 오디오

- **파일명**: `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav`
- **출처**: 공식 모델 아카이브 내 번들 제공 테스트 파일
- **규격**: 16,000 Hz, 1 채널 (mono), 16-bit PCM, 4.61초 (73,728 samples)
- **정답 텍스트 (Ground Truth)**: `조금만 생각을 하면서 살면 훨씬 편할 거야.`
