import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ConnectionOverlay } from './connection-overlay.js';

function setupDom() {
  document.body.innerHTML = `
    <div id="chat-layout"><button id="behind">behind</button></div>
    <div id="connection-overlay" class="hidden" role="alert">
      <p class="connection-overlay-detail">Reconnecting to DecafClaw…</p>
      <p data-role="attempt" aria-hidden="true"></p>
    </div>
  `;
  return {
    root: /** @type {HTMLElement} */ (document.getElementById('connection-overlay')),
    layout: /** @type {HTMLElement} */ (document.getElementById('chat-layout')),
    attemptLine: /** @type {HTMLElement} */ (
      document.querySelector('[data-role="attempt"]')
    ),
  };
}

describe('ConnectionOverlay', () => {
  /** @type {ReturnType<typeof setupDom>} */
  let dom;

  beforeEach(() => { dom = setupDom(); });
  afterEach(() => { document.body.innerHTML = ''; });

  it('starts hidden and leaves the layout interactive', () => {
    const overlay = new ConnectionOverlay(dom.root, dom.layout);
    expect(overlay.visible).toBe(false);
    expect(dom.root.classList.contains('hidden')).toBe(true);
    expect(dom.layout.hasAttribute('inert')).toBe(false);
  });

  it('covers the app and makes the layout inert while shown', () => {
    const overlay = new ConnectionOverlay(dom.root, dom.layout);
    overlay.show();
    expect(overlay.visible).toBe(true);
    expect(dom.root.classList.contains('hidden')).toBe(false);
    expect(dom.layout.hasAttribute('inert')).toBe(true);
  });

  it('narrates the next reconnect attempt', () => {
    const overlay = new ConnectionOverlay(dom.root, dom.layout);
    overlay.show();
    overlay.narrate({ attempt: 3, delayMs: 4000 });
    expect(dom.attemptLine.textContent).toBe('Attempt 3 · next try in 4s');
  });

  it('clears the attempt line on the first drop', () => {
    const overlay = new ConnectionOverlay(dom.root, dom.layout);
    overlay.show();
    overlay.narrate({ attempt: 2, delayMs: 2000 });
    overlay.narrate();
    expect(dom.attemptLine.textContent).toBe('');
  });

  it('restores interaction and clears narration when the socket reopens', () => {
    const overlay = new ConnectionOverlay(dom.root, dom.layout);
    overlay.show();
    overlay.narrate({ attempt: 1, delayMs: 1000 });
    overlay.hide();
    expect(overlay.visible).toBe(false);
    expect(dom.root.classList.contains('hidden')).toBe(true);
    expect(dom.layout.hasAttribute('inert')).toBe(false);
    expect(dom.attemptLine.textContent).toBe('');
  });
});
