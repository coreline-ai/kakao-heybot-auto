import assert from 'node:assert/strict';
import test from 'node:test';
import { validateMp4 } from '../../src/videos/qc.js';

test('rejects non MP4 data before invoking ffprobe', async () => {
  await assert.rejects(
    validateMp4('/does/not/matter', Buffer.from('not-a-video'), 1024, '/not-used'),
    /VIDEO_SIZE_INVALID|VIDEO_SIGNATURE_INVALID/,
  );
});
