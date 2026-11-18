import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Only process.env is needed here; avoids adding @types/node for one line.
declare const process: { env: Record<string, string | undefined> };

// GitLab project pages are served from /<repo-name>/. The GitHub Pages build sets
// BASE_PATH=/ because it is served from the custom domain reactor.jeffreywoodbury.dev.
export default defineConfig({
  base: process.env.BASE_PATH ?? '/reactor-kinetics-sweep/',
  plugins: [svelte()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
