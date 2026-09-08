import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` is same-origin (`/api/...`). Experience (8004) is the
    // BFF that sets the session cookie and relays to Identity/Knowledge.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8004",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../app/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('@monaco-editor') || id.includes('monaco-editor')) {
              return 'vendor-monaco';
            }
            if (id.includes('echarts')) {
              return 'vendor-echarts';
            }
            if (id.includes('mapbox-gl') || id.includes('react-map-gl')) {
              return 'vendor-mapbox';
            }
            if (id.includes('reactflow')) {
              return 'vendor-reactflow';
            }
            if (id.includes('@blueprintjs')) {
              return 'vendor-blueprint';
            }
          }
        },
      },
    },
  },
})
