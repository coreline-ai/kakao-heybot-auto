import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export type LifecycleAction = "start" | "stop" | "restart";

export interface LifecycleController {
  run(label: string, action: LifecycleAction): Promise<void>;
}

export class LaunchdLifecycleController implements LifecycleController {
  constructor(
    private readonly domain = `gui/${process.getuid?.() ?? 0}`,
    private readonly timeoutMs = 10_000,
  ) {}

  async run(label: string, action: LifecycleAction): Promise<void> {
    if (!/^ai\.coreline\.heybot\.proxy-[a-z0-9-]+$/.test(label)) {
      throw new Error("INVALID_LAUNCHD_LABEL");
    }
    const target = `${this.domain}/${label}`;
    const args =
      action === "stop"
        ? ["kill", "TERM", target]
        : ["kickstart", "-k", target];
    await execFileAsync("/bin/launchctl", args, {
      timeout: this.timeoutMs,
      maxBuffer: 16_384,
    });
  }
}
