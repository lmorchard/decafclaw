/**
 * @xterm/addon-fit vendor entry — normalize CJS/ESM interop and re-export the
 * named class (see xterm-entry.js). Bundled into vendor/bundle/xterm-addon-fit.js
 * with @xterm/xterm external (resolved at runtime via the importmap).
 */
import * as mod from '@xterm/addon-fit';

const src = mod.FitAddon ? mod : (mod.default || {});
export const FitAddon = src.FitAddon;
export default src;
