import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { Schema } from 'prosemirror-model';
import { EditorState } from 'prosemirror-state';
import { wikiLinkMarkdown } from '../lib/milkdown-wiki-link.js';

// Same override strategy as wiki-editor.test.js — only Milkdown's `Editor`
// is stubbed, the rest of the kit stays real. The stub's `action` also runs
// the callback with a test-provided ctx, the way the real editor does: the
// link-popover code fetches the ProseMirror view via
// `editor.action(ctx => ctx.get(editorViewCtx))`.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  const kitEvc = /** @type {any} */ (actual).editorViewCtx;
  /** @type {{actions: unknown[], view: unknown, destroy: () => void}[]} */
  const editors = [];
  const make = () => {
    /** @type {any} */
    const editor = {
      actions: [],
      view: null,
      action(fn) {
        editor.actions.push(fn);
        fn({
          get: (/** @type {unknown} */ slice) => (slice === kitEvc ? editor.view : null),
        });
      },
      destroy: () => {},
    };
    const chainable = {
      config: () => chainable,
      use: () => chainable,
      create: async () => { editors.push(editor); return editor; },
    };
    return chainable;
  };
  return { ...actual, Editor: { make }, __editors: editors };
});

await import('./wiki-editor.js');
/** Editors built by the stub above, in mount order. */
const { __editors: editors } = /** @type {any} */ (await import('@milkdown/kit'));

// ── Fixtures ──────────────────────────────────────────────────────────

/** Test schema: the same wiki_link mark shape as milkdown-wiki-link.js. */
function makeSchema() {
  return new Schema({
    nodes: {
      doc: { content: 'block+' },
      paragraph: { content: 'inline*', group: 'block' },
      text: { group: 'inline' },
    },
    marks: {
      wiki_link: { attrs: { target: { default: null } } },
    },
  });
}

/**
 * @param {ReturnType<typeof makeSchema>} schema
 * @param {{target: string | null, display: string}} link
 */
function makeDoc(schema, link) {
  const mark = schema.marks.wiki_link;
  const children = [schema.text('before ', [])];
  if (link.display) {
    children.push(schema.text(link.display, [mark.create(link.target ? { target: link.target } : {})]));
  }
  children.push(schema.text(' after', []));
  return schema.node('doc', null, [schema.node('paragraph', null, children)]);
}

/**
 * Mirror the doc in the DOM the way the wiki-link mark's toDOM renders it,
 * so the component's closest('a.wiki-link') and posAtDOM see production shape.
 * @param {HTMLElement} root
 * @param {{target: string | null, display: string}} link
 * @returns {HTMLElement} the paragraph
 */
function renderLinkDom(root, link) {
  const p = document.createElement('p');
  p.append(document.createTextNode('before '));
  if (link.display) {
    const a = document.createElement('a');
    a.className = 'wiki-link';
    if (link.target) a.setAttribute('data-wiki-page', link.target);
    a.append(document.createTextNode(link.display));
    p.append(a);
  }
  p.append(document.createTextNode(' after'));
  root.append(p);
  return p;
}

/**
 * A fake ProseMirror view over a real EditorState: real schema, doc, and
 * transactions; only `posAtDOM` (which needs a real layout) and `dispatch`
 * (intercepted instead of letting PM rewrite the jsdom tree) stand in.
 * @param {any} doc
 * @param {HTMLElement} domParagraph
 */
function makeFakeView(doc, domParagraph) {
  let state = EditorState.create({ doc });
  /** @type {any} */
  const view = {
    get state() { return state; },
    /** @type {unknown[]} */
    dispatches: [],
    dispatch(/** @type {any} */ tr) {
      view.dispatches.push(tr);
      state = state.apply(tr);
    },
    focus() {},
    posAtDOM(/** @type {Node} */ domNode) {
      // The fixture is one paragraph of inline content; walk it in
      // document order and return where domNode's content begins.
      let pos = 1; // doc(0) + paragraph boundary
      for (const child of Array.from(domParagraph.childNodes)) {
        if (child === domNode) return pos;
        pos += (child.textContent || '').length;
      }
      throw new Error('posAtDOM: node is not in the fixture paragraph');
    },
  };
  return view;
}

