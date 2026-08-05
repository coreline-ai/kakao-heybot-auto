import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import type { AddressInfo } from "node:net";
import { createConversationServer } from "../../src/http/server.js";
import type { ConversationProxyConfig } from "../../src/config/config.js";

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolvePromise) => server.listen(0, "127.0.0.1", resolvePromise));
  const address = server.address() as AddressInfo;
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: Server): Promise<void> {
  await new Promise<void>((resolvePromise, reject) => server.close((error) => error ? reject(error) : resolvePromise()));
}

test("routes only authenticated conversation requests to the selected provider", async (t) => {
  const root = mkdtempSync(resolve(tmpdir(), "conversation-proxy-test-"));
  const managerSecret = "m".repeat(48);
  const codexSecret = "c".repeat(48);
  const grokSecret = "g".repeat(48);
  const managerSecretFile = resolve(root, "manager.secret");
  const codexSecretFile = resolve(root, "codex.secret");
  const grokSecretFile = resolve(root, "grok.secret");
  writeFileSync(managerSecretFile, managerSecret);
  writeFileSync(codexSecretFile, codexSecret);
  writeFileSync(grokSecretFile, grokSecret);
  const providers: Server[] = [];
  const provider = (engine: string, secret: string) => {
    const server = createServer(async (request, response) => {
      assert.equal(request.headers.authorization, `Bearer ${secret}`);
      assert.equal(request.headers["x-heybot-service-id"], "conversation");
      let body = "";
      for await (const chunk of request) body += Buffer.from(chunk).toString("utf8");
      const parsed = JSON.parse(body) as { capability: string; input: { messages: unknown[] } };
      assert.equal(parsed.capability, "conversation.respond.v1");
      assert.equal(parsed.input.messages.length, 2);
      const output = Buffer.from(JSON.stringify({ requestId: "r1", engine, text: `${engine} reply`, latencyMillis: 3 }));
      response.writeHead(200, { "content-type": "application/json", "content-length": output.length });
      response.end(output);
    });
    providers.push(server);
    return listen(server);
  };
  const [codexBaseUrl, grokBaseUrl] = await Promise.all([provider("codex", codexSecret), provider("grok", grokSecret)]);
  const context = createConversationServer({
    host: "127.0.0.1", port: 0, managerSecretFile,
    codexBaseUrl, codexSecretFile, grokBaseUrl, grokSecretFile,
    requestMaxBytes: 32_768, timeoutMs: 5_000,
  } satisfies ConversationProxyConfig);
  const baseUrl = await listen(context.server);
  t.after(async () => { await close(context.server); await Promise.all(providers.map(close)); });

  const body = JSON.stringify({ requestId: "r1", engine: "grok", kind: "WAKE_WORD", promptVersion: "heybot-persona-v2", messages: [
    { role: "system", content: "same" }, { role: "user", content: "안녕" },
  ] });
  const unauthorized = await fetch(`${baseUrl}/v1/conversation/respond`, { method: "POST", body, headers: { "content-type": "application/json" } });
  assert.equal(unauthorized.status, 401);
  const response = await fetch(`${baseUrl}/v1/conversation/respond`, { method: "POST", body, headers: { authorization: `Bearer ${managerSecret}`, "content-type": "application/json" } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { requestId: "r1", engine: "grok", text: "grok reply", latencyMillis: 3 });

  const audioBody = JSON.stringify({
    requestId: "audio-summary-r1", engine: "grok", kind: "AUDIO_SUMMARY",
    promptVersion: "heybot-persona-v2", messages: [
      { role: "system", content: "음성 요약" },
      { role: "user", content: "S0001 ".repeat(800) },
    ],
  });
  const audioResponse = await fetch(`${baseUrl}/v1/conversation/audio-summary`, {
    method: "POST", body: audioBody,
    headers: { authorization: `Bearer ${managerSecret}`, "content-type": "application/json" },
  });
  assert.equal(audioResponse.status, 200);
  const mismatchedRoute = await fetch(`${baseUrl}/v1/conversation/respond`, {
    method: "POST", body: audioBody,
    headers: { authorization: `Bearer ${managerSecret}`, "content-type": "application/json" },
  });
  assert.equal(mismatchedRoute.status, 400);

  const invalidVersion = await fetch(`${baseUrl}/v1/conversation/respond`, {
    method: "POST",
    body: JSON.stringify({
      requestId: "r2", engine: "grok", kind: "WAKE_WORD", promptVersion: "v2<script>",
      messages: [{ role: "system", content: "same" }, { role: "user", content: "안녕" }],
    }),
    headers: { authorization: `Bearer ${managerSecret}`, "content-type": "application/json" },
  });
  assert.equal(invalidVersion.status, 400);
});
