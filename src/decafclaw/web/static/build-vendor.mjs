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
  {
    name: '@milkdown/kit',
    entry: join(__dirname, 'milkdown-entry.js'),
    outfile: join(outdir, 'milkdown.js'),
    external: [],
  },
  {
    name: 'codemirror',
    entry: join(__dirname, 'codemirror-entry.js'),
    outfile: join(outdir, 'codemirror.js'),
    external: [],
  },
  {
    name: 'highlight.js',
    entry: join(__dirname, 'hljs-entry.js'),
    outfile: join(outdir, 'highlight.js'),
    external: [],
  },
  {
    name: 'leaflet',
    entry: join(__dirname, 'leaflet-entry.js'),
    outfile: join(outdir, 'leaflet.js'),
  },
  {
    name: '@xterm/xterm',
    entry: join(__dirname, 'xterm-entry.js'),
    outfile: join(outdir, 'xterm.js'),
  },
  {
    name: '@xterm/addon-fit',
    entry: join(__dirname, 'xterm-addon-fit-entry.js'),
    outfile: join(outdir, 'xterm-addon-fit.js'),
    external: ['@xterm/xterm'],
  },
  {
    name: '@xterm/addon-serialize',
    entry: join(__dirname, 'xterm-addon-serialize-entry.js'),
    outfile: join(outdir, 'xterm-addon-serialize.js'),
    external: ['@xterm/xterm'],
  },
  {
    name: '@xterm/addon-web-links',
    entry: join(__dirname, 'xterm-addon-web-links-entry.js'),
    outfile: join(outdir, 'xterm-addon-web-links.js'),
    external: ['@xterm/xterm'],
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
    mainFields: ['module', 'main'],
    conditions: ['import', 'module', 'browser', 'default'],
  });
}

// Copy Pico CSS (no bundling needed — it's just CSS)
const picoSrc = join(__dirname, 'node_modules', '@picocss', 'pico', 'css', 'pico.min.css');
const picoDst = join(outdir, 'pico.min.css');
cpSync(picoSrc, picoDst);
console.log('Copied pico.min.css');

// Copy Leaflet CSS (panes, controls, zoom buttons; not imported by the JS).
const leafletCssSrc = join(__dirname, 'node_modules', 'leaflet', 'dist', 'leaflet.css');
const leafletCssDst = join(outdir, 'leaflet.css');
cpSync(leafletCssSrc, leafletCssDst);
console.log('Copied leaflet.css');

// Copy Leaflet's image assets. leaflet.css references them via relative
// url(images/...) (layers control, default marker icons). The map widget
// uses an SVG divIcon and no layers control today, so nothing requests them
// yet — but shipping the CSS without its images leaves dangling refs that
// would 404 the moment a layers control or default marker is used. Keep the
// vendored CSS self-contained. Resolves to /static/vendor/bundle/images/.
const leafletImagesSrc = join(__dirname, 'node_modules', 'leaflet', 'dist', 'images');
const leafletImagesDst = join(outdir, 'images');
cpSync(leafletImagesSrc, leafletImagesDst, { recursive: true });
console.log('Copied leaflet images');

// Copy xterm.js CSS (terminal cursor/selection styling; not imported by the JS).
const xtermCssSrc = join(__dirname, 'node_modules', '@xterm', 'xterm', 'css', 'xterm.css');
const xtermCssDst = join(outdir, 'xterm.css');
cpSync(xtermCssSrc, xtermCssDst);
console.log('Copied xterm.css');

console.log('Vendor bundle complete!');
