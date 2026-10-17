import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/zrender/")) return "chart-renderer";
          if (id.includes("/node_modules/echarts/")) return "chart-core";
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.PLATESG_API_URL || "http://127.0.0.1:8765",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
