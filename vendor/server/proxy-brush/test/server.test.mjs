import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { authenticate, loadConfig, validateCreate } from "../src/index.mjs";

test("rejects a non-loopback listener", () => {
  assert.throws(
    () => loadConfig({ PEN_BRUSH_PROXY_HOST: "0.0.0.0" }),
    /PEN_BRUSH_PROXY_HOST must be loopback/
  );
});

test("internal rendering requires the draw identity and validates source PNG before enqueue", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "proxy-brush-test-"));
  const secret = join(root, "draw.secret");
  await writeFile(secret, "test-secret-which-is-not-used-in-production\n", { mode: 0o600 });
  const config = loadConfig({
    PEN_BRUSH_PROXY_ENABLED: "false",
    PEN_BRUSH_PROXY_RUNTIME_DIR: join(root, "runtime"),
    PEN_BRUSH_PROXY_DRAW_SECRET_FILE: secret,
  }, root);
  t.after(async () => {
    await rm(root, { recursive: true, force: true });
  });

  assert.equal(authenticate({ headers: {} }, config), false);
  assert.equal(authenticate({ headers: {
    authorization: "Bearer test-secret-which-is-not-used-in-production",
    "x-heybot-service-id": "draw",
  } }, config), true);
  assert.throws(
    () => validateCreate({ requestId: "smoke-1", sourcePngBase64: "AAAAAAA=", seed: 1 }),
    /SOURCE_PNG_INVALID/
  );
});
