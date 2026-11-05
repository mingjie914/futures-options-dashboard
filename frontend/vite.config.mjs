import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    'import.meta.env.OBSERVE_PREVIEW': JSON.stringify(process.env.DASHBOARD_OBSERVE_PREVIEW === '1'),
    'import.meta.env.INTRADAY_PREVIEW': JSON.stringify(process.env.DASHBOARD_INTRADAY_PREVIEW === '1'),
  },
  envPrefix: ["VITE_", "NEXT_PUBLIC_"],
  build: {
    outDir: "dist",
    manifest: true,
    emptyOutDir: true,
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api/seats/v1": { target: "http://127.0.0.1:8796" },
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
  plugins: [react(), {
    name: 'navigation-code',
    transformIndexHtml: {order:'post', handler(html, context) {
      if (!context.bundle) return html;
      const names = {'seats/Dashboard.tsx':'seats','futures/App.jsx':'futures','Options.jsx':'options','Observe.jsx':'observe','Intraday.jsx':'intraday'};
      const files = Object.values(context.bundle).filter(item=>item.type==='chunk'&&item.facadeModuleId);
      const code = Object.fromEntries(Object.entries(names).flatMap(([name,id])=>{
        const chunk=files.find(item=>item.facadeModuleId.replaceAll('\\','/').endsWith('/src/'+name));
        return chunk?[[id,chunk.fileName]]:[];
      }));
      return [{tag:'script',attrs:{id:'navigation-code',type:'application/json'},children:JSON.stringify(code),injectTo:'head'}];
    }},
  }],
});
