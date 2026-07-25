# 헤이봇 이미지 프록시 운영 가이드

작성 기준: `2026-07-25 KST`

## 1. 현재 구성

```text
PD20 Iris
  -> ADB reverse 127.0.0.1:4340
  -> proxy-manager :4340
  -> proxy-image   :4347
  -> proxy-codex   :4348
  -> Codex CLI
  -> PNG 구조 검증
  -> 픽셀 QC
  -> PD20 byte 다운로드
  -> 카카오톡 원래 chatId에 이미지 전송
```

- 세 프록시는 서로 다른 Node.js OS process다.
- 이미지 생성 동시성 기본값은 1이며 FIFO queue를 사용한다.
- 생성 중에도 manager 상태 조회, 다른 기능, Iris GLM 텍스트 대화는 별도로 동작한다.
- PD20은 manager route secret만 보유한다.
- Codex CLI는 `proxy-codex`에서만 실행한다.
- ID는 서버 JSON에서 문자열로 유지한다.

## 2. 최초 준비

```bash
cd /Volumes/Eprojects/project_202607/kakao-new-bot/new-bot/vendor/server
./scripts/bootstrap-secrets.sh
```

기존 secret은 유지한다. 모든 credential을 의도적으로 교체할 때만 다음을 실행한다.

```bash
./scripts/bootstrap-secrets.sh --force
```

`--force` 후에는 PD20의 `/data/local/private/iris-image-proxy.token`도 다시 배포해야 한다.

## 3. foreground 운영

```bash
./scripts/start-stack.sh
./scripts/self-test-stack.sh
curl --fail http://127.0.0.1:4340/ready
```

종료:

```bash
./scripts/stop-stack.sh
```

로그:

```text
proxy-codex/runtime/logs/server.log
proxy-image/runtime/logs/server.log
proxy-manager/runtime/logs/server.log
```

## 4. launchd 상시 운영

먼저 plist 생성과 문법만 검사한다.

```bash
./scripts/install-launchd.sh --render-only
```

foreground 프로세스를 종료한 다음 설치한다.

```bash
./scripts/stop-stack.sh
./scripts/install-launchd.sh
```

외장 볼륨은 launchd에서 직접 접근할 때 `EX_CONFIG`가 발생할 수 있다. 설치 스크립트는 빌드 결과·설정·의존성과 최초 durable runtime을 다음 내부 경로로 동기화한 뒤 실행한다.

```text
~/Library/Application Support/HeyBotProxy
```

소스와 빌드는 외장 볼륨에서 관리하고 상시 실행·DB·artifact·로그는 내부 미러가 소유한다. 설정이나 코드를 변경한 뒤 `install-launchd.sh`를 다시 실행하면 서비스가 갱신된다.

각 서비스는 독립적으로 재시작된다.

```bash
launchctl print gui/$(id -u)/ai.coreline.heybot.proxy-codex
launchctl print gui/$(id -u)/ai.coreline.heybot.proxy-image
launchctl print gui/$(id -u)/ai.coreline.heybot.proxy-manager
```

제거:

```bash
./scripts/uninstall-launchd.sh
```

내부 미러 방식도 사용할 수 없는 경우에는 `start-stack.sh`를 Terminal에서 실행하는 foreground 방식을 사용한다.

## 5. PD20 배포

```bash
cd /Volumes/Eprojects/project_202607/kakao-new-bot/new-bot
./scripts/start_iris_glm_pd20.sh
```

스크립트가 다음을 수행한다.

1. release APK 빌드 및 PD20 전송
2. manager route secret을 root 전용 파일로 전송
3. `adb reverse tcp:4340 tcp:4340` 설정
4. GLM과 독립된 이미지 coordinator 기동

확인:

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
"$ADB" -s 0123456789ABCDEF reverse --list
"$ADB" -s 0123456789ABCDEF shell \
  'stat -c "%a:%U:%G" /data/local/private/iris-image-proxy.token'
