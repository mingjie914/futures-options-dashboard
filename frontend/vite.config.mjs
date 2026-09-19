import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api/futures/v1": {
        target: "http://127.0.0.1:8795",
        rewrite: (path) => path.replace(/^\/api\/futures\/v1/, "/api"),
      },
      "/api/options/v1": {
        target: "http://127.0.0.1:8794",
      },
    },
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  preview: {
    host: "127.0.0.1",
  },
  plugins: [react()],
});
