import { createServer, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import { authenticate, readSecret } from "../auth/auth.js";
import type { ManagerConfig } from "../config/config.js";
import { checkProxy } from "../health/health.js";
import {
  LaunchdLifecycleController,
  type LifecycleController,
} from "../lifecycle/launchd.js";
import { ProxyRegistry } from "../registry/registry.js";
import { forwardGatewayRequest } from "../router/proxy.js";

function json(response: ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": data.length,
    "cache-control": "no-store",
  });
  response.end(data);
}

function publicProxy(proxy: ReturnType<ProxyRegistry["all"]>[number]): Record<string, unknown> {
  return {
    id: proxy.id,
    enabled: proxy.enabled,
    exposure: proxy.exposure,
    routePrefix: proxy.routePrefix,
    dependencies: proxy.dependencies,
    launchdLabel: proxy.launchdLabel,
  };
}

export interface ManagerServerContext {
  server: Server;
  registry: ProxyRegistry;
}

export function createManagerServer(
  config: ManagerConfig,
  lifecycle: LifecycleController = new LaunchdLifecycleController(),
): ManagerServerContext {
  const routeSecret = readSecret(config.routeSecretFile);
  const adminSecret = readSecret(config.adminSecretFile);
  const registry = new ProxyRegistry(config.registryFile);
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return json(response, 200, { ok: true, service: "proxy-manager" });
      }
      if (request.method === "GET" && url.pathname === "/ready") {
        const enabled = registry.allEnabled();
        const results = await Promise.all(enabled.map((proxy) => checkProxy(proxy, config)));
        const statusById = new Map(results.map((item) => [item.id, item]));
        const proxies = results.map((result) => {
          const definition = registry.find(result.id)!;
          const dependencyReady = definition.dependencies.every(
            (dependency) => statusById.get(dependency)?.ready === true,
          );
          return { ...result, ready: result.ready && dependencyReady };
        });
        const ready = proxies.every((item) => item.ready);
        return json(response, ready ? 200 : 503, { ready, proxies });
      }

      if (url.pathname.startsWith("/manager/")) {
        if (!authenticate(request.headers.authorization, adminSecret)) {
          return json(response, 401, { error: { code: "ADMIN_UNAUTHORIZED" } });
        }
        if (request.method === "GET" && url.pathname === "/manager/v1/proxies") {
          return json(response, 200, { proxies: registry.all().map(publicProxy) });
        }
        const proxyMatch = url.pathname.match(/^\/manager\/v1\/proxies\/([a-z0-9-]+)$/);
        if (request.method === "GET" && proxyMatch) {
          const proxy = registry.find(proxyMatch[1]!);
          return proxy
            ? json(response, 200, publicProxy(proxy))
            : json(response, 404, { error: { code: "PROXY_NOT_FOUND" } });
        }
        const testAll = url.pathname === "/manager/v1/proxies/test-all/readiness";
        if (request.method === "POST" && testAll) {
          const results = await Promise.all(
            registry.allEnabled().map((proxy) =>
              checkProxy(proxy, config, proxy.readinessTestPath, "POST"),
            ),
          );
          return json(response, results.every((item) => item.ready) ? 200 : 503, {
            ready: results.every((item) => item.ready),
            proxies: results,
          });
        }
        const testMatch = url.pathname.match(
          /^\/manager\/v1\/proxies\/([a-z0-9-]+)\/test\/(readiness|canary)$/,
        );
        if (request.method === "POST" && testMatch) {
          const proxy = registry.find(testMatch[1]!);
          if (!proxy?.enabled) {
            return json(response, 404, { error: { code: "PROXY_NOT_FOUND" } });
          }
          const isCanary = testMatch[2] === "canary";
          if (isCanary && request.headers["x-confirm-cost"] !== "true") {
            return json(response, 412, {
              error: { code: "CANARY_CONFIRMATION_REQUIRED" },
            });
          }
          const result = await checkProxy(
            proxy,
            config,
            isCanary ? proxy.canaryTestPath : proxy.readinessTestPath,
            "POST",
            isCanary,
          );
          return json(response, result.ready ? 200 : 503, result);
        }
        const lifecycleMatch = url.pathname.match(
          /^\/manager\/v1\/proxies\/([a-z0-9-]+)\/(start|stop|restart)$/,
        );
        if (request.method === "POST" && lifecycleMatch) {
          if (!config.lifecycleEnabled) {
            return json(response, 403, { error: { code: "LIFECYCLE_DISABLED" } });
          }
          const proxy = registry.find(lifecycleMatch[1]!);
          if (!proxy?.enabled) {
            return json(response, 404, { error: { code: "PROXY_NOT_FOUND" } });
          }
          if (!proxy.launchdLabel) {
            return json(response, 409, { error: { code: "LAUNCHD_LABEL_UNAVAILABLE" } });
          }
          await lifecycle.run(
            proxy.launchdLabel,
            lifecycleMatch[2] as "start" | "stop" | "restart",
          );
          return json(response, 202, {
            proxyId: proxy.id,
            action: lifecycleMatch[2],
            accepted: true,
          });
        }
        return json(response, 404, { error: { code: "NOT_FOUND" } });
      }

      const proxy = registry.route(url.pathname);
      if (!proxy) return json(response, 404, { error: { code: "ROUTE_NOT_FOUND" } });
      if (!authenticate(request.headers.authorization, routeSecret)) {
        return json(response, 401, { error: { code: "ROUTE_UNAUTHORIZED" } });
      }
      try {
        await forwardGatewayRequest(request, response, proxy, config);
      } catch (error) {
        if (response.headersSent) {
          response.destroy(error as Error);
          return;
        }
        const code = (error as Error).message;
        if (code === "BODY_TOO_LARGE") {
          return json(response, 413, { error: { code } });
        }
        return json(response, 502, { error: { code: "PROXY_UNAVAILABLE" } });
      }
    } catch (error) {
      if (!response.headersSent) {
        return json(response, 500, {
          error: { code: "MANAGER_INTERNAL_ERROR" },
        });
      }
      response.destroy(error as Error);
    }
  });
  return { server, registry };
}
