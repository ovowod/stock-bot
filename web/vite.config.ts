import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // 문자열로만 적으면 Vite가 Host를 대상 주소로 바꿔, 서버의 출처 검사가 로그인을 거부한다.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
});
