import { afterEach, describe, expect, it } from 'vitest';
import './widget.js';

/** @param {Record<string, unknown>} data */
async function mount(data) {
  const el = /** @type {import('./widget.js').WebFrameWidget} */ (
    document.createElement('dc-widget-web-frame')
  );
  el.data = data;
  document.body.append(el);
  await el.updateComplete;
  return el;
}

describe('WebFrameWidget', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('renders the iframe with the requested url', async () => {
    const el = await mount({ url: 'http://localhost:2718', title: 'Marimo' });
    const frame = /** @type {HTMLIFrameElement} */ (el.querySelector('iframe'));
    expect(frame.getAttribute('src')).toBe('http://localhost:2718');
    expect(frame.getAttribute('title')).toBe('Marimo');
  });

  it('themes the titlebar with app tokens instead of a fixed light background', async () => {
    const el = await mount({ url: 'http://localhost:2718', title: 'Marimo' });
    const header = /** @type {HTMLElement} */ (el.querySelector('header.web-frame-header'));
    const style = header.getAttribute('style') ?? '';

    expect(header.textContent).toContain('Marimo');
    expect(style).toContain('var(--pico-card-sectioning-background-color');
    expect(style).toContain('var(--pico-muted-border-color');
    expect(style).not.toContain('#fafafa');
  });

  it('keeps the titlebar and reload control when the tab has no title', async () => {
    const el = await mount({ url: 'http://localhost:2718' });
    const header = el.querySelector('header.web-frame-header');

    expect(header).not.toBeNull();
    expect(header?.querySelector('button.web-frame-reload')).not.toBeNull();
  });

  it('reloads the embedded application by reassigning the iframe src', async () => {
    const el = await mount({ url: 'http://localhost:2718', title: 'Marimo' });
    const frame = /** @type {HTMLIFrameElement} */ (el.querySelector('iframe'));
    let assigned = 0;
    Object.defineProperty(frame, 'src', {
      configurable: true,
      get: () => 'http://localhost:2718',
      set: () => { assigned += 1; },
    });

    /** @type {HTMLButtonElement} */ (el.querySelector('button.web-frame-reload')).click();

    expect(assigned).toBe(1);
  });

  it('renders a notice when the url is empty', async () => {
    const el = await mount({});
    expect(el.textContent).toContain('No URL specified for web frame.');
  });
});
