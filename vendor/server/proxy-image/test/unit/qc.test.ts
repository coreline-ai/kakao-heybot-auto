import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { PNG } from "pngjs";
import { validatePng } from "../../src/images/qc.js";

function image(mode: "gradient" | "black" | "white"): Buffer {
  const png = new PNG({ width: 256, height: 256 });
  for (let y = 0; y < png.height; y += 1) {
    for (let x = 0; x < png.width; x += 1) {
      const offset = (y * png.width + x) * 4;
      const value = mode === "gradient" ? (x + y) % 256 : mode === "black" ? 0 : 255;
      png.data[offset] = value;
      png.data[offset + 1] = mode === "gradient" ? x : value;
      png.data[offset + 2] = mode === "gradient" ? y : value;
      png.data[offset + 3] = 255;
    }
  }
  return PNG.sync.write(png);
}

test("accepts a real decoded PNG with varied pixels", () => {
  const data = image("gradient");
  const result = validatePng(data, 2_000_000);
  assert.equal(result.width, 256);
  assert.equal(result.sha256, createHash("sha256").update(data).digest("hex"));
  assert.ok(result.contrast > 4);
  assert.ok(result.entropy > 1);
});

test("rejects signature, black, white and low-entropy images", () => {
  assert.throws(() => validatePng(Buffer.from("not png"), 2_000_000));
  assert.throws(() => validatePng(image("black"), 2_000_000), /IMAGE_TOO_DARK/);
  assert.throws(() => validatePng(image("white"), 2_000_000), /IMAGE_TOO_BRIGHT/);
});
