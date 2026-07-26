# Iris GLM 자동응답 운영 설정

## 목적

이 문서는 PD20에서 root로 실행되는 Iris에 Z.AI GLM 자동응답을 제한적으로 활성화하는 절차를 기록한다.

- 수신: Iris가 카카오톡 DB에서 복호화한 새 텍스트 메시지
- 조건: 허용된 4개 오픈방의 실제 `chat_id`와 `헤이봇` 호출어가 모두 일치
- 추론: Z.AI 일반 Chat Completion API
- 발신: 기존 Iris `Replier.sendMessage()` → 카카오톡 내부 `NotificationActionService`

`iris_bot`, Python 서버, Android NotificationListenerService는 필요하지 않다.

## 안전 기본값

- `IRIS_GLM_ENABLED=true`를 명시하지 않으면 GLM은 비활성화된다.
- API Key/토큰은 Iris JSON 설정 파일, APK, BuildConfig, Git에 저장하지 않는다.
- 수신 메시지는 `헤이봇` 호출어가 있을 때만 외부 Z.AI API에 전달된다. 단, 코어라인 AI 연구소 관리자가 전역 일반대화 모드를 켠 동안 별도의 일반대화 allowlist 안의 일반 텍스트는 `REPLY`·`WAIT`·`IGNORE` 판정용으로 전달될 수 있다.
- GLM 오류·시간 초과·429는 카카오톡에 자동 오류 메시지를 보내지 않는다.
- Z.AI SSE 스트리밍 응답을 내부에서 한 문장으로 합친 뒤 최종 텍스트 한 건만 전송한다. 스트리밍 토큰을 여러 메시지로 전송하지 않는다.
- 429는 `Retry-After` 헤더가 있으면 이를 우선하고, 없으면 15초·30초 간격으로 최대 `IRIS_GLM_RATE_LIMIT_RETRIES`회 재시도한다.
- 방마다 FIFO Queue와 worker를 분리하고, 전체 GLM 호출은 기본 2건까지만 병렬 실행한다.
- 방별 30초 3회, 사용자별 60초 5회 제한과 8초 동일 메시지 중복 차단을 Queue 등록 전에 적용한다.
- 대화 기억은 `(chat_id,user_id)`별 최근 4턴·30분이며 root 전용 파일에 원자적으로 저장한다. 호출어 대화와 일반대화의 **성공 전송된** 질문·답변이 이 문맥을 함께 사용하므로, 같은 사용자는 두 경로를 오가도 앞선 대화를 이어갈 수 있다. 다른 참가자·다른 방의 발화는 절대 포함하지 않는다.
- 관리자 명령은 닉네임이 아니라 Kakao DB의 숫자 `user_id` exact match로만 허용하며, 코어라인 AI 연구소 control room에서만 실행한다.
- 호출어·일반대화의 GLM 텍스트는 전송 직전 동일한 safety policy를 통과한다. secret-like 출력은 전체 차단하고 이메일·전화·주민번호·카드번호 형태는 고정 문구로 마스킹한다.
- 일반대화의 timeout·429·network·server 실패가 5분 안에 3건 누적되면 일반대화 mode만 자동 OFF한다. 호출어 GLM·이미지는 계속 동작한다. Iris HTTP 관리 API는 기본 비활성화다.
- 일반대화 ON/OFF 의도는 root 전용 원자적 상태 파일에 저장한다. 정상 재배포·프로세스 재시작은 기존 상태를 복원하며, 파일이 없거나 손상되면 안전하게 OFF로 시작한다.

## Iris HTTP 관리 API 보안(P0)

현재 PD20 운영 스크립트는 `IRIS_HTTP_API_ENABLED=false`로 Iris HTTP 관리 API를
기본 비활성화한다. 따라서 Mac의 `127.0.0.1:3000`에는 상시 ADB forward가 생기지
않으며, GLM 자동응답과 이미지 생성에 필요한 `adb reverse tcp:4340 tcp:4340`만 유지한다.

- `/aot`, `/query`, `/decrypt`는 운영 HTTP route에서 제거됐다.
- `/reply`, `/dashboard`, `/config`, `/ws`는 HTTP API를 명시적으로 켠 경우에도 별도
  admin Bearer secret 없이는 접근할 수 없다.
- `/health`만 최소 `{"ok":true}` 응답으로 익명 허용한다. 대화·설정·queue·파일 경로는
  반환하지 않는다.
- config, startup log, HTTP admin secret은 `/data/local/private` 아래 `root:root`,
  mode `600`이어야 한다. 대화 원문·webhook payload·응답 body는 로그에 남기지 않는다.

HTTP 관리 API가 꼭 필요한 경우에만 root shell에서 전용 secret을 만든다. **GLM token,
image proxy route secret, Kakao AOT와 같은 값을 재사용하지 않는다.** 다음 명령은 secret
값을 출력하지 않는다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell "su root sh -c '
  umask 077
  head -c 48 /dev/urandom | base64 | tr -d "\\n" > /data/local/private/iris-http-admin.token
  chown root:root /data/local/private/iris-http-admin.token
  chmod 600 /data/local/private/iris-http-admin.token
