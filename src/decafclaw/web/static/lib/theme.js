/**
 * Theme registry + resolve/apply/persistence logic for the web UI.
 *
 * Two-axis model: `data-theme` (light|dark|absent = Pico's structural base) ×
 * `data-palette` (dracula|solarized-light|absent = our color overlay). A single
 * theme *name* selects a descriptor carrying both, so nonsensical combos
 * (e.g. Dracula-in-light) are unrepresentable. See docs/web-ui-design.md.
 *
 * Persists the active theme name under localStorage `decafclaw-theme`
 * (backward compatible with the legacy light/dark/system values).
 */

export const STORAGE_KEY = 'decafclaw-theme';
export const DEFAULT_THEME = 'system';

/**
 * @typedef {Object} ThemeDescriptor
 * @property {string} name
 * @property {string} label
 * @property {'base'|'palette'} kind
 * @property {'light'|'dark'|null} dataTheme
 * @property {string|null} dataPalette
 * @property {string} [icon]  emoji shown on base buttons
 * @property {string} [dot]   representative color for a palette swatch
 */

/** @type {ThemeDescriptor[]} — ordered for UI rendering. */
export const THEMES = [
  { name: 'light',                label: 'Light',                kind: 'base',    dataTheme: 'light', dataPalette: null,                   icon: '☀️' },
  { name: 'dark',                 label: 'Dark',                 kind: 'base',    dataTheme: 'dark',  dataPalette: null,                   icon: '🌙' },
  { name: 'system',               label: 'System',               kind: 'base',    dataTheme: null,    dataPalette: null,                   icon: '💻' },
  { name: 'dracula',              label: 'Dracula',              kind: 'palette', dataTheme: 'dark',  dataPalette: 'dracula',              dot: '#bd93f9' },
  { name: 'solarized-light',      label: 'Solarized Light',      kind: 'palette', dataTheme: 'light', dataPalette: 'solarized-light',      dot: '#268bd2' },
  { name: 'solarized-dark',       label: 'Solarized Dark',       kind: 'palette', dataTheme: 'dark',  dataPalette: 'solarized-dark',       dot: '#268bd2' },
  { name: 'nord',                 label: 'Nord',                 kind: 'palette', dataTheme: 'dark',  dataPalette: 'nord',                 dot: '#88c0d0' },
  { name: 'tokyo-night-storm',    label: 'Tokyo Night Storm',    kind: 'palette', dataTheme: 'dark',  dataPalette: 'tokyo-night-storm',    dot: '#7aa2f7' },
  { name: 'github-dark-dimmed',   label: 'GitHub Dark Dimmed',   kind: 'palette', dataTheme: 'dark',  dataPalette: 'github-dark-dimmed',   dot: '#539bf5' },
  { name: 'one-dark',             label: 'One Dark',             kind: 'palette', dataTheme: 'dark',  dataPalette: 'one-dark',             dot: '#61aeee' },
  { name: 'gruvbox-dark',         label: 'Gruvbox Dark',         kind: 'palette', dataTheme: 'dark',  dataPalette: 'gruvbox-dark',         dot: '#fe8019' },
  { name: 'everforest-dark',      label: 'Everforest Dark',      kind: 'palette', dataTheme: 'dark',  dataPalette: 'everforest-dark',      dot: '#a7c080' },
  { name: 'kanagawa-wave',        label: 'Kanagawa Wave',        kind: 'palette', dataTheme: 'dark',  dataPalette: 'kanagawa-wave',        dot: '#7e9cd8' },
  { name: 'catppuccin-mocha',     label: 'Catppuccin Mocha',     kind: 'palette', dataTheme: 'dark',  dataPalette: 'catppuccin-mocha',     dot: '#cba6f7' },
  { name: 'catppuccin-macchiato', label: 'Catppuccin Macchiato', kind: 'palette', dataTheme: 'dark',  dataPalette: 'catppuccin-macchiato', dot: '#c6a0f6' },
  { name: 'rose-pine-moon',       label: 'Rosé Pine Moon',       kind: 'palette', dataTheme: 'dark',  dataPalette: 'rose-pine-moon',       dot: '#ea9a97' },
  { name: 'material-palenight',   label: 'Material Palenight',   kind: 'palette', dataTheme: 'dark',  dataPalette: 'material-palenight',   dot: '#82aaff' },
  { name: 'night-owl',            label: 'Night Owl',            kind: 'palette', dataTheme: 'dark',  dataPalette: 'night-owl',            dot: '#7fdbca' },
  { name: 'ayu-mirage',           label: 'Ayu Mirage',           kind: 'palette', dataTheme: 'dark',  dataPalette: 'ayu-mirage',           dot: '#ffcc66' },
];

const THEME_BY_NAME = new Map(THEMES.map((t) => [t.name, t]));

/**
 * @param {string|null|undefined} name
 * @returns {ThemeDescriptor}
 */
export function resolveTheme(name) {
  return THEME_BY_NAME.get(name) ?? THEME_BY_NAME.get(DEFAULT_THEME);
}

/**
 * Apply a theme by name to <html>, setting or clearing both attributes.
 * @param {string} name
 * @returns {string} the resolved theme name
 */
export function applyTheme(name) {
  const t = resolveTheme(name);
  const el = document.documentElement;
  if (t.dataTheme) el.setAttribute('data-theme', t.dataTheme);
  else el.removeAttribute('data-theme');
  if (t.dataPalette) el.setAttribute('data-palette', t.dataPalette);
  else el.removeAttribute('data-palette');
  return t.name;
}

/** @returns {string} a valid theme name (falls back to DEFAULT_THEME) */
export function loadStoredTheme() {
  let stored = null;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* unavailable */ }
  return THEME_BY_NAME.has(stored) ? stored : DEFAULT_THEME;
}

/** @param {string} name */
export function storeTheme(name) {
  try { localStorage.setItem(STORAGE_KEY, name); } catch { /* unavailable */ }
}
