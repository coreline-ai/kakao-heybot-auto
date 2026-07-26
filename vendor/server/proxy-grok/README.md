# HeyBot Grok CLI Proxy (`proxy-grok`)

공식 Grok CLI OAuth profile을 가진 Mac에서 `video.generate`만 고정 실행하는 내부 engine이다.
PD20이나 카카오 방에는 직접 노출하지 않으며 `proxy-video` (`x-heybot-service-id: video`)만 접근할 수 있다.

> API key·REST·웹 자동화 fallback은 구현하지 않는다. registry 기본값은 OFF다.

## 고정 실행 계약

- absolute `GROK_PROXY_CLI_COMMAND`만 실행한다.
- 작업별 private workspace에서 `grok -p`와 `--output-format json --max-turns 8 --no-memory --no-subagents`를 고정 사용한다.
- 최소 child environment(`HOME`, 제한된 `PATH`, `TERM`, `NO_COLOR`)만 전달한다.
- stdout/stderr은 1 MiB로 제한하고, 취소/timeout은 detached process group에 `SIGTERM` 후 `SIGKILL`을 보낸다.
- CLI JSON의 session ID와 `videos/*.mp4` 상대 link 한 개만 수용한다. session root 안에서 `realpath` confinement 후 runtime artifact로 atomic copy 한다.
- raw prompt, CLI stdout/stderr, OAuth token/profile 경로는 HTTP 응답·로그에 넣지 않는다.

## 내부 API

```text
POST   /internal/v1/grok/jobs
GET    /internal/v1/grok/jobs/:jobId
GET    /internal/v1/grok/jobs/:jobId/artifact
DELETE /internal/v1/grok/jobs/:jobId
POST   /internal/v1/self-test/readiness
POST   /internal/v1/self-test/capabilities/video.generate  (x-confirm-cost: true)
```

요청은 `capability: "video.generate"`와 bounded `input.prompt`만 허용한다. 큐는 concurrency 1, pending 2다.

## 운영 전 조건

1. 전용 OS profile에서 운영자가 공식 `grok login`을 수동 완료한다.
2. CLI command, CLI home, session root을 절대 경로로 설정하고 secrets file 권한을 제한한다.
3. `proxy-grok` → `proxy-video` → manager registry는 순서대로 readiness만 검사한다.
4. 비용 승인 후 private-room canary 한 건과 native Kakao 영상 전달을 확인해야 enable한다.

## 검증 상태

- TypeScript build·unit test 완료
- 실제 OAuth CLI에서 `videos/1.mp4` 6초 H.264 artifact 사전 확인
- ZDR 오류 재시도 정책, real launchd service profile, native Kakao E2E는 enable 전 별도 검증 대상