'"
```

API를 활성화하는 경우에만 기동 환경에 아래 두 값을 추가한다. 일반 운영에서는 추가하지
않는다. 관리가 끝나면 다시 `false`로 재기동하고 임시 ADB forward가 남지 않았는지 확인한다.

```text
IRIS_HTTP_API_ENABLED=true
IRIS_HTTP_ADMIN_SECRET_FILE=/data/local/private/iris-http-admin.token
```

활성화 뒤에도 관리 포트는 상시 열지 않는다. 필요한 Mac 터미널에서만 **임시로**
3000과 다른 local port를 만들고, 종료·중단 시 반드시 제거한다. 아래는 응답 본문이나
secret을 출력·저장하지 않고 protected API의 상태 코드만 확인하는 예시다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF
LOCAL_PORT=13000

cleanup() {
  "$ADB" -s "$SERIAL" forward --remove "tcp:$LOCAL_PORT" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$ADB" -s "$SERIAL" forward "tcp:$LOCAL_PORT" tcp:3000
read -r -s -p "HTTP admin secret: " IRIS_HTTP_ADMIN_SECRET
printf '\n'
curl --fail --silent --show-error --output /dev/null \\
  --write-out 'protected API status: %{http_code}\n' \\
  -H "Authorization: Bearer $IRIS_HTTP_ADMIN_SECRET" \\
  "http://127.0.0.1:$LOCAL_PORT/config"
unset IRIS_HTTP_ADMIN_SECRET
```

이 절차는 `IRIS_HTTP_API_ENABLED=true`로 배포한 경우에만 사용한다. secret은 shell
history·파일·Git에 쓰지 말고, 작업을 끝낸 뒤 process를 다시 기본값(`false`)으로 기동한다.

## 사전 조건

- Iris GLM 빌드 APK가 PD20의 `/data/local/tmp/Iris-glm.apk`에 있어야 한다.
- 카카오톡과 Iris가 root로 정상 기동되어 있어야 한다.
- 신규 발급한 Z.AI API Key 또는 짧은 수명의 Bearer/JWT를 준비한다.
- 키를 채팅, 소스, `config.json`, shell history, Git에 기록하지 않는다.

루팅된 단말은 장기 API Key를 완전히 보호할 수 없다. 운영 전에는 Token Broker가 발급한 짧은 수명의 토큰 사용을 권장한다.

## 비밀 파일 주입

