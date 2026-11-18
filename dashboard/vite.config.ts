import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.ASC_DEV_API ?? "http://127.0.0.1:8780";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": api, "/healthz": api },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 800 },
});
