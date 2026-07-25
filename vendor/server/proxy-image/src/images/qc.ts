import { createHash } from "node:crypto";
import { PNG } from "pngjs";

export interface ImageQcResult {
  width: number;
  height: number;
  bytes: number;
  sha256: string;
  brightness: number;
  contrast: number;
  entropy: number;
}

export function validatePng(data: Buffer, maximumBytes: number): ImageQcResult {
  if (data.length < 24 || data.length > maximumBytes) throw new Error("IMAGE_SIZE_INVALID");
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (!data.subarray(0, 8).equals(signature)) throw new Error("IMAGE_SIGNATURE_INVALID");
  let png: PNG;
  try {
    png = PNG.sync.read(data, { skipRescale: true });
  } catch {
    throw new Error("IMAGE_DECODE_FAILED");
  }
  if (png.width < 256 || png.height < 256 || png.width > 4096 || png.height > 4096) {
    throw new Error("IMAGE_DIMENSIONS_INVALID");
  }
  if (png.width * png.height > 16_777_216) throw new Error("IMAGE_PIXEL_LIMIT");

  const histogram = new Array<number>(32).fill(0);
  let count = 0;
  let sum = 0;
  let sumSquares = 0;
  const stride = Math.max(1, Math.floor((png.width * png.height) / 250_000));
  for (let pixel = 0; pixel < png.width * png.height; pixel += stride) {
    const offset = pixel * 4;
    const alpha = png.data[offset + 3] ?? 0;
    if (alpha < 16) continue;
    const red = png.data[offset] ?? 0;
    const green = png.data[offset + 1] ?? 0;
    const blue = png.data[offset + 2] ?? 0;
    const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    count += 1;
    sum += luminance;
    sumSquares += luminance * luminance;
    histogram[Math.min(31, Math.floor(luminance / 8))]! += 1;
  }
  if (count < 1_000) throw new Error("IMAGE_VISIBLE_PIXELS_LOW");
  const brightness = sum / count;
  const contrast = Math.sqrt(Math.max(0, sumSquares / count - brightness * brightness));
  let entropy = 0;
  for (const bucket of histogram) {
    if (bucket === 0) continue;
    const probability = bucket / count;
    entropy -= probability * Math.log2(probability);
  }
  if (brightness < 5) throw new Error("IMAGE_TOO_DARK");
  if (brightness > 250) throw new Error("IMAGE_TOO_BRIGHT");
  if (contrast < 4) throw new Error("IMAGE_LOW_CONTRAST");
  if (entropy < 1) throw new Error("IMAGE_LOW_ENTROPY");

  return {
    width: png.width,
    height: png.height,
    bytes: data.length,
    sha256: createHash("sha256").update(data).digest("hex"),
    brightness: Math.round(brightness * 100) / 100,
    contrast: Math.round(contrast * 100) / 100,
    entropy: Math.round(entropy * 100) / 100,
  };
}
