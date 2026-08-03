/**
 * Caret-movement regressions for the command menu (#139, Copilot review).
 *
 * NOT a frozen check — `components/chat-input.test.js` is, and it is read-only.
 * This file covers what that one does not: `#handleKeydown` used to decide
 * whether to intercept Tab/Arrow/Escape from the cached `_trigger`, which is
 * only recomputed on `input`. A caret moved WITHOUT typing (arrow keys, a mouse
 * click) left that cache stale — reachable by moving the caret to before the
 * prefix character of an open `/…` token, so the menu kept intercepting keys for a token
 * the caret had already left — and Tab was swallowed while doing nothing,
 * because `#commitCommand` recomputes the context and early-returns.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import './chat-input.js';

const MENU = '.command-menu';

const COMMANDS = [
  { name: 'help', description: 'List commands', argument_hint: '' },
  { name: 'mcp__demo__summarize', description: 'Summarize', argument_hint: '<text>' },
];

/** @returns {Promise<any>} */
async function mount() {
  const el = /** @type {any} */ (document.createElement('chat-input'));
  el.commands = COMMANDS;
  document.body.appendChild(el);
  await el.updateComplete;
  return el;
}

const textareaOf = (/** @type {any} */ el) => el.querySelector('textarea');

/** Type `text`, caret left at the end — the same shape as a real keystroke. */
async function type(/** @type {any} */ el, /** @type {string} */ text) {
  const ta = textareaOf(el);
  ta.value = text;
  ta.selectionStart = ta.selectionEnd = text.length;
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  await el.updateComplete;
}

/** Move the caret the way a click or an arrow key does: no `input` event. */
async function moveCaret(/** @type {any} */ el, /** @type {number} */ to, kind = 'click') {
  const ta = textareaOf(el);
  ta.selectionStart = ta.selectionEnd = to;
  ta.dispatchEvent(kind === 'click'
    ? new Event('click', { bubbles: true })
    : new KeyboardEvent('keyup', { key: 'ArrowLeft', bubbles: true }));
  await el.updateComplete;
}

/** @returns {Promise<KeyboardEvent>} */
async function press(/** @type {any} */ el, /** @type {string} */ key) {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  textareaOf(el).dispatchEvent(ev);
  await el.updateComplete;
  return ev;
}

beforeEach(() => { document.body.innerHTML = ''; });

describe('the command menu tracks the caret, not just the typing', () => {
  it('closes when a click moves the caret out of the token', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();

    await moveCaret(el, 0);   // before the `/`

    expect(el.querySelector(MENU)).toBeNull();
  });

  it('closes when arrow keys walk the caret out of the token', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();

    await moveCaret(el, 0, 'keyup');   // before the `/`

    expect(el.querySelector(MENU)).toBeNull();
  });

  // The bug this file exists for. Tab was preventDefault()ed off a stale
  // cache and then did nothing, so focus could not leave the composer.
  it('leaves Tab alone once the caret has left the token', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();   // open before the move
    await moveCaret(el, 0);

    const ev = await press(el, 'Tab');

    expect(ev.defaultPrevented).toBe(false);
    expect(textareaOf(el).value).toBe('/mc');
  });

  it('does not intercept the arrow keys once the caret has left the token', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();   // open before the move
    await moveCaret(el, 0);

    expect((await press(el, 'ArrowDown')).defaultPrevented).toBe(false);
    expect((await press(el, 'ArrowUp')).defaultPrevented).toBe(false);
  });

  // Escape outside the token must fall through to its existing meaning
  // (stop the turn while busy), not be eaten as "dismiss the menu".
  it('lets Escape reach the stop handler once the caret has left the token', async () => {
    const el = await mount();
    el.busy = true;
    await el.updateComplete;
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();   // open before the move
    await moveCaret(el, 0);

    let stopped = false;
    el.addEventListener('stop', () => { stopped = true; });
    await press(el, 'Escape');

    expect(stopped).toBe(true);
  });

  // The two halves of the fix are separable, and this case is the ONLY one that
  // discriminates the second half. The keyup/click handlers null `_trigger`
  // whenever the caret moves through a normal event, which is what every case
  // above exercises — verified by probe: they all still pass with the keydown
  // gate removed. This one moves the caret with NO event at all, so nothing has
  // resynced and the cache is genuinely stale at keydown time. It fails unless
  // `#handleKeydown` consults the live caret rather than trusting `_trigger`.
  it('does not intercept Tab when the caret moved with no intervening event', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();

    // Programmatic move: no click, no keyup, no input. `_trigger` still says
    // a `/mc` token is open.
    const ta = textareaOf(el);
    ta.selectionStart = ta.selectionEnd = 0;

    const ev = await press(el, 'Tab');

    expect(ev.defaultPrevented, 'Tab swallowed off a stale cache').toBe(false);
    expect(ta.value).toBe('/mc');
  });

  // Guards the fix's own footgun: syncMenu runs on keyup, and keyup fires after
  // every ArrowDown keydown. Resetting the highlight unconditionally there
  // would snap it back to 0 on each press — and no frozen test would notice,
  // because a synthetic press() dispatches no keyup.
  it('keeps the highlight across a keydown/keyup pair', async () => {
    const el = await mount();
    await type(el, '/');
    const highlighted = () => /** @type {any[]} */ ([...el.querySelectorAll('.command-menu-item')])
      .findIndex((r) => r.classList.contains('highlighted'));
    expect(highlighted()).toBe(0);

    await press(el, 'ArrowDown');
    expect(highlighted()).toBe(1);

    // The keyup the browser always sends after that keydown.
    const ta = textareaOf(el);
    ta.dispatchEvent(new KeyboardEvent('keyup', { key: 'ArrowDown', bubbles: true }));
    await el.updateComplete;

    expect(highlighted(), 'keyup reset the highlight').toBe(1);
  });
});
