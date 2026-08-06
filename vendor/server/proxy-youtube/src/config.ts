import { isAbsolute, resolve } from "node:path";

export interface YoutubeProxyConfig {
  host: string; port: number; managerSecretFile: string; runtimeDir: string; databaseFile: string;
  ytDlpBin: string; ffprobeBin: string; ffmpegBin: string; runnerMode: "cli" | "fake"; requestMaxBytes: number;
  /** Hard artifact bound accepted by Android and exposed to Kakao. */
  maxBytes: number;
  /** Conservative target used to select the Kakao-lite output profile. */
  kakaoTargetBytes: number;
  maxDurationSeconds: number; processTimeoutMs: number; maxConcurrency: number;
  maxPendingPerRoom: number; artifactTtlMs: number;
}
function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const raw = env[name]; if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const value = Number(raw); if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} is out of range`); return value;
}
function absolute(env: NodeJS.ProcessEnv, name: string, fallback: string): string {
  const value = env[name]?.trim() || fallback; if (!isAbsolute(value)) throw new Error(`${name} must be absolute`); return value;
}
export function loadYoutubeProxyConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): YoutubeProxyConfig {
  const host = env.YOUTUBE_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error("YOUTUBE_PROXY_HOST must be loopback");
  const runner = (env.YOUTUBE_PROXY_RUNNER_MODE || "cli").trim().toLowerCase();
  if (runner !== "cli" && runner !== "fake") throw new Error("YOUTUBE_PROXY_RUNNER_MODE must be cli or fake");
  const runtimeDir = resolve(cwd, env.YOUTUBE_PROXY_RUNTIME_DIR?.trim() || "./runtime");
  // Leave headroom below Android's 50 MiB transport guard while preserving
  // enough source detail for Kakao's subsequent direct-share processing.
  const maxBytes = integer(env, "YOUTUBE_PROXY_MAX_BYTES", 42 * 1024 * 1024, 1024, 100 * 1024 * 1024);
  const kakaoTargetBytes = integer(
    env,
    "YOUTUBE_PROXY_KAKAO_TARGET_BYTES",
    38 * 1024 * 1024,
    1024 * 1024,
    maxBytes,
  );
  return { host, port: integer(env, "YOUTUBE_PROXY_PORT", 4364, 1, 65535),
    managerSecretFile: resolve(cwd, env.YOUTUBE_PROXY_MANAGER_SECRET_FILE?.trim() || "./runtime/secrets/manager.secret"),
    runtimeDir, databaseFile: resolve(runtimeDir, "db/youtube.sqlite3"), runnerMode: runner,
    ytDlpBin: absolute(env, "YOUTUBE_PROXY_YTDLP_BIN", "/usr/local/bin/yt-dlp"),
    ffprobeBin: absolute(env, "YOUTUBE_PROXY_FFPROBE_BIN", "/usr/bin/ffprobe"),
    ffmpegBin: absolute(env, "YOUTUBE_PROXY_FFMPEG_BIN", "/usr/bin/ffmpeg"),
    requestMaxBytes: integer(env, "YOUTUBE_PROXY_REQUEST_MAX_BYTES", 8192, 1024, 65536),
    maxBytes, kakaoTargetBytes,
    maxDurationSeconds: integer(env, "YOUTUBE_PROXY_MAX_DURATION_SECONDS", 900, 1, 7200),
    processTimeoutMs: integer(env, "YOUTUBE_PROXY_PROCESS_TIMEOUT_MS", 10 * 60_000, 10_000, 3600_000),
    maxConcurrency: integer(env, "YOUTUBE_PROXY_MAX_CONCURRENCY", 1, 1, 4),
    maxPendingPerRoom: integer(env, "YOUTUBE_PROXY_MAX_PENDING_PER_ROOM", 1, 1, 8),
    artifactTtlMs: integer(env, "YOUTUBE_PROXY_ARTIFACT_TTL_MS", 30 * 60_000, 60_000, 24 * 60 * 60_000),
  };
}
