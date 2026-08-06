/**
 * Frozen acceptance checks for issue #139 — command autocomplete in `chat-input`.
 *
 * Two groups live here on purpose:
 *
 *  - "command autocomplete" encodes criterion C1 plus the settled design
 *    decisions from the issue (live open on `/` or `!` at start of line,
 *    fuzzy *subsequence* matching, Arrow navigation, Tab commits, Esc
 *    dismisses, Enter never commits). These FAIL until the feature lands.
 *
 *  - "existing send/stop behaviour (menu closed)" is guard G2: the keyboard
 *    contract that already ships. These PASS today and must keep passing —
 *    the autocomplete keydown handling is being threaded through the same
 *    `#handleKeydown`, which is exactly where a regression would hide.
 *
 * Contract this file defines (no implementation existed when it was written):
 *  - `chat-input.commands` — Array of `{name, description, argument_hint}`.
 *  - `.command-menu` — the suggestion list container, rendered only while open.
 *  - `.command-menu-item` — one row per suggestion, carrying
 *    `data-command="<name>"` and rendering the command name as text.
 *  - `.command-menu-item.highlighted` — the single highlighted row.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import './chat-input.js';

/** The command source named by criterion C1. */
const COMMANDS = [
  { name: 'help', description: 'List available commands', argument_hint: '' },
  {
    name: 'mcp__demo__summarize',
    description: 'Summarize a block of text',
    argument_hint: '<text> [language]',
  },
];

const MENU = '.command-menu';
const ROW = '.command-menu-item';
const HIGHLIGHTED = '.command-menu-item.highlighted';

/**
 * @param {{commands?: object[], busy?: boolean}} [opts]
 * @returns {Promise<any>}
 */
async function mount({ commands = COMMANDS, busy = false } = {}) {
  const el = /** @type {any} */ (document.createElement('chat-input'));
  el.commands = commands;
  el.busy = busy;
  document.body.appendChild(el);
  await el.updateComplete;
  return el;
}

/** @param {any} el @returns {HTMLTextAreaElement} */
function textareaOf(el) {
  const ta = /** @type {HTMLTextAreaElement|null} */ (el.querySelector('textarea'));
  if (!ta) throw new Error('chat-input rendered no textarea');
  return ta;
}

/**
 * Simulate the user typing `text` into the (empty) textarea, caret at the end.
 * @param {any} el @param {string} text
 */
async function type(el, text) {
  const ta = textareaOf(el);
  ta.value = text;
  ta.selectionStart = ta.selectionEnd = text.length;
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  await el.updateComplete;
  return ta;
}

/**
 * Dispatch a keydown on the textarea and let lit settle.
 * @param {any} el @param {string} key @param {KeyboardEventInit} [init]
 */
async function press(el, key, init = {}) {
  const ev = new KeyboardEvent('keydown', {
    key, bubbles: true, cancelable: true, ...init,
  });
  textareaOf(el).dispatchEvent(ev);
  await el.updateComplete;
  return ev;
}

/** Command names currently offered, in render order. @param {any} el */
function suggested(el) {
  return [...el.querySelectorAll(ROW)].map(
    (/** @type {Element} */ row) => row.getAttribute('data-command'),
  );
}

