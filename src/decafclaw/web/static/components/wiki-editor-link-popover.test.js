import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { Schema } from 'prosemirror-model';
import { EditorState } from 'prosemirror-state';
import { wikiLinkMarkdown } from '../lib/milkdown-wiki-link.js';

// Same override strategy as wiki-editor.test.js — only Milkdown's `Editor`
// is stubbed, the rest of the kit stays real. The stub's `action` also runs
// the callback with a test-provided ctx, the way the real editor does: the
// link-popover code fetches the ProseMirror view via
// `editor.action(ctx => ctx.get(editorViewCtx))`. The stub also captures
// the `config` callback so a test can wire the `markdownUpdated` listener
// (`ctx.get(listenerCtx)`) exactly as the real editor would.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  const kit = /** @type {any} */ (actual);
  /** @type {any[]} */
  const editors = [];
  const make = () => {
    /** @type {any} */
    const editor = {
      actions: [],
      view: null,
      /** @type {((ctx: unknown) => void) | null} */
      configFn: null,
      /** @type {((ctx: unknown, md: unknown, prev: unknown) => void)[]} */
      mdListeners: [],
      getCtx() {
        return {
          set: () => {},
          get: (/** @type {unknown} */ slice) => {
            if (slice === kit.editorViewCtx) return editor.view;
            if (slice === kit.listenerCtx) {
              return {
                markdownUpdated: (/** @type {any} */ cb) => editor.mdListeners.push(cb),
              };
            }
            return null;
          },
        };
      },
      action(fn) {
        editor.actions.push(fn);
        fn(editor.getCtx());
      },
      destroy: () => {},
    };
    const chainable = {
      config: (/** @type {any} */ fn) => { editor.configFn = fn; return chainable; },
      use: () => chainable,
      create: async () => { editors.push(editor); return editor; },
    };
    return chainable;
  };
  return { ...kit, Editor: { make }, __editors: editors };
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
      strong: {},
      em: {},
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
 * @param {((domNode: Node, offset: number) => number) | null} [posFn]
 *   override posAtDOM (e.g. to resolve inside a later run of a multi-run
 *   link, as a click-position resolution would)
 */
