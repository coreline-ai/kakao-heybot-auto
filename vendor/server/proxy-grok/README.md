# HeyBot Grok Proxy (`proxy-grok`)

Mac mini의 Grok CLI를 한 곳에서 실행하고, 허용된 `proxy-*` 패키지에 capability 기반 내부 Job API로 제공하기 위한 **공용 실행 엔진 프록시**다.

현재 상태는 **향후 비디오 생성을 위한 구조 예약·계약 설계 단계**다. Grok CLI 설치 상태, 실제 명령, 인증 방식과 비디오 생성 capability는 아직 검증하지 않았으며 구현 시 doctor 단계에서 확정한다.

## 전체 위치

```text
PD20 Iris
  -> proxy-manager
  -> proxy-video
  -> proxy-grok
  -> Grok CLI
```

`proxy-grok`는 PD20에 노출하지 않는다. 초기 허용 caller는 `video`, capability는 `video.generate`로 계획한다.

## 단독 책임

1. Grok CLI 경로·버전·인증·capability readiness를 확인한다.
2. Grok 실행 전역 queue, timeout, cancel과 process 종료를 소유한다.
3. job별 격리 workspace와 최소 child environment를 만든다.
4. caller별 credential과 capability allowlist를 검증한다.
5. CLI 출력을 로컬 원시 artifact로 정규화한다.
6. 원시 artifact의 path confinement·크기·media type과 retention을 관리한다.

## 소유하지 않는 책임

- 카카오톡 방·호출어 정책
- 사용자용 비디오 job 상태와 응답 문구
- 비디오 duration·codec·해상도·재생 가능성 QC
- 최종 비디오 장기 보관과 카카오 전송
- 외부 `/v1/video` route
- 임의 shell·argv·env·workdir·output path 실행

비디오 도메인 책임은 `proxy-video`가 담당한다.

## 예정 구조

```text
proxy-grok/
├── README.md
├── .env.example
├── config/
│   └── capabilities.example.json
├── package.json
├── package-lock.json
├── tsconfig.json
├── src/
│   ├── index.ts
│   ├── config/
│   ├── http/
│   ├── auth/
│   ├── capabilities/
│   ├── jobs/
│   ├── queue/
│   ├── cli/
│   ├── workspace/
│   ├── artifacts/
│   └── observability/
├── scripts/
│   ├── doctor.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── self-test.sh
│   ├── bootstrap_grok_cli.sh
│   └── launchd/
├── test/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
└── runtime/
```

## 내부 API

```text
POST   /internal/v1/grok/jobs
GET    /internal/v1/grok/jobs/:jobId
GET    /internal/v1/grok/jobs/:jobId/artifacts/:artifactId
DELETE /internal/v1/grok/jobs/:jobId

GET    /health
GET    /ready
POST   /internal/v1/self-test/readiness
POST   /internal/v1/self-test/capabilities/:capabilityId
```

예정 요청:

```json
{
  "requestId": "video-01J...",
  "capability": "video.generate",
  "input": {
    "prompt": "비 오는 미래 도시를 달리는 작은 로봇"
  },
  "artifactContract": {
    "acceptedMediaTypes": ["video/mp4"],
    "maxArtifacts": 1,
    "maxBytesPerArtifact": 536870912
  }
}
```

실제 Grok CLI 명령과 출력 형식은 지금 가정하지 않는다. capability adapter가 검증된 CLI 동작을 위 내부 계약으로 정규화한다.

## 독립 프로세스와 Engine Queue

`proxy-grok`는 `proxy-codex`와 별도 OS process·queue·CLI child를 사용한다.

```text
GROK_PROXY_QUEUE_CONCURRENCY=1
GROK_PROXY_QUEUE_MAX_PENDING=4
GROK_PROXY_QUEUE_WAIT_TIMEOUT_MS=1800000
```

따라서 Codex 이미지 생성과 Grok 비디오 생성은 병렬 실행할 수 있다. 한 engine의 queue full·timeout·CLI hang이 다른 engine의 queue에 영향을 주면 안 된다.

## 인증과 격리

- caller마다 별도 credential 사용
- 초기 caller `video`만 `video.generate` 허용
- manager 상태·테스트 credential 별도
- Grok 인증정보는 `proxy-grok`만 접근
- 다른 패키지의 source 또는 runtime 파일 직접 참조 금지

상위 구조는 [proxy-manager](../proxy-manager/README.md), 비디오 도메인은 [proxy-video](../proxy-video/README.md), 전체 구조는 [프록시 플랫폼 아키텍처](../docs/PROXY_PLATFORM_ARCHITECTURE.md)와 [병렬 실행·Queue 모델](../docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md)을 따른다.
