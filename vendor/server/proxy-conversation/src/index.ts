import { loadConversationProxyConfig } from "./config/config.js";
import { createConversationServer } from "./http/server.js";

const config = loadConversationProxyConfig();
const { server } = createConversationServer(config);
server.listen(config.port, config.host, () => console.log(JSON.stringify({ level: "info", event: "server_started", service: "proxy-conversation", host: config.host, port: config.port })));
function stop(): void { server.close(() => process.exit(0)); setTimeout(() => process.exit(1), 10_000).unref(); }
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
