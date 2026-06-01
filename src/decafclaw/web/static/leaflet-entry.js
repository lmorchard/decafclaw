/**
 * Leaflet vendor entry — normalizes Leaflet's ESM/UMD exports to a single
 * default export so consumers can `import L from 'leaflet'` regardless of
 * which build esbuild resolves.
 *
 * Bundled into vendor/bundle/leaflet.js by build-vendor.mjs.
 * Imported via the importmap entry "leaflet".
 */
import * as leaflet from 'leaflet';

// ESM build: named exports live on the namespace (leaflet.map, etc.).
// UMD build via esbuild interop: the L object lands on `.default`.
const L = leaflet.default && leaflet.default.map ? leaflet.default : leaflet;
export default L;
