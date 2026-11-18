/**
 * Tab favicon badge for the selected conversation's status (#1288).
 *
 * Draws the base DecafClaw icon plus a status badge on a 32x32 offscreen
 * canvas and points every `<link rel="icon">` at the resulting PNG. When the
 * conversation returns to idle the original hrefs are restored.
 *
 * Canvas (not an SVG data URI) because not every browser renders SVG favicons;
 * every favicon link is updated because leaving the 16/32px defaults in place
 * would let the browser pick an unbadged icon over the status image.
 */

/** @typedef {'idle' | 'busy' | 'waiting' | 'finished'} ConversationStatus */

const BASE_ICON_HREF = '/static/icons/favicon-32.png';
const ICON_SIZE = 32;
const BADGE_RADIUS = 9;

/**
 * Badge fill and center glyph per status; `idle` has no badge.
 * @type {Record<ConversationStatus, {fill: string, mark: string} | null>}
 */
const BADGES = {
  idle: null,
  waiting: { fill: '#f59e0b', mark: '!' },
  finished: { fill: '#22c55e', mark: '✓' },
  busy: { fill: '#3b82f6', mark: '…' },
};

/**
 * Captured defaults, one per favicon link present on first use.
 * @type {Array<{link: HTMLLinkElement, href: string | null}> | null}
 */
let defaults = null;
/** @type {Promise<HTMLImageElement | null> | null} */
let baseIcon = null;
/** @type {ConversationStatus} */
let applied = 'idle';

function rememberDefaults() {
  if (defaults) return;
  defaults = Array.from(document.querySelectorAll('link[rel="icon"]')).map((link) => ({
    link: /** @type {HTMLLinkElement} */ (link),
    href: link.getAttribute('href'),
  }));
}

function restoreDefaults() {
  for (const { link, href } of defaults || []) {
    if (href === null) link.removeAttribute('href');
    else link.setAttribute('href', href);
  }
}

/** @returns {Promise<HTMLImageElement | null>} */
function loadBaseIcon() {
  if (!baseIcon) {
    baseIcon = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => {
        // Allow a later status change to retry after a transient failure.
        baseIcon = null;
        resolve(null);
      };
      img.src = BASE_ICON_HREF;
    });
  }
  return baseIcon;
}

/**
 * @param {ConversationStatus} status
 * @param {CanvasImageSource} base
 * @returns {string | null} PNG data URI, or null when canvas is unavailable
 */
function drawBadge(status, base) {
  const badge = BADGES[status];
  if (!badge) return null;
  const canvas = document.createElement('canvas');
  canvas.width = ICON_SIZE;
  canvas.height = ICON_SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  ctx.drawImage(base, 0, 0, ICON_SIZE, ICON_SIZE);

  const center = ICON_SIZE - BADGE_RADIUS;
  ctx.beginPath();
  ctx.arc(center, center, BADGE_RADIUS, 0, Math.PI * 2);
  ctx.fillStyle = badge.fill;
  ctx.fill();
  // White ring keeps the badge legible over the icon regardless of its colors.
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#ffffff';
  ctx.stroke();

  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 13px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(badge.mark, center, center + 0.5);

  return canvas.toDataURL('image/png');
}

/**
 * Point the tab favicon at the icon matching `status`, or restore the defaults
 * when idle. Safe to call on every store change: an unchanged status is a
 * no-op, and a newer status supersedes an icon still loading.
 * @param {ConversationStatus} status
 * @returns {Promise<void>}
 */
export async function updateFaviconBadge(status) {
  if (status === applied) return;
  applied = status;
  rememberDefaults();

  const badge = BADGES[status];
  if (!badge) {
    restoreDefaults();
    return;
  }

  const base = await loadBaseIcon();
  // A status change while loading wins; the newer call owns the favicon.
  if (!base || applied !== status) return;

  const href = drawBadge(status, base);
  if (!href) return;
  for (const { link } of defaults || []) link.setAttribute('href', href);
}
