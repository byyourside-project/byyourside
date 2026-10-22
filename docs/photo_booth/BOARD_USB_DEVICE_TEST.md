# Android 보드 카메라·마이크·스피커 시행착오 및 인수인계

기록일: 2026-10-23 (PC 기준). 보드의 날짜/시간이 PC와 달라 로그와 녹음 파일명에는 10-22 또는 다른 날짜가 표시된다.

## 결론과 프로젝트 적용 방향

**현재 보드 펌웨어에서는 기본 Android 카메라 앱과 일반 오디오 앱을 그대로 사용하는 경로에 문제가 있었다. USB 장치를 직접 사용하는 경로로 테스트에 성공했다.**

| 대상 | 실패한 경로 | 성공했거나 확인한 경로 | 현재 판단 |
|---|---|---|---|
| 카메라 | 기본 Camera 앱: 켜자마자 종료. USB 웹캠 연결 후에도 Android 카메라 서비스는 0개로 표시 | UVCAndroid의 USB Camera 앱이 웹캠에 직접 접근, 640×480 YUYV 영상 표시 | 카메라 하드웨어는 동작. 현재 펌웨어에서 기본 Camera API 경로로 웹캠이 노출되지 않음 |
| 스피커 | 일반 Music/녹음 앱 재생: 무음 또는 잡음, USB 출력 준비 오류 | USB 오디오 장치에 `su 0 tinyplay ... -D 2 -d 0` 직접 재생. 사용자가 정상 소리를 들었다고 확인 | 현재 보드의 일반 오디오 출력 경로에 오류가 있음. 직접 재생 방식은 동작 |
| 마이크 | 기본 녹음 앱으로 생긴 초기 AMR 파일이 262/422바이트 등 매우 작았음. 정상 녹음으로 판단할 근거가 부족 | `su 0 tinycap`으로 두 USB 입력에서 약 10초 PCM WAV 생성. 제작한 녹음기 앱에서도 녹음 파일 생성 | 실제 신호는 수집됨. 명료한 목소리와 STT 품질은 추가 확인 필요 |

포토부스 보드 앱은 **UVC 웹캠 직접 입력 + USB 오디오 직접 녹음/재생**을 연결하는 방향으로 진행한다. 이번에 설치한 테스트 앱이 기존 웹 포토부스에 자동으로 연결된 것은 아니다. 브라우저 `getUserMedia` 또는 기본 카메라 API로 이번 웹캠을 사용할 수 있다고 가정하지 않는다.

## 환경과 연결 장치

- 보드 표기: Coretronic Reality / COREXOM **R6490Q**. 강의 자료의 QCS6490 보드 계열.
- Android 정보: `Lahaina for arm64`, Android 13 userdebug, 빌드 표기 `RB3-Gen2-QSSI-v1_5-0.0.10 test-keys`.
- 카메라: 삼성 **SPC-A1200MB**, USB 표시명 `SPC-A1200MB Webcam`, 제조사 표시 `Sonix Technology Co., Ltd.`.
- 별도 USB 오디오: **GeneralPlus USB Audio Device**, 입력과 출력이 모두 노출됨.
- USB 마우스: Logitech G102 LIGHTSYNC. 이전 사진의 Logitech 케이블은 웹캠이 아니라 마우스였다.
- 모니터는 보드의 사용 중인 USB-C 영상 연결로 동작. 디버깅을 위해 해당 케이블을 빼지 않았다.
- 테스트 당시 PC IP `192.168.0.30`, 보드 IP **`192.168.0.26`**, ADB `192.168.0.26:5555`. DHCP이므로 재연결 시 주소가 바뀔 수 있다.
- 테스트 당시 ALSA 카드: `0` 보드 내장, `1` 웹캠 마이크, `2` GeneralPlus 입력/출력. 카드 번호는 고정값이 아니므로 다시 확인한다.

## 1. 네트워크 연결부터 막혔던 과정

1. 랜 포트의 LED는 켜졌지만 보드 브라우저에서 PC 테스트 페이지가 열리지 않았다.
2. WebView Browser Tester에서 `http://192.168.0.30:8877/board-check` 요청에 `net::ERR_INTERNET_DISCONNECTED`가 표시됐다. 이 상태는 웹 서버 정상 여부와 별개로 보드 네트워크가 준비되지 않은 상태였다.
3. Android 설정 화면에는 예상한 Ethernet/IP 상세 메뉴가 없었다. 존재하지 않는 메뉴를 계속 찾는 방식으로 진행하지 않는다.
4. 공유기 DHCP 목록의 `.25`는 후보였을 뿐 보드라는 확인이 없었다. 응답도 없었다. DHCP 기록과 LED만으로 통신 성공을 판단하면 안 된다.
5. 보드에 RJ45가 두 개 있어 다른 포트로 바꾸는 시험을 안내했고, 이후 사용자가 주소 끝자리 **26**을 확인했다. 포트별 펌웨어 지원 차이는 별도로 입증하지 않았다.
6. PC에서 `.26` ping 응답과 TCP 5555 열림을 확인한 후 `adb connect` 성공, `adb devices`에 `device`로 표시됐다.

