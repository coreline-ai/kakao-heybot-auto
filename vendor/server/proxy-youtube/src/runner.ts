import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { access, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";
import type { YoutubeProxyConfig } from "./config.js";
const exec = promisify(execFile);

/** A bounded device-friendly MP4 profile selected from source duration. */
export interface KakaoLiteProfile {
  /** Landscape output ceiling. */
  width: number;
  height: number;
  /** Portrait output ceiling. Keeping the long edge avoids 270px shorts. */
  portraitWidth: number;
  portraitHeight: number;
  fps: number;
  videoBitrate: number;
  maxVideoBitrate: number;
  audioBitrate: number;
  estimatedMaximumBytes: number;
}

/**
 * Select a small server-side output before any bytes reach Android.  CRF-only
 * encoding made final size depend on source motion/noise; a VBV ceiling keeps
 * Kakao's in-app processing load bounded even for a difficult source.
 */
export function selectKakaoLiteProfile(
  durationSeconds: number,
  targetBytes: number,
): KakaoLiteProfile {
  if (!Number.isFinite(durationSeconds) || durationSeconds < 1) {
    throw new Error("YOUTUBE_UNSUPPORTED_SOURCE");
  }
  const duration = Math.ceil(durationSeconds);
  // This is deliberately a quality-balanced profile rather than a tiny
  // transport profile.  Android never transcodes this MP4, and Kakao can
  // apply another encode after direct-share, so 426x240 / 180kbps is visibly
  // degraded in the recipient room.
  const tier = duration <= 3 * 60
    ? {
        width: 854, height: 480, portraitWidth: 480, portraitHeight: 854,
        preferredVideo: 1_100_000, audio: 96_000,
      }
    : duration <= 5 * 60
      ? {
          width: 854, height: 480, portraitWidth: 480, portraitHeight: 854,
          preferredVideo: 880_000, audio: 96_000,
        }
      : duration <= 10 * 60
        ? {
            width: 640, height: 360, portraitWidth: 360, portraitHeight: 640,
            preferredVideo: 400_000, audio: 64_000,
          }
        : {
            width: 480, height: 270, portraitWidth: 270, portraitHeight: 480,
            preferredVideo: 260_000, audio: 64_000,
          };

  // Reserve 5% for muxing and encoder variation.  The computed ceiling takes
  // precedence over a quality preference, which is what holds long videos to
  // the configured output budget.
  const permittedTotal = Math.floor((targetBytes * 8 * 0.95) / duration);
  const maxVideoBitrate = Math.floor(permittedTotal - tier.audio);
  if (maxVideoBitrate < 96_000) throw new Error("YOUTUBE_FILE_TOO_LARGE");
  const videoBitrate = Math.min(
    tier.preferredVideo,
    Math.floor(maxVideoBitrate * 0.88),
  );
  const estimatedMaximumBytes = Math.ceil(
    ((maxVideoBitrate + tier.audio) * duration) / 8 * 1.02,
  );
  return {
    width: tier.width,
    height: tier.height,
    portraitWidth: tier.portraitWidth,
    portraitHeight: tier.portraitHeight,
    fps: 24,
    videoBitrate,
    maxVideoBitrate,
    audioBitrate: tier.audio,
    estimatedMaximumBytes,
  };
}

/**
 * Keeps a portrait source's long edge instead of applying a landscape-only
 * 854x480 box.  `force_divisible_by=2` preserves H.264/yuv420p compatibility.
 */
export function kakaoBalancedScaleFilter(profile: KakaoLiteProfile): string {
  return "fps=" + profile.fps +
    ",scale=w='if(gte(iw,ih),min(" + profile.width + ",iw),min(" + profile.portraitWidth + ",iw))'" +
    ":h='if(gte(iw,ih),min(" + profile.height + ",ih),min(" + profile.portraitHeight + ",ih))'" +
    ":force_original_aspect_ratio=decrease:force_divisible_by=2";
}

export interface YoutubeRunner { readiness():Promise<{ready:boolean;version?:string;reason?:string}>; download(jobId:string,url:string,signal:AbortSignal):Promise<{path:string;bytes:number;sha256:string}>; }
export function canonicalYoutubeUrl(value:string):string { const url=new URL(value); if(url.protocol!=="https:"||url.username||url.password||url.port)throw new Error("YOUTUBE_URL_INVALID"); const host=url.hostname.toLowerCase().replace(/^www\./,""); let id:string|undefined; if(host==="youtu.be") id=url.pathname.split("/").filter(Boolean)[0]; else if(host==="youtube.com"||host==="m.youtube.com"){if(url.pathname==="/watch")id=url.searchParams.get("v")??undefined;else if(url.pathname.startsWith("/shorts/"))id=url.pathname.split("/")[2];} else throw new Error("YOUTUBE_URL_INVALID"); if(!id||!/^[A-Za-z0-9_-]{11}$/.test(id)||url.searchParams.has("list"))throw new Error("YOUTUBE_URL_INVALID"); return `https://www.youtube.com/watch?v=${id}`; }
export function createYoutubeRunner(config:YoutubeProxyConfig):YoutubeRunner { return config.runnerMode === "fake" ? fake(config) : cli(config); }
function cli(config: YoutubeProxyConfig): YoutubeRunner {
  return {
    async readiness() {
      try {
        await access(config.ytDlpBin);
        await access(config.ffprobeBin);
        await access(config.ffmpegBin);
        const { stdout } = await exec(config.ytDlpBin, ["--version"], {
          timeout: 5_000,
          maxBuffer: 4_096,
        });
        return { ready: true, version: stdout.trim().slice(0, 80) };
      } catch {
        return { ready: false, reason: "YTDLP_OR_MEDIA_TOOL_UNAVAILABLE" };
      }
    },
    async download(jobId, input, signal) {
      const url = canonicalYoutubeUrl(input);
      const work = resolve(config.runtimeDir, "work", jobId);
      const out = resolve(config.runtimeDir, "artifacts", `${jobId}.mp4`);
      await rm(work, { recursive: true, force: true });
      await mkdir(work, { recursive: true, mode: 0o700 });
      await mkdir(resolve(config.runtimeDir, "artifacts"), { recursive: true, mode: 0o700 });
      try {
        // `--print-json` includes every available format and can easily exceed
        // a bounded child-process buffer.  Ask yt-dlp for only the policy fields.
        const metadata = await exec(
          config.ytDlpBin,
          [
            "--no-config",
            "--no-playlist",
            "--skip-download",
            "--print",
            "%(duration)s\t%(is_live)s",
            "--",
            url,
          ],
          { timeout: 30_000, maxBuffer: 4_096, signal },
        );
        const [durationRaw, liveRaw] = metadata.stdout.trim().split("\t", 2);
        const duration = Number(durationRaw);
        if (
          !Number.isFinite(duration) ||
          duration < 1 ||
          duration > config.maxDurationSeconds ||
          liveRaw?.trim().toLowerCase() === "true"
        ) {
          throw new Error("YOUTUBE_UNSUPPORTED_SOURCE");
        }
        const profile = selectKakaoLiteProfile(duration, config.kakaoTargetBytes);

        const template = resolve(work, "source.%(ext)s");
        await exec(
          config.ytDlpBin,
          [
            "--no-config",
            "--no-playlist",
            "--no-progress",
            "--no-warnings",
            "--no-write-info-json",
            "--no-write-thumbnail",
            "--no-write-subs",
            // Prefer a broadly Kakao-compatible AVC/AAC MP4 over a newer AV1
            // representation that can decode inconsistently on recipients' clients.
            "--format",
            "bestvideo[vcodec^=avc1][ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]",
            "--merge-output-format",
            "mp4",
            "--output",
            template,
            "--",
            url,
          ],
          { timeout: config.processTimeoutMs, maxBuffer: 64 * 1024, signal },
        );
        const downloaded = resolve(work, "source.mp4");
        const produced = resolve(work, "normalized.mp4");
        // Normalize on the server. Android only stores this final MP4 and
        // delivers it; it never performs a video transcode itself.
        await exec(
          config.ffmpegBin,
          [
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            downloaded,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            kakaoBalancedScaleFilter(profile),
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level:v",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-b:v",
            `${profile.videoBitrate}`,
            "-maxrate",
            `${profile.maxVideoBitrate}`,
            "-bufsize",
            `${profile.maxVideoBitrate * 2}`,
            "-c:a",
            "aac",
            "-b:a",
            `${profile.audioBitrate}`,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            produced,
          ],
          { timeout: config.processTimeoutMs, maxBuffer: 64 * 1024, signal },
        );
        const bytes = await readFile(produced);
        if (
          bytes.length < 16 ||
          bytes.length > config.maxBytes ||
          bytes.subarray(4, 8).toString("ascii") !== "ftyp"
        ) {
          throw new Error("YOUTUBE_RESULT_INVALID");
        }
        const probe = await exec(
          config.ffprobeBin,
          [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,profile,pix_fmt",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            produced,
          ],
          { timeout: 10_000, maxBuffer: 4_096, signal },
        );
        const probeFields = probe.stdout
          .trim()
          .split(/\r?\n/)
          .map((value) => value.trim().toLowerCase());
        if (
          probeFields[0] !== "h264" ||
          !probeFields[1]?.includes("baseline") ||
          probeFields[2] !== "yuv420p"
        ) {
          throw new Error("YOUTUBE_RESULT_CODEC_INVALID");
        }
        await writeFile(`${out}.tmp`, bytes, { mode: 0o600 });
        await rename(`${out}.tmp`, out);
        return {
          path: out,
          bytes: bytes.length,
          sha256: createHash("sha256").update(bytes).digest("hex"),
        };
      } catch (error) {
        await rm(`${out}.tmp`, { force: true });
        throw normalize(error);
      } finally {
        await rm(work, { recursive: true, force: true });
      }
    },
  };
}
function fake(config:YoutubeProxyConfig):YoutubeRunner { return { async readiness(){return {ready:true,version:"fake"}}, async download(jobId,input){canonicalYoutubeUrl(input);const out=resolve(config.runtimeDir,"artifacts",`${jobId}.mp4`);await mkdir(resolve(config.runtimeDir,"artifacts"),{recursive:true,mode:0o700});const bytes=Buffer.from([0,0,0,16,0x66,0x74,0x79,0x70,0x69,0x73,0x6f,0x6d,0,0,0,0]);await writeFile(out,bytes,{mode:0o600});return {path:out,bytes:bytes.length,sha256:createHash("sha256").update(bytes).digest("hex")};} }; }
function normalize(error:unknown):Error {const raw=(error as Error)?.message||"YOUTUBE_DOWNLOAD_FAILED";if(/YOUTUBE_[A-Z_]+/.test(raw))return new Error(raw.match(/YOUTUBE_[A-Z_]+/)![0]);return new Error("YOUTUBE_DOWNLOAD_FAILED");}
