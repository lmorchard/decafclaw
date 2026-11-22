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
import { STORAGE_KEY } from '../lib/context-provenance-preference.js';

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

  it('sends from the merged primary action when idle', async () => {
    const el = await mount();
    const onSend = vi.fn();
    const onStop = vi.fn();
    el.addEventListener('send', onSend);
    el.addEventListener('stop', onStop);
    await type(el, 'hello there');

    el.querySelector('.composer-primary-btn').click();
    await el.updateComplete;

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].detail.text).toBe('hello there');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('stops from the merged primary action while busy and empty', async () => {
    const el = await mount({ busy: true });
    const onSend = vi.fn();
    const onStop = vi.fn();
    el.addEventListener('send', onSend);
    el.addEventListener('stop', onStop);

    el.querySelector('.composer-primary-btn').click();
    await el.updateComplete;

    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('steers from the merged primary action while busy with text', async () => {
    const el = await mount({ busy: true });
    const onSend = vi.fn();
    const onStop = vi.fn();
    el.addEventListener('send', onSend);
    el.addEventListener('stop', onStop);
    await type(el, 'one more thing');

    el.querySelector('.composer-primary-btn').click();
    await el.updateComplete;

    expect(onStop).not.toHaveBeenCalled();
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].detail).toEqual({
      text: 'one more thing',
      attachments: [],
    });
  });

  it('steers from the merged primary action while busy with attachments only', async () => {
    const el = await mount({ busy: true });
    const onSend = vi.fn();
    const onStop = vi.fn();
    el.addEventListener('send', onSend);
    el.addEventListener('stop', onStop);
    el._pendingAttachments = [
      { filename: 'notes.txt', mime_type: 'text/plain', file: new File(['x'], 'notes.txt'), previewUrl: null },
    ];
    await el.updateComplete;

    el.querySelector('.composer-primary-btn').click();
    await el.updateComplete;

    expect(onStop).not.toHaveBeenCalled();
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].detail.attachments).toEqual([
      { filename: 'notes.txt', mime_type: 'text/plain', file: expect.any(File) },
    ]);
  });

  it('flips the primary affordance as soon as the busy composer gains or loses text', async () => {
    const el = await mount({ busy: true });
    /** @returns {HTMLButtonElement} */
    const button = () => el.querySelector('.composer-primary-btn');
    /** @returns {string} */
    const label = () => el.querySelector('.composer-primary-label').textContent;

    expect(label()).toBe('Stop');
    expect(button().classList.contains('stop-btn')).toBe(true);
    expect(button().getAttribute('aria-label')).toBe('Stop generation');
    expect(el.querySelector('.composer-primary-icon').textContent).toBe('\u25A0');

    await type(el, 's');

    expect(label()).toBe('Send');
    expect(button().classList.contains('stop-btn')).toBe(false);
    expect(button().getAttribute('aria-label')).toBe('Send message');
    expect(button().getAttribute('title')).toBe('Send message');
    expect(el.querySelector('.composer-primary-icon').textContent).toBe('\u27A4');

    await type(el, '');

    expect(label()).toBe('Stop');
    expect(button().classList.contains('stop-btn')).toBe(true);
    expect(button().getAttribute('aria-label')).toBe('Stop generation');
    expect(el.querySelector('.composer-primary-icon').textContent).toBe('\u25A0');
  });

  it('still stops on Escape while busy with text', async () => {
    const el = await mount({ busy: true });
    const onStop = vi.fn();
    el.addEventListener('stop', onStop);
    await type(el, 'one more thing');

    await press(el, 'Escape');

    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('offers a deliberate Stop in the conversation menu while busy with text', async () => {
    const el = await mount({ busy: true });
    const onSend = vi.fn();
    const onStop = vi.fn();
    el.addEventListener('send', onSend);
    el.addEventListener('stop', onStop);
    await type(el, 'one more thing');

    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;

    const item = [...el.querySelectorAll('.conversation-menu-item')]
      .find((b) => b.querySelector('.conversation-menu-label')?.textContent.trim() === 'Stop');
    expect(item).toBeTruthy();
    item.click();
    await el.updateComplete;

    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('omits the menu Stop when idle', async () => {
    const el = await mount();
    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;

    const labels = [...el.querySelectorAll('.conversation-menu-label')]
      .map((l) => l.textContent.trim());
    expect(labels).not.toContain('Stop');
  });
});

describe('chat-input message history navigation', () => {
  it('navigates backward through history on ArrowUp and forward on ArrowDown', async () => {
    const el = await mount();
    
    // Send two messages
    await type(el, 'first message');
    await press(el, 'Enter');
    await type(el, 'second message');
    await press(el, 'Enter');

    // Type a draft
    await type(el, 'current draft');
    expect(textareaOf(el).value).toBe('current draft');

    // ArrowUp goes to second message
    await press(el, 'ArrowUp');
    expect(textareaOf(el).value).toBe('second message');

    // ArrowUp goes to first message
    await press(el, 'ArrowUp');
    expect(textareaOf(el).value).toBe('first message');

    // ArrowUp stops at first message (no wrap)
    await press(el, 'ArrowUp');
    expect(textareaOf(el).value).toBe('first message');

    // ArrowDown goes back to second message
    await press(el, 'ArrowDown');
    expect(textareaOf(el).value).toBe('second message');

    // ArrowDown goes back to draft
    await press(el, 'ArrowDown');
    expect(textareaOf(el).value).toBe('current draft');

    // ArrowDown stops at draft
    await press(el, 'ArrowDown');
    expect(textareaOf(el).value).toBe('current draft');
  });

  it('only navigates when cursor is on the first/last line respectively', async () => {
    const el = await mount();
    await type(el, 'history message');
    await press(el, 'Enter');

    // Type a multi-line draft
    await type(el, 'line 1\nline 2');
    const ta = textareaOf(el);
    
    // Move cursor to end of line 2
    ta.selectionStart = ta.selectionEnd = ta.value.length;
    await press(el, 'ArrowUp');
    // Shouldn't navigate because not on first line
    expect(ta.value).toBe('line 1\nline 2');

    // Move cursor to line 1
    ta.selectionStart = ta.selectionEnd = 0;
    await press(el, 'ArrowUp');
    // Now it navigates
    expect(ta.value).toBe('history message');

    // Since 'history message' is single line, it's also the last line.
    // Move cursor to start of it.
    ta.selectionStart = ta.selectionEnd = 0;
    await press(el, 'ArrowDown');
    // Navigates forward because single line is also last line
    expect(ta.value).toBe('line 1\nline 2');
  });

  it('resets history navigation when sending a new message', async () => {
    const el = await mount();
    
    await type(el, 'first message');
    await press(el, 'Enter');

    await type(el, 'draft');
    await press(el, 'ArrowUp');
    expect(textareaOf(el).value).toBe('first message');

    // Send the history message again
    await press(el, 'Enter');
    expect(textareaOf(el).value).toBe('');

    // Send should reset draft and history index. ArrowUp should now show 'first message' again
    await press(el, 'ArrowUp');
    expect(textareaOf(el).value).toBe('first message');
  });
});

describe('chat-input mention autocomplete', () => {
  it('triggers autocomplete on "@" anywhere and fetches suggestions', async () => {
    vi.useFakeTimers();
    try {
      // Mock global fetch
      const mockResults = {
        results: [
          { type: 'file', id: 'src/agent.py', label: 'src/agent.py', description: 'Workspace File' },
          { type: 'vault', id: 'TestPage', label: 'TestPage', description: 'Vault Page' },
          { type: 'mcp', id: 'demo/notes', label: 'mcp/demo/notes', description: 'MCP Resource' }
        ]
      };
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
        JSON.stringify(mockResults),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ));

      const el = await mount();
      await type(el, 'Check @');

      // Fast-forward timers to trigger debounced fetch
      await vi.advanceTimersByTimeAsync(150);

      expect(fetchSpy).toHaveBeenCalledWith('/api/autocomplete?q=', expect.objectContaining({
        method: 'GET',
        credentials: 'same-origin',
      }));
      await el.updateComplete;

      expect(el.querySelector(MENU)).not.toBeNull();
      const rows = el.querySelectorAll(ROW);
      expect(rows).toHaveLength(3);
      expect(rows[0].getAttribute('data-mention-id')).toBe('src/agent.py');
      expect(rows[1].getAttribute('data-mention-id')).toBe('TestPage');
      expect(rows[2].getAttribute('data-mention-id')).toBe('demo/notes');
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores a response for a superseded mention query', async () => {
    vi.useFakeTimers();
    try {
      const pending = [];
      vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(resolve => pending.push(resolve)));
      const el = await mount();

      await type(el, '@old');
      await vi.advanceTimersByTimeAsync(150);
      await type(el, '@new');
      await vi.advanceTimersByTimeAsync(150);
      expect(pending).toHaveLength(2);

      pending[1](new Response(JSON.stringify({ results: [
        { type: 'file', id: 'new.md', label: 'new.md', description: 'Workspace File' },
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      await vi.waitFor(() => expect(el._mentionMatches[0]?.id).toBe('new.md'));

      pending[0](new Response(JSON.stringify({ results: [
        { type: 'file', id: 'old.md', label: 'old.md', description: 'Workspace File' },
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      await Promise.resolve();
      expect(el._mentionMatches.map(item => item.id)).toEqual(['new.md']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('inserts correct syntax when selecting a file mention', async () => {
    const el = await mount();
    await type(el, 'Check @');
    el._mentionMatches = [
      { type: 'file', id: 'src/agent.py', label: 'src/agent.py', description: 'Workspace File' }
    ];
    await el.updateComplete;

    await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('Check @src/agent.py ');
    expect(el.querySelector(MENU)).toBeNull();
  });

  it('inserts bracketed syntax when selecting a vault page mention', async () => {
    const el = await mount();
    await type(el, 'See @');
    el._mentionMatches = [
      { type: 'vault', id: 'TestPage', label: 'TestPage', description: 'Vault Page' }
    ];
    await el.updateComplete;

    await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('See @[[TestPage]] ');
    expect(el.querySelector(MENU)).toBeNull();
  });

  it('inserts prefixed syntax when selecting an MCP resource mention', async () => {
    const el = await mount();
    await type(el, 'Inspect @');
    el._mentionMatches = [
      { type: 'mcp', id: 'demo/notes', label: 'mcp/demo/notes', description: 'MCP Resource' }
    ];
    await el.updateComplete;

    await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('Inspect @mcp/demo/notes ');
    expect(el.querySelector(MENU)).toBeNull();
  });

  it('does not insert an unknown completion variant as a file mention', async () => {
    const el = await mount();
    await type(el, 'Check @');
    el._mentionMatches = [
      { type: 'added', id: 'new-target', label: 'New target', description: 'New variant' }
    ];
    await el.updateComplete;

    await press(el, 'Tab');

    expect(textareaOf(el).value).toBe('Check @');
  });
});

describe('chat-input accessible button names (#166)', () => {
  /** @param {any} el @param {string} label */
  const byLabel = (el, label) => el.querySelector(`button[aria-label="${label}"]`);

  it('labels the send, attach, and conversation actions buttons', async () => {
    const el = await mount();

    expect(byLabel(el, 'Send message')?.querySelector('.composer-primary-label')?.textContent.trim()).toBe('Send');
    expect(byLabel(el, 'Attach file')?.classList.contains('attach-btn')).toBe(true);
    expect(byLabel(el, 'Conversation actions')?.classList.contains('conversation-actions-btn')).toBe(true);
  });

  it('merges stop into the single primary action while busy', async () => {
    const el = await mount({ busy: true });

    const primary = byLabel(el, 'Stop generation');
    expect(primary?.classList.contains('stop-btn')).toBe(true);
    expect(primary?.querySelector('.composer-primary-label')?.textContent.trim()).toBe('Stop');
    expect(el.querySelectorAll('.composer-primary-btn')).toHaveLength(1);
    expect(byLabel(el, 'Send message')).toBeNull();
  });

  it('labels the attachment remove button', async () => {
    const el = await mount();
    el._pendingAttachments = [{ filename: 'notes.txt' }];
    await el.updateComplete;

    expect(byLabel(el, 'Remove attachment')?.classList.contains('attachment-remove')).toBe(true);
  });

  it('no longer renders a side-question button in the composer (moved to the control bar, issue #1190)', async () => {
    const el = await mount();
    expect(byLabel(el, 'Ask side question')).toBeNull();
  });


  it('gives every rendered button a non-empty aria-label', async () => {
    const el = await mount({ busy: true });
    el._pendingAttachments = [{ filename: 'notes.txt' }];
    await el.updateComplete;

    const buttons = [...el.querySelectorAll('button')];
    // conversation-actions + attach + merged send/stop + attachment-remove (the
    // side-question entry moved to the chat-controls action cluster, issue
    // #1190; the conversation menu itself is a single toggle, issue #1192;
    // send and stop merged into one primary action, issue #1540).
    expect(buttons.length).toBe(4);
    for (const b of buttons) {
      expect(b.getAttribute('aria-label')?.trim()).toBeTruthy();
    }
  });
});

describe('chat-input conversation actions menu (issue #1192)', () => {
  /** Open the menu via the conversation actions toggle. @param {any} el */
  async function openMenu(el) {
    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;
    return el.querySelector('.conversation-menu');
  }

  it('renders a conversation actions button left of the attach button', async () => {
    const el = await mount();
    const btn = el.querySelector('.conversation-actions-btn');
    expect(btn).toBeTruthy();
    expect(btn.getAttribute('aria-label')).toBe('Conversation actions');
    const attach = el.querySelector('.attach-btn');
    expect(attach).toBeTruthy();
    // Left of the paperclip: the actions button precedes the attach button in
    // the input row's DOM order.
    expect(btn.compareDocumentPosition(attach) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders the conversation actions toggle with the ⋮ glyph', async () => {
    const el = await mount();
    const btn = el.querySelector('.conversation-actions-btn');
    expect(btn).toBeTruthy();
    expect(btn.textContent.trim()).toBe('⋮');
  });

  it('renders the menu closed until the conversation actions button is clicked', async () => {
    const el = await mount();
    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('opens a vertical menu when the conversation actions button is clicked', async () => {
    const el = await mount();
    const menu = await openMenu(el);
    expect(menu).not.toBeNull();
    expect(menu.classList.contains('conversation-menu')).toBe(true);
    expect(menu.getAttribute('aria-label')).toBe('Conversation actions');
    expect(el.querySelector('.conversation-actions-btn').getAttribute('aria-expanded')).toBe('true');
  });

  it.each([
    ['Context', 'toggle-context-inspector'],
    ['Canvas', 'conversation-action'],
    ['Terminal', 'conversation-action'],
    ['Side question', 'open-side-question'],
  ])('dispatches a %s menu action when its item is picked', async (label, eventName) => {
    const el = await mount();
    el.convId = 'c1';
    const handler = vi.fn();
    document.addEventListener(eventName, handler);
    const menu = await openMenu(el);

    const item = [...menu.querySelectorAll('.conversation-menu-item')]
      .find((b) => b.querySelector('.conversation-menu-label')?.textContent.trim() === label);
    expect(item).toBeTruthy();
    item.click();
    await el.updateComplete;

    document.removeEventListener(eventName, handler);
    expect(handler).toHaveBeenCalledTimes(1);
    if (eventName === 'conversation-action') {
      expect(handler.mock.calls[0][0].detail).toEqual({ action: label === 'Canvas' ? 'canvas' : 'terminal' });
    }
    if (eventName === 'toggle-context-inspector') {
      expect(handler.mock.calls[0][0].detail).toEqual({ convId: 'c1' });
    }
    // Picking an item closes the menu.
    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('renders the model picker and mode control inside the menu when the store provides options', async () => {
    const el = await mount();
    const store = /** @type {any} */ (new EventTarget());
    store.activeModel = 'm1';
    store.availableModels = ['m1', 'm2'];
    store.modelGroups = [{ name: 'Models', models: ['m1', 'm2'] }];
    store.activeMode = 'default';
    store.availableModes = [
      { name: 'default', description: 'Standard' },
      { name: 'dev', description: 'Development' },
    ];
    store.isBusy = false;
    el.store = store;
    await el.updateComplete;

    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;

    const menu = el.querySelector('.conversation-menu');
    expect(menu).not.toBeNull();

    const picker = /** @type {any} */ (menu.querySelector('model-picker'));
    expect(picker).toBeTruthy();
    expect(picker.inline).toBe(true);
    expect([...picker.querySelectorAll('.model-picker-option')].map((row) => row.dataset.model))
      .toEqual(['m1', 'm2']);
    expect(picker.querySelector('.model-picker-option.selected').dataset.model).toBe('m1');

    const selects = [...menu.querySelectorAll('select')];
    expect(selects).toHaveLength(1);
    const [modeSelect] = selects;
    expect(modeSelect.labels[0].textContent.trim()).toBe('Mode');
    expect(modeSelect.value).toBe('default');
    expect([...modeSelect.options].map((o) => o.value)).toEqual(['default', 'dev']);

    const modelHandler = vi.fn();
    const modeHandler = vi.fn();
    document.addEventListener('set-model', modelHandler);
    document.addEventListener('set-mode', modeHandler);

    const modelRow = [...picker.querySelectorAll('.model-picker-option')]
      .find((row) => row.dataset.model === 'm2');
    modelRow.click();
    modeSelect.value = 'dev';
    modeSelect.dispatchEvent(new Event('change', { bubbles: true }));
    await el.updateComplete;

    document.removeEventListener('set-model', modelHandler);
    document.removeEventListener('set-mode', modeHandler);

    expect(modelHandler).toHaveBeenCalledTimes(1);
    expect(modelHandler.mock.calls[0][0].detail).toEqual({ model: 'm2' });
    expect(modeHandler).toHaveBeenCalledTimes(1);
    expect(modeHandler.mock.calls[0][0].detail).toEqual({ mode: 'dev' });

    // Changing a control does not close the menu.
    expect(el.querySelector('.conversation-menu')).not.toBeNull();
  });

  it('falls back to defaultModel and updates when store activeModel changes', async () => {
    const el = await mount();
    const store = /** @type {any} */ (new EventTarget());
    store.activeModel = '';
    store.defaultModel = 'm2';
    store.availableModels = ['m1', 'm2'];
    store.modelGroups = [{ name: 'Models', models: ['m1', 'm2'] }];
    store.activeMode = 'default';
    store.availableModes = [{ name: 'default' }];
    store.isBusy = false;
    el.store = store;
    await el.updateComplete;

    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;

    const picker = /** @type {any} */ (el.querySelector('model-picker'));
    expect(picker.querySelector('.model-picker-option.selected').dataset.model).toBe('m2');

    // Switching conversation updates the picker
    store.activeModel = 'm1';
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(picker.querySelector('.model-picker-option.selected').dataset.model).toBe('m1');
  });

  it('updates the menu disabled state when only busy changes', async () => {
    const el = await mount();
    const store = /** @type {any} */ (new EventTarget());
    store.activeModel = 'm1';
    store.availableModels = ['m1', 'm2'];
    store.modelGroups = [{ name: 'Models', models: ['m1', 'm2'] }];
    store.activeMode = 'default';
    store.availableModes = [
      { name: 'default', description: 'Standard' },
      { name: 'dev', description: 'Development' },
    ];
    store.isBusy = false;
    el.store = store;
    await el.updateComplete;

    const menu = await openMenu(el);
    expect(menu).not.toBeNull();
    const picker = /** @type {any} */ (menu.querySelector('model-picker'));
    const modeSelect = menu.querySelector('#conversation-menu-mode');

    // Idle: both settings controls are enabled.
    expect(picker.disabled).toBe(false);
    expect(modeSelect.disabled).toBe(false);

    // Only busy moves — no other store field changes, so the re-render must
    // come from _isBusy itself being reactive.
    store.isBusy = true;
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;

    expect(picker.disabled).toBe(true);
    expect(modeSelect.disabled).toBe(true);

    store.isBusy = false;
    store.dispatchEvent(new Event('change'));
    await el.updateComplete;

    expect(picker.disabled).toBe(false);
    expect(modeSelect.disabled).toBe(false);
  });

  it('moves attach into the menu and opens the file picker (#1540)', async () => {
    const el = await mount();
    const input = /** @type {HTMLInputElement} */ (el.querySelector('#file-input'));
    const clickSpy = vi.spyOn(input, 'click');
    const menu = await openMenu(el);

    const item = [...menu.querySelectorAll('.conversation-menu-item')]
      .find((b) => b.querySelector('.conversation-menu-label')?.textContent.trim() === 'Attach file');
    expect(item).toBeTruthy();
    item.click();
    await el.updateComplete;

    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('closes the menu on Escape', async () => {
    const el = await mount();
    const menu = await openMenu(el);
    expect(menu).not.toBeNull();

    el.querySelector('.conversation-actions-btn').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
    await el.updateComplete;

    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('closes the menu on outside click', async () => {
    const el = await mount();
    await openMenu(el);
    expect(el.querySelector('.conversation-menu')).not.toBeNull();

    document.body.click();
    await el.updateComplete;

    expect(el.querySelector('.conversation-menu')).toBeNull();
  });

  it('persists and broadcasts the context provenance preference (#1563)', async () => {
    localStorage.removeItem(STORAGE_KEY);
    const el = await mount();
    const menu = await openMenu(el);

    const toggle = /** @type {HTMLInputElement} */ (menu.querySelector('#conversation-menu-context-provenance'));
    expect(toggle).toBeTruthy();
    expect(menu.querySelector('.conversation-menu-toggle').textContent).toContain(
      'Show context capsules',
    );
    expect(toggle.checked).toBe(true);

    const handler = vi.fn();
    document.addEventListener('context-provenance-preference-changed', handler);

    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    await el.updateComplete;

    document.removeEventListener('context-provenance-preference-changed', handler);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false');
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ enabled: false });
  });
});
