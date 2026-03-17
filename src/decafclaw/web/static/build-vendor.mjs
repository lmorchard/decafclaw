/**
 * Bundle vendor dependencies into individual ESM files.
 *
 * Each vendor lib gets its own file in vendor/bundle/ so we can
 * map them individually in the import map. This keeps our components
 * as unbundled ES modules while giving us npm-managed dependencies.
 *
 * Usage: npm run build (from the static/ directory)
 */

import * as esbuild from 'esbuild';
import { cpSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const outdir = join(__dirname, 'vendor', 'bundle');

mkdirSync(outdir, { recursive: true });

// Bundle each vendor lib as a separate ESM file
const bundles = [
  {
    name: 'lit',
    entry: 'lit',
    outfile: join(outdir, 'lit.js'),
  },
  {
    name: 'lit/directives/unsafe-html',
    entry: 'lit/directives/unsafe-html.js',
    outfile: join(outdir, 'lit-unsafe-html.js'),
    external: ['lit'],
  },
  {
    name: 'marked',
    entry: 'marked',
    outfile: join(outdir, 'marked.js'),
  },
  {
    name: 'dompurify',
    entry: 'dompurify',
    outfile: join(outdir, 'dompurify.js'),
  },
];

for (const bundle of bundles) {
  console.log(`Bundling ${bundle.name}...`);
  await esbuild.build({
    entryPoints: [bundle.entry],
    bundle: true,
    format: 'esm',
    outfile: bundle.outfile,
    external: bundle.external || [],
    minify: true,
    target: 'es2022',
    platform: 'browser',
  });
}

// Copy Pico CSS (no bundling needed — it's just CSS)
const picoSrc = join(__dirname, 'node_modules', '@picocss', 'pico', 'css', 'pico.min.css');
const picoDst = join(outdir, 'pico.min.css');
cpSync(picoSrc, picoDst);
console.log('Copied pico.min.css');

console.log('Vendor bundle complete!');
