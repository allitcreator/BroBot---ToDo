/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Собираем локально и выкладываем статику: на vps4 одно ядро и VPN-нода.
    outDir: "dist",
    sourcemap: false,
    target: "es2020",
  },
  test: {
    environment: "jsdom",
    globals: true,
    clearMocks: true,
    setupFiles: "./src/test-setup.ts",
  },
});