전원 LED, 랜 링크 LED, IP 할당, PC에서 실제 통신, ADB 연결은 각각 따로 확인한다. 기존 PC 방화벽에는 해당 Python 실행 파일의 허용 규칙이 있었으며 방화벽 차단으로 확정하지 않았다. 공유기 설정 변경이나 보드 초기화는 하지 않았다.

## 2. 기본 카메라 앱이 즉시 종료된 이유

카메라를 꽂지 않았을 때도 앱이 바로 종료된다는 사용자 관찰이 맞았다. USB 연결 여부를 반복 확인하기 전에 실제 로그를 조사했다.

`dumpsys media.camera`:

```text
Number of camera devices: 0
Number of normal camera devices: 0
Number of public camera devices visible to API1: 0
```

기본 앱 패키지 `com.android.camera2`의 카메라·마이크 권한은 자동 회수 상태였다. 테스트 과정에서 CAMERA/RECORD_AUDIO 권한을 복구한 후 다시 실행했고, 다음 예외로 종료되는 것을 확인했다.

```text
Process: com.android.camera2
java.lang.ArrayIndexOutOfBoundsException: length=0; index=0
AndroidCameraAgentImpl$AndroidCameraDeviceInfo.getCharacteristics
CameraController.getCharacteristics
CaptureModule.getFacingFromCameraId
CaptureModule.init
CameraActivity.onCreateTasks
```

카메라 목록이 비어 있는데 첫 카메라의 특성을 조회하는 앱 오류다. ‘카메라가 없습니다’라는 안내 대신 종료된다. 권한 복구만으로 해결되지 않았다.

**웹캠을 꽂은 뒤에도** USB 목록에는 정상 표시됐고 `/dev/video2`, `/dev/video3`의 이름이 해당 웹캠으로 확인됐지만 Android 카메라 서비스는 계속 0개였다. 이는 USB/커널 인식과 Android 기본 카메라 서비스 노출이 서로 다른 단계임을 보여준다. ‘첫 카메라가 없으니 아무 카메라나 꽂으면 무조건 해결’은 잘못된 가정이다.

### 설치된 다른 앱 확인과 잘못 고른 테스트 앱

- Camera (`com.android.camera2`)
- Snapdragon Camera (`org.codeaurora.snapcam`)
- Camera2 Video (`com.example.android.camera2.video`)
- Cam2Test (`com.qualcomm.qti.cam2test`)
- Secure Camera Sample (`com.qualcomm.qti.seccamsample`)

Cam2Test를 일반 웹캠 테스트용으로 실행했지만 다음 메시지가 나왔다.

```text
Unable to create MediaSecureProcessor ... provide correct destination.
```

이는 보안 처리 기능을 생성하지 못한 오류다. 이 결과로 웹캠 고장을 판단할 수 없다. 나열한 다른 앱 모두를 USB 웹캠 지원 앱으로 검증한 것은 아니다. Voice Activation은 호출어/음성 활성화 테스트 앱으로 보이며 일반 녹음 테스트의 대체재로 검증하지 않았다.

### 실제 성공한 카메라 경로

