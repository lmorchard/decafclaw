import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/** @param {string} p */
const here = (p) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    // Mirror the browser import map in index.html: bare specifiers that point
    // at a vendor bundle resolve to that bundle's *entry*, not the npm package
    // root. `@milkdown/kit`'s root export has none of the names the entry
    // re-exports from its subpaths (`$remark`, `commonmark`, …), so without
    // this a component test importing wiki-editor fails at module load.
    // Anchored so only the bare specifier is rewritten — the entry's own
    // `@milkdown/kit/core` etc. must still resolve to the npm package.
    alias: [
      { find: /^@milkdown\/kit$/, replacement: here('./milkdown-entry.js') },
    ],
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.js'],
    include: ['lib/**/*.test.js', 'components/**/*.test.js', 'widgets/**/*.test.js'],
  },
});
