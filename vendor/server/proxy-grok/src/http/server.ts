import { createReadStream, statSync } from 'node:fs';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { URL } from 'node:url';
import { authenticateBearer, readSecret } from '../auth/auth.js';
import type { GrokProxyConfig } from '../config/config.js';
import { publicJob } from '../jobs/types.js';
import { GrokJobStore } from '../storage/store.js';
import { GrokCliRunner } from '../cli/runner.js';
import { GrokJobProcessor } from '../queue/processor.js';
import { CliGrokTextRunner, FakeGrokTextRunner, type GrokTextRunner } from '../cli/text-runner.js';
import type { GrokTextRequest } from '../conversation/types.js';
import { BoundedConversationQueue } from '../conversation/queue.js';

function json(res: ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': data.length, 'cache-control': 'no-store' });
  res.end(data);
}
async function readBody(req: IncomingMessage, max: number): Promise<unknown> {
  const chunks: Buffer[] = []; let bytes = 0;
  for await (const chunk of req) { const value = Buffer.from(chunk); bytes += value.length; if (bytes > max) throw new Error('BODY_TOO_LARGE'); chunks.push(value); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new Error('INVALID_JSON'); }
}
function createInput(value: unknown, config: GrokProxyConfig): { requestId: string; prompt: string } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
  const body = value as Record<string, unknown>; const input = body.input as Record<string, unknown> | undefined;
  if (Object.keys(body).some((key) => !['requestId', 'capability', 'input', 'artifactContract'].includes(key)) ||
      typeof body.requestId !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId) ||
      body.capability !== 'video.generate' || !input || typeof input.prompt !== 'string' ||
      input.prompt.trim().length < 1 || input.prompt.length > config.promptMaxChars) throw new Error('INVALID_REQUEST');
  return { requestId: body.requestId, prompt: input.prompt.trim() };
}
export interface GrokServerContext { server: Server; shutdown(): Promise<void>; }
export function createGrokServer(config: GrokProxyConfig, textRunner: GrokTextRunner = new CliGrokTextRunner(config)): GrokServerContext {
  const secret = readSecret(config.videoSecretFile); const conversationSecret = readSecret(config.conversationSecretFile); const store = new GrokJobStore(config.databaseFile);
  const runner = new GrokCliRunner(config); const processor = new GrokJobProcessor(store, runner, config); processor.start();
  const textQueue = new BoundedConversationQueue(config.textQueueConcurrency, config.textQueueMaxPending);
  const create = (requestId: string, prompt: string): { body: Record<string, unknown>; existing: boolean } | undefined => {
    const existing = store.find(requestId); if (existing) return { body: publicJob(existing), existing: true };
    if (store.countPending() >= config.queueMaxPending) return undefined;
    const job = store.create(requestId, prompt); processor.kick(); return { body: publicJob(job), existing: false };
  };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    try {
      if (request.method === 'GET' && url.pathname === '/health') return json(response, 200, { ok: true, service: 'proxy-grok' });
      if (request.method === 'GET' && url.pathname === '/ready') return json(response, 200, { ready: true, queue: processor.snapshot(), text: textQueue.snapshot() });
      const serviceId = typeof request.headers['x-heybot-service-id'] === 'string' ? request.headers['x-heybot-service-id'] : '';
      const authenticatedVideo = serviceId === config.videoServiceId && authenticateBearer(request.headers.authorization, secret);
      const authenticatedConversation = serviceId === config.conversationServiceId && authenticateBearer(request.headers.authorization, conversationSecret);
      if (!authenticatedVideo && !authenticatedConversation) return json(response, 401, { error: { code: 'UNAUTHORIZED' } });
      if (request.method === 'POST' && url.pathname === '/internal/v1/self-test/readiness') return json(response, 200, { ready: true });
      if (request.method === 'POST' && url.pathname === '/internal/v1/self-test/capabilities/video.generate') {
        if (request.headers['x-confirm-cost'] !== 'true') return json(response, 412, { error: { code: 'CANARY_CONFIRMATION_REQUIRED' } });
        const job = create(`canary-${Date.now()}`, '검증용: 밝은 배경에서 웃으며 손을 흔드는 작은 분홍 로봇, 6초 영상');
        return job ? json(response, 202, job.body) : json(response, 429, { error: { code: 'GROK_QUEUE_FULL' } });
      }
      if (request.method === 'POST' && url.pathname === '/internal/v1/grok/jobs') {
        const input = createInput(await readBody(request, config.requestMaxBytes), config); const job = create(input.requestId, input.prompt);
        return job ? json(response, job.existing ? 200 : 202, job.body) : json(response, 429, { error: { code: 'GROK_QUEUE_FULL' } });
      }
      if (request.method === 'POST' && url.pathname === '/internal/v1/grok/conversation') {
        if (!authenticatedConversation) return json(response, 403, { error: { code: 'CAPABILITY_FORBIDDEN' } });
        const value = await readBody(request, config.requestMaxBytes);
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
        const body = value as Record<string, unknown>;
        if (Object.keys(body).some((key) => !['requestId', 'capability', 'input'].includes(key)) ||
          body.capability !== 'conversation.respond.v1' || typeof body.requestId !== 'string' ||
          !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId)) throw new Error('INVALID_REQUEST');
        const input = body.input;
        if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('INVALID_INPUT');
        const rawMessages = (input as Record<string, unknown>).messages;
        if (!Array.isArray(rawMessages) || rawMessages.length < 1 || rawMessages.length > 32) throw new Error('INVALID_MESSAGES');
        const messages = rawMessages.map((item) => {
          if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('INVALID_MESSAGE');
          const message = item as Record<string, unknown>;
          if (Object.keys(message).some((key) => !['role', 'content'].includes(key)) ||
            !['system', 'user', 'assistant'].includes(String(message.role)) ||
            typeof message.content !== 'string' || message.content.trim().length < 1 || message.content.length > 4_000) throw new Error('INVALID_MESSAGE');
          return { role: message.role as GrokTextRequest['messages'][number]['role'], content: message.content.trim() };
        });
        const result = await textQueue.run(() => textRunner.run({ requestId: body.requestId as string, messages }, AbortSignal.timeout(config.textTimeoutMs)));
        return json(response, 200, { requestId: result.requestId, engine: 'grok', text: result.text, latencyMillis: result.latencyMillis });
      }
      const file = url.pathname.match(/^\/internal\/v1\/grok\/jobs\/([0-9a-f-]+)\/artifact$/);
      if (file && request.method === 'GET') {
        const job = store.get(file[1]!); if (!job || job.status !== 'succeeded' || !job.artifactPath) return json(response, 404, { error: { code: 'GROK_ARTIFACT_NOT_FOUND' } });
        response.writeHead(200, { 'content-type': 'video/mp4', 'content-length': statSync(job.artifactPath).size, 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }); createReadStream(job.artifactPath).pipe(response); return;
      }
      const jobMatch = url.pathname.match(/^\/internal\/v1\/grok\/jobs\/([0-9a-f-]+)$/);
      if (jobMatch) {
        const job = store.get(jobMatch[1]!); if (!job) return json(response, 404, { error: { code: 'GROK_JOB_NOT_FOUND' } });
        if (request.method === 'GET') return json(response, 200, publicJob(job));
        if (request.method === 'DELETE') { const changed = await processor.cancel(job.id); return json(response, changed ? 202 : 409, publicJob(store.get(job.id)!)); }
      }
      return json(response, 404, { error: { code: 'NOT_FOUND' } });
    } catch (error) { const code = (error instanceof Error ? error.message : 'INVALID_REQUEST').slice(0, 64); return json(response, code === 'BODY_TOO_LARGE' ? 413 : 400, { error: { code } }); }
  });
  let closing: Promise<void> | undefined;
  return { server, shutdown: () => (closing ??= (textQueue.close(), processor.close().then(() => store.close()))) };
}
