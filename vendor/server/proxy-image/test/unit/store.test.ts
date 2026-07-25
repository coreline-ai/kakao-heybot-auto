import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { ImageJobStore } from "../../src/storage/store.js";

test("preserves 18-digit IDs and recovers running work as FIFO queued", () => {
  const root = mkdtempSync(resolve(tmpdir(), "image-store-test-"));
  const path = resolve(root, "jobs.sqlite3");
  let store = new ImageJobStore(path);
  const first = store.create({
    requestId: "r1",
    chatId: "18480337854645134",
    userId: "7216943976749157453",
    logId: "900719925474099312",
    prompt: "첫 번째",
  });
  const second = store.create({
    requestId: "r2",
    chatId: "18480337854645134",
    userId: "7216943976749157453",
    logId: "900719925474099313",
    prompt: "두 번째",
  });
  store.create({
    requestId: "r3",
    chatId: "18226456888539938",
    userId: "7216943976749157453",
    logId: "900719925474099314",
    prompt: "세 번째",
  });
  assert.equal(store.get(first.id)?.chatId, "18480337854645134");
  assert.equal(store.countPending(), 3);
  assert.equal(store.countRoomPending("18480337854645134"), 2);
  assert.equal(store.markRunning(first.id), true);
  assert.equal(store.nextQueued()?.id, second.id);
  store.close();

  store = new ImageJobStore(path);
  assert.equal(store.get(first.id)?.status, "queued");
  assert.equal(store.nextQueued()?.requestId, "r1");
  store.close();
});