아래 절차는 **새로 발급한 키**를 root 전용 파일로 입력한다. 실제 키 문자열은 이 문서나 명령어 예시에 넣지 않는다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell
su root
mkdir -p /data/local/private
chmod 700 /data/local/private
umask 077
cat > /data/local/private/iris-glm.token
# 여기서 새 토큰을 한 줄로 붙여넣고 Ctrl+D를 누른다.
chmod 600 /data/local/private/iris-glm.token
exit
exit
```

파일에는 다음 중 하나만 넣는다.

```text
<Z.AI API Key>
```

또는

```text
Bearer <short-lived token>
```

Iris는 앞에 `Bearer `가 없으면 자동으로 붙인다. 파일 내용과 토큰 값은 출력하거나 `cat`으로 확인하지 않는다.

### 관리자 ID 파일

관리자로 허용할 숫자 `user_id`를 한 줄에 하나씩 저장한다. 닉네임이나 프로필명은 사용할 수 없다.
실제 ID를 모르면 먼저 Iris의 수신 DB/API에서 본인이 보낸 메시지의 `user_id`를 확인한다.

```bash
"$ADB" -s "$SERIAL" shell
su root
umask 077
cat > /data/local/private/iris-bot-admins.txt
# 숫자 user_id를 한 줄에 하나씩 입력한 뒤 Ctrl+D
chmod 600 /data/local/private/iris-bot-admins.txt
exit
exit
```

파일이 없거나 읽을 수 없으면 GLM 대화는 계속 동작하지만 관리자 명령은 모두 비활성화된다. ID 값은 이 문서·Git·로그에 쓰지 않는다.
로그에는 관리자 ID 목록이나 대화 원문을 출력하지 않는다.

### 일반대화 사용자 block 파일

일반대화에서만 제외할 숫자 ID를 root 전용 파일에 저장한다. 빈 파일은 차단 대상이 없다는
뜻이며 호출어 질문에는 영향을 주지 않는다.

```bash
"$ADB" -s "$SERIAL" shell
su root
umask 077
touch /data/local/private/iris-general-conversation-blocks.txt
chmod 600 /data/local/private/iris-general-conversation-blocks.txt
exit
exit
```

허용 형식은 한 줄에 하나이며 공백·중복·음수·0·overflow·그 밖의 구문은 허용하지 않는다.

```text
<userId>
<chatId>:<userId>
```

- `<userId>`: 모든 일반대화 허용방에서 해당 사용자를 제외
- `<chatId>:<userId>`: 지정 방의 해당 사용자만 제외
- 파일 손상·읽기 실패·잘못된 권한에서는 일반대화만 fail-closed되고 호출어 기능은 유지
- 정책 변경은 원자적 파일 교체 후 Iris 재기동으로 반영
- ID 원문은 status·설정 보기·로그에 출력하지 않음

### 일반대화 모드 상태 파일

`헤이봇 대화 시작/종료`와 circuit 자동 정지 결과는 아래 파일에 원자적으로 저장한다.

```text
/data/local/private/iris-general-conversation-mode.json
```

- 파일은 앱이 직접 만들며 `600 root:root`여야 한다. 배포 스크립트는 기존 내용을 덮어쓰지 않는다.
- `대화 시작`은 ON 저장이 성공한 뒤에만 runtime mode를 켠다. 저장 실패 시 호출어 대화만 유지한다.
- `대화 종료`와 circuit trip은 OFF를 저장한 뒤 현재 epoch와 대기 중인 일반대화를 무효화한다.
- 정상 프로세스 종료의 `close()`는 진행 중 작업만 무효화하며 저장된 ON/OFF 의도를 바꾸지 않는다.
- 최초 도입처럼 파일이 없거나 JSON이 손상·과대·지원하지 않는 버전이면 OFF로 시작한다. 손상 파일은 격리하고 자동 ON하지 않는다.
- 최초 도입 후 계속 사용할 때는 코어라인 AI 연구소 관리자가 `헤이봇 대화 시작`을 한 번 실행해야 한다.

## 대화 엔진 선택

GLM은 Android 내부에서 동작하는 기본 엔진입니다. Codex와 Grok은 Android가 직접
접속하지 않고 `proxy-manager → proxy-conversation → provider proxy` 경로로 호출합니다.
세 엔진은 동일한 헤이봇 프롬프트·사용자별 문맥·응답 안전정책을 사용하며, 응답을 만드는
provider만 바뀝니다.

엔진 변경은 코어라인 AI 연구소의 지정 관리자만 실행할 수 있고 모든 텍스트 허용방에
전역 적용됩니다. 기존 `헤이봇 대화 시작/상태/종료`의 일반대화 모드와 별개로, `대화
상태`에는 현재 엔진이 함께 표시됩니다.

```text
헤이봇 대화 기본
헤이봇 대화 코덱스
헤이봇 대화 그록
헤이봇 대화 상태
```

- `기본`: Android GLM으로 즉시 복귀합니다.
- `코덱스`: 개발·구조화 중심의 Codex text capability를 사용합니다.
- `그록`: Grok text capability를 사용합니다.
- 프록시가 준비되지 않은 엔진은 변경되지 않습니다. 외부 provider 오류 때 GLM으로
  조용히 바꾸지 않으며, 일반대화는 기존 circuit breaker 기준으로 자동 정지될 수 있습니다.
- 엔진 설정은 `/data/local/private/iris-conversation-engine.conf`에 root 전용으로
  저장되고, 파일이 없거나 손상되면 GLM으로 복구합니다.

운영 연결은 아래 환경변수로 제어합니다. 실제 PD20 배포 스크립트가 manager route secret과
GLM 기본 모드를 초기화하므로 일반적인 재배포에서는 직접 입력할 필요가 없습니다.

```text
IRIS_CONVERSATION_PROXY_ENABLED=true
IRIS_CONVERSATION_PROXY_BASE_URL=http://127.0.0.1:4340
IRIS_CONVERSATION_PROXY_SECRET_FILE=/data/local/private/iris-conversation-proxy.token
IRIS_CONVERSATION_ENGINE_FILE=/data/local/private/iris-conversation-engine.conf
IRIS_CONVERSATION_PROXY_TIMEOUT_MS=100000
```

Codex/Grok 텍스트 응답은 CLI가 첫 바이트를 반환하기 전까지 수 초 이상 걸릴 수
있습니다. Android proxy client는 위 timeout을 connect/read/write/call 전체에 적용하며,
OkHttp 기본 10초 read timeout으로 대화가 끊기지 않도록 합니다.

Mac 프록시 registry에는 `conversation` gateway(`/v1/conversation`, port `4361`)가
등록되어 있으며 Codex text queue와 Grok text queue는 각각 기존 image/video queue와
분리되어 있습니다. Android는 manager `4340`만 사용하고 Codex/Grok internal port에
직접 접근하지 않습니다.

## 중앙 방 권한 사용법

방별 권한 변경은 **코어라인 AI 연구소의 지정 관리자만** 할 수 있다. 방 이름은
표시용이며, 실제 권한은 변경되지 않는 `chatId`로 저장한다. `R01` 같은 방 참조값은
`헤이봇 방 목록`에서 확인한다.

```text
헤이봇 방 목록
헤이봇 방 상태 R02
헤이봇 방 텍스트 허용 R02
헤이봇 방 일반대화 불허용 R02
헤이봇 방 이미지 허용 R02
헤이봇 방 영상 허용 R01
헤이봇 방 펜브러쉬 허용 R01
헤이봇 방 적용 <코드>
헤이봇 방 취소
```

변경 명령은 즉시 저장하지 않고 대상·예정값·확인 코드를 보여준다. 같은 관리자가
2분 안에 `헤이봇 방 적용 <코드>`를 보내야 저장·적용된다. 적용 뒤에는 Iris 재기동 없이
새 입력과 아직 전송되지 않은 GLM·이미지 작업에 바로 반영된다.

- 작업은 방·capability별 revision을 기록한다. 다른 방의 변경이나 같은 방의
  `텍스트`/`일반대화` 변경은 이미 허용된 이미지 작업을 취소하지 않는다.
- 같은 방의 `이미지`를 끈 뒤 다시 켜도 기존 image revision 작업은 stale로
  취급해 bytes를 전송하지 않는다. 새 요청만 새 revision으로 생성된다.

- `텍스트` 불허용: 호출어 대화와 일반대화를 모두 중단한다.
- `일반대화` 불허용: 호출어 대화는 유지하고 호출어 없는 참여만 중단한다.
- `이미지` 불허용: 새 이미지 생성·상태·취소·재전송 요청을 중단한다.
- `영상` 불허용: 새 영상 생성·상태·취소·재전송 요청을 중단하고,
  이미 완료된 결과도 전송 직전에 capability revision을 다시 확인한다.
- `펜브러쉬` 불허용: 새 펜브러쉬 생성·상태·취소·재전송 요청을 중단하고,
  이미 완료된 결과도 전송 직전에 capability revision을 다시 확인한다.
- 코어라인 AI 연구소의 텍스트 권한은 관리 경로 보호를 위해 끌 수 없다.

초기 policy는 `config/iris-room-capabilities.bootstrap.json`이며, PD20의
`/data/local/private/iris-room-capabilities.json`이 실제 상태다. 기동 스크립트는
파일이 없을 때만 bootstrap을 복사하므로, 채팅에서 변경한 권한을 덮어쓰지 않는다.
이 파일은 `600 root:root`여야 한다.

### 현재 카톡방 식별

방 참조값만 외우지 않아도 된다. 원하는 카카오톡 방에서 아래처럼 입력하면, 현재
방의 관리용 참조값과 등록된 방 제목·권한을 함께 보여준다.

```text
헤이봇 카톡방

