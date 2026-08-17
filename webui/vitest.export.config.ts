import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: { environment: 'node', include: ['export.factory.test.ts'] },
});
