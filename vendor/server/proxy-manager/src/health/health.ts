import type { ManagerConfig } from "../config/config.js";
import { readSecret } from "../auth/auth.js";
import type { ProxyDefinition } from "../registry/registry.js";

export interface ProxyHealth {
  id: string;
  ready: boolean;
  status: number;
  latencyMs: number;
  reason?: string;
}

export async function checkProxy(
  proxy: ProxyDefinition,
  config: ManagerConfig,
  path = proxy.readyPath,
  method = "GET",
  confirmCost = false,
): Promise<ProxyHealth> {
  const started = Date.now();
  try {
    const secret = readSecret(proxy.managerClientSecretFile);
    const response = await fetch(new URL(path, proxy.targetBaseUrl), {
      method,
      signal: AbortSignal.timeout(config.healthTimeoutMs),
      headers: {
        authorization: `Bearer ${secret}`,
        "x-heybot-service-id": "manager",
        ...(confirmCost ? { "x-confirm-cost": "true" } : {}),
      },
    });
    let reason: string | undefined;
    let bodyReady: boolean | undefined;
    if (response.headers.get("content-type")?.includes("application/json")) {
      const body = (await response.json()) as {
        ready?: boolean;
        reason?: string;
        error?: { code?: string };
      };
      bodyReady = body.ready;
      reason = body.reason ?? body.error?.code;
    }
    return {
      id: proxy.id,
      ready: response.ok && bodyReady !== false,
      status: response.status,
      latencyMs: Date.now() - started,
      reason,
    };
  } catch (error) {
    return {
      id: proxy.id,
      ready: false,
      status: 0,
      latencyMs: Date.now() - started,
      reason: (error as Error).name === "TimeoutError" ? "TIMEOUT" : "UNAVAILABLE",
    };
  }
}
