import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { timingSafeEqual } from "node:crypto";

function readSecret(path: string): string {
  const secret = readFileSync(path, "utf8").trim();
  if (secret.length < 24 || secret.length > 512) {
    throw new Error("Secret file must contain 24-512 characters");
  }
  return secret;
}

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function bearerToken(header: string | string[] | undefined): string | undefined {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return undefined;
  return header.slice("Bearer ".length);
}

export class CodexAuthenticator {
  readonly #managerSecret: string;
  readonly #callerSecretsDir: string;
  readonly #callerCache = new Map<string, string>();

  constructor(managerSecretFile: string, callerSecretsDir: string) {
    this.#managerSecret = readSecret(managerSecretFile);
    this.#callerSecretsDir = callerSecretsDir;
  }

  authenticateManager(header: string | string[] | undefined): boolean {
    const token = bearerToken(header);
    return token !== undefined && safeEqual(token, this.#managerSecret);
  }

  authenticateCaller(
    caller: string,
    header: string | string[] | undefined,
  ): boolean {
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(caller)) return false;
    const token = bearerToken(header);
    if (token === undefined) return false;
    let expected = this.#callerCache.get(caller);
    if (expected === undefined) {
      try {
        expected = readSecret(resolve(this.#callerSecretsDir, `${caller}.secret`));
      } catch {
        return false;
      }
      this.#callerCache.set(caller, expected);
    }
    return safeEqual(token, expected);
  }
}
