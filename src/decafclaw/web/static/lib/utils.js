/**
 * Encode a page path for use in URLs, preserving `/` separators.
 * @param {string} page
 * @returns {string}
 */
export function encodePagePath(page) {
  return page.split('/').map(encodeURIComponent).join('/');
}

/**
 * Format an ISO timestamp to a locale time string (HH:MM).
 * @param {string} ts
 * @returns {string}
 */
export function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Format an ISO timestamp as a short relative time (e.g. "2m ago", "3h ago").
 * @param {string} ts
 * @returns {string}
 */
export function formatRelativeTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const diffMs = Date.now() - d.getTime();
  if (isNaN(diffMs)) return '';
  if (diffMs < 60_000) return 'just now';
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

/**
 * Copy text to the clipboard, with a fallback for non-secure contexts.
 *
 * `navigator.clipboard` only exists in secure contexts (HTTPS or localhost).
 * The deployed agent serves over plain http://, where `navigator.clipboard`
 * is `undefined` — accessing `.writeText` throws. Localhost masks this because
 * it counts as a secure context. So we fall back to the legacy
 * `document.execCommand('copy')` path via a hidden textarea.
 *
 * @param {string} text
 * @returns {Promise<void>} resolves on success, rejects on failure
 */
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch (err) {
    // Clipboard API present but rejected (e.g. permission / focus) — fall
    // through to the legacy path rather than failing outright.
    console.debug('clipboard API writeText failed, using fallback:', err);
  }

  // Legacy fallback: a hidden textarea + execCommand('copy').
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '-9999px';
  document.body.appendChild(ta);
  const prevFocus = /** @type {HTMLElement | null} */ (document.activeElement);
  ta.select();
  try {
    if (!document.execCommand('copy')) {
      throw new Error('copy command was rejected by the browser');
    }
  } finally {
    document.body.removeChild(ta);
    if (prevFocus && typeof prevFocus.focus === 'function') prevFocus.focus();
  }
}

/**
 * Set up a drag-to-resize handle that adjusts a CSS custom property.
 *
 * Restores the last saved width from localStorage on init, and persists
 * the width on mouseup.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.handle       - The draggable handle element
 * @param {HTMLElement} opts.container    - The layout element whose bounding rect provides the origin
 * @param {number}      opts.minWidth     - Minimum width in px
 * @param {number}      opts.maxWidth     - Maximum width in px
 * @param {string}      opts.storageKey   - localStorage key for persisting width
 * @param {string}      opts.cssVar       - CSS custom property name (e.g. '--sidebar-width')
 */
export function setupResizeHandle({ handle, container, minWidth, maxWidth, storageKey, cssVar }) {
  // Restore saved width
  const saved = localStorage.getItem(storageKey);
  if (saved) document.documentElement.style.setProperty(cssVar, saved + 'px');

  let dragging = false;

  handle.addEventListener('mousedown', (e) => {
    dragging = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const newWidth = Math.min(maxWidth, Math.max(minWidth, e.clientX - rect.left));
    document.documentElement.style.setProperty(cssVar, newWidth + 'px');
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const w = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim();
    localStorage.setItem(storageKey, String(parseInt(w)));
  });
}
