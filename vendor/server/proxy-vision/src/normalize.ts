import { spawn } from "node:child_process";
import type { VisionConfig } from "./config.js";

export interface FetchedVisionImage {
  data: Buffer;
  mediaType: string;
}

export async function normalizeVisionImage(
  source: FetchedVisionImage,
  config: VisionConfig,
  signal: AbortSignal,
): Promise<FetchedVisionImage> {
  if (source.mediaType !== "image/gif") return source;
  const data = await convertGifFirstFrame(source.data, config.ffmpegCommand, config.imageMaxBytes, signal);
  return { data, mediaType: "image/png" };
}

export function convertGifFirstFrame(
  input: Buffer,
  command: string,
  maxOutputBytes: number,
  signal: AbortSignal,
): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new Error("VISION_CANCELLED"));
    const child = spawn(command, [
      "-hide_banner", "-loglevel", "error", "-nostdin",
      "-f", "gif", "-i", "pipe:0", "-frames:v", "1",
      "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ], { stdio: ["pipe", "pipe", "pipe"] });
    const output: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;

    const finish = (error?: Error, value?: Buffer) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      if (error) reject(error); else resolve(value!);
    };
    const abort = () => {
      child.kill("SIGKILL");
      finish(new Error("VISION_CANCELLED"));
    };
    signal.addEventListener("abort", abort, { once: true });
    child.once("error", () => finish(new Error("GIF_CONVERSION_FAILED")));
    child.stdout.on("data", (chunk: Buffer) => {
      outputBytes += chunk.length;
      if (outputBytes > maxOutputBytes) {
        child.kill("SIGKILL");
        finish(new Error("SOURCE_TOO_LARGE"));
        return;
      }
      output.push(Buffer.from(chunk));
    });
    child.stderr.resume();
    child.stdin.on("error", () => undefined);
    child.once("close", (code) => {
      if (settled) return;
      const data = Buffer.concat(output);
      const png = data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
      if (code !== 0 || !png || data.length === 0) return finish(new Error("GIF_CONVERSION_FAILED"));
      finish(undefined, data);
    });
    child.stdin.end(input);
  });
}