현재 카톡방
R01. 코어라인 AI 연구소
텍스트: 허용 | 일반대화: 허용
이미지: 허용 | 영상: 허용 | 펜브러쉬: 허용
```

관리자 방 권한 명령에는 이 응답의 `R01`처럼 표시된 참조값을 사용한다.

## 펜브러쉬 영상 사용법

펜브러쉬는 기존 `헤이봇 영상`과 별개의 기능이다. 원본 일러스트의 어두운 펜 외곽선을
먼저 그린 뒤, 그 선화가 끝난 다음 브러시가 색을 채우는 세로 영상만 만든다.

```text
헤이봇 펜브러쉬 웃으며 손을 흔드는 분홍 로봇
헤이봇 펜브러쉬 상태
헤이봇 펜브러쉬 취소
헤이봇 펜브러쉬 재전송
```

- 초기 규격은 **10초·1장면·세로 1080×1920·무음·H.264/AAC MP4**로 고정이다.
- 2026-07-26 운영 기준으로 코어라인 AI 연구소(`R01`)만 `영상`·`펜브러쉬`가
  허용되어 있고, 나머지 관리 방은 불허용이다. 코어라인 AI 연구소의 관리자만
  `헤이봇 방 영상 허용|불허용 R01` 또는 `헤이봇 방 펜브러쉬 허용|불허용 R01` 뒤
  확인 코드를 적용할 수 있다.
- 일반 대화에 "펜브러쉬"라는 단어가 있는 것만으로는 실행되지 않는다. 반드시 문장
  처음의 `헤이봇 펜브러쉬 <설명>` 형식이어야 한다.
- 결과물은 공개 링크를 만들지 않고 원래 요청한 방에 MP4 첨부로만 전송한다.
- 실제 원본 이미지 생성은 비용이 발생할 수 있으므로, 운영 활성화와 첫 카카오 전송은
  별도 승인 후 비공개 시험 방에서 한 건으로 검증한다.

## 실행 설정

| 환경 변수 | 설정값 | 역할 |
|---|---|---|
| `IRIS_GLM_ENABLED` | `true` | 자동응답 활성화 |
| `IRIS_GLM_BASE_URL` | `https://api.z.ai/api/paas/v4/` | Z.AI 일반 API base URL |
| `IRIS_GLM_MODEL` | `glm-4.5-flash` | PD20 실측 평균 1.19초의 주 모델 |
| `IRIS_GLM_FALLBACK_MODEL` | 미설정 | 필요할 때만 주 모델의 429 또는 시간 초과용 대체 모델을 지정 |
| `IRIS_GLM_TRIGGER` | `헤이봇` | 메시지 앞 호출어 |
| `IRIS_GLM_ALLOWED_CHAT_IDS` | 4개 관리 방의 고정 상한 | 동적 room policy가 허용할 수 있는 입력 후보 범위 |
| `IRIS_GLM_API_KEY_FILE` | `/data/local/private/iris-glm.token` | root 전용 비밀 파일 |
| `IRIS_GLM_TIMEOUT_MS` | `120000` | API 전체 호출 시간 제한 |
| `IRIS_GENERAL_CONVERSATION_TIMEOUT_MS` | `15000` | 호출어 없는 일반대화 판정 전용 시간 제한. 느린 판정이 방 worker를 장시간 점유하지 않게 함 |
| `IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS` | 4개 관리 방의 고정 상한 | 동적 room policy·user block·global mode 이전의 일반대화 후보 범위 |
| `IRIS_GENERAL_CONVERSATION_BLOCK_FILE` | `/data/local/private/iris-general-conversation-blocks.txt` | 전역 `userId` 또는 `chatId:userId` block, `600 root:root` |
| `IRIS_GENERAL_CONVERSATION_MODE_FILE` | `/data/local/private/iris-general-conversation-mode.json` | 관리자 ON/OFF 의도와 circuit OFF를 원자적으로 저장, `600 root:root` |
| `IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS` | `300000` | 일반대화 외부 장애 sliding window |
| `IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD` | `3` | window 안에서 일반대화를 자동 OFF할 장애 횟수 |
| `IRIS_GLM_MAX_TOKENS` | `128` | 일반 대화 최종 답장의 최대 생성 토큰 |
| `IRIS_GLM_TEMPERATURE` | `0.2` | 응답 다양성 |
| `IRIS_GLM_RATE_LIMIT_RETRIES` | `2` | HTTP 429일 때 최대 재시도 횟수 |
| `IRIS_GLM_ROOM_QUEUE_CAPACITY` | `8` | 방별 대기 Queue 상한, 범위 1~100 |
| `IRIS_GLM_TOTAL_QUEUE_CAPACITY` | `24` | 전체 대기 Queue 상한, 방별 상한 이상·최대 500 |
| `IRIS_GLM_MAX_CONCURRENCY` | `2` | 전체 동시 GLM 호출 상한, 범위 1~16 |
| `IRIS_GLM_ROOM_RATE_WINDOW_MS` | `30000` | 방별 호출 제한 window |
| `IRIS_GLM_ROOM_RATE_MAX` | `3` | window당 방별 허용 건수 |
| `IRIS_GLM_USER_RATE_WINDOW_MS` | `60000` | 사용자별 호출 제한 window |
| `IRIS_GLM_USER_RATE_MAX` | `5` | window당 사용자별 허용 건수 |
| `IRIS_GLM_DUPLICATE_WINDOW_MS` | `8000` | 동일 방·사용자·정규화 메시지 중복 차단 |
| `IRIS_GLM_MEMORY_FILE` | `/data/local/private/iris-bot-memory.json` | 원자적 대화 기억 파일 |
| `IRIS_GLM_MEMORY_MAX_TURNS` | `4` | `(chat_id,user_id)`별 최근 turn 수 |
| `IRIS_GLM_MEMORY_TTL_MS` | `1800000` | 대화 기억 TTL 30분 |
| `IRIS_GLM_MEMORY_MAX_BYTES` | `1048576` | 기억 JSON 최대 1 MiB |
| `IRIS_GLM_MEMORY_MAX_CONVERSATIONS` | `512` | 최대 대화 key 수 |
| `IRIS_BOT_ADMIN_USER_IDS_FILE` | `/data/local/private/iris-bot-admins.txt` | root 전용 관리자 ID 목록 |
| `IRIS_BOT_ADMIN_CONTROL_CHAT_ID` | `18480337854645134` | 관리자 설정·전역 일반대화 제어가 가능한 코어라인 AI 연구소 방 |
| `IRIS_BOT_ROOM_POLICY_FILE` | `/data/local/private/iris-room-capabilities.json` | root 전용 동적 방 capability 정책 |

