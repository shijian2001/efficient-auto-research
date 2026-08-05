import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base: './' -> relative asset URLs, so the built site works at any path
// (domain root or a GitHub Pages project subpath like /Arbor/).
export default defineConfig({
  base: './',
  plugins: [react()],
});
