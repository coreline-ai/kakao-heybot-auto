import { mkdirSync } from "node:fs";
import { loadCodexProxyConfig } from "./config/config.js";
import { CliCodexRunner, FakeCodexRunner } from "./cli/runner.js";
import { createCodexServer } from "./http/server.js";

const config = loadCodexProxyConfig();
mkdirSync(config.runtimeDir, { recursive: true, mode: 0o700 });
const runner =
  config.runnerMode === "fake" ? new FakeCodexRunner(config) : new CliCodexRunner(config);
const { server, shutdown: shutdownJobs } = createCodexServer(config, runner);

server.listen(config.port, config.host, () => {
  console.log(
    JSON.stringify({
      level: "info",
      event: "server_started",
      service: "proxy-codex",
      host: config.host,
      port: config.port,
      runner: config.runnerMode,
    }),
  );
});

function shutdown(): void {
  server.close(() => {
    void shutdownJobs().then(() => process.exit(0));
  });
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
