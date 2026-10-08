import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        // Don't rewrite - backend expects /api/v1/... paths
      },
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: '',
      },
    },
  },
  build: {
    // Separate vendor chunks for better caching
    rollupOptions: {
      output: {
        manualChunks: {
          // Element Plus is ~500KB gzipped — isolate so code changes
          // don't bust the browser cache for the entire UI library
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          // Vue core — rarely changes
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
          // Chart library (if present)
          'chart-vendor': ['echarts', 'vue-echarts'],
        },
      },
    },
    // Warn when chunks exceed 600KB
    chunkSizeWarningThreshold: 600,
    // Ensure CSS is code-split per component
    cssCodeSplit: true,
  },
})
