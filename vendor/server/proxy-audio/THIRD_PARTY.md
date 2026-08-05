# proxy-audio third-party runtime inventory

작성 기준: `2026-08-04 KST`

`proxy-audio`가 운영 Mac에서 실행할 때 사용하는 외부 런타임 목록이다. 아래 binary와
model은 Git, Android APK, `proxy-audio` npm package에 포함하지 않는다.

| 구성 요소 | 현재 운영값 | 라이선스·배포 경계 |
|---|---|---|
| whisper.cpp | `whisper-cli 1.9.1`, `/opt/homebrew/bin/whisper-cli` | Homebrew formula metadata 기준 MIT. host에 별도 설치하며 APK·저장소에 bundle하지 않는다. |
| Whisper model | `ggml-large-v3-turbo.bin`, 1,624,555,275 bytes, SHA-256 `1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69` | runtime 전용 파일이다. Git에 넣지 않으며 재배포 전에는 취득한 배포처의 model license를 다시 확인한다. |
| FFmpeg/ffprobe | 운영 binary `8.1.1`, `/opt/homebrew/bin/ffmpeg`, `/opt/homebrew/bin/ffprobe` | 운영 binary가 `--enable-gpl`로 build되어 있다. host 외부 의존성으로만 사용하고 APK·저장소·배포 archive에 bundle하지 않는다. binary를 재배포할 때는 해당 GPL 의무를 별도 이행한다. |
| Node.js | launchd runtime `24.13.1` | host에 별도 설치한다. |

## 무결성 gate

- model은 launchd의 `AUDIO_PROXY_WHISPER_MODEL_SHA256`과 실제 SHA-256이 일치해야 한다.
- `scripts/doctor.sh`와 `/ready`가 binary·model·checksum을 모두 확인한다.
- checksum 불일치나 runtime 누락 시 audio만 `ready=false`가 되며 기존 기능은 유지한다.

## 제거

audio runtime을 폐기할 때는 launchd 서비스를 먼저 중지한 뒤 운영 디렉터리의 model,
암호화 transcript DB, audio 전용 secret을 운영 절차에 따라 제거한다. 저장소 삭제만으로
host runtime이 삭제되지는 않는다.
