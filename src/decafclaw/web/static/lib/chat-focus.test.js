import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { shouldFocusChatInput } from './chat-focus.js';

/**
 * Build a chat-input stand-in containing a textarea, plus a sibling input
 * that stands in for "somewhere else the user is typing" (canvas terminal,
 * wiki editor, ...).
 */
function setupDom() {
  document.body.innerHTML = `
    <div id="chat-input"><textarea id="composer"></textarea></div>
    <div id="canvas"><textarea id="terminal"></textarea></div>
  `;
  return {
    chatInputEl: document.getElementById('chat-input'),
    composer: /** @type {HTMLTextAreaElement} */ (document.getElementById('composer')),
    terminal: /** @type {HTMLTextAreaElement} */ (document.getElementById('terminal')),
  };
}

describe('shouldFocusChatInput', () => {
  /** @type {ReturnType<typeof setupDom>} */
  let dom;

  beforeEach(() => { dom = setupDom(); });
  afterEach(() => { document.body.innerHTML = ''; });

  const base = () => ({
    readOnly: false,
    convChanged: false,
    turnFinished: false,
    chatInputEl: dom.chatInputEl,
  });

  it('does not focus on an ordinary change (streamed chunk, tool status)', () => {
    // The regression this guards: the store emits `change` after every
    // WebSocket message, so "a conversation is open" must not imply "focus".
    expect(shouldFocusChatInput(base())).toBe(false);
  });

  it('does not focus mid-turn while the user is typing elsewhere', () => {
    dom.terminal.focus();
    expect(shouldFocusChatInput(base())).toBe(false);
  });

  it('focuses when the agent finishes a turn and focus is unclaimed', () => {
    expect(shouldFocusChatInput({ ...base(), turnFinished: true })).toBe(true);
  });

  it('does not steal focus at turn end when the user is typing elsewhere', () => {
    dom.terminal.focus();
    expect(document.activeElement).toBe(dom.terminal);
    expect(shouldFocusChatInput({ ...base(), turnFinished: true })).toBe(false);
  });

  it('still focuses at turn end when focus is already inside the chat input', () => {
    dom.composer.focus();
    expect(shouldFocusChatInput({ ...base(), turnFinished: true })).toBe(true);
  });

  it('focuses on conversation switch', () => {
    expect(shouldFocusChatInput({ ...base(), convChanged: true })).toBe(true);
  });

  it('focuses on conversation switch even from a focused element elsewhere', () => {
    // Switching conversations is an explicit navigation — landing in the
    // composer is the point, and the click that caused it may have left
    // focus on a sidebar control.
    dom.terminal.focus();
    expect(shouldFocusChatInput({ ...base(), convChanged: true })).toBe(true);
  });

  it('never focuses a read-only conversation', () => {
    expect(shouldFocusChatInput({ ...base(), readOnly: true, convChanged: true })).toBe(false);
    expect(shouldFocusChatInput({ ...base(), readOnly: true, turnFinished: true })).toBe(false);
  });

  it('tolerates a missing chat input element', () => {
    dom.composer.focus();
    // No element to compare containment against — the focused composer now
    // reads as "somewhere else", so a turn end must not yank it.
    expect(shouldFocusChatInput({ ...base(), chatInputEl: null, turnFinished: true })).toBe(false);
  });
});
