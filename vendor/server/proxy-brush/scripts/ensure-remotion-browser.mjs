import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const browserRoot = path.join(root, "runtime", "remotion-browser");
const rendererPath = path.join(root, "engine", "node_modules", "@remotion", "renderer", "dist", "index.js");
fs.mkdirSync(browserRoot, { recursive: true, mode: 0o700 });
const packagePath = path.join(browserRoot, "package.json");
if (!fs.existsSync(packagePath)) fs.writeFileSync(packagePath, JSON.stringify({ private: true }) + "\n", { mode: 0o600 });
process.chdir(browserRoot);
const renderer = await import(pathToFileURL(rendererPath).href);
const result = await renderer.ensureBrowser({ logLevel: "info", chromeMode: "headless-shell" });
if (!result.path || !fs.existsSync(result.path)) throw new Error("REMOTION_BROWSER_UNAVAILABLE");
fs.writeFileSync(path.join(browserRoot, "browser.json"), `${JSON.stringify({ schemaVersion: 1, executable: path.resolve(result.path), mode: "headless-shell" })}\n`, { mode: 0o600 });
