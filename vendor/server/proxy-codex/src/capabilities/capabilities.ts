import { readFileSync } from "node:fs";

export interface Capability {
  id: string;
  enabled: boolean;
  allowedCallers: string[];
  timeoutMs: number;
  maxArtifacts: number;
  acceptedMediaTypes: string[];
  maxBytesPerArtifact: number;
}

interface CapabilityDocument {
  schemaVersion: number;
  capabilities: Capability[];
}

export class CapabilityRegistry {
  readonly #capabilities = new Map<string, Capability>();

  constructor(path: string) {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as CapabilityDocument;
    if (parsed.schemaVersion !== 1 || !Array.isArray(parsed.capabilities)) {
      throw new Error("Unsupported capability registry");
    }
    for (const capability of parsed.capabilities) {
      if (
        !["image.generate", "conversation.respond.v1"].includes(capability.id) ||
        !Array.isArray(capability.allowedCallers) ||
        capability.allowedCallers.length === 0
      ) {
        throw new Error("Invalid capability entry");
      }
      if (this.#capabilities.has(capability.id)) throw new Error("Duplicate capability id");
      this.#capabilities.set(capability.id, capability);
    }
  }

  requireAllowed(id: string, caller: string): Capability {
    const capability = this.#capabilities.get(id);
    if (!capability?.enabled) throw new Error("CAPABILITY_DISABLED");
    if (!capability.allowedCallers.includes(caller)) throw new Error("CAPABILITY_FORBIDDEN");
    return capability;
  }

  list(): Capability[] {
    return [...this.#capabilities.values()].map((item) => ({ ...item }));
  }
}
