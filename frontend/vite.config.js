import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During `npm run dev`, /api is proxied to the Python server on :8000.
// `npm run build` emits into ../backend's expected static directory; the
// Python server serves those files in production.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
