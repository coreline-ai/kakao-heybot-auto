import { createHash } from "node:crypto";
import { PNG } from "pngjs";
export function validatePng(data: Buffer, maxBytes: number): { bytes: number; sha256: string } {
  if (data.length < 24 || data.length > maxBytes || data.subarray(1,4).toString("ascii") !== "PNG") throw new Error("SOURCE_PNG_INVALID");
  let parsed: PNG; try { parsed = PNG.sync.read(data, { checkCRC: true }); } catch { throw new Error("SOURCE_PNG_INVALID"); }
  if (!parsed.width || !parsed.height || parsed.width * parsed.height > 33_177_600) throw new Error("SOURCE_PNG_DIMENSIONS_INVALID");
  return { bytes: data.length, sha256: createHash("sha256").update(data).digest("hex") };
}
