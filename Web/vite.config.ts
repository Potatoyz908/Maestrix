import { defineConfig } from "vite";

// Dev server proxies to the Swift core (port is dynamic: pass CORE_PORT).
const corePort = process.env.CORE_PORT ?? "0";

export default defineConfig({
  server: {
    proxy: {
      "/cli": `http://127.0.0.1:${corePort}`,
      "/api": `http://127.0.0.1:${corePort}`,
      "/ws": { target: `ws://127.0.0.1:${corePort}`, ws: true },
    },
  },
  test: { include: ["tests/**/*.test.ts"] },
});