공개 소스 [UVCAndroid](https://github.com/shiyinghan/UVCAndroid)의 [1.0.13 릴리스](https://github.com/shiyinghan/UVCAndroid/releases/tag/1.0.13)에서 `app-debug.apk`를 설치했다.

- 패키지: `com.herohan.uvcapp`
- 앱 이름: **USB Camera**
- USB 장치 선택: `SPC-A1200MB Webcam`
- 로그: `frameSize=(640,480)@YUYV`
- 화면에 실제 주변 물체 영상이 표시됐고 사용자가 성공을 확인했다.

기본 앱에서 ‘기본 카메라로 지정’하는 설정만으로 해결되는 상황이 아니다. 기본 Camera API에 노출하려면 현재 펌웨어의 외부 카메라 지원을 따로 조사/구현해야 한다. 그 작업은 이번에 수행하지 않았다.

## 3. 스피커: 장치 인식과 실제 출력은 별개

`dumpsys audio`에서 GeneralPlus가 `USB Headset Out`으로 잡히고, 미디어 출력도 USB를 선택한 상태였다. 음소거는 꺼져 있었고 당시 볼륨은 3/15였다. 그러나 이것만으로 소리가 난다고 판단할 수 없다.

일반 Music 앱에 48kHz/16bit/stereo의 자체 생성 멜로디 WAV를 넣었을 때 사용자가 **잡음만 들린다**고 보고했다. 로그에는 다음 오류가 반복됐다.

```text
USB_AUDIO_RX: ASoC: backend prepare failed -131
pcm_open_prepare_helper: pcm_prepare returned -1
```

일반 Music 앱을 종료하고 같은 파일을 해당 USB PCM 장치에 직접 재생했다.

```sh
su 0 tinyplay /sdcard/Music/speaker-test.wav -D 2 -d 0
```

명령은 오류 없이 끝났고 사용자가 **“방금 소리 들림”**이라고 확인했다. 물리적 스피커/USB 연결은 사용할 수 있고, 이번 실패는 일반 Android 오디오 경로와 구분해야 한다. 오디오 HAL/드라이버의 정확한 결함 위치나 수정 방법까지 확정한 것은 아니다.

## 4. 마이크: 직접 녹음 성공, 음성 품질은 추가 확인

웹캠 내장 마이크와 GeneralPlus 별도 입력이 모두 ALSA 입력으로 보였다.

- 일반 shell의 `tinycap`은 `/dev/snd/... Permission denied`로 실패했다.
- 기본 Sound Recorder는 권한/대기 화면과 작은 AMR 파일만으로 성공 여부를 확정할 수 없었다. ‘파일이 생김 = 목소리 녹음 성공’은 아니다.
- 스피커와 같은 직접 접근 방식에서 **기존 개발 펌웨어의 `su 0`**를 사용하니 약 10초 WAV 생성에 성공했다.

```sh
su 0 tinycap /data/local/tmp/mic-webcam.wav -D 1 -d 0 -c 1 -r 48000 -b 16 -T 10
su 0 tinycap /data/local/tmp/mic-usb.wav -D 2 -d 0 -c 1 -r 48000 -b 16 -T 10
```

| 입력 | 녹음 길이 | Peak (16bit 기준) | RMS | 판단 |
|---|---:|---:|---:|---|
| 웹캠 내장 | 9.984초 | 87 | 19.9 | 신호가 매우 작음 |
| GeneralPlus USB | 9.984초 | 2,227 | 227.3 | 웹캠보다 큰 신호 수집 |

이 수치는 해당 시점의 녹음 결과다. 큰 신호가 반드시 명료한 사람 목소리를 의미하지는 않는다. USB 녹음은 stereo로 변환하고 4배 증폭해서 직접 재생했지만, **사용자가 본인 목소리를 명료하게 들었다는 확인은 기록되지 않았다.** 마이크 테스트 완료나 STT 성공으로 보고하지 않는다.

## 5. 이번에 제작·설치한 보드 전용 앱

| 앱 | 패키지 | 기능 | 확인 범위 |
|---|---|---|---|
| 곁 USB 녹음기 | `com.byyourside.usbrecorder` | USB 입력·출력 선택, 녹음 시작/정지, WAV 저장, 듣기, 파일 내보내기 | 앱 버튼으로 5.205초 WAV 생성, 249856 frames/499756 bytes 헤더와 신호 확인, 재생 명령 성공. 내보내기 완료까지 별도 검증하지 않음 |
| 곁 USB 스피커 | `com.byyourside.usbspeaker` | USB 출력 선택, 테스트 멜로디, 파일 불러오기, 변환, 재생/정지 | WAV 파일 선택과 변환, 앱 버튼으로 재생/정지 상태 확인. 이후 사용자가 제공한 MP3를 불러와 약 283.9초 변환본과 재생 상태 확인 |

음악 시험에는 사용자가 직접 제공한 MP3를 사용했다. 음원은 저장소/배포 ZIP에 포함하지 않는다. 온라인에서 별도 무단 음원을 다운로드하지 않았다.

### 구현과 제한

- **일반 Android 휴대폰용 독립 실행 앱이 아니다.** 현재 개발 보드의 ADB 및 `su 0` 기능으로 로컬 연결 도우미를 실행하는 방식이다.
- APK 설치만으로는 직접 접근 기능이 동작하지 않는다. **보드 재부팅 후 PC에서 실행 스크립트가 필요하다.**
- 녹음기 도우미는 보드 `127.0.0.1:18779`, 스피커 도우미는 `127.0.0.1:18781`에만 바인딩한다. 앱별 로컬 인증값을 확인하고 음성을 외부 서버로 전송하지 않는다.
- 녹음은 48kHz/16bit/mono WAV, 최대 5분. 앱 화면을 벗어나도 작업이 계속되므로 정지 버튼으로 종료한다.
- 스피커 앱은 Android 디코더가 지원하는 오디오를 48kHz/16bit/stereo WAV로 변환한다. 모든 코덱을 시험한 것은 아니다. PCM 크기 64MiB/변환 시간 2분 제한이 있다.
- 두 앱이 같은 USB 장치를 동시에 사용하지 않도록 다른 앱의 녹음/재생을 먼저 정지한다. 앱 간 공통 오디오 잠금은 아직 구현하지 않았다.
- 직접 재생 음량은 Android 미디어 볼륨과 다를 수 있다. 스피커 물리 음량을 낮게 시작한다.
- 녹음기 원본: `/data/local/tmp/byyourside-audio/rec-*.wav`. 앱 제거만으로 이 폴더가 지워지는 것은 아니며 보드 초기화 등에 대비해 필요한 파일은 내보낸다.
- 스피커 변환본: `/sdcard/Android/data/com.byyourside.usbspeaker/files/Music/`. 앱 제거 시 사라질 수 있다.
- 부트 자동 실행 설정, 펌웨어, SELinux 정책, 장치 파일 권한은 변경하지 않았다. 앱은 개발용 서명이다.

## 6. 다시 시작할 때

추가 편의 앱 **곁 IP 확인** (`com.byyourside.boardip`)을 보드에 설치했다. 앱을 열면 현재 IPv4, 연결 인터페이스, `adb connect IP:5555` 명령을 보여주며 1.5초마다 갱신한다. 루트/별도 도우미 없이 실행된다. 현재 `eth1 / 192.168.0.26` 표시를 확인했다. ADB 포트 자체 수신과 PC 접근/인증 성공은 구분한다. 물리적 랜선 재연결 시험은 아직 별도로 하지 않았다. 배포물은 작업공간 `outputs/byyourside-board-ip.zip`에 있다.

1. 보드 전원·영상 연결·USB 웹캠·USB 오디오·랜선을 연결한다.
2. 실제 보드 IP를 확인하고 PC에서 ADB 연결한다. `.26` 및 ALSA 카드 1/2를 영구 주소/번호로 가정하지 않는다.
3. 각 배포 폴더에서 다음을 실행한다. 스크립트는 APK 설치/갱신, 도우미 시작, 앱 실행을 수행한다.

```powershell
pwsh -NoProfile -File .\start-recorder.ps1 -BoardAddress 192.168.0.26:5555
pwsh -NoProfile -File .\start-speaker.ps1 -BoardAddress 192.168.0.26:5555
```

4. 카메라는 **USB Camera**, 녹음은 **곁 USB 녹음기**, 음악 재생은 **곁 USB 스피커**에서 확인한다.
5. 장치 인식만 확인하고 끝내지 말고 영상 표시, 목소리 녹음/청취, 멜로디 출력으로 각각 검증한다.

## 7. 산출물과 증거 위치

이 문서는 Git 저장소 내부에 있다. 아래 배포물/로그는 **이 PC의 작업공간 산출물이며 저장소에 포함되지 않았다.** 작업공간 기준 경로:

- `outputs/byyourside-usb-recorder.zip`: APK, 소스, 실행 스크립트, ADB 도구, 사용 설명서.
- `outputs/byyourside-usb-speaker.zip`: APK, 소스, 실행 스크립트, ADB 도구, 사용 설명서.
- `work/usb-recorder/`, `work/usb-speaker/`: 빌드 작업 소스. 향후 앱 통합 시 저장소에 별도 편입 필요.
- `outputs/board-camera-diagnostics/camera-crash.txt`: 기본 앱 실제 예외.
- `outputs/board-camera-diagnostics/camera-service.txt`: Android 카메라 서비스 상태.
- `outputs/board-camera-diagnostics/usb-webcam-connected.txt`: 웹캠 연결 후 USB 상태.
- 같은 폴더 `uvc-preview.png`, `recorder-app.png`, `speaker-app.png`: 성공 화면.
- 같은 폴더 테스트 WAV/AMR: 입력·출력 진단 자료. 사용자 음성 및 주변 영상이 포함될 수 있으므로 별도 공개하지 않는다.

## 다음 구현에서 남은 일

1. UVC 카메라 입력을 실제 포토부스 미리보기·촬영 기능과 연결한다.
2. 직접 USB 마이크 입력을 STT로 전달하고 실제 한국어 발화 인식률을 확인한다.
3. USB 출력에 안내음/TTS를 연결한다. 아직 보드 STT·학습 LLM·TTS의 전체 흐름을 검증하지 않았다.
4. 도우미의 안전한 실행/종료, 재부팅 후 시작 방식, 앱 간 장치 점유 관리, 소리 크기 제어를 제품 형태로 정리한다.
5. 장기적으로 기본 Camera API/Android 오디오 경로를 수정할지, 앱의 직접 USB 방식을 유지할지 결정한다. 이번 성공은 후자에 대한 기능 시험이다.
