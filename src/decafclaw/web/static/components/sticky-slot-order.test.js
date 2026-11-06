import { describe, expect, it } from 'vitest';
import indexSource from '../index.html?raw';

// Regression coverage for #1215: the pinned sticky widget slot must render
// above the conversation control strip, which in turn sits directly above the
// chat input. The ordering is static markup in index.html — #chat-main is a
// plain `display: flex; flex-direction: column` (styles/layout.css) with no
// `order` / `column-reverse` / grid anywhere, so source order is visual order.
describe('sticky slot ordering (#1215)', () => {
  it('renders the sticky slot above the conversation controls and below the chat view', () => {
    const doc = new DOMParser().parseFromString(indexSource, 'text/html');
    const chatView = doc.querySelector('chat-view');
    const stickySlot = doc.querySelector('sticky-slot');
    const chatControls = doc.querySelector('chat-controls');
    const chatInput = doc.querySelector('chat-input');

    expect(chatView).toBeTruthy();
    expect(stickySlot).toBeTruthy();
    expect(chatControls).toBeTruthy();
    expect(chatInput).toBeTruthy();

    // Desired: chat-view -> sticky-slot -> chat-controls -> chat-input.
    expect(stickySlot.compareDocumentPosition(chatControls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chatControls.compareDocumentPosition(chatInput) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chatView.compareDocumentPosition(stickySlot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(stickySlot.compareDocumentPosition(chatInput) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