/** The text node carrying the wiki_link mark, or null. */
function markedTextNode(doc) {
  /** @type {any | null} */
  let found = null;
  doc.descendants((/** @type {any} */ n) => {
    if (n.isText && n.marks.some((/** @type {any} */ m) => m.type.name === 'wiki_link')) found = n;
    return true;
  });
  return found;
}

async function mountEditor() {
  const el = /** @type {import('./wiki-editor.js').WikiEditor} */ (document.createElement('wiki-editor'));
  el.page = 'Test Page';
  el.content = '';
  document.body.appendChild(el);
  await el.updateComplete;
  // firstUpdated builds the editor asynchronously, outside the update cycle.
  await vi.waitFor(() => expect(editors).toHaveLength(1));
  return el;
}

/**
 * Wire the fixture: doc + mirrored DOM + fake view on the stub editor.
 * @param {import('./wiki-editor.js').WikiEditor} el
 * @param {ReturnType<typeof makeSchema>} schema
 * @param {{target: string | null, display: string}} link
 * @returns {any} the fake view
 */
function setupFixture(el, schema, link) {
  const doc = makeDoc(schema, link);
  /** @type {HTMLElement} */
  const root = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root'));
  const p = renderLinkDom(root, link);
  const fakeView = makeFakeView(doc, p);
  /** @type {any} */ (editors[0]).view = fakeView;
  return fakeView;
}

function singleClick(/** HTMLElement */ link) {
  const evt = new MouseEvent('click', { bubbles: true, cancelable: true });
  link.dispatchEvent(evt);
  return evt;
}

