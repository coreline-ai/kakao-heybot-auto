import type { VisionConfig } from "./config.js";
import type { VisionSource } from "./types.js";

export function validateSource(source: VisionSource, config: VisionConfig, now = Date.now()): void {
  let url: URL;
  try { url = new URL(source.url); } catch { throw new Error("INVALID_SOURCE_URL"); }
  if (
    url.protocol !== "https:" || url.hostname !== config.allowedSourceHost ||
    url.username || url.password || (url.port && url.port !== "443") || url.hash
  ) throw new Error("FORBIDDEN_SOURCE");
  if (source.url.length > 4_096 || !url.pathname) throw new Error("INVALID_SOURCE_URL");
  if (!Number.isSafeInteger(source.width) || source.width < 1 || source.width > 16_384 ||
      !Number.isSafeInteger(source.height) || source.height < 1 || source.height > 16_384 ||
      !Number.isSafeInteger(source.declaredBytes) || source.declaredBytes < 1 || source.declaredBytes > config.imageMaxBytes) {
    throw new Error("INVALID_SOURCE_METADATA");
  }
  if (!Number.isSafeInteger(source.expiresAtMillis) || source.expiresAtMillis <= now) throw new Error("SOURCE_EXPIRED");
}

export async function fetchSource(source: VisionSource, config: VisionConfig, signal: AbortSignal): Promise<{data:Buffer; mediaType:string}> {
  validateSource(source, config);
  const response = await fetch(source.url, {
    method: "GET", redirect: "manual", signal,
    headers: { accept: "image/png,image/jpeg,image/webp,image/gif" },
  });
  if (response.status >= 300 && response.status < 400) throw new Error("SOURCE_REDIRECT_FORBIDDEN");
  if (!response.ok) throw new Error("SOURCE_FETCH_FAILED");
  const declaredMediaType = (response.headers.get("content-type") || "").split(";", 1)[0]!.trim().toLowerCase();
  const length = Number(response.headers.get("content-length") || 0);
  if (length && (length > config.imageMaxBytes || length !== source.declaredBytes)) throw new Error("SOURCE_SIZE_MISMATCH");
  const data = Buffer.from(await response.arrayBuffer());
  if (data.length > config.imageMaxBytes) throw new Error("SOURCE_TOO_LARGE");
  if (data.length !== source.declaredBytes) throw new Error("SOURCE_SIZE_MISMATCH");
  const png=data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
  const jpeg=data[0]===0xff && data[1]===0xd8 && data.at(-2)===0xff && data.at(-1)===0xd9;
  const webp=data.subarray(0,4).toString("ascii")==="RIFF" && data.subarray(8,12).toString("ascii")==="WEBP";
  const gif=data.subarray(0,6).toString("ascii")==="GIF87a" || data.subarray(0,6).toString("ascii")==="GIF89a";
  const mediaType = png ? "image/png" : jpeg ? "image/jpeg" : webp ? "image/webp" : gif ? "image/gif" : "";
  if (!mediaType) throw new Error("INVALID_IMAGE");
  if (declaredMediaType && declaredMediaType !== "application/octet-stream" && declaredMediaType !== mediaType) {
    throw new Error("INVALID_IMAGE");
  }
  return {data,mediaType};
}
