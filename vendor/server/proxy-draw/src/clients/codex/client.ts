import { readSecret } from "../../auth/auth.js";
import type { DrawProxyConfig } from "../../config/config.js";

export interface CodexJobResponse {
  jobId: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  error?: { code: string };
  artifacts: Array<{ artifactId: string; mediaType: string; bytes: number; sha256: string }>;
}

export class CodexClient {
  readonly #secret: string;
  constructor(private readonly config: DrawProxyConfig) { this.#secret = readSecret(config.codexSecretFile); }
  async readiness(signal?: AbortSignal): Promise<{ ready: boolean; reason?: string }> {
    try {
      const response = await fetch(`${this.config.codexBaseUrl}/ready`, { signal });
      const body = await response.json() as { ready?: boolean; reason?: string };
      return { ready: response.ok && body.ready === true, reason: body.reason };
    } catch { return { ready: false, reason: "CODEX_UNAVAILABLE" }; }
  }
  async create(requestId: string, prompt: string, signal: AbortSignal): Promise<CodexJobResponse> {
    const response = await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/jobs`, { method: "POST", signal,
      headers: this.headers("application/json"),
      body: JSON.stringify({ requestId, capability: "image.generate", input: { prompt }, artifactContract: { acceptedMediaTypes: ["image/png"], maxArtifacts: 1, maxBytesPerArtifact: this.config.imageMaxBytes } }),
    });
    const body = await response.json() as CodexJobResponse & { error?: { code: string } };
    if (!response.ok) throw new Error(body.error?.code || "CODEX_CREATE_FAILED");
    return body;
  }
  async get(jobId: string, signal: AbortSignal): Promise<CodexJobResponse> {
    const response = await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/jobs/${encodeURIComponent(jobId)}`, { signal, headers: this.headers() });
    const body = await response.json() as CodexJobResponse & { error?: { code: string } };
    if (!response.ok) throw new Error(body.error?.code || "CODEX_STATUS_FAILED");
    return body;
  }
  async download(jobId: string, artifactId: string, signal: AbortSignal): Promise<Buffer> {
    const response = await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}`, { signal, headers: this.headers() });
    if (!response.ok || response.headers.get("content-type") !== "image/png") throw new Error("CODEX_ARTIFACT_DOWNLOAD_FAILED");
    const length = Number(response.headers.get("content-length") || 0);
    if (length > this.config.imageMaxBytes) throw new Error("SOURCE_IMAGE_SIZE_INVALID");
    const data = Buffer.from(await response.arrayBuffer());
    if (!data.length || data.length > this.config.imageMaxBytes) throw new Error("SOURCE_IMAGE_SIZE_INVALID");
    return data;
  }
  async cancel(jobId: string): Promise<void> { await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE", headers: this.headers() }).catch(() => undefined); }
  private headers(contentType?: string): Record<string, string> { return { authorization: `Bearer ${this.#secret}`, "x-heybot-service-id": this.config.codexServiceId, ...(contentType ? { "content-type": contentType } : {}) }; }
}
