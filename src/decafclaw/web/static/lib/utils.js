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
 * Extract a server-provided error without trusting arbitrary response JSON.
 * @param {unknown} body
 * @returns {string}
 */
export function responseError(body) {
  if (!body || typeof body !== 'object' || !("error" in body)) return '';
  return typeof body.error === 'string' ? body.error : '';
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
 * @typedef {number | ((rect: DOMRect) => number)} ResizeSize
 */

/** @type {HTMLElement | null} */
let resizeDragShield = null;

/**
 * Show a full-viewport overlay for the duration of a resize drag.
 *
 * The overlay keeps mouse events in the parent document. Without it, a
 * pointer over an `<iframe>` (canvas widgets, embedded content) sends
 * `mousemove`/`mouseup` to the iframe document instead, so the drag freezes
 * and the width is never persisted.
 */
function beginResizeDrag() {
  if (!resizeDragShield) {
    resizeDragShield = document.createElement('div');
    resizeDragShield.className = 'resize-drag-shield';
    resizeDragShield.setAttribute('aria-hidden', 'true');
    document.body.appendChild(resizeDragShield);
  }
  resizeDragShield.classList.add('active');
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}

function endResizeDrag() {
  resizeDragShield?.classList.remove('active');
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
}

/**
 * Set up a drag-to-resize handle that adjusts a CSS custom property.
 *
 * Restores the last saved width from localStorage on init, and persists
 * the target width on mouseup. A drag shield routes pointer events through
 * the parent document while the drag crosses iframes.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.handle       - The draggable handle element
 * @param {HTMLElement} opts.container    - The layout element whose bounding rect provides the origin
 * @param {'left' | 'right'} [opts.edge]  - Panel edge: 'left' grows rightward from clientX (default), 'right' grows leftward from the container's right edge
 * @param {ResizeSize} opts.minWidth      - Minimum width in px, or a function of the layout rect
 * @param {ResizeSize} opts.maxWidth      - Maximum width in px, or a function of the layout rect
 * @param {ResizeSize} [opts.offset]      - Pixels between the container edge and the dragged panel (default 0)
 * @param {string}      opts.storageKey   - localStorage key for persisting width
 * @param {string}      opts.cssVar       - CSS custom property name (e.g. '--sidebar-width')
 */
export function setupResizeHandle({
  handle, container, edge = 'left', minWidth, maxWidth, offset = 0, storageKey, cssVar,
}) {
  // Restore saved width
  const saved = localStorage.getItem(storageKey);
  if (saved) document.documentElement.style.setProperty(cssVar, saved + 'px');

  const size = (value, rect) => (typeof value === 'function' ? value(rect) : value);

  let dragging = false;
  /** @type {number | null} */
  let lastWidth = null;

  handle.addEventListener('mousedown', (e) => {
    dragging = true;
    lastWidth = null;
    handle.classList.add('dragging');
    beginResizeDrag();
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const rect = container.getBoundingClientRect();
    const gap = size(offset, rect);
    const raw = edge === 'right'
      ? rect.right - gap - e.clientX
      : e.clientX - rect.left - gap;
    const newWidth = Math.min(size(maxWidth, rect), Math.max(size(minWidth, rect), raw));
    lastWidth = newWidth;
    document.documentElement.style.setProperty(cssVar, newWidth + 'px');
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    endResizeDrag();
    if (lastWidth !== null) {
      localStorage.setItem(storageKey, String(Math.round(lastWidth)));
    }
    lastWidth = null;
  });
}
