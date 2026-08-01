# proxy-vision

Kakao CDN의 검증된 단일 이미지를 받아 Codex `image.analyze.v1` capability로 분석하는 내부 도메인 프록시다.

- gateway: `/v1/vision/jobs`
- source allowlist: exact `talk.kakaocdn.net`, HTTPS only, redirect disabled
- maximum source: 10 MiB
- signed source URL is cleared from SQLite when a job reaches a terminal state
- output: strict versioned Korean summary JSON