현재 운영은 실측 성공·최저 지연 모델인 `glm-4.5-flash`만 사용한다. 대체 모델을
지정하면 주 모델의 용량 제한(HTTP 429) 또는 시간 초과일 때만 시도한다.

기동 명령:

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[a]i.coreline.heybot\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  touch /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  chown root:root /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  chmod 600 /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  : > /data/local/private/iris-glm-startup.log
  IRIS_CONFIG_PATH=/data/local/private/iris-config.json \\
  IRIS_HTTP_API_ENABLED=false \\
  IRIS_HTTP_ADMIN_SECRET_FILE=/data/local/private/iris-http-admin.token \\
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GLM_API_KEY_FILE=/data/local/private/iris-glm.token \\
  IRIS_GLM_TIMEOUT_MS=120000 \\
  IRIS_GENERAL_CONVERSATION_TIMEOUT_MS=15000 \\
  IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GENERAL_CONVERSATION_BLOCK_FILE=/data/local/private/iris-general-conversation-blocks.txt \\
  IRIS_GENERAL_CONVERSATION_MODE_FILE=/data/local/private/iris-general-conversation-mode.json \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS=300000 \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD=3 \\
  IRIS_GLM_MAX_TOKENS=128 \\
  IRIS_GLM_TEMPERATURE=0.2 \\
  IRIS_GLM_RATE_LIMIT_RETRIES=2 \\
  IRIS_GLM_ROOM_QUEUE_CAPACITY=8 \\
  IRIS_GLM_TOTAL_QUEUE_CAPACITY=24 \\
  IRIS_GLM_MAX_CONCURRENCY=2 \\
  IRIS_GLM_ROOM_RATE_WINDOW_MS=30000 \\
  IRIS_GLM_ROOM_RATE_MAX=3 \\
  IRIS_GLM_USER_RATE_WINDOW_MS=60000 \\
  IRIS_GLM_USER_RATE_MAX=5 \\
  IRIS_GLM_DUPLICATE_WINDOW_MS=8000 \\
  IRIS_GLM_MEMORY_FILE=/data/local/private/iris-bot-memory.json \\
  IRIS_GLM_MEMORY_MAX_TURNS=4 \\
  IRIS_GLM_MEMORY_TTL_MS=1800000 \\
  IRIS_GLM_MEMORY_MAX_BYTES=1048576 \\
  IRIS_GLM_MEMORY_MAX_CONVERSATIONS=512 \\
  IRIS_BOT_ADMIN_USER_IDS_FILE=/data/local/private/iris-bot-admins.txt \\
  IRIS_BOT_ADMIN_CONTROL_CHAT_ID=18480337854645134 \\
  IRIS_BOT_ROOM_POLICY_FILE=/data/local/private/iris-room-capabilities.json \\
  CLASSPATH=/data/local/tmp/Iris-glm.apk \\
  app_process / ai.coreline.heybot.Main \\
  > /data/local/private/iris-glm-startup.log 2>&1 &
