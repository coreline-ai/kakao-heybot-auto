import assert from "node:assert/strict";
import { test } from "node:test";
import { loadDrawProxyConfig } from "../../src/config/config.js";
import { validatePng } from "../../src/images-qc.js";
import { sourceInstruction } from "../../src/orchestration/processor.js";

test("draw proxy accepts only loopback listeners and dependencies", () => {
  assert.throws(
    () => loadDrawProxyConfig({ DRAW_PROXY_HOST: "0.0.0.0" }, "/tmp/draw-test"),
    /DRAW_PROXY_HOST must be loopback/,
  );
  assert.throws(
    () => loadDrawProxyConfig({ DRAW_PROXY_BRUSH_BASE_URL: "https://example.com" }, "/tmp/draw-test"),
    /DRAW_PROXY_BRUSH_BASE_URL must be loopback HTTP/,
  );
  const config = loadDrawProxyConfig({ DRAW_PROXY_PORT: "4359" }, "/tmp/draw-test");
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.brushBaseUrl, "http://127.0.0.1:4360");
  assert.equal(config.queueConcurrency, 1);
});

test("draw proxy rejects malformed generated source images before rendering", () => {
  assert.throws(
    () => validatePng(Buffer.from("not-a-png"), 1024),
    /SOURCE_PNG_INVALID/,
  );
});

test("draw source instruction requests a detailed painted illustration, not flat pastel art", () => {
  const instruction=sourceInstruction("하얀 종이 위 핑크 로봇 캐릭터가 손을 흔드는 모습");
  assert.match(instruction,/high-detail vertical 9:16 PNG illustration/);
  assert.match(instruction,/tactile hand-painted pigment texture/);
  assert.match(instruction,/not flat vector art or a colouring-book page/);
  assert.doesNotMatch(instruction,/broad separated flat pastel/);
});
