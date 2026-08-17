import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));

describe('community export panel', () => {
  it('does not import an isolated hub page', () => {
    const text = readFileSync(resolve(here, 'src/App.tsx'), 'utf8');
    expect(text.includes('InternalHub')).toBe(false);
    expect(text.includes('from "./pages/Internal')).toBe(false);
  });
  it('does not import an isolated operations pane', () => {
    const text = readFileSync(
      resolve(here, 'src/pages/Operations.tsx'),
      'utf8',
    );
    expect(text.includes('OperationsPage')).toBe(false);
    expect(text.includes('internalOn')).toBe(false);
  });
});