'"
```

성공적으로 설정을 읽으면 시작 로그에 아래 한 줄이 나타난다.

```text
GLM auto-reply enabled
General conversation policy ready=true rooms=4 reason=READY
GLM P1 scheduler ready (concurrency=2, roomQueue=8, totalQueue=24)
Conversation memory ready (conversations=..., turns=...)
```

토큰 원문·프롬프트 원문·응답 원문은 시작 로그에 기록하지 않는다.

### 안전 기동 스크립트

위 설정값은 프로젝트의 `scripts/start_iris_glm_pd20.sh`에도 고정되어 있다. 이 스크립트는
토큰을 읽거나 출력하지 않고, 토큰 파일의 존재·권한(`600 root:root`)만 확인한 뒤 APK 배포,
분리된 중지/기동, private 파일 metadata와 GLM·scheduler·room policy readiness log/PID를 확인한다.

```bash
chmod +x scripts/start_iris_glm_pd20.sh
scripts/start_iris_glm_pd20.sh
```

기본값은 현재 PD20 serial, 현재 릴리스 APK 및 본 문서의 채팅방/모델 설정이다. 다른 ADB나
단말을 명시해야 할 때에만 다음처럼 환경 변수를 사용한다.

```bash
ADB=/path/to/adb SERIAL=<serial> APK=/path/to/Iris-release.apk \
  scripts/start_iris_glm_pd20.sh
```

스크립트는 `/data/local/private`가 `700 root:root`, 토큰·기존 기억·기존 관리자·일반대화
block/mode 파일이 `600 root:root`인지 내용 노출 없이 검사한다. block 파일이 없으면 빈 root
전용 파일을 생성하지만 mode 파일은 앱이 원자적으로 관리하므로 생성·덮어쓰기하지 않는다.
관리자 파일이 없으면 경고 후 기동하며 관리자 명령만 비활성화된다.

## 카카오 명령

| 명령 | 권한 | 처리 경로 |
|---|---|---|
| `헤이봇 <질문>` | 허용된 방의 일반 사용자 | 방별 FIFO → 전체 동시성 제한 → GLM |
| `헤이봇 도움말` | 일반 사용자 | 로컬 즉시 응답, GLM 미호출 |
| `헤이봇 내 기억 초기화` | 일반 사용자 | 현재 `(chat_id,user_id)` 기억만 삭제 |
| `헤이봇 상태` | 코어라인 AI 연구소의 관리자 | Queue·latency·성공/실패·제한·기억 상태 |
| `헤이봇 설정 보기` | 코어라인 AI 연구소의 관리자 | 비밀을 제외한 운영값 요약 |
| `헤이봇 전체 기억 초기화` | 코어라인 AI 연구소의 관리자 | 모든 대화 기억 삭제·저장 |
| `헤이봇 사용자 기억 초기화 <user_id>` | 코어라인 AI 연구소의 관리자 | 대상 사용자의 모든 방 기억 삭제·저장 |
| `헤이봇 대화 시작` | 코어라인 AI 연구소의 관리자 | ON 상태를 저장한 뒤 호출어 없는 판정 시작, circuit reset. 저장 실패 시 시작 거부 |
| `헤이봇 대화 상태` | 코어라인 AI 연구소의 관리자 | mode·상태 저장·정책·적용 방 수·circuit·최근 generic 사유와 현재 응답 엔진 확인 |
| `헤이봇 대화 종료` | 코어라인 AI 연구소의 관리자 | OFF 상태 저장 후 모든 일반대화 허용방의 호출어 없는 판정 즉시 중단 |
| `헤이봇 대화 기본` | 코어라인 AI 연구소의 관리자 | 호출어·일반대화 응답 엔진을 Android 자체 GLM으로 전역 변경 |
| `헤이봇 대화 코덱스` | 코어라인 AI 연구소의 관리자 | 호출어·일반대화 응답 엔진을 Codex 프록시로 전역 변경 |
| `헤이봇 대화 그록` | 코어라인 AI 연구소의 관리자 | 호출어·일반대화 응답 엔진을 Grok 프록시로 전역 변경 |
| `헤이봇 자체진단` | 코어라인 AI 연구소의 관리자 | 외부 호출 없는 QUICK 진단 |
| `헤이봇 자체진단 통합/기기/카나리` | 코어라인 AI 연구소의 관리자 | readiness·기기 metadata·승인 대기 CANARY 진단 |

관리자 명령은 exact `user_id`와 `IRIS_BOT_ADMIN_CONTROL_CHAT_ID`가 모두 일치해야 한다. 다른 방의 관리자 명령은 실행되지 않는다. 로컬 명령은 GLM Queue를 거치지 않으므로 다른 방에서 120초 요청이 실행 중이어도 처리된다.
미인가 사용자는 관리자 목록을 알 수 없고 단순 거부 메시지만 받는다.

`헤이봇 도움말`은 일반 기능 2개 메시지로 나누어 표시한다. 코어라인 AI 연구소의
관리자에게는 일반 도움말 뒤에 일반대화, 응답 엔진, 운영·방 권한 설명 3개 메시지를
추가한다. 각 메시지는 카카오톡 응답 상한 480자 이내이므로 뒤쪽 관리자 명령이 잘리지
않는다.

### Android 자체진단

`헤이봇 자체진단`은 기본적으로 외부 생성·카카오톡 전송 없이 parser, 정책, 기억,
엔진 mode, queue/admission, 응답 safety를 fake gateway로 점검한다. 단계별 동작은 다음과 같다.

```text
헤이봇 자체진단             # QUICK: 네트워크·DB 쓰기·Replier 없음
헤이봇 자체진단 통합        # proxy-manager /ready와 conversation proxy readiness
헤이봇 자체진단 기기        # private 파일·프로세스 실행 환경·기기 metadata 읽기
헤이봇 자체진단 카나리      # 실제 생성은 하지 않고 승인 필요 상태만 확인
```

각 결과는 `PASS/WARN/FAIL/SKIP`, stable code, latency만 표시하며 token·대화 원문·절대
경로는 출력하지 않는다. 실제 GLM/Codex/Grok·이미지·영상·펜브러쉬 생성과 카카오톡 전송은
향후 별도 시험방·nonce·명시 확인을 통과한 CANARY에서만 허용한다.

ADB에서 카카오톡 명령 없이 Android 내부 runner만 확인할 때는 다음을 사용한다.

```bash
CLASSPATH=/data/local/tmp/Iris-glm.apk \
  app_process / ai.coreline.heybot.Main --self-test quick
