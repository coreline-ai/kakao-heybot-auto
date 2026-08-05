# proxy-audio

카카오 `type=18`의 검증된 MP3/M4A/WAV source reference를 내려받아 FFmpeg로 16 kHz mono WAV로 정규화하고 `whisper.cpp`로 한국어 STT를 수행하는 독립 프록시다.

- 외부 진입점: proxy-manager `/v1/audio`
- 내부 포트: `4363`
- source: HTTPS `talk.kakaocdn.net`만 허용, redirect 차단
- transcript: AES-256-GCM 암호화 SQLite 저장, 기본 TTL 24시간
- raw/normalized audio: terminal 상태에서 즉시 삭제
- 실제 모델과 바이너리는 저장소에 포함하지 않는다.

`AUDIO_PROXY_RUNNER_MODE=fake`는 hermetic 자동 테스트 전용이며 운영에서 사용하지 않는다.

## 운영 활성 gate

1. `whisper-cli`와 benchmark를 통과한 model을 `runtime/models/`에 둔다.
2. model의 64자리 소문자 SHA-256을 `AUDIO_PROXY_WHISPER_MODEL_SHA256`에 설정한다.
3. `scripts/doctor.sh`와 `/ready`를 통과한 뒤 manager registry의 audio entry를 활성화한다.
4. PD20은 manager `127.0.0.1:4340/v1/audio`만 호출하며 4363에 직접 연결하지 않는다.

model, binary, transcript DB, source URL, secrets는 Git에 포함하지 않는다. `/ready`는 model checksum을 확인한다. 동일 file size/mtime은 5분간 cache하고, 만료 후에는 마지막 검증 결과를 즉시 반환하며 하나의 background SHA-256 재검증만 수행한다. size/mtime가 바뀌면 새 model을 동기 검증해 fail-closed를 유지한다.

현재 host runtime의 버전·checksum·라이선스·비번들 경계는
[`THIRD_PARTY.md`](THIRD_PARTY.md)에 기록한다.

## API

```text
POST   /v1/audio/transcriptions
GET    /v1/audio/transcriptions/<jobId>?chatId=<chatId>
DELETE /v1/audio/transcriptions/<jobId>?chatId=<chatId>
DELETE /v1/audio/transcriptions/<jobId>/purge?chatId=<chatId>
GET    /health
GET    /ready
POST   /v1/self-test/readiness
```

모든 job API는 manager 전용 Bearer secret을 요구한다. `requestId`는 idempotent하며 조회·취소·삭제는 생성 당시 `chatId`가 정확히 일치해야 한다.

## 자동 검증

```bash
npm install
npm test
AUDIO_PROXY_RUNNER_MODE=fake npm run doctor
```

fake mode는 인증·job·암호화·파일 판정 회귀에만 사용하며 실제 STT 완료 판정으로 사용하지 않는다.

## PD20 제한 E2E

합성·동의된 fixture와 잠금 해제된 PD20에서만 다음 canary를 사용한다. 기본 대상은
코어라인 AI 연구소(R01)이며 다른 방에는 전송하지 않는다.

```bash
FIXTURE=/tmp/heybot-audio-canary.m4a FORMAT=m4a ./scripts/run_live_audio_canary_pd20.sh
FIXTURE=/tmp/heybot-audio-canary.mp3 FORMAT=mp3 ./scripts/run_live_audio_canary_pd20.sh
FIXTURE=/tmp/heybot-audio-canary.wav FORMAT=wav ./scripts/run_live_audio_canary_pd20.sh

ENGINE=CODEX IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST=true ./scripts/run_live_audio_canary_pd20.sh
ENGINE=GROK IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST=true ./scripts/run_live_audio_canary_pd20.sh

IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST=true \
IRIS_LIVE_AUDIO_CANARY_VERIFY_CONTROLS=true \
./scripts/run_live_audio_canary_pd20.sh

IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST=true \
IRIS_LIVE_AUDIO_CANARY_CANCEL_AFTER_START=true \
./scripts/run_live_audio_canary_pd20.sh

DURATION_SECONDS=1800 ./scripts/run_audio_soak_pd20.sh
```

canary의 `ENGINE`은 `/data/local/tmp`의 실행별 임시 mode 파일만 사용하므로 운영의 전역
대화 엔진 설정을 바꾸지 않는다. `REUSE_LATEST=true`는 같은 방의 최신 지원 오디오를
재사용하며, 기존 request ID가 이미 terminal 상태이면 먼저 `헤이봇 음성 삭제`로 정리한다.
