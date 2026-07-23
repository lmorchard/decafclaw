/**
 * @xterm/addon-serialize vendor entry — normalize CJS/ESM interop and re-export
 * the named classes (see xterm-entry.js). Bundled into
 * vendor/bundle/xterm-addon-serialize.js with @xterm/xterm external.
 */
import * as mod from '@xterm/addon-serialize';

const src = mod.SerializeAddon ? mod : (mod.default || {});
export const SerializeAddon = src.SerializeAddon;
export const HTMLSerializeHandler = src.HTMLSerializeHandler;
export default src;
