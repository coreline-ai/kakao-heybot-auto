# HeyBot Proxy Server Workspace

이 디렉터리는 Mac mini에서 실행하는 헤이봇 서버 기능들의 상위 컨테이너다.

외부 진입점과 프록시 관리는 [`proxy-manager`](proxy-manager/README.md), 이미지 생성은 [`proxy-image`](proxy-image/README.md), 사용자 이미지 분석은 [`proxy-vision`](proxy-vision/README.md), 음성 STT는 [`proxy-audio`](proxy-audio/README.md), 공용 Codex CLI 실행은 [`proxy-codex`](proxy-codex/README.md)에서 각각 독립적으로 구현한다. 이후 기능 프록시는 `proxy-<기능>` 형식의 형제 패키지로 추가하고, Codex가 필요하면 `proxy-codex`의 내부 capability API를 호출한다.

향후 비디오는 [`proxy-video`](proxy-video/README.md)가 담당하고 Grok CLI 실행은 [`proxy-grok`](proxy-grok/README.md)에 위임한다. 두 패키지는 현재 비활성 구조 예약 상태다.

현재 상태는 **이미지 생성 경로·방별 Job 소유권·cross-room 차단·자동 테스트·Mac 실제 Codex 생성·PD20 카카오 이미지 E2E·launchd 상시 기동 완료**다. Mac sleep/wake, 물리 USB 재연결, 24시간 상시 운영 검증은 남아 있다. `old-bot` 소스는 라이선스상 검토·참조 전용이며 코드를 복사하지 않고 새로 구현했다.

## 목표

```text
카카오 메시지
  -> PD20 Iris
  -> ADB reverse :4340
  -> proxy-manager
  -> /v1/image 분기
  -> proxy-image :4347
  -> proxy-codex :4348
  -> Codex CLI
  -> proxy-codex artifact
  -> proxy-image 이미지 QC
  -> 검증된 PNG
  -> proxy-manager
  -> PD20 다운로드
  -> Iris 이미지 전송
```

## 문서

- [프록시 관리자](proxy-manager/README.md)
- [이미지 프록시](proxy-image/README.md)
- [이미지 분석 프록시](proxy-vision/README.md)
- [음성 STT 프록시](proxy-audio/README.md)
- [Codex CLI 프록시](proxy-codex/README.md)
- [비디오 프록시—향후](proxy-video/README.md)
- [Grok CLI 프록시—향후](proxy-grok/README.md)
- [전체 프록시 플랫폼 아키텍처](docs/PROXY_PLATFORM_ARCHITECTURE.md)
- [독립 프로세스·병렬 실행·Queue 모델](docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md)
- [상세 분석 및 권장 아키텍처](docs/IMAGE_PROXY_ADOPTION_ANALYSIS.md)
- [구현 계획](../../dev-plan/implement_20260725_075640.md)
- [이미지 프록시 운영 가이드](docs/IMAGE_PROXY_OPERATIONS.md)

## 현재 확인된 환경

- Node.js: `v24.13.1`
- npm: `11.8.0`
- Cloudflared: `/opt/homebrew/bin/cloudflared`
- 전역 Codex CLI: `codex-cli 0.145.0`
- Codex 인증: `codex login status` 통과
- Codex CLI 비대화식 실행 및 실제 PNG 생성: 통과
- 관리자 포트: `127.0.0.1:4340`
- 이미지 프록시 내부 포트: `127.0.0.1:4347`
- Codex 프록시 내부 포트: `127.0.0.1:4348`
- 비디오 프록시 예약 포트 `4357`: 비활성
- Grok 프록시 예약 포트 `4358`: 비활성
- 음성 프록시 내부 포트 `4363`: 구현 완료, STT model benchmark 전 registry 비활성
- PD20 ADB reverse: `tcp:4340 -> tcp:4340` 적용

Codex 인증정보는 서버 runtime으로 복사하지 않는다. readiness와 비용이 발생할 수 있는 실제 생성 canary는 분리한다.

## 빠른 실행

```bash
cd /Volumes/Eprojects/project_202607/kakao-new-bot/new-bot/vendor/server
./scripts/bootstrap-secrets.sh
./scripts/start-stack.sh
./scripts/self-test-stack.sh
./scripts/test-watchdog.sh
```

종료:

```bash
./scripts/stop-stack.sh
```

상시 운영용 launchd 파일만 먼저 검증:

```bash
./scripts/install-launchd.sh --render-only
```

## 서버 디렉터리

```text
vendor/server/
├── README.md
├── .gitignore
├── docs/
├── proxy-manager/           # 단일 진입점·registry·분기·점검·관리
├── proxy-image/             # 이미지 job·도메인 정책·픽셀 QC
├── proxy-vision/            # Kakao CDN 검증·Vision job·분석 결과 계약
├── proxy-audio/             # Kakao 음성 검증·FFmpeg·한국어 STT job
├── proxy-codex/             # 공용 Codex CLI 실행·전역 queue·artifact
├── proxy-video/             # 향후 비디오 job·미디어 QC
└── proxy-grok/              # 향후 Grok CLI 실행
```

## 범위 원칙

1. 기본 전송은 공개 URL이 아니라 Iris 직접 이미지 전송이다.
2. PD20은 `proxy-manager`에만 연결하고 개별 프록시 포트를 알지 않는다.
3. `proxy-manager`는 registry의 `routePrefix`로 요청을 해당 프록시에 분기한다.
4. 각 프록시는 자체 패키지·설정·테스트·runtime을 보유한다.
5. Codex CLI는 `proxy-codex`만 실행하며 다른 프록시는 내부 Job API로 호출한다.
6. Codex 전역 queue·격리 workspace·인증·process 관리는 `proxy-codex`가 단독 소유한다.
7. 기능별 도메인 검증·QC·최종 artifact는 호출 프록시가 소유한다.
8. API secret, Codex 인증정보, 원문 프롬프트 로그는 저장소에 커밋하지 않는다.
9. 공개 OG 페이지와 Cloudflare Tunnel은 선택 기능으로 후순위 구현한다.
10. Grok CLI도 `proxy-grok`만 실행하며 `proxy-video`는 내부 capability API로 호출한다.
11. 모든 프록시는 각각 독립 OS 프로세스로 실행하고 queue·DB·장애를 격리한다.
12. 생성 API는 비동기 job으로 즉시 반환하며 CLI 실행 중에도 대화·상태 조회·다른 프록시가 계속 동작한다.
13. domain queue와 engine queue의 동시성·총 대기·방별 대기 한계는 환경설정으로 변경할 수 있어야 한다.
14. 이미지 status/file/cancel은 생성 당시의 exact `chatId`가 일치해야 하며 cross-room 오류는 job 존재 정보를 노출하지 않는다.