CLASSPATH=/data/local/tmp/Iris-glm.apk \
  app_process / ai.coreline.heybot.Main --self-test integration
```

운영 배포의 HTTP API가 명시적으로 활성화된 경우에만 인증된 loopback
`GET /self-test?mode=quick|integration|device|canary`를 사용할 수 있다. 기본 PD20 설정은
HTTP API OFF이므로 카카오톡 관리자 명령 또는 `app_process --self-test`를 사용한다.

## 사용자별 대화 문맥

- 기본 문맥은 정확한 `(chat_id, user_id)` 단위다. 같은 방이라도 다른 참여자의 발화는 문맥에 넣지 않는다.
- 일반대화에서 `WAIT`로 판단된 미완성 발화만 같은 사용자에게 최대 2개·2분 동안 임시로 붙인다. 이는 메모리 파일에 저장되지 않으며, Iris 재기동·일반대화 종료·회로 차단·기억 초기화 때 삭제된다.
- `REPLY`가 safety 검사와 카카오 전송을 모두 통과한 뒤에만 user/assistant turn을 영속 기억에 저장한다. `IGNORE`, invalid JSON, 모델 실패, 안전 차단, 전송 실패는 assistant turn을 만들지 않는다.
- 방 전체 공유 문맥은 지원하지 않는다. 공개방에서 다른 사람의 대화를 잘못 이어받는 위험을 피하기 위한 정책이다.

## Queue·호출 제한 동작

1. 텍스트·허용방·봇 본인 메시지 여부를 확인한다.
2. 로컬 명령이면 즉시 처리한다.
3. GLM 질문이면 중복 `log_id`와 8초 동일 메시지를 차단한다.
4. 방별·사용자별 sliding window 제한을 적용한다.
5. 방별 FIFO Queue에 등록하고 전체 GLM semaphore를 획득할 때만 API를 호출한다.
6. 방별 또는 전체 Queue가 가득 차면 GLM을 호출하지 않고 잠시 후 재시도 안내를 보낸다.

동일 메시지는 `trim → 연속 공백 축소 → 소문자화 → SHA-256` key만 메모리에 보관한다.
운영 로그에는 사용자 메시지 원문·GLM 응답 원문·Authorization 값이 남지 않는다.

## 응답 safety와 일반대화 circuit

모델이 반환한 호출어 답변과 일반대화 `REPLY`는 카카오 전송 직전에 다음 단일 경계를 통과한다.

1. `<think>`와 code fence를 제거하고 공백을 정규화한다.
2. Bearer/Authorization, API key·token·secret/password assignment, `IRIS_*=` 값,
   `/data/local/private`, root credential 형태가 있으면 답변 전체를 보내지 않는다.
3. 이메일·전화번호·주민등록번호·카드번호 형태는 각각 고정된 마스킹 문구로 치환한다.
4. 빈 결과는 보내지 않고 최종 텍스트는 480자로 제한한다.

차단된 원문과 개인정보 원문은 로그·metric에 저장하지 않는다. 상태에는 안전 차단·마스킹
누계만 표시한다. 로컬 도움말·관리자 상태와 이미지 bytes는 이 모델 출력 sanitizer 대상이 아니다.

일반대화 circuit은 일반대화 외부 GLM 호출에서 발생한 timeout·429·network·server 오류만
센다. 기본 5분 window에서 3번째 실패가 발생하면 mode epoch를 무효화하고 일반대화만 OFF한다.
OFF 상태도 mode 파일에 저장하므로 프로세스 재시작으로 자동 재활성화되지 않는다.
대기·진행 중이던 오래된 일반대화 결과도 전송하지 않는다. 자동 재활성화는 하지 않으며
코어라인 AI 연구소 관리자가 `헤이봇 대화 시작`을 다시 실행하면 실패 window를 초기화한다.

## 제한 E2E 테스트

1. PD20의 봇 계정이 아닌 다른 카카오 계정으로 코어라인 AI 연구소 방에 다음을 전송한다.

   ```text
   헤이봇 안녕
   ```

2. 카카오톡 UI에서 최종 텍스트 답장이 정확히 한 건 발신되는지 확인한다.
3. 다음은 답장 0건이어야 한다.
   - `안녕`처럼 호출어 없는 메시지
   - 봇 계정이 보낸 메시지
   - 허용되지 않은 채팅방의 `헤이봇` 호출
   - 빈 `헤이봇`, `헤이봇:` 메시지
4. 토큰을 임시로 잘못된 값으로 바꾼 뒤 401을 만들었을 때, Iris 프로세스가 유지되고 카카오톡 오류 메시지를 보내지 않는지 확인한다.

## 중지 및 롤백

일반대화만 즉시 끄려면 코어라인 AI 연구소의 exact 관리자가 다음 명령을 보낸다.

```text
헤이봇 대화 종료
```

재기동 뒤에도 일반대화를 구성하지 않으려면 수동 기동 환경에서
`IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS`를 제거한다. 그러면 일반대화 policy가
fail-closed되어 `헤이봇 대화 시작`을 거부하지만 호출어 GLM과 이미지 기능은 유지된다.
정상 재기동은 저장된 mode를 복원하므로, 단순히 프로세스를 재시작하는 것은 일반대화
중지 방법이 아니다. 반드시 `헤이봇 대화 종료`를 사용하거나 정책 자체를 제거한다.

GLM만 비활성화하려면 GLM 관련 환경 변수를 빼고 동일 APK를 재기동한다. DBObserver는
계속 동작한다. HTTP 관리 API는 위 P0 설정에 따라 별도로 disabled 또는 인증 필요 상태다.

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=0123456789ABCDEF

"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[a]i.coreline.heybot\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  touch /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  chown root:root /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  chmod 600 /data/local/private/iris-config.json /data/local/private/iris-glm-startup.log
  : > /data/local/private/iris-glm-startup.log
  IRIS_CONFIG_PATH=/data/local/private/iris-config.json \\
  IRIS_HTTP_API_ENABLED=false \\
  IRIS_HTTP_ADMIN_SECRET_FILE=/data/local/private/iris-http-admin.token \\
  CLASSPATH=/data/local/tmp/Iris-glm.apk \\
  app_process / ai.coreline.heybot.Main \\
  > /data/local/private/iris-glm-startup.log 2>&1 &
'"
```