```

정상 권한은 `600:root:root`다.

## 6. 카카오 명령

```text
헤이봇 이미지 분홍색 로봇이 연구실에서 인사하는 그림
헤이봇 이미지 상태
헤이봇 이미지 취소
헤이봇 이미지 재전송
```

이미지 명령은 허용된 `chat_id`에서만 처리한다. 최종 이미지는 작업 생성 당시 저장한 불변 `chatId`로 전송한다.

이미지·영상은 텍스트의 `NotificationActionService`가 아니라 카카오 공유 Activity를 사용한다. 카카오톡 앱 잠금이 걸려 있으면 `awaiting_unlock`으로 전환하고, 잠금 해제 후 `헤이봇 이미지 재전송`으로 다시 보낸다. Activity 시작만으로 완료 처리하지 않고 카카오 DB에서 봇 계정의 이미지 로그(`isMine=true`, type `2` 또는 `3`)를 관찰한 뒤에만 `delivered`로 기록한다. 같은 방의 delivery는 Mutex로 직렬화한다.

### Job 방 소유권

- 생성 `requestId`는 `image:<chatId>:<logId>` 형식이며 원래 방을 포함한다.
- 상태·파일 다운로드·취소 요청은 저장된 불변 `chatId`를 query로 함께 보내야 한다.
- 다른 방 또는 누락된 `chatId`로 기존 job을 조회하면 job 존재 여부·ID·metadata를 노출하지 않고 거부한다.
- 같은 `requestId`를 다른 방에서 재사용해도 기존 job을 반환하지 않는다.
- PD20 coordinator는 명령 시점의 현재 방 값이 아니라 로컬 job에 저장된 원래 `chatId`를 사용한다.

## 7. Queue 기본값

| 위치 | 설정 | 기본값 |
|---|---|---:|
| proxy-image | 생성 동시성 | 1 |
| proxy-image | 전체 pending | 20 |
| proxy-image | 방별 pending | 3 |
| proxy-codex | CLI 동시성 | 1 |
| proxy-codex | 전체 pending | 8 |
| PD20 | 방별 pending | 3 |

변경은 각 `.env.example`에 정의된 환경변수를 launchd plist 또는 foreground 실행 환경에 넣는다.

## 8. 점검과 장애 처리

- manager `/health` 성공, `/ready` 실패: 하위 프록시 또는 Codex dependency 확인
- `IMAGE_QUEUE_FULL`: 전체 이미지 queue가 가득 참
- `ROOM_QUEUE_LIMIT`: 같은 방의 pending 한도 초과
- `CODEX_QUEUE_FULL`: 공용 Codex CLI queue가 가득 참
- `PROXY_UNAVAILABLE`: image 프로세스 또는 ADB reverse 확인
- 이미지 실패 중에도 텍스트 GLM은 독립 경로이므로 계속 사용할 수 있어야 한다.

watchdog는 60초 시작 유예 후 30초마다 확인하며 3회 연속 실패 시 세 서비스를 의존 순서로 재기동한다. PD20이 연결되어 있으면 매 주기 `adb reverse tcp:4340 tcp:4340`도 검사하고 누락 시 자동 복원한다.

실제 단말·launchd를 건드리지 않는 watchdog 회귀 검사는 다음 명령으로 실행한다.

```bash
./scripts/test-watchdog.sh
```

fake ADB/curl/launchctl fixture로 reverse 누락 복구, PD20 미연결 시 무동작,
readiness 성공의 failure counter 초기화, 3회 실패 뒤 고정 의존 순서 재기동을 검증한다.

## 9. 보존·로그 관리

- Codex 원시 artifact: 1시간
- 검증된 최종 image artifact: 24시간
- 각 프로세스가 1분마다 만료 artifact를 정리한다.
- 수동 확인:

```bash
./scripts/retention-cleanup.sh --dry-run
./scripts/retention-cleanup.sh --execute
./scripts/rotate-logs.sh
```

원문 prompt, secret, Codex 인증정보를 운영 로그에 기록하지 않는다.

## 10. 검증 상태

- 서버 자동 테스트: 26개 통과
- Android unit test: 100개 통과
- Android release APK: 빌드·PD20 배포 통과
- manager → image → 실제 Codex → QC → PNG download: 통과
- PD20 APK·secret·ADB reverse·Iris 기동: 통과
- 실제 외부 카카오 메시지 → 생성 → 봇 이미지 DB 로그 → `delivered`: 통과
- launchd 내부 미러 상시 기동·개별 proxy 강제 종료 자동 복구: 통과
- ADB reverse 제거 후 watchdog 자동 복원: 통과
- watchdog fake-ADB 회귀: 통과
- Mac sleep/wake, 물리 USB 분리·재연결, 24시간 soak: 운영 검증 필요
