import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies API calls to the FastAPI backend so the frontend can
// use relative "/api" URLs in development without CORS friction.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
