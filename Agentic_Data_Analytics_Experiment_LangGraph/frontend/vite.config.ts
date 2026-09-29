import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": process.env.ADA_BACKEND ?? "http://localhost:8000",
      "/ws": { target: (process.env.ADA_BACKEND ?? "http://localhost:8000").replace("http", "ws"), ws: true },
    },
  },
});
