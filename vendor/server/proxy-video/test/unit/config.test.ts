import assert from 'node:assert/strict';
import test from 'node:test';
import { loadVideoProxyConfig } from '../../src/config/config.js';

test('video proxy uses the internal Grok loopback route and one pending job per room', () => {
  const config = loadVideoProxyConfig({ VIDEO_PROXY_FFPROBE_COMMAND: '/bin/true' }, '/tmp/video-test');
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 4357);
  assert.equal(config.grokBaseUrl, 'http://127.0.0.1:4358');
  assert.equal(config.queueMaxPendingPerRoom, 1);
  assert.equal(config.publicPublishEnabled, false);
});
