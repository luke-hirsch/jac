/**
 * The connector's PDF preview (an MCP App), built into one file by vite.connector.config.ts and
 * served by the connector as `ui://jac/preview` (backend/jac/mcp.py).
 */
import { App } from "@modelcontextprotocol/ext-apps";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { connectHost } from "./host";
import { PreviewApp } from "./preview-app";
import "./preview.css";

const app = new App(
  { name: "jac-preview", version: "1.0.0" },
  { availableDisplayModes: ["inline", "fullscreen"] },
);
const host = connectHost(app);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PreviewApp app={app} host={host} />
  </StrictMode>,
);
