# HeyBot Proxy Manager (`proxy-manager`)

Mac mini에서 실행하는 모든 `proxy-*` 패키지의 **단일 진입점, registry, 요청 분기, 상태 점검, 테스트 및 수명주기 관리 제어면**이다.

현재 상태는 **구현 및 자동 테스트 완료**다. lifecycle API는 별도 admin secret을 요구하고 환경변수로 활성화하기 전에는 OFF다.

## 책임

1. PD20 Iris가 접근하는 유일한 HTTP 진입점을 제공한다.
2. `gateway` 프록시는 registry의 `routePrefix`를 기준으로 외부 요청을 전달한다.
3. 등록된 프록시의 health, readiness 및 명시적 canary test를 집계한다.
4. route 인증과 프록시별 내부 인증을 분리한다.
5. 프록시 상태, 응답 시간, 최근 오류를 민감정보 없이 제공한다.
6. 관리자 전용 인증이 있는 경우에만 launchd 기반 start·stop·restart를 허용한다.
7. 실제 이미지·오디오·영상 생성 로직은 포함하지 않는다.

manager는 생성 queue를 소유하지 않는다. `POST /v1/<기능>/jobs`를 해당 domain proxy에 전달해 `202 Accepted`를 빠르게 반환하고, 장시간 생성 작업을 기다리거나 event loop를 점유하지 않는다.

## 구현 구조

```text
proxy-manager/
├── README.md
├── .env.example
├── config/
│   └── proxies.example.json
├── package.json
├── package-lock.json
├── tsconfig.json
├── src/
│   ├── index.ts
│   ├── config/                  # manager 환경 설정
│   ├── registry/                # proxy 정의 로드·검증
│   ├── router/                  # prefix 분기·streaming 전달
│   ├── auth/                    # route/admin/internal 인증 분리
│   ├── health/                  # health·ready 집계
│   ├── lifecycle/               # launchd driver
│   ├── http/                    # 관리 API
│   └── http/                    # 관리 API·상태
├── scripts/
│   ├── doctor.sh
│   ├── start.sh
│   ├── stop.sh
│   ├── self-test.sh
│   └── launchd/
├── test/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
└── runtime/                     # Git 제외
    ├── state/
    ├── logs/
    └── secrets/
```

## 연결과 분기

```text
PD20 Iris
  -> ADB reverse 127.0.0.1:4340
  -> proxy-manager
       ├── /v1/image/* -> proxy-image 127.0.0.1:4347
       ├── /v1/audio/* -> proxy-audio <추후>
       └── /v1/video/* -> proxy-video <추후>

proxy-image -> proxy-codex 127.0.0.1:4348
proxy-video -> proxy-grok  127.0.0.1:4358  # 향후·비활성
```

PD20은 manager 포트와 route 인증정보만 알고, 개별 프록시의 포트·내부 secret·수명주기 방식은 알지 않는다.

```bash
adb -s 0123456789ABCDEF reverse tcp:4340 tcp:4340
```

## Registry

프록시는 코드 조건문이 아니라 `config/proxies.json` 등록으로 추가한다.

필수 항목:

- 고유 `id`
- `enabled`
- `exposure`: `gateway` 또는 `internal`
- gateway 프록시의 충돌하지 않는 `routePrefix`
- loopback `targetBaseUrl`
- health·ready·test endpoint
- manager가 해당 프록시를 호출할 때 사용하는 client secret 파일
- 선택적 launchd label

시작 시 다음 조건을 검증하고 잘못된 registry이면 manager가 ready 상태가 되지 않아야 한다.

- 중복 `id`
- gateway 프록시의 중복 또는 포함 관계로 충돌하는 prefix
- internal 프록시에 외부 routePrefix 지정
- loopback이 아닌 target
- 지원하지 않는 URL scheme
- 빠진 secret/test 설정

## API 계약

### 데이터 경로

```text
POST   /v1/image/jobs
GET    /v1/image/jobs/:jobId
GET    /v1/image/jobs/:jobId/file
DELETE /v1/image/jobs/:jobId
```

