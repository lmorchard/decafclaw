import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './side-question-drawer.js';
import './chat-input.js';

describe('SideQuestionDrawer (#1141)', () => {
  /** @type {any} */
  let drawer;
  /** @type {any} */
  let chatInput;

  beforeEach(() => {
    drawer = document.createElement('side-question-drawer');
    drawer.convId = 'conv-test-1';
    document.body.appendChild(drawer);

    chatInput = document.createElement('chat-input');
    document.body.appendChild(chatInput);
  });

  afterEach(() => {
    drawer.remove();
    chatInput.remove();
    vi.restoreAllMocks();
  });

  it('renders nothing when closed, renders drawer when open', async () => {
    expect(drawer.open).toBe(false);
    expect(drawer.querySelector('.drawer-header')).toBeNull();

    drawer.openDrawer();
    await drawer.updateComplete;

    expect(drawer.open).toBe(true);
    expect(drawer.querySelector('.drawer-header')).not.toBeNull();
    expect(drawer.querySelector('.drawer-title')?.textContent).toContain('Side Question');

    drawer.closeDrawer();
    await drawer.updateComplete;

    expect(drawer.open).toBe(false);
    expect(drawer.querySelector('.drawer-header')).toBeNull();
  });

  it('toggles open state', async () => {
    expect(drawer.open).toBe(false);
    drawer.toggle();
    await drawer.updateComplete;
    expect(drawer.open).toBe(true);

    drawer.toggle();
    await drawer.updateComplete;
    expect(drawer.open).toBe(false);
  });

  it('streams explanation response into ephemeral thread', async () => {
    drawer.openDrawer();
    await drawer.updateComplete;

    const sseChunks = [
      'event: chunk\ndata: {"type":"chunk","text":"Hello "}\n\n',
      'event: chunk\ndata: {"type":"chunk","text":"world!"}\n\n',
      'event: done\ndata: {"type":"done","text":"Hello world!"}\n\n',
    ];

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        for (const chunk of sseChunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      /** @type {any} */ ({
        ok: true,
        body: stream,
      })
    );

    await drawer.sendQuestion('What does this function do?');
    await drawer.updateComplete;

    expect(drawer._thread.length).toBe(2);
    expect(drawer._thread[0]).toEqual({
      role: 'user',
      content: 'What does this function do?',
    });
    expect(drawer._thread[1]).toEqual({
      role: 'assistant',
      content: 'Hello world!',
    });

    const userMsg = drawer.querySelector('.side-user-msg');
    expect(userMsg?.textContent).toContain('What does this function do?');

    const assistantMsg = drawer.querySelector('.side-assistant-msg');
    expect(assistantMsg?.textContent).toContain('Hello world!');
  });

  it('copies explanation to main chat composer without auto-submitting (criterion C5)', async () => {
    drawer.openDrawer();
    drawer._thread = [
      { role: 'user', content: 'What is this?' },
      { role: 'assistant', content: 'This is a test explanation.' },
    ];
    await drawer.updateComplete;
    await chatInput.updateComplete;

    const sendEventSpy = vi.fn();
    document.addEventListener('send', sendEventSpy);

    const sendToMainBtn = drawer.querySelector('.send-to-main-btn');
    expect(sendToMainBtn).not.toBeNull();

    sendToMainBtn.click();
    await drawer.updateComplete;

    const textarea = chatInput.querySelector('textarea');
    expect(textarea?.value).toBe('This is a test explanation.');

    // Crucial: verify that clicking 'Send to main conversation' does NOT auto-submit
    expect(sendEventSpy).not.toHaveBeenCalled();
  });

  it('allows stopping an in-flight side question without affecting main turn', async () => {
    drawer.openDrawer();
    await drawer.updateComplete;

    let aborted = false;
    const stream = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('event: chunk\ndata: {"type":"chunk","text":"Partial..."}\n\n'));
      },
      cancel() {
        aborted = true;
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation((url, opts) => {
      opts?.signal?.addEventListener('abort', () => {
        aborted = true;
      });
      return Promise.resolve(/** @type {any} */ ({
        ok: true,
        body: stream,
      }));
    });

    const promise = drawer.sendQuestion('Long query');
    await drawer.updateComplete;

    drawer.stopGeneration();
    await promise;
    await drawer.updateComplete;

    expect(aborted).toBe(true);
    expect(drawer._thread.some((m) => m.content.includes('[cancelled]'))).toBe(true);
  });

  it('passes side-topic history for follow-up questions', async () => {
    drawer.openDrawer();
    drawer._thread = [
      { role: 'user', content: 'Initial question' },
      { role: 'assistant', content: 'Initial answer' },
    ];
    await drawer.updateComplete;

    let requestBody = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation((url, opts) => {
      requestBody = JSON.parse(opts?.body);
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('event: done\ndata: {"type":"done","text":"Follow-up answer"}\n\n'));
          controller.close();
        },
      });
      return Promise.resolve(/** @type {any} */ ({
        ok: true,
        body: stream,
      }));
    });

    await drawer.sendQuestion('Follow-up question');

    expect(requestBody).not.toBeNull();
    expect(requestBody.question).toBe('Follow-up question');
    expect(requestBody.side_history).toEqual([
      { role: 'user', content: 'Initial question' },
      { role: 'assistant', content: 'Initial answer' },
    ]);
  });

  it('clears side question thread on clearThread()', async () => {
    drawer.openDrawer();
    drawer._thread = [
      { role: 'user', content: 'Q1' },
      { role: 'assistant', content: 'A1' },
    ];
    await drawer.updateComplete;

    expect(drawer._thread.length).toBe(2);
    drawer.clearThread();
    await drawer.updateComplete;

    expect(drawer._thread.length).toBe(0);
    expect(drawer.querySelector('.empty-hint')).not.toBeNull();
  });
});
