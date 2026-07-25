import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type ProxyExposure = "gateway" | "internal";

export interface ProxyDefinition {
  id: string;
  enabled: boolean;
  exposure: ProxyExposure;
  routePrefix?: string;
  targetBaseUrl: string;
  healthPath: string;
  readyPath: string;
  readinessTestPath: string;
  canaryTestPath: string;
  canaryRequiresExplicitConfirmation: boolean;
  managerClientSecretFile: string;
  launchdLabel?: string;
  dependencies: string[];
}

interface RegistryDocument {
  schemaVersion: number;
  proxies: ProxyDefinition[];
}

function isLoopback(url: URL): boolean {
  return (
    url.protocol === "http:" &&
    ["127.0.0.1", "::1", "localhost"].includes(url.hostname)
  );
}

export class ProxyRegistry {
  readonly #definitions: ProxyDefinition[];

  constructor(path: string) {
    const document = JSON.parse(readFileSync(path, "utf8")) as RegistryDocument;
    if (document.schemaVersion !== 1 || !Array.isArray(document.proxies)) {
      throw new Error("Unsupported proxy registry");
    }
    const base = resolve(path, "..");
    const ids = new Set<string>();
    const prefixes: string[] = [];
    for (const proxy of document.proxies) {
      if (!/^[a-z][a-z0-9-]{0,31}$/.test(proxy.id) || ids.has(proxy.id)) {
        throw new Error("Duplicate or invalid proxy id");
      }
      ids.add(proxy.id);
      if (!["gateway", "internal"].includes(proxy.exposure)) {
        throw new Error("Invalid proxy exposure");
      }
      const target = new URL(proxy.targetBaseUrl);
      if (!isLoopback(target)) throw new Error("Proxy target must be loopback HTTP");
      if (proxy.exposure === "internal" && proxy.routePrefix !== undefined) {
        throw new Error("Internal proxy cannot have routePrefix");
      }
      if (proxy.exposure === "gateway") {
        if (!proxy.routePrefix?.match(/^\/v1\/[a-z][a-z0-9-]*$/)) {
          throw new Error("Gateway proxy requires a valid routePrefix");
        }
        for (const prefix of prefixes) {
          if (
            prefix === proxy.routePrefix ||
            prefix.startsWith(`${proxy.routePrefix}/`) ||
            proxy.routePrefix.startsWith(`${prefix}/`)
          ) {
            throw new Error("Gateway routePrefix conflict");
          }
        }
        prefixes.push(proxy.routePrefix);
      }
      if (!Array.isArray(proxy.dependencies)) throw new Error("Invalid dependencies");
      proxy.managerClientSecretFile = resolve(base, "..", proxy.managerClientSecretFile);
    }
    for (const proxy of document.proxies) {
      for (const dependency of proxy.dependencies) {
        if (!ids.has(dependency)) throw new Error("Unknown proxy dependency");
      }
    }
    this.#definitions = document.proxies;
  }

  allEnabled(): ProxyDefinition[] {
    return this.#definitions.filter((item) => item.enabled);
  }

  all(): ProxyDefinition[] {
    return this.#definitions.map((item) => ({ ...item, dependencies: [...item.dependencies] }));
  }

  find(id: string): ProxyDefinition | undefined {
    return this.#definitions.find((item) => item.id === id);
  }

  route(pathname: string): ProxyDefinition | undefined {
    return this.#definitions.find(
      (item) =>
        item.enabled &&
        item.exposure === "gateway" &&
        item.routePrefix !== undefined &&
        (pathname === item.routePrefix || pathname.startsWith(`${item.routePrefix}/`)),
    );
  }
}
