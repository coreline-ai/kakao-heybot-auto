# HeyBot Video Proxy (`proxy-video`)

비디오 요청·job·미디어 QC·최종 artifact를 담당하고 Grok CLI 실행은 내부 `proxy-grok` API에 위임하는 **비디오 도메인 프록시**다.

현재 상태는 **향후 구현을 위한 구조 예약 단계**이며 manager registry에서 비활성화한다.

## 처리 흐름

```text
proxy-manager /v1/video/*
  -> proxy-video
  -> proxy-grok capability: video.generate
  -> 원시 video artifact
  -> codec·duration·resolution·재생 가능성 QC
  -> 최종 MP4
  -> proxy-manager binary streaming
```

## 예정 책임

- 비디오 prompt·옵션 schema 검증
- video job과 grok job ID 매핑
- 방별 active job·사용량 제한
- 장시간 비동기 polling·cancel
- 원시 artifact 다운로드
- MP4 signature·codec·duration·fps·해상도·용량 검증
- 최종 artifact와 전송 상태 관리

Grok CLI process, Grok 인증정보, CLI workspace는 직접 다루지 않는다.

## 독립 프로세스와 Queue

향후 `proxy-video`도 별도 OS process와 durable domain queue를 가진다.

```text
VIDEO_PROXY_QUEUE_CONCURRENCY=1
VIDEO_PROXY_QUEUE_MAX_PENDING=4
VIDEO_PROXY_QUEUE_MAX_PENDING_PER_ROOM=1
VIDEO_PROXY_QUEUE_POLICY=fifo
```

이미지 queue와 독립적이므로 이미지 생성 중에도 비디오 job 접수·실행이 가능하다. 각 video job의 `chatId`와 `grokJobId`를 저장해 완료 결과를 원래 방으로 전달한다.

## 예정 구조

```text
proxy-video/
├── README.md
├── .env.example
├── package.json
├── package-lock.json
├── tsconfig.json
├── src/
│   ├── index.ts
│   ├── config/
│   ├── http/
│   ├── auth/
│   ├── jobs/
│   ├── queue/
│   ├── orchestration/
│   ├── clients/grok/
│   ├── videos/
│   ├── storage/
│   └── observability/
├── scripts/launchd/
├── test/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── runtime/
```

## 외부 계약

```text
POST   /v1/video/jobs
GET    /v1/video/jobs/:jobId
GET    /v1/video/jobs/:jobId/file
DELETE /v1/video/jobs/:jobId
GET    /health
GET    /ready
```

## proxy-grok 연동

```text
POST   http://127.0.0.1:4358/internal/v1/grok/jobs
GET    http://127.0.0.1:4358/internal/v1/grok/jobs/:jobId
GET    http://127.0.0.1:4358/internal/v1/grok/jobs/:jobId/artifacts/:artifactId
DELETE http://127.0.0.1:4358/internal/v1/grok/jobs/:jobId
```

초기 capability는 `video.generate`, caller identity는 `video`로 예약한다.

Grok 실행 경계는 [proxy-grok](../proxy-grok/README.md), 전체 구조는 [프록시 플랫폼 아키텍처](../docs/PROXY_PLATFORM_ARCHITECTURE.md)와 [병렬 실행·Queue 모델](../docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md)을 따른다.
