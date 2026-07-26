import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import type { GrokProxyConfig } from "../config/config.js";
import type { GrokTextRequest, GrokTextResponse } from "../conversation/types.js";

const OUTPUT_LIMIT = 32_768;

function instruction(request: GrokTextRequest): string {
  return [
    "You are a text-only assistant.",
    "Answer in Korean when the user uses Korean.",
    "Do not use image, video, browser, terminal, shell, file, or other tools.",
    "Return only the final assistant answer without markdown fences or status labels.",
    `MESSAGES_JSON=${JSON.stringify(request.messages)}`,
  ].join("\n");
}

async function runProcess(
  config: GrokProxyConfig,
  request: GrokTextRequest,
  signal: AbortSignal,
): Promise<string> {
  const workspace = resolve(config.runtimeDir, "text", request.requestId);
  mkdirSync(workspace, { recursive: true, mode: 0o700 });
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(config.cliCommand, [
      "-p", instruction(request), "--output-format", "json", "--max-turns", "2", "--no-memory", "--no-subagents", "--cwd", workspace,
    ], {
      cwd: workspace,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: { HOME: config.cliHome, PATH: `${resolve(config.cliCommand, "..")}: /usr/bin:/bin`.replace(": ", ":"), TERM: "dumb", NO_COLOR: "1" },
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const terminate = (signalName: NodeJS.Signals): void => {
      if (!child.pid) return;
      try { process.kill(-child.pid, signalName); } catch { child.kill(signalName); }
    };
    const timeout = setTimeout(() => {
      timedOut = true;
      terminate("SIGTERM");
      setTimeout(() => terminate("SIGKILL"), 2_000).unref();
    }, config.textTimeoutMs);
    const onAbort = (): void => terminate("SIGTERM");
    signal.addEventListener("abort", onAbort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => { if (stdout.length <= OUTPUT_LIMIT) stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk: Buffer) => { if (stderr.length <= OUTPUT_LIMIT) stderr += chunk.toString("utf8"); });
    child.once("error", (error) => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      if (signal.aborted) return reject(new Error("TEXT_CANCELLED"));
      if (timedOut) return reject(new Error("GROK_TEXT_TIMEOUT"));
      if (code !== 0 || stdout.length > OUTPUT_LIMIT || stderr.length > OUTPUT_LIMIT) return reject(new Error("GROK_TEXT_EXIT_NONZERO"));
      let parsed: { text?: unknown };
      try { parsed = JSON.parse(stdout) as { text?: unknown }; } catch { return reject(new Error("GROK_TEXT_PROTOCOL")); }
      if (typeof parsed.text !== "string" || !parsed.text.trim()) return reject(new Error("GROK_TEXT_OUTPUT_INVALID"));
      resolvePromise(parsed.text.trim());
    });
  });
}

export interface GrokTextRunner {
  run(request: GrokTextRequest, signal: AbortSignal): Promise<GrokTextResponse>;
  readiness(): Promise<{ ready: boolean; reason?: string }>;
}

export class CliGrokTextRunner implements GrokTextRunner {
  constructor(private readonly config: GrokProxyConfig) {}

  async readiness(): Promise<{ ready: boolean; reason?: string }> {
    return { ready: true };
  }

  async run(request: GrokTextRequest, signal: AbortSignal): Promise<GrokTextResponse> {
    const started = Date.now();
    const text = await runProcess(this.config, request, signal);
    if (text.length > this.config.textMaxOutputChars) throw new Error("GROK_TEXT_OUTPUT_INVALID");
    return { requestId: request.requestId, text, latencyMillis: Date.now() - started };
  }
}

export class FakeGrokTextRunner implements GrokTextRunner {
  async readiness(): Promise<{ ready: boolean }> { return { ready: true }; }
  async run(request: GrokTextRequest): Promise<GrokTextResponse> {
    const last = request.messages.at(-1)?.content.trim() || "질문";
    if (last.includes("[FAIL]")) throw new Error("GROK_TEXT_FAKE_FAILURE");
    return { requestId: request.requestId, text: `Grok 테스트 응답: ${last.slice(0, 240)}`, latencyMillis: 1 };
  }
}
