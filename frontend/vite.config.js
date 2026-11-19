import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8080',
      '/copilot-api': { target: 'http://localhost:8093', rewrite: p => p.replace(/^\/copilot-api/, '') },
      '/kbase-api': { target: 'http://localhost:8094', rewrite: p => p.replace(/^\/kbase-api/, '') },
      '/workflow-api': { target: 'http://localhost:8095', rewrite: p => p.replace(/^\/workflow-api/, '') }
    }
  }
})