/** Index of the highlighted row, or -1. @param {any} el */
function highlightedIndex(el) {
  const rows = [...el.querySelectorAll(ROW)];
  return rows.findIndex((/** @type {Element} */ r) => r.matches(HIGHLIGHTED));
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('chat-input command autocomplete', () => {
  it('offers the fuzzy matches for "/mc" and nothing else', async () => {
    const el = await mount();

    await type(el, '/mc');

    expect(el.querySelector(MENU)).not.toBeNull();
    // "help" has no `m`, so it cannot be a subsequence match.
    expect(suggested(el)).toEqual(['mcp__demo__summarize']);
    const row = el.querySelector(ROW);
    expect(row.textContent).toContain('mcp__demo__summarize');
  });

  it('matches by subsequence, not by prefix', async () => {
    const el = await mount();

    // d-s-u-m appears in order inside "mcp__demo__summarize" but is not a
    // prefix of it. "/mc" alone would pass a prefix-only implementation.
    await type(el, '/dsum');

    expect(suggested(el)).toEqual(['mcp__demo__summarize']);
  });

  it('requires the subsequence to be in order, not merely present', async () => {
    const el = await mount();

    // Every one of e, o, d occurs in "mcp__demo__summarize", so the common
    // shortcut `[...q].every(ch => name.includes(ch))` offers it. In order,
    // though, the last `d` sits at index 5 — before the `o` at index 8 — so a
    // real subsequence scan rejects it.
    await type(el, '/eod');

    expect(suggested(el)).toEqual([]);
  });

  it('opens on "!" at the start of the line as well as "/"', async () => {
    const el = await mount();

    await type(el, '!mc');

    expect(el.querySelector(MENU)).not.toBeNull();
    expect(suggested(el)).toEqual(['mcp__demo__summarize']);
  });

  it('stays closed when the trigger is not at the start of the line', async () => {
    const el = await mount();

    await type(el, 'see /mc');

    expect(el.querySelector(MENU)).toBeNull();
  });

  it('opens on a trigger starting a later line, not just the whole value', async () => {
    const el = await mount();

    // The criterion says "start of the line". A `value.startsWith('/')` test
    // greens every other case in this file while leaving the menu dead for
    // anyone who used Shift+Enter first.
    await type(el, 'hello\n/mc');

    expect(el.querySelector(MENU)).not.toBeNull();
    expect(suggested(el)).toEqual(['mcp__demo__summarize']);
  });

  it('highlights the first suggestion and moves it with ArrowDown/ArrowUp', async () => {
    const el = await mount();

    // A bare "/" matches every command, so there is something to navigate.
    await type(el, '/');
    expect(suggested(el)).toEqual(['help', 'mcp__demo__summarize']);
    expect(el.querySelectorAll(HIGHLIGHTED)).toHaveLength(1);
    expect(highlightedIndex(el)).toBe(0);

    await press(el, 'ArrowDown');
    expect(highlightedIndex(el)).toBe(1);
    expect(el.querySelectorAll(HIGHLIGHTED)).toHaveLength(1);

    await press(el, 'ArrowUp');
    expect(highlightedIndex(el)).toBe(0);
    expect(el.querySelectorAll(HIGHLIGHTED)).toHaveLength(1);
  });

  it('commits the highlighted suggestion on Tab and closes the menu', async () => {
    const el = await mount();
    await type(el, '/mc');

    const ev = await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('/mcp__demo__summarize ');
    expect(el.querySelector(MENU)).toBeNull();
    // Tab must not also move focus out of the composer.
    expect(ev.defaultPrevented).toBe(true);
  });

  it('commits the moved highlight on Tab, not the first match', async () => {
    const el = await mount();
    // "/mc" matches exactly one command, so highlight 0 and matches[0] are the
    // same entry — a Tab that commits matches[0] and never reads the highlight
    // greens the criterion above (and drains the Arrow test of consequence).
    // From a bare "/", matches[0] is `help`, so that fake produces "/help ".
    await type(el, '/');
    await press(el, 'ArrowDown');

    await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('/mcp__demo__summarize ');
    expect(el.querySelector(MENU)).toBeNull();
  });

  it('dismisses the menu on Escape', async () => {
    const el = await mount();
    await type(el, '/mc');
    expect(el.querySelector(MENU)).not.toBeNull();

    await press(el, 'Escape');

    expect(el.querySelector(MENU)).toBeNull();
    // Escape dismissed the menu; it must not have eaten the typed text.
    expect(textareaOf(el).value).toBe('/mc');
  });

  it('keeps Enter as send while the menu is open — it never commits', async () => {
    const el = await mount();
    const onSend = vi.fn();
    el.addEventListener('send', onSend);
    await type(el, '/mc');

    await press(el, 'Enter');

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].detail.text).toBe('/mc');
  });

  it('scrolls the highlighted item into view with block: "nearest" when highlight moves (C1)', async () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');
    const el = await mount();

    await type(el, '/');
    expect(suggested(el)).toEqual(['help', 'mcp__demo__summarize']);

    scrollSpy.mockClear();

    await press(el, 'ArrowDown');
    expect(highlightedIndex(el)).toBe(1);

    const targetRow = el.querySelector(`${ROW}[data-command="mcp__demo__summarize"]`);
    expect(targetRow).not.toBeNull();
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenLastCalledWith({ block: 'nearest' });
    expect(vi.mocked(scrollSpy).mock.contexts[0]).toBe(targetRow);
  });

  it('scrolls to the last item when highlight wraps on ArrowUp from index 0 (C2)', async () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');
    const el = await mount();

    await type(el, '/');
    expect(suggested(el)).toEqual(['help', 'mcp__demo__summarize']);
    expect(highlightedIndex(el)).toBe(0);

    scrollSpy.mockClear();

    await press(el, 'ArrowUp');
    expect(highlightedIndex(el)).toBe(1);

    const targetRow = el.querySelector(`${ROW}[data-command="mcp__demo__summarize"]`);
    expect(targetRow).not.toBeNull();
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenLastCalledWith({ block: 'nearest' });
    expect(vi.mocked(scrollSpy).mock.contexts[0]).toBe(targetRow);
  });
});

describe('chat-input existing send/stop behaviour (menu closed)', () => {
  it('dispatches send with {text, attachments} on Enter', async () => {
    const el = await mount();
    const onSend = vi.fn();
    el.addEventListener('send', onSend);
    await type(el, 'hello there');

    await press(el, 'Enter');

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].detail).toEqual({
      text: 'hello there',
      attachments: [],
    });
  });

  it('does not send on Shift+Enter', async () => {
    const el = await mount();
    const onSend = vi.fn();
    el.addEventListener('send', onSend);
    await type(el, 'hello there');

    await press(el, 'Enter', { shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(textareaOf(el).value).toBe('hello there');
  });

  it('leaves Tab alone so focus can still leave the composer', async () => {
    const el = await mount();
    await type(el, 'hello there');
    expect(el.querySelector(MENU)).toBeNull();

    const ev = await press(el, 'Tab');

    // An unconditional `if (e.key === 'Tab') e.preventDefault()` at the top of
    // the keydown handler satisfies every autocomplete case above while
    // permanently trapping keyboard focus in the textarea.
    expect(ev.defaultPrevented).toBe(false);
    expect(textareaOf(el).value).toBe('hello there');
  });

  it('dispatches stop on Escape while busy', async () => {
    const el = await mount({ busy: true });
    const onStop = vi.fn();
    el.addEventListener('stop', onStop);
    await type(el, 'hello there');

    await press(el, 'Escape');

    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
