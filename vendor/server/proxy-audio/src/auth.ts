import { readFileSync } from "node:fs";
import { timingSafeEqual } from "node:crypto";

export function readSecret(path: string): string {
  const value = readFileSync(path, "utf8").trim();
  if (value.length < 24 || value.length > 512) throw new Error("INVALID_SECRET_FILE");
  return value;
}

export function authenticate(header: string | string[] | undefined, expected: string): boolean {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7));
  const wanted = Buffer.from(expected);
  return supplied.length === wanted.length && timingSafeEqual(supplied, wanted);
}
