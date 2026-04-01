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
