import { afterEach, describe, expect, it, vi } from 'vitest';

import { handleConversationLinkClick } from './conversation-links.js';

/** @type {HTMLElement[]} */
const mounted = [];
/** @type {((event: MouseEvent) => void)|null} */
let listener = null;

afterEach(() => {
  mounted.splice(0).forEach((el) => el.remove());
  if (listener) document.removeEventListener('click', listener);
  listener = null;
});

/**
 * Mount an anchor and route its clicks through the handler.
 * @param {{className?: string, attrs?: Record<string, string>, href?: string, parent?: HTMLElement}} opts
 * @returns {{anchor: HTMLAnchorElement, handlers: {showWikiPage: ReturnType<typeof vi.fn>, showFilePage: ReturnType<typeof vi.fn>}, events: MouseEvent[]}}
 */
function mount(opts = {}) {
  const handlers = {
    showWikiPage: vi.fn(),
    showFilePage: vi.fn(),
  };
  const anchor = document.createElement('a');
  anchor.textContent = 'link';
  if (opts.className) anchor.className = opts.className;
  if (opts.href) anchor.href = opts.href;
  for (const [name, value] of Object.entries(opts.attrs || {})) {
    anchor.setAttribute(name, value);
  }
  (opts.parent || document.body).append(anchor);
  mounted.push(anchor);

  /** @type {MouseEvent[]} */
  const events = [];
  listener = (event) => {
    events.push(event);
    handleConversationLinkClick(event, handlers);
  };
  document.addEventListener('click', listener);
  return { anchor, handlers, events };
}

/**
 * @param {Element} el
 * @param {MouseEventInit} [init]
 */
function click(el, init = {}) {
  el.dispatchEvent(new MouseEvent('click', {
    bubbles: true,
    cancelable: true,
    button: 0,
    ...init,
  }));
}

describe('conversation link routing', () => {
  it('opens a vault page in the current session on a plain click', () => {
    const { anchor, handlers, events } = mount({
      className: 'wiki-link',
      attrs: { 'data-wiki-page': 'Notes/My Page' },
      href: '/vault/Notes/My%20Page',
    });
    click(anchor);
    expect(handlers.showWikiPage).toHaveBeenCalledWith('Notes/My Page');
    expect(handlers.showFilePage).not.toHaveBeenCalled();
    expect(events[0].defaultPrevented).toBe(true);
  });

  it('opens a workspace file in the current session on a plain click', () => {
    const { anchor, handlers, events } = mount({
      className: 'workspace-link',
      attrs: { 'data-workspace-path': 'drafts/blog-post.md' },
      href: '/api/workspace/drafts/blog-post.md',
    });
    click(anchor);
    expect(handlers.showFilePage).toHaveBeenCalledWith('drafts/blog-post.md');
    expect(handlers.showWikiPage).not.toHaveBeenCalled();
    expect(events[0].defaultPrevented).toBe(true);
  });

  it('handles a click on a child of the link', () => {
    const { anchor, handlers } = mount({
      className: 'workspace-link',
      attrs: { 'data-workspace-path': 'notes.md' },
      href: '/api/workspace/notes.md',
    });
    const child = document.createElement('strong');
    child.textContent = 'notes';
    anchor.replaceChildren(child);
    click(child);
    expect(handlers.showFilePage).toHaveBeenCalledWith('notes.md');
  });

  it('leaves modified clicks to the browser', () => {
    const { anchor, handlers, events } = mount({
      className: 'workspace-link',
      attrs: { 'data-workspace-path': 'notes.md' },
    });
    click(anchor, { metaKey: true });
    expect(handlers.showFilePage).not.toHaveBeenCalled();
    expect(events[0].defaultPrevented).toBe(false);

    click(anchor, { ctrlKey: true });
    expect(handlers.showFilePage).not.toHaveBeenCalled();
  });

  it('leaves wiki links inside the wiki pane to wiki-page', () => {
    const pane = document.createElement('div');
    pane.id = 'wiki-main';
    document.body.append(pane);
    mounted.push(pane);
    const { anchor, handlers, events } = mount({
      className: 'wiki-link',
      attrs: { 'data-wiki-page': 'Other Page' },
      parent: pane,
    });
    click(anchor);
    expect(handlers.showWikiPage).not.toHaveBeenCalled();
    expect(events[0].defaultPrevented).toBe(false);
  });

  it('ignores ordinary links and link-less clicks', () => {
    const { anchor, handlers, events } = mount({});
    click(anchor);
    expect(handlers.showWikiPage).not.toHaveBeenCalled();
    expect(handlers.showFilePage).not.toHaveBeenCalled();
    expect(events[0].defaultPrevented).toBe(false);
  });
});
