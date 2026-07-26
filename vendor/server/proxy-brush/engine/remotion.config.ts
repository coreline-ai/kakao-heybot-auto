// Remotion CLI 전역 설정 — 렌더 품질 값은 참조 시스템(new-video-gen)과 동일하게 고정.
// Phase 2 골든 파리티 비교(픽셀 diff)의 전제 조건이므로 임의 변경 금지.
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setCodec("h264");
Config.setPixelFormat("yuv420p");
Config.setCrf(18);
