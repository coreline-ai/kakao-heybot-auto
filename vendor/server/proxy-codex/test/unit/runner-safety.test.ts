import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { PNG } from "pngjs";
import { CliCodexRunner } from "../../src/cli/runner.js";
import { loadCodexProxyConfig } from "../../src/config/config.js";
import type { CodexJob } from "../../src/jobs/types.js";

function job(id: string): CodexJob {
  const now = Date.now();
  return {
    id,
    caller: "image",
    requestId: id,
    capability: "image.generate",
    prompt: "안전 테스트",
    status: "running",
    createdAt: now,
    updatedAt: now,
  };
}

function executable(root: string, name: string, body: string): string {
  const path = resolve(root, name);
  writeFileSync(path, `#!/bin/sh\n${body}\n`);
  chmodSync(path, 0o700);
  return path;
}

test("rejects a symlink artifact even when the CLI exits successfully", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "codex-symlink-test-"));
  const outside = resolve(root, "outside.png");
  writeFileSync(outside, "not relevant");
  const cli = executable(root, "fake-codex", `ln -s '${outside}' artifact.png`);
  const config = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_CLI_BIN: cli,
    },
    root,
  );
  await assert.rejects(
    new CliCodexRunner(config).run(job("11111111-1111-4111-8111-111111111111"), new AbortController().signal),
    /ARTIFACT_PATH_INVALID/,
  );
});

test("timeout terminates the detached CLI process group including its child", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "codex-timeout-test-"));
  const cli = executable(
    root,
    "hanging-codex",
    "(sleep 30) &\necho $! > child.pid\nsleep 30",
  );
  const base = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_CLI_BIN: cli,
    },
    root,
  );
  // Allow the shell enough time to create its child PID before the timeout.
  const config = { ...base, jobTimeoutMs: 1_000 };
  const target = job("22222222-2222-4222-8222-222222222222");
  await assert.rejects(
    new CliCodexRunner(config).run(target, new AbortController().signal),
    /CODEX_TIMEOUT/,
  );
  const pidFile = resolve(config.runtimeDir, "jobs", target.id, "workspace", "child.pid");
  const childPid = Number(readFileSync(pidFile, "utf8").trim());
  const deadline = Date.now() + 3_000;
  while (Date.now() < deadline) {
    try {
      process.kill(childPid, 0);
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 25));
    } catch {
      return;
    }
  }
  assert.fail(`orphan process still exists: ${childPid}`);
});

test("keeps an untrusted prompt in stdin and strips unrelated child environment", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "codex-prompt-boundary-test-"));
  const sourcePng = resolve(root, "fixture.png");
  const png = new PNG({ width: 64, height: 64 });
  png.data.fill(255);
  writeFileSync(sourcePng, PNG.sync.write(png));
  const cli = executable(
    root,
    "capture-codex",
    [
      "printf '%s\\n' \"$@\" > argv.txt",
      "cat > stdin.txt",
      "pwd > cwd.txt",
      "env > env.txt",
      `cp '${sourcePng}' artifact.png`,
    ].join("\n"),
  );
  const config = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_CLI_BIN: cli,
    },
    root,
  );
  const target = job("33333333-3333-4333-8333-333333333333");
  target.prompt = "\"; touch PROMPT_ESCAPED; #\n$HEYBOT_TEST_SECRET";
  const previousSecret = process.env.HEYBOT_TEST_SECRET;
  process.env.HEYBOT_TEST_SECRET = "must-not-reach-child";
  try {
    await new CliCodexRunner(config).run(target, new AbortController().signal);
  } finally {
    if (previousSecret === undefined) delete process.env.HEYBOT_TEST_SECRET;
    else process.env.HEYBOT_TEST_SECRET = previousSecret;
  }

  const workspace = resolve(config.runtimeDir, "jobs", target.id, "workspace");
  assert.equal(existsSync(resolve(workspace, "PROMPT_ESCAPED")), false);
  assert.doesNotMatch(readFileSync(resolve(workspace, "argv.txt"), "utf8"), /PROMPT_ESCAPED/);
  assert.ok(
    readFileSync(resolve(workspace, "stdin.txt"), "utf8").includes(
      'VISUAL_DESCRIPTION_JSON="\\"; touch PROMPT_ESCAPED; #\\n$HEYBOT_TEST_SECRET"',
    ),
  );
  assert.equal(
    realpathSync(readFileSync(resolve(workspace, "cwd.txt"), "utf8").trim()),
    realpathSync(workspace),
  );
  assert.doesNotMatch(
    readFileSync(resolve(workspace, "env.txt"), "utf8"),
    /HEYBOT_TEST_SECRET|must-not-reach-child/,
  );
});
