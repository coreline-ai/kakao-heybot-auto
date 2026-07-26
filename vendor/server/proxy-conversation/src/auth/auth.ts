import { readFileSync } from "node:fs";
import { timingSafeEqual } from "node:crypto";

export function readSecret(path: string): string {
  const value = readFileSync(path, "utf8").trim();
  if (!value) throw new Error("EMPTY_SECRET");
  return value;
}

export function authenticate(value: string | string[] | undefined, secret: string): boolean {
  const raw = Array.isArray(value) ? value[0] : value;
  const token = raw?.replace(/^Bearer\s+/i, "").trim() || "";
  const expected = secret.replace(/^Bearer\s+/i, "").trim();
  const a = Buffer.from(token);
  const b = Buffer.from(expected);
  return a.length === b.length && a.length > 0 && timingSafeEqual(a, b);
}
