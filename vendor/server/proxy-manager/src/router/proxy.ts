import { request as httpRequest, type IncomingHttpHeaders } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { ManagerConfig } from "../config/config.js";
import type { ProxyDefinition } from "../registry/registry.js";
import { readSecret } from "../auth/auth.js";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "authorization",
  "host",
]);

async function readLimitedBody(
  request: IncomingMessage,
  maximumBytes: number,
): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > maximumBytes) throw new Error("BODY_TOO_LARGE");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

function forwardedHeaders(
  input: IncomingHttpHeaders,
  secret: string,
  bodyLength: number,
): Record<string, string> {
  const result: Record<string, string> = {
    authorization: `Bearer ${secret}`,
    "content-length": String(bodyLength),
    "x-heybot-service-id": "manager",
  };
  for (const [name, value] of Object.entries(input)) {
    if (HOP_BY_HOP.has(name) || value === undefined || name === "content-length") continue;
    result[name] = Array.isArray(value) ? value.join(", ") : value;
  }
  return result;
}

export async function forwardGatewayRequest(
  request: IncomingMessage,
  response: ServerResponse,
  proxy: ProxyDefinition,
  config: ManagerConfig,
): Promise<void> {
  const body = await readLimitedBody(request, config.requestMaxBytes);
  const target = new URL(request.url ?? "/", proxy.targetBaseUrl);
  const secret = readSecret(proxy.managerClientSecretFile);
  await new Promise<void>((resolvePromise, reject) => {
    const upstream = httpRequest(
      target,
      {
        method: request.method,
        headers: forwardedHeaders(request.headers, secret, body.length),
        timeout: config.streamIdleTimeoutMs,
      },
      (upstreamResponse) => {
        const headers: Record<string, string | string[]> = {};
        for (const [name, value] of Object.entries(upstreamResponse.headers)) {
          if (!HOP_BY_HOP.has(name) && value !== undefined) headers[name] = value;
        }
        response.writeHead(upstreamResponse.statusCode ?? 502, headers);
        upstreamResponse.pipe(response);
        upstreamResponse.once("end", resolvePromise);
        upstreamResponse.once("error", reject);
      },
    );
    // `ClientRequest#setTimeout` is an inactivity timeout, not a connect-only
    // timeout. A synchronous conversation response may legitimately take
    // longer than the TCP connect budget, so keep the two budgets separate.
    const connectTimer = setTimeout(() => {
      upstream.destroy(new Error("UPSTREAM_CONNECT_TIMEOUT"));
    }, config.connectTimeoutMs);
    upstream.once("socket", (socket) => {
      const clearConnectTimer = (): void => clearTimeout(connectTimer);
      socket.once("connect", clearConnectTimer);
      if (!socket.connecting) clearConnectTimer();
    });
    upstream.setTimeout(config.streamIdleTimeoutMs, () => {
      upstream.destroy(new Error("UPSTREAM_STREAM_IDLE_TIMEOUT"));
    });
    upstream.once("response", () => clearTimeout(connectTimer));
    upstream.once("error", reject);
    upstream.end(body);
  });
}
