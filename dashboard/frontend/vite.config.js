import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/static/',
  plugins: [react(), tailwindcss()],
  build: {
    minify: false,  // Disable minification to rule out esbuild bundling bugs
    rollupOptions: {
      output: {
        inlineDynamicImports: false
      }
    }
  },
  server: {
    port: 3000,
    host: true,
    proxy: {
      '/api': { target: 'http://localhost:8081', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8081', ws: true }
    }
  }
})
