import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/postcss';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const projectDirectory = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: path.join(projectDirectory, 'local'),
  publicDir: path.join(projectDirectory, 'public'),
  resolve: { alias: { '@': projectDirectory } },
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: {
    outDir: path.join(projectDirectory, '../src/issuepilot/web_static'),
    emptyOutDir: true,
  },
});
