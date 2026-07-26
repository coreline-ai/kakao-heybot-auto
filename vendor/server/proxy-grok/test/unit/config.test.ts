import assert from 'node:assert/strict';
import test from 'node:test';
import { loadGrokProxyConfig } from '../../src/config/config.js';

test('grok proxy requires an absolute CLI command and keeps loopback defaults', () => {
  assert.throws(() => loadGrokProxyConfig({}, '/tmp/grok-test'), /GROK_PROXY_CLI_COMMAND/);
  const config = loadGrokProxyConfig({ GROK_PROXY_CLI_COMMAND: '/bin/echo' }, '/tmp/grok-test');
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 4358);
  assert.equal(config.queueMaxPending, 2);
  assert.equal(config.cliCommand, '/bin/echo');
});
