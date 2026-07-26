import { readSecret } from "../../auth/auth.js";
import type { DrawProxyConfig } from "../../config/config.js";

export interface BrushJobResponse {
  jobId: string;
  requestId: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  error?: { code: string };
  file?: { mediaType: string; bytes: number; sha256: string };
}

export class BrushClient {
  readonly #secret: string;
  constructor(private readonly config: DrawProxyConfig) { this.#secret = readSecret(config.brushSecretFile); }
  async readiness(signal?: AbortSignal): Promise<{ ready: boolean }> {
    try { const response = await fetch(`${this.config.brushBaseUrl}/ready`, { signal }); const body = await response.json() as { ready?: boolean }; return { ready: response.ok && body.ready === true }; }
    catch { return { ready: false }; }
  }
  async create(requestId: string, source: Buffer, seed: number, signal: AbortSignal): Promise<BrushJobResponse> {
    const response = await fetch(`${this.config.brushBaseUrl}/internal/v1/pen-brush/jobs`, { method: "POST", signal, headers: this.headers("application/json"), body: JSON.stringify({ requestId, sourcePngBase64: source.toString("base64"), seed }) });
    const body = await response.json() as BrushJobResponse & { error?: { code: string } };
    if (!response.ok) throw new Error(body.error?.code || "BRUSH_CREATE_FAILED");
    return body;
  }
  async get(jobId: string, signal: AbortSignal): Promise<BrushJobResponse> {
    const response = await fetch(`${this.config.brushBaseUrl}/internal/v1/pen-brush/jobs/${encodeURIComponent(jobId)}`, { signal, headers: this.headers() });
    const body = await response.json() as BrushJobResponse & { error?: { code: string } };
    if (!response.ok) throw new Error(body.error?.code || "BRUSH_STATUS_FAILED");
    return body;
  }
  async download(jobId: string, signal: AbortSignal): Promise<Buffer> {
    const response = await fetch(`${this.config.brushBaseUrl}/internal/v1/pen-brush/jobs/${encodeURIComponent(jobId)}/file`, { signal, headers: this.headers() });
    if (!response.ok || response.headers.get("content-type") !== "video/mp4") throw new Error("BRUSH_ARTIFACT_DOWNLOAD_FAILED");
    const length = Number(response.headers.get("content-length") || 0);
    if (length > this.config.videoMaxBytes) throw new Error("VIDEO_SIZE_INVALID");
    const data = Buffer.from(await response.arrayBuffer());
    if (!data.length || data.length > this.config.videoMaxBytes) throw new Error("VIDEO_SIZE_INVALID");
    return data;
  }
  async cancel(jobId: string): Promise<void> { await fetch(`${this.config.brushBaseUrl}/internal/v1/pen-brush/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE", headers: this.headers() }).catch(() => undefined); }
  private headers(contentType?: string): Record<string, string> { return { authorization: `Bearer ${this.#secret}`, "x-heybot-service-id": this.config.brushServiceId, ...(contentType ? { "content-type": contentType } : {}) }; }
}
