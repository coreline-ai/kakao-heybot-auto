import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

export class TranscriptCipher {
  readonly #key: Buffer;

  constructor(keyFile: string) {
    const raw = readFileSync(keyFile, "utf8").trim();
    if (!/^[0-9a-fA-F]{64}$/.test(raw)) throw new Error("INVALID_TRANSCRIPT_KEY");
    this.#key = Buffer.from(raw, "hex");
  }

  encrypt(value: unknown): string {
    const nonce = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.#key, nonce);
    const ciphertext = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
    return Buffer.concat([nonce, cipher.getAuthTag(), ciphertext]).toString("base64");
  }

  decrypt<T>(encoded: string): T {
    const value = Buffer.from(encoded, "base64");
    if (value.length < 29) throw new Error("INVALID_TRANSCRIPT_CIPHERTEXT");
    const nonce = value.subarray(0, 12);
    const tag = value.subarray(12, 28);
    const decipher = createDecipheriv("aes-256-gcm", this.#key, nonce);
    decipher.setAuthTag(tag);
    return JSON.parse(Buffer.concat([decipher.update(value.subarray(28)), decipher.final()]).toString("utf8")) as T;
  }
}
