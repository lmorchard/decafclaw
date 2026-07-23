/**
 * xterm.js vendor entry — the npm package is CJS/UMD, so its classes land
 * either on the namespace or under `default` depending on how esbuild's interop
 * resolves it. Normalize (see leaflet-entry.js for the same pattern) and
 * re-export NAMED so consumers can `import { Terminal } from '@xterm/xterm'`
 * (matching the package's TypeScript types that `make check-js` validates).
 * `default` is kept too, for the addon bundles' internal xterm import.
 *
 * Bundled into vendor/bundle/xterm.js by build-vendor.mjs.
 */
import * as mod from '@xterm/xterm';

const src = mod.Terminal ? mod : (mod.default || {});
export const Terminal = src.Terminal;
export default src;