manager는 등록된 `/v1/image` prefix를 선택하고 URI를 유지한 채 `proxy-image`로 전달한다. 파일 응답은 Base64로 변환하지 않고 binary streaming으로 전달한다.

`proxy-codex`는 `exposure: internal`이므로 PD20 데이터 경로로 절대 분기하지 않는다. `proxy-image`가 `127.0.0.1:4348` 내부 API를 직접 호출하며 manager는 상태·테스트·수명주기와 dependency readiness만 관리한다.

같은 규칙으로 향후 `proxy-video`는 `/v1/video` gateway, `proxy-grok`는 internal engine으로 등록한다. 둘은 CLI 검증 전까지 `enabled: false`를 유지한다.

### 관리·테스트 경로

```text
GET  /health
GET  /ready
GET  /manager/v1/proxies
GET  /manager/v1/proxies/:id
POST /manager/v1/proxies/:id/test/readiness
POST /manager/v1/proxies/test-all/readiness
POST /manager/v1/proxies/:id/test/canary
POST /manager/v1/proxies/:id/start
POST /manager/v1/proxies/:id/stop
POST /manager/v1/proxies/:id/restart
```

- readiness test는 비용이 없는 상태 점검이다.
- canary test는 실제 생성 비용이 생길 수 있어 proxy id와 확인 헤더를 명시해야 한다.
- start·stop·restart는 route secret이 아닌 Mac 로컬 admin secret을 요구한다.
- lifecycle API는 기본 OFF이며 launchd 구성이 검증된 뒤 활성화한다.

## 인증 경계

```text
PD20 route secret
  -> proxy-manager에서 검증
  -> inbound Authorization 제거
  -> proxy별 internal secret으로 교체
  -> proxy-image에서 internal secret 검증

proxy-image
  -> codex service identity + internal secret
  -> proxy-codex caller/capability allowlist 검증
```

관리 API는 별도의 admin secret을 사용한다. secret 값은 registry나 `.env`에 직접 기록하지 않고 파일 경로만 설정한다. 한 패키지가 다른 패키지의 `runtime` 파일을 직접 읽지 않으며, bootstrap 단계에서 동일 credential을 필요한 양쪽에 각각 배치한다.

## 실패 격리

- `proxy-image`가 중단되어도 manager와 다른 프록시는 계속 동작한다.
- `proxy-codex` 장애 시 dependency가 있는 프록시만 degraded 상태로 표시한다.
- 해당 route만 `503 PROXY_NOT_READY` 또는 `502 PROXY_UNAVAILABLE`을 반환한다.
- manager 재시작이 실행 중인 이미지 job을 자동 취소하지 않는다.
- manager는 job 상태를 소유하지 않으며 해당 프록시에 조회를 전달한다.
- circuit breaker는 프록시별로 분리한다.
- 각 proxy는 별도 OS process·launchd service로 실행한다.
- 한 proxy의 queue full·CLI hang·재시작이 다른 proxy route를 멈추면 안 된다.

## 구현 순서

1. registry schema와 충돌 검증
2. fake proxy 기반 prefix router와 binary streaming
3. route/internal/admin 인증 분리
4. health·readiness 집계와 test runner
5. `proxy-image`와 `proxy-codex` contract/dependency test
6. launchd lifecycle driver와 운영 스크립트

이미지 프록시 상세는 [proxy-image](../proxy-image/README.md), Codex 실행 경계는 [proxy-codex](../proxy-codex/README.md), 향후 비디오는 [proxy-video](../proxy-video/README.md)와 [proxy-grok](../proxy-grok/README.md), 공통 규칙은 [프록시 플랫폼 아키텍처](../docs/PROXY_PLATFORM_ARCHITECTURE.md)와 [병렬 실행·Queue 모델](../docs/PROXY_CONCURRENCY_AND_QUEUE_MODEL.md), 현재 구현 단계는 [구현 계획](../../../dev-plan/implement_20260725_075640.md)을 따른다.