function makeFakeView(doc, domParagraph, posFn = null) {
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
    posAtDOM(/** @type {Node} */ domNode, /** @type {number} */ offset) {
      if (posFn) return posFn(domNode, offset);
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

/** All text nodes carrying the wiki_link mark, in document order. */
function markedTextNodes(doc) {
  /** @type {any[]} */
  const out = [];
  doc.descendants((/** @type {any} */ n) => {
    if (n.isText && n.marks.some((/** @type {any} */ m) => m.type.name === 'wiki_link')) out.push(n);
    return true;
  });
  return out;
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

/**
 * A link whose display text spans three marked runs — a nested <strong>
 * splits the middle run, the way Milkdown renders [[T|Old **Bold** End]].
 * Positions: "Old " 8-12, "Bold" 12-16, " End" 16-20 (doc size 26).
 * @param {import('./wiki-editor.js').WikiEditor} el
 * @param {ReturnType<typeof makeSchema>} schema
 * @param {{target: string}} opts
 * @param {((domNode: Node, offset: number) => number) | null} [posFn]
 */
function setupNestedFixture(el, schema, opts, posFn = null) {
  const wiki = schema.marks.wiki_link;
  const strong = schema.marks.strong;
  const doc = schema.node('doc', null, [
    schema.node('paragraph', null, [
      schema.text('before ', []),
      schema.text('Old ', [wiki.create({ target: opts.target })]),
      schema.text('Bold', [wiki.create({ target: opts.target }), strong.create()]),
      schema.text(' End', [wiki.create({ target: opts.target })]),
      schema.text(' after', []),
    ]),
  ]);
  /** @type {HTMLElement} */
  const root = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root'));
  const p = document.createElement('p');
  p.append(document.createTextNode('before '));
  const a = document.createElement('a');
  a.className = 'wiki-link';
  a.setAttribute('data-wiki-page', opts.target);
  a.append(document.createTextNode('Old '));
  const s = document.createElement('strong');
  s.append(document.createTextNode('Bold'));
  a.append(s);
  a.append(document.createTextNode(' End'));
  p.append(a);
  p.append(document.createTextNode(' after'));
  root.append(p);
  const fakeView = makeFakeView(doc, p, posFn);
  /** @type {any} */ (editors[0]).view = fakeView;
  return { fakeView, a, strong: s };
}

function singleClick(/** HTMLElement */ link) {
  const evt = new MouseEvent('click', { bubbles: true, cancelable: true });
  link.dispatchEvent(evt);
  return evt;
}

function altClick(/** HTMLElement */ link) {
  const evt = new MouseEvent('click', { bubbles: true, cancelable: true, altKey: true });
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

/** Open the popover (Alt+click opens it immediately — no disambiguation delay). */
async function openPopover(/** import('./wiki-editor.js').WikiEditor */ el, /** HTMLElement */ link) {
  altClick(link);
  await vi.waitFor(() => expect(el._linkEdit).not.toBeNull());
  await vi.waitFor(() => expect(document.querySelector('.wiki-link-popover')).not.toBeNull());
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

  it('opens on Alt+click with the current target and display', async () => {
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

  it('plain click navigates to the target immediately and opens no popover', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    const evt = singleClick(link);
    expect(evt.defaultPrevented).toBe(true);
    // No disambiguation delay: navigation is synchronous, and no popover
    // is ever opened.
    expect(pages).toEqual(['Old Target']);
    expect(el._linkEdit).toBeNull();
  });

  it('plain click on a plain [[link]] navigates to its text', async () => {
    setupFixture(el, schema, { target: null, display: 'Plain' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    singleClick(link);

    expect(pages).toEqual(['Plain']);
    expect(el._linkEdit).toBeNull();
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

  it('the popover Open link navigates to the current target and closes without applying', async () => {
    fakeView = setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    await openPopover(el, link);
    setField('target', 'New Target');
    clickButton('.wiki-link-popover-goto');

    expect(el._linkEdit).toBeNull();
    expect(pages).toEqual(['New Target']);
    // The edit is not applied — the document is untouched.
    expect(fakeView.dispatches).toHaveLength(0);
  });

  it('the popover Open link label follows the live Target field value', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);
    expect(/** @type {HTMLButtonElement} */ (document.querySelector('.wiki-link-popover-goto')).textContent.trim())
      .toBe('Open "Old Target"');

    setField('target', 'New Target');
    await el.updateComplete;

    expect(/** @type {HTMLButtonElement} */ (document.querySelector('.wiki-link-popover-goto')).textContent.trim())
      .toBe('Open "New Target"');
    // The re-render must not steal focus or re-select the field mid-typing.
    const target = /** @type {HTMLInputElement} */ (
      document.querySelector('.wiki-link-popover input[data-field="target"]'));
    expect(document.activeElement).toBe(target);
  });

  it('the popover Open link with an empty target closes without navigating', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    await openPopover(el, link);
    setField('target', '   ');
    clickButton('.wiki-link-popover-goto');

    expect(el._linkEdit).toBeNull();
    expect(pages).toEqual([]);
  });

  it('double click navigates on each click and opens no popover', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    // A double click is two clicks plus the dblclick event; each click
    // navigates and the dblclick itself is a no-op (the host navigates
    // idempotently).
    singleClick(link);
    singleClick(link);
    link.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));

    expect(pages).toEqual(['Old Target', 'Old Target']);
    expect(el._linkEdit).toBeNull();
  });

  it('plain click closes an already-open popover and navigates', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    await openPopover(el, link);
    expect(el._linkEdit).not.toBeNull();
    singleClick(link);

    expect(el._linkEdit).toBeNull();
    expect(pages).toEqual(['Old Target']);
  });

  it('resolves the full span of a multi-run link even when resolved at the last run', async () => {
    // posAtDOM resolves to 18 — inside the last run (" End", 16-20), the
    // way a click-position resolution would. A non-transitive span walk
    // would drop the leading "Old " run.
    const { fakeView, a } = setupNestedFixture(el, schema, { target: 'Old Target' }, () => 18);
    link = a;

    await openPopover(el, link);
    setField('target', 'New Target');
    clickButton('.wiki-link-popover-apply');

    const nodes = markedTextNodes(fakeView.state.doc);
    expect(nodes.map((/** @type {any} */ n) => n.text)).toEqual(['Old ', 'Bold', ' End']);
    for (const n of nodes) {
      expect(n.marks.some(
        (/** @type {any} */ m) => m.type.name === 'wiki_link' && m.attrs.target === 'New Target',
      )).toBe(true);
    }
  });

  it('target-only edit updates the mark in place, preserving nested marks', async () => {
    const { fakeView, a } = setupNestedFixture(el, schema, { target: 'Old Target' });
    link = a;

    await openPopover(el, a);
    setField('target', 'New Target');
    clickButton('.wiki-link-popover-apply');

    const nodes = markedTextNodes(fakeView.state.doc);
    expect(nodes).toHaveLength(3);
    expect(nodes.map((/** @type {any} */ n) => n.text)).toEqual(['Old ', 'Bold', ' End']);
    for (const n of nodes) {
      expect(n.marks.some(
        (/** @type {any} */ m) => m.type.name === 'wiki_link' && m.attrs.target === 'New Target',
      )).toBe(true);
    }
    // The bold run kept its strong mark (mark set, order-insensitive).
    expect(nodes[1].marks.map((/** @type {any} */ m) => m.type.name).sort())
      .toEqual(['strong', 'wiki_link']);
  });

  it('a display edit replaces the whole run, flattening nested formatting', async () => {
    // Documented trade-off: the fields are plain text, so editing the
    // display replaces the full marked run (nested formatting is dropped).
    const { fakeView, a } = setupNestedFixture(el, schema, { target: 'Old Target' });
    link = a;

    await openPopover(el, a);
    setField('display', 'Fresh');
    clickButton('.wiki-link-popover-apply');

    const nodes = markedTextNodes(fakeView.state.doc);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].text).toBe('Fresh');
    expect(nodes[0].marks.map((/** @type {any} */ m) => m.type.name)).toEqual(['wiki_link']);
  });

  it('apply with unchanged fields makes no document change', async () => {
    const { fakeView, a } = setupNestedFixture(el, schema, { target: 'Old Target' });
    link = a;

    await openPopover(el, a);
    clickButton('.wiki-link-popover-apply');

    expect(el._linkEdit).toBeNull();
    expect(fakeView.dispatches).toHaveLength(0);
  });

  it('closes the popover if the document changes while it is open', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));
    // Wire the markdownUpdated listener the way the real editor does.
    /** @type {any} */
    const editor = editors[0];
    editor.configFn(editor.getCtx());
    expect(editor.mdListeners).toHaveLength(1);

    await openPopover(el, link);
    // The user edits (or undoes) the document while the popover is open —
    // the captured span is now stale, so the edit must be dropped.
    editor.mdListeners[0](null, 'changed content', 'Old Display');
    await el.updateComplete;

    expect(el._linkEdit).toBeNull();
    expect(document.querySelector('.wiki-link-popover')).toBeNull();
  });

  it('opens with the Target field focused and selected', async () => {
    setupFixture(el, schema, { target: 'Old Target', display: 'Old Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    await openPopover(el, link);

    const target = /** @type {HTMLInputElement} */ (
      document.querySelector('.wiki-link-popover input[data-field="target"]'));
    expect(document.activeElement).toBe(target);
  });

  it('a plain click on a nested element of the same link navigates and closes', async () => {
    const { a, strong } = setupNestedFixture(el, schema, { target: 'Old Target' });
    link = a;
    /** @type {string[]} */
    const pages = [];
    el.addEventListener('wiki-navigate', (e) => pages.push(/** @type {CustomEvent} */ (e).detail.page));

    await openPopover(el, a);
    expect(el._linkEdit).not.toBeNull();

    singleClick(strong); // a nested element of the link being edited

    expect(el._linkEdit).toBeNull();
    expect(pages).toEqual(['Old Target']);
  });

  it('Alt+click on a nested element of the same link keeps the popover open', async () => {
    const { a, strong } = setupNestedFixture(el, schema, { target: 'Old Target' });
    link = a;

    await openPopover(el, a);
    const before = el._linkEdit;
    expect(before).not.toBeNull();

    altClick(strong); // a nested element of the very link being edited

    expect(el._linkEdit).toBe(before); // still open — no close/reopen
  });

  it('clamps popover left to margin on a narrow viewport', async () => {
    setupFixture(el, schema, { target: 'Target', display: 'Display' });
    link = /** @type {HTMLElement} */ (el.querySelector('.milkdown-root a.wiki-link'));

    vi.spyOn(link, 'getBoundingClientRect').mockReturnValue(/** @type {DOMRect} */ ({
      left: 100,
      right: 150,
      top: 50,
      bottom: 70,
      width: 50,
      height: 20,
    }));
    const innerWidthSpy = vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(320);

    try {
      await openPopover(el, link);
      expect(el._linkEdit).not.toBeNull();
      // On 320px viewport with LINK_EDIT_MARGIN = 8, left clamps to 8
      expect(el._linkEdit.left).toBe(8);
    } finally {
      innerWidthSpy.mockRestore();
    }
  });
});