function setField(/** string */ name, /** string */ value) {
  const input = /** @type {HTMLInputElement} */ (
    document.querySelector(`.wiki-link-popover input[data-field="${name}"]`));
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function clickButton(/** string */ selector) {
  /** @type {HTMLButtonElement} */ (document.querySelector(selector)).click();
}

/** Open the popover (waits out the single/double-click delay). */
async function openPopover(/** import('./wiki-editor.js').WikiEditor */ el, /** HTMLElement */ link) {
  singleClick(link);
  await vi.waitFor(() => expect(el._linkEdit).not.toBeNull());
}

describe('wiki-editor wiki-link popover', () => {
  /** @type {import('./wiki-editor.js').WikiEditor} */
  let el;
  /** @type {ReturnType<typeof makeSchema>} */
  let schema;
  /** @type {any} */
  let fakeView;
  /** @type {HTMLElement} */
  let link;

  beforeEach(async () => {
    editors.length = 0;
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ modified: 1 }),
    })));
    el = await mountEditor();
    schema = makeSchema();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('opens on single click with the current target and display', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);

    const target = /** @type {HTMLInputElement} */ (document.querySelector('.wiki-link-popover input[data-field="target"]'));
    const display = /** @type {HTMLInputElement} */ (document.querySelector('.wiki-link-popover input[data-field="display"]'));
    expect(target.value).toBe('Old Target');
    expect(display.value).toBe('Old Display');
    // The popover is portaled to document.body, not inside the component.
    expect(el.contains(document.querySelector('.wiki-link-popover'))).toBe(false);
  });

  it('shows the display text as the target for plain [[links]]', async () => {
    setupFixture(el, schema, { target: null, display: 'Plain' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);

    const target = /** @type {HTMLInputElement} */ (document.querySelector('.wiki-link-popover input[data-field="target"]'));
    const display = /** @type {HTMLInputElement} */ (document.querySelector('.wiki-link-popover input[data-field="display"]'));
    expect(target.value).toBe('Plain');
    expect(display.value).toBe('Plain');
  });

  it('prevents default on the link click and does not navigate', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    const evt = singleClick(link);
    expect(evt.defaultPrevented).toBe(true);

    // Past the open delay: the popover came up, and no navigation happened.
    await vi.waitFor(() => expect(el._linkEdit).not.toBeNull());
    expect(pages).toEqual([]);
  });

  it('stops propagation so a document-level link interceptor never fires', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    let docSawLinkClick = 0;
    const spy = (/** @type {Event} */ e) => {
      const t = /** @type {Element | null} */ (e.target);
      if (t?.closest?.('a.wiki-link')) docSawLinkClick += 1;
    };
    document.addEventListener('click', spy);
    try {
      singleClick(link);
    } finally {
      document.removeEventListener('click', spy);
    }
    expect(docSawLinkClick).toBe(0);
  });

  it('apply updates target and display and serializes as [[t|d]]', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    setField('target', 'New Target');
    setField('display', 'New Display');
    clickButton('.wiki-link-popover-apply');

    expect(el._linkEdit).toBeNull();
    expect(fakeView.dispatches).toHaveLength(1);
    const marked = markedTextNode(fakeView.state.doc);
    expect(marked.text).toBe('New Display');
    expect(marked.marks[0].attrs.target).toBe('New Target');
    expect(wikiLinkMarkdown(marked.marks[0].attrs.target, marked.text))
      .toBe('[[New Target|New Display]]');
  });

  it('apply with target equal to display stores the plain [[text]] form', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    setField('target', 'Same');
    setField('display', 'Same');
    clickButton('.wiki-link-popover-apply');

    const marked = markedTextNode(fakeView.state.doc);
    expect(marked.text).toBe('Same');
    expect(marked.marks[0].attrs.target).toBeNull();
    expect(wikiLinkMarkdown(marked.marks[0].attrs.target, marked.text)).toBe('[[Same]]');
  });

  it('apply with an empty target drops the target attribute', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    setField('target', '');
    clickButton('.wiki-link-popover-apply');

    const marked = markedTextNode(fakeView.state.doc);
    expect(marked.text).toBe('Old Display');
    expect(marked.marks[0].attrs.target).toBeNull();
    expect(wikiLinkMarkdown(marked.marks[0].attrs.target, marked.text)).toBe('[[Old Display]]');
  });

  it('refuses to apply an empty display and keeps the popover open', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    setField('display', '   ');
    clickButton('.wiki-link-popover-apply');

    expect(el._linkEdit).not.toBeNull();
    expect(fakeView.dispatches).toHaveLength(0);
  });

  it('cancel discards the edit and leaves the document untouched', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    const original = fakeView.state.doc;

    await openPopover(el, link);
    setField('target', 'New Target');
    setField('display', 'New Display');
    clickButton('.wiki-link-popover .secondary');

    expect(el._linkEdit).toBeNull();
    expect(fakeView.dispatches).toHaveLength(0);
    expect(fakeView.state.doc.eq(original)).toBe(true);
  });

  it('Escape closes the popover without applying', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    const popover = /** @type {HTMLElement} */ (document.querySelector('.wiki-link-popover'));
    popover.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Escape', bubbles: true, cancelable: true,
    }));

    expect(el._linkEdit).toBeNull();
    expect(fakeView.dispatches).toHaveLength(0);
  });

  it('Enter in a field applies the edit', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    setField('target', 'New Target');
    const target = /** @type {HTMLInputElement} */ (
      document.querySelector('.wiki-link-popover input[data-field="target"]'));
    target.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', bubbles: true, cancelable: true,
    }));

    expect(el._linkEdit).toBeNull();
    expect(fakeView.dispatches).toHaveLength(1);
    const marked = markedTextNode(fakeView.state.doc);
    expect(marked.marks[0].attrs.target).toBe('New Target');
    expect(marked.text).toBe('Old Display');
  });

  it('a click outside the popover closes it', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    document.body.click();

    expect(el._linkEdit).toBeNull();
  });

  it('double click still dispatches wiki-navigate and opens no popover', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    // A double click is two clicks plus the dblclick event.
    singleClick(link);
    singleClick(link);
    link.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));

    expect(pages).toEqual(['Old Target']);
    // Past the open delay: the pending popover was cancelled, not shown.
    // (Negative assertion — nothing to await, so a bounded wait is the
    // honest check here.)
    await new Promise(r => setTimeout(r, 350));
    expect(el._linkEdit).toBeNull();
  });

  it('double click closes an already-open popover', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    expect(el._linkEdit).not.toBeNull();
    link.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));

    expect(el._linkEdit).toBeNull();
  });
});
