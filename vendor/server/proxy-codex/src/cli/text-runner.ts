import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import type { CodexProxyConfig } from "../config/config.js";
import type { CodexTextRequest, CodexTextResponse } from "../conversation/types.js";

const OUTPUT_LIMIT = 32_768;

function childEnvironment(config: CodexProxyConfig, workspace: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of ["PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  if (config.codexHome) env.CODEX_HOME = config.codexHome;
  env.TMPDIR = resolve(workspace, "tmp");
  return env;
}

function instruction(request: CodexTextRequest): string {
  return [
    "You are a text-only assistant.",
    "Answer the conversation in Korean when the user uses Korean.",
    "Do not use tools, image generation, video generation, browser, terminal, shell, or files.",
    "Return only the final assistant answer. Do not use markdown fences or status labels.",
    `MESSAGES_JSON=${JSON.stringify(request.messages)}`,
  ].join("\n");
}

async function runProcess(
  executable: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv; stdin: string; timeoutMs: number; signal: AbortSignal },
): Promise<{ code: number | null; stdout: string }> {
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(executable, args, {
      cwd: options.cwd,
      env: options.env,
      detached: true,
      stdio: ["pipe", "pipe", "ignore"],
    });
    let stdout = "";
    let timedOut = false;
    const terminate = (signal: NodeJS.Signals): void => {
      if (!child.pid) return;
      try { process.kill(-child.pid, signal); } catch { child.kill(signal); }
    };
    const timeout = setTimeout(() => {
      timedOut = true;
      terminate("SIGTERM");
      setTimeout(() => terminate("SIGKILL"), 2_000).unref();
    }, options.timeoutMs);
    const onAbort = (): void => {
      terminate("SIGTERM");
      setTimeout(() => terminate("SIGKILL"), 2_000).unref();
    };
    options.signal.addEventListener("abort", onAbort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => {
      if (stdout.length <= OUTPUT_LIMIT) stdout += chunk.toString("utf8");
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      options.signal.removeEventListener("abort", onAbort);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timeout);
      options.signal.removeEventListener("abort", onAbort);
      if (options.signal.aborted) return reject(new Error("TEXT_CANCELLED"));
      if (timedOut) return reject(new Error("CODEX_TEXT_TIMEOUT"));
      resolvePromise({ code, stdout });
    });
    child.stdin.end(options.stdin);
  });
}

export interface CodexTextRunner {
  run(request: CodexTextRequest, signal: AbortSignal): Promise<CodexTextResponse>;
  readiness(): Promise<{ ready: boolean; reason?: string }>;
}

export class CliCodexTextRunner implements CodexTextRunner {
  constructor(private readonly config: CodexProxyConfig) {}

  async readiness(): Promise<{ ready: boolean; reason?: string }> {
    const result = await runProcess(this.config.cliBin, ["--version"], {
      cwd: this.config.runtimeDir,
      env: childEnvironment(this.config, this.config.runtimeDir),
      stdin: "",
      timeoutMs: 10_000,
      signal: new AbortController().signal,
    }).catch((error: Error) => ({ code: -1, stdout: error.message }));
    return result.code === 0 && result.stdout.trim().startsWith("codex-cli ")
      ? { ready: true }
      : { ready: false, reason: "CODEX_TEXT_CLI_UNAVAILABLE" };
  }

  async run(request: CodexTextRequest, signal: AbortSignal): Promise<CodexTextResponse> {
    const started = Date.now();
    const workspace = resolve(this.config.runtimeDir, "text", request.requestId);
    mkdirSync(resolve(workspace, "tmp"), { recursive: true, mode: 0o700 });
    const output = resolve(workspace, "final.txt");
    rmSync(output, { force: true });
    const args = [
      "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-rules",
      "--sandbox", "workspace-write", "-C", workspace, "-o", output, "-",
    ];
    const result = await runProcess(this.config.cliBin, args, {
      cwd: workspace,
      env: childEnvironment(this.config, workspace),
      stdin: instruction(request),
      timeoutMs: this.config.textTimeoutMs,
      signal,
    });
    if (result.code !== 0) throw new Error("CODEX_TEXT_EXIT_NONZERO");
    const text = readFileSync(output, "utf8").trim();
    if (!text || text.length > this.config.textMaxOutputChars) throw new Error("CODEX_TEXT_OUTPUT_INVALID");
    return { requestId: request.requestId, text, latencyMillis: Date.now() - started };
  }
}

export class FakeCodexTextRunner implements CodexTextRunner {
  async readiness(): Promise<{ ready: boolean }> { return { ready: true }; }
  async run(request: CodexTextRequest): Promise<CodexTextResponse> {
    const last = request.messages.at(-1)?.content.trim() || "질문";
    if (last.includes("[FAIL]")) throw new Error("CODEX_TEXT_FAKE_FAILURE");
    return { requestId: request.requestId, text: `Codex 테스트 응답: ${last.slice(0, 240)}`, latencyMillis: 1 };
  }
}
