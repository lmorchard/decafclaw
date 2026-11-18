import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * jsdom does not implement image loading or 2D canvas rendering, so both are
 * stubbed: the fake `Image` fires `onload` on the next microtask, and the fake
 * canvas context records the badge glyph/color as `drawState` so `toDataURL`
 * can return a per-status href. The module under test only ever asks for an
 * href and assigns it, which is what these tests assert.
 */

const DEFAULT_HEAD = `
  <link rel="icon" href="/static/icons/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/icons/favicon-16.png">
  <link rel="apple-touch-icon" href="/static/icons/favicon-256.png">
`;

/** @type {{ mark: string, color: string, drawn: number }} */
let drawState;

class FakeImage {
  /** @type {string} */
  src = '';
  /** @type {null | (() => void)} */
  onload = null;
  /** @type {null | (() => void)} */
  onerror = null;
  static instances = 0;

  constructor() {
    FakeImage.instances++;
    // `onload`/`src` are assigned synchronously after construction; resolve once
    // the current stack unwinds.
    queueMicrotask(() => this.onload?.());
  }
}

/** @returns {string[]} hrefs of the plain `rel="icon"` links, in DOM order */
const iconHrefs = () =>
  [...document.querySelectorAll('link[rel="icon"]')].map((link) =>
    link.getAttribute('href'),
  );

const DEFAULT_HREFS = [
  '/static/icons/favicon.ico',
  '/static/icons/favicon-32.png',
  '/static/icons/favicon-16.png',
];

/** Each test gets a fresh module instance (module-level default/cached state). */
async function loadModule() {
  vi.resetModules();
  return import('./favicon-badge.js');
}

describe('favicon badge', () => {
  beforeEach(() => {
    document.head.innerHTML = DEFAULT_HEAD;
    drawState = { mark: '', color: '', drawn: 0 };
    FakeImage.instances = 0;

    vi.stubGlobal('Image', FakeImage);

    let fillStyle = '';
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () => /** @type {any} */ ({
        drawImage: () => {
          drawState.drawn++;
        },
        beginPath: () => {},
        arc: () => {},
        fill: () => {
          drawState.color = fillStyle;
        },
        stroke: () => {},
        fillText: (/** @type {string} */ text) => {
          drawState.mark = text;
        },
        get fillStyle() {
          return fillStyle;
        },
        set fillStyle(value) {
          fillStyle = value;
        },
      }),
    );
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(
      () => `data:image/png;base64,${drawState.mark}`,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('leaves the default favicons untouched when idle', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('idle');

    expect(iconHrefs()).toEqual(DEFAULT_HREFS);
    expect(FakeImage.instances).toBe(0);
  });

  it('restores every default href after an active status', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('waiting');
    expect(iconHrefs().every((href) => href?.startsWith('data:image/png'))).toBe(true);

    await updateFaviconBadge('idle');
    expect(iconHrefs()).toEqual(DEFAULT_HREFS);
  });

  it('draws an orange attention badge while waiting', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('waiting');

    expect(drawState.mark).toBe('!');
    expect(drawState.color).toBe('#f59e0b');
    expect(iconHrefs()).toEqual([
      'data:image/png;base64,!',
      'data:image/png;base64,!',
      'data:image/png;base64,!',
    ]);
  });

  it('draws a green checkmark for the finished status', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('finished');

    expect(drawState.mark).toBe('✓');
    expect(drawState.color).toBe('#22c55e');
    expect(iconHrefs()[1]).toBe('data:image/png;base64,✓');
  });

  it('draws a busy badge while a turn is running', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('busy');

    expect(drawState.mark).toBe('…');
    expect(drawState.color).toBe('#3b82f6');
    expect(iconHrefs()[1]).toBe('data:image/png;base64,…');
  });

  it('replaces the href when the aggregate status changes', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('busy');
    await updateFaviconBadge('finished');
    expect(iconHrefs()[1]).toBe('data:image/png;base64,✓');

    await updateFaviconBadge('waiting');
    expect(iconHrefs()[1]).toBe('data:image/png;base64,!');
  });

  it('does not redraw when the status is unchanged', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('busy');
    const toDataURL = /** @type {any} */ (HTMLCanvasElement.prototype.toDataURL);
    expect(toDataURL).toHaveBeenCalledTimes(1);

    await updateFaviconBadge('busy');
    expect(toDataURL).toHaveBeenCalledTimes(1);
  });

  it('leaves unrelated icon links (apple-touch-icon) alone', async () => {
    const { updateFaviconBadge } = await loadModule();

    await updateFaviconBadge('finished');

    const touch = document.querySelector('link[rel="apple-touch-icon"]');
    expect(touch?.getAttribute('href')).toBe('/static/icons/favicon-256.png');
  });
});
