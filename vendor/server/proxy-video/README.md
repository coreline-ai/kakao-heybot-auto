# HeyBot Video Proxy (`proxy-video`)

`proxy-video`는 카카오 방에 귀속된 비디오 job, 큐, MP4 QC와 최종 artifact를 담당한다.
Grok OAuth·CLI는 직접 보유하지 않고 내부 loopback `proxy-grok`에만 위임한다.

> 현재 registry와 Android 환경 변수는 **기본 OFF**다. private-room E2E와 비용 승인 전에는 enable하지 않는다.

## 흐름

```text
PD20 Iris --ADB reverse--> proxy-manager /v1/video
  -> proxy-video :4357
  -> proxy-grok :4358
  -> authenticated official Grok CLI
```

- gateway API: `POST/GET/DELETE /v1/video/jobs`, `GET .../file`
- 모든 job 조회·취소·파일은 `jobId + chatId`로 재검증한다.
- queue: 전체 2개, 방당 1개, concurrency 1.
- MP4 `ftyp`, 파일 크기, `ffprobe`의 primary H.264·5~11초·256~1920px을 통과한 파일만 보관한다. 부가 AAC/MJPEG stream은 허용한다.
- artifact는 24시간 뒤 삭제 대상이며 public URL은 만들지 않는다.

## 운영 안전장치

- loopback bind, manager→video와 video→grok 별도 secret file, constant-time 비교
- prompt 외에 CLI argv/env/cwd/output path를 사용자 입력으로 받지 않음
- `POST /v1/self-test/generate`는 `x-confirm-cost: true` 없이는 실행되지 않음
- `GET /ready`는 Grok readiness와 queue 상태만 확인하며 생성하지 않음

## 명령 계약

Android 명령은 `헤이봇 영상 <설명>`이며, `영상 상태`·`영상 취소`·`영상 재전송`을 지원한다.
방 권한은 control room 관리자만 `헤이봇 방 영상 허용 R02` → `헤이봇 방 적용 <코드>`로 켤 수 있다.

## 검증 상태

- TypeScript build·unit test 완료
- 실제 Grok CLI의 6초 MP4 생성은 사전 검증됨
- launchd/registry enable, Android reverse 및 native Kakao outgoing-video log `16`의 물리 E2E는 아직 수행하지 않음