기동 로그의 `GLM auto-reply disabled`를 확인한다.

## 장애 코드

| 증상 | 의미 | 조치 |
|---|---|---|
| `GLM auto-reply disabled: ...` | 환경 변수 형식 또는 필수 값 누락 | 키를 제외한 환경 변수와 chat_id 형식을 확인 |
| `GlmFailure.Unauthorized` | 토큰/키 인증 실패 | 키를 재발급하거나 짧은 토큰을 갱신 |
| `GlmFailure.RateLimited` | API 사용량 또는 속도 제한 | 잠시 대기하고 Z.AI 사용량 정책 확인 |
| `GlmFailure.Timeout` | 호출어 질문 120초 또는 일반대화 판정 15초 안에 완료되지 않음 | `kind`·`elapsedMs`·`budgetMs` 진단 로그로 경로를 구분하고 네트워크·Z.AI 상태 확인 |
| 일반대화 mode가 자동으로 꺼짐 | 5분 안에 일반대화 외부 장애 3건으로 circuit trip | `헤이봇 대화 상태`의 generic 사유와 Z.AI 상태 확인 후 `헤이봇 대화 시작`으로 수동 복구 |
| `일반대화 정책이 준비되지 않아...` | allowlist 누락/범위 오류 또는 block 파일 형식·권한 오류 | 시작 로그의 generic policy reason과 root 파일 metadata를 확인 |
| 답장 없음, 오류 로그 없음 | 호출어/방/텍스트 타입 필터에서 제외 | `헤이봇 <질문>` 형식과 허용 chat_id 확인 |
