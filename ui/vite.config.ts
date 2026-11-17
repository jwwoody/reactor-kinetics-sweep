import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// GitHub/GitLab project pages are served from /<repo-name>/.
export default defineConfig({
  base: '/reactor-kinetics-sweep/',
  plugins: [svelte()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
