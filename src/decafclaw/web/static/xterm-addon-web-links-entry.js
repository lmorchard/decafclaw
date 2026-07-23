/**
 * @xterm/addon-web-links vendor entry — normalize CJS/ESM interop and re-export
 * the named class (see xterm-entry.js). Bundled into
 * vendor/bundle/xterm-addon-web-links.js with @xterm/xterm external.
 */
import * as mod from '@xterm/addon-web-links';

const src = mod.WebLinksAddon ? mod : (mod.default || {});
export const WebLinksAddon = src.WebLinksAddon;
export default src;
