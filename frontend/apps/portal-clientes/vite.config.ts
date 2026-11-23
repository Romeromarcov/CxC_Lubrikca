import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/portal/",
  publicDir: "public",
  build: {
    outDir: "../../../src/cxc/web/static/portal",
    emptyOutDir: true,
  },
});
