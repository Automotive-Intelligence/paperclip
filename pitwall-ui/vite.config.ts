import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/pitwall-static/',
  build: {
    outDir: '../static/pitwall-react',
    emptyOutDir: true,
  },
});
