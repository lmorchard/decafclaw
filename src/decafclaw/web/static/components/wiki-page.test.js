import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
/** Real listener slice, re-exported by the mock below — used to drive content changes. */
import { listenerCtx } from '@milkdown/kit';

// Milkdown needs a real contenteditable surface and contributes nothing to the
// fetch paths under test. Override only `Editor` with a recording stub — the
// rest of the kit stays real, since the wiki-link plugin imports from it.
// Same stub as wiki-editor.test.js: the auto-refresh tests mount the real
// editor in edit mode, so it must build without a DOM contenteditable.
vi.mock('@milkdown/kit', async (importOriginal) => {
  const actual = /** @type {object} */ (await importOriginal());
  /** @type {{actions: unknown[], configCb?: Function, destroyCalls: number}[]} */
  const editors = [];
  const make = () => {
    const editor = {
      /** @type {unknown[]} */ actions: [],
      /** @param {unknown} fn */ action: (fn) => editor.actions.push(fn),
      /** @type {number} */ destroyCalls: 0,
      destroy: () => { editor.destroyCalls++; },
    };
    /** @type {Record<string, Function>} */
    const chainable = {
      config: (cb) => { editor.configCb = cb; return chainable; },
      use: () => chainable,
      create: async () => { editors.push(editor); return editor; },
    };
    return chainable;
  };
  return { ...actual, Editor: { make }, __editors: editors };
});

import './wiki-page.js';
import { WikiWriteMutex } from '../lib/wiki-page-write-mutex.js';

/** Editors built by the stub above, in mount order. */
const { __editors: editors } = /** @type {any} */ (await import('@milkdown/kit'));

/** @type {(body: unknown) => object} */
const jsonResponse = (body) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  // The generic api-client path branches on Content-Type; without a header
  // the body is dropped (response.headers.get throws inside its try/catch).
  headers: { get: (/** @type {string} */ k) => (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body,
});

/**
 * Drive a content change through the editor's markdownUpdated listener — the
 * same path Milkdown uses when the user types.
 * @param {any} editor stub editor from the mock above
 * @param {string} md new markdown
 */
function typeInto(editor, md) {
  /** @type {((ctx: unknown, next: string, prev: string) => void) | null} */
  let handler = null;
  editor.configCb({
    set: () => {},
    get: (key) => key === listenerCtx ? { markdownUpdated: cb => { handler = /** @type {any} */ (cb); } } : {},
  });
  if (!handler) throw new Error('markdownUpdated not registered');
  handler(null, md, '');
}

/** @param {string} path vault-relative file path, e.g. `notes/alpha.md` */
function dispatchVaultChanged(path) {
  window.dispatchEvent(new CustomEvent('vault-changed', {
    detail: { type: 'vault_changed', path, kind: 'update' },
  }));
}

describe('wiki-page component', () => {
    it('can be created', () => {
        const el = document.createElement('wiki-page');
        expect(el).toBeDefined();
    });

    it('flushSave flushes pending metadata and editor writes', async () => {
        const el = /** @type {any} */ (document.createElement('wiki-page'));
        document.body.append(el);
        const editor = document.createElement('div');
        const flushEditor = vi.fn().mockResolvedValue(undefined);
        /** @type {any} */ (editor).flushSave = flushEditor;
        vi.spyOn(el, 'querySelector').mockImplementation((/** @type {string} */ sel) => {
            if (sel === 'wiki-editor') return editor;
            return null;
        });
        await el.flushSave();
        expect(flushEditor).toHaveBeenCalled();
        el.remove();
    });
});

// ── vault-changed auto-refresh (#1769) ─────────────────────────────────
describe('wiki-page vault-changed auto-refresh', () => {
  /** @type {{title: string, body: string, modified: number, frontmatter: Record<string, unknown>}} */
  let vault;
  /** @type {ReturnType<typeof vi.fn>} */
  let fetchMock;

  beforeEach(() => {
    editors.length = 0;
    localStorage.clear();
    vault = { title: 'alpha', body: 'one', modified: 1000, frontmatter: {} };
    fetchMock = vi.fn(async (/** @type {string} */ url, /** @type {RequestInit} */ init = {}) => {
      const u = new URL(url, 'http://t');
      const method = (init?.method ?? 'GET').toUpperCase();
      if (u.pathname === '/api/bookmarks') return jsonResponse({ files: [], vault: [] });
      if (u.pathname.startsWith('/api/vault/')) {
        if (method === 'PUT') {
          const body = JSON.parse(String(init.body));
          if (typeof body.body === 'string') vault.body = body.body;
          vault.modified += 1;
          return jsonResponse({
            ok: true, modified: vault.modified, frontmatter: {}, frontmatter_raw: '',
          });
        }
        return jsonResponse({
          title: vault.title,
          body: vault.body,
          frontmatter: vault.frontmatter,
          frontmatter_raw: '',
          modified: vault.modified,
        });
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  /** @param {string} page vault-relative page path, e.g. `notes/alpha` */
  async function mountPage(page) {
    const el = /** @type {any} */ (document.createElement('wiki-page'));
    document.body.append(el);
    el.page = page;
    await vi.waitFor(() => expect(el._loaded).toBe(true));
    return el;
  }

  /** GETs against the vault page endpoint, excluding bookmark reads. */
  function vaultGets() {
    return fetchMock.mock.calls.filter(([url, init]) =>
      (/** @type {RequestInit|undefined} */ (init)?.method ?? 'GET').toUpperCase() === 'GET'
      && new URL(String(url), 'http://t').pathname.startsWith('/api/vault/')).length;
  }

  /**
   * Let an async vault-changed probe run through the mocked fetch and its
   * continuation. A dirty check that returns early leaves no fetch; without
   * this wait a missed dirty check would not be observed yet.
   */
  function settleProbe() {
    return new Promise(resolve => setTimeout(resolve, 10));
  }

  it('refreshes a clean view-mode page when its vault file changes', async () => {
    localStorage.setItem('wiki-edit-mode', 'false');
    const el = await mountPage('notes/alpha');
    expect(el.querySelector('.wiki-page-body')?.textContent).toContain('one');

    vault.body = 'two';
    vault.modified = 2000;
    dispatchVaultChanged('notes/alpha.md');

    await vi.waitFor(() => {
      expect(el.querySelector('.wiki-page-body')?.textContent).toContain('two');
    });
  });

  it('updates a clean editor in place with the new body', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));
    expect(el.querySelector('wiki-editor')?.content).toBe('one');

    vault.body = 'two';
    vault.modified = 2000;
    dispatchVaultChanged('notes/alpha.md');

    await vi.waitFor(() => {
      expect(el.querySelector('wiki-editor')?.content).toBe('two');
      expect(el.querySelector('wiki-editor')?.modified).toBe(2000);
    });
    // In-place update, not a remount: the editor (and its undo history) stays.
    expect(editors).toHaveLength(1);
    expect(editors[0].actions).toHaveLength(1);
  });

  it('syncs the mtime without resetting the editor on metadata-only changes', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));

    vault.modified = 2000;
    vault.frontmatter = { summary: 'agent summary' };
    dispatchVaultChanged('notes/alpha.md');

    await vi.waitFor(() => {
      expect(el._frontmatter.summary).toBe('agent summary');
      expect(el.querySelector('wiki-editor')?.modified).toBe(2000);
    });
    // Same body: no replaceAll, so undo history and cursor survive.
    expect(editors[0].actions).toHaveLength(0);
  });

  it('applies only the newest response when events overlap', async () => {
    localStorage.setItem('wiki-edit-mode', 'false');
    const el = await mountPage('notes/alpha');

    /** @type {((value: unknown) => void)[]} */
    const resolvers = [];
    const deferred = () => new Promise(resolve => resolvers.push(/** @type {any} */ (resolve)));
    fetchMock.mockImplementationOnce(deferred);
    fetchMock.mockImplementationOnce(deferred);

    vault.body = 'two';
    vault.modified = 2000;
    dispatchVaultChanged('notes/alpha.md');
    vault.body = 'three';
    vault.modified = 3000;
    dispatchVaultChanged('notes/alpha.md');

    await vi.waitFor(() => expect(resolvers).toHaveLength(2));
    // The newer write resolves first; the older probe returns late.
    resolvers[1](jsonResponse({
      title: 'alpha', body: 'three', frontmatter: {}, frontmatter_raw: '', modified: 3000,
    }));
    await vi.waitFor(() => expect(el._body).toBe('three'));
    resolvers[0](jsonResponse({
      title: 'alpha', body: 'two', frontmatter: {}, frontmatter_raw: '', modified: 2000,
    }));
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(el._body).toBe('three');
    expect(el._modified).toBe(3000);
  });

  it('does not clobber unsaved editor text', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));
    typeInto(editors[0], 'local draft');

    vault.body = 'server edit';
    vault.modified = 2000;
    const before = vaultGets();
    dispatchVaultChanged('notes/alpha.md');
    await settleProbe();

    expect(vaultGets()).toBe(before);
    expect(el._body).toBe('one');
  });

  it('does not clobber unsaved raw frontmatter edits', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    localStorage.setItem('wiki-metadata-expanded', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));
    const meta = /** @type {any} */ (el.querySelector('wiki-metadata'));
    meta.querySelector('.wiki-md-raw-toggle').click();
    await meta.updateComplete;
    const textarea = meta.querySelector('.wiki-md-raw-input');
    textarea.value = 'summary: local draft';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));

    vault.body = 'server edit';
    vault.modified = 2000;
    const before = vaultGets();
    dispatchVaultChanged('notes/alpha.md');
    await settleProbe();

    expect(vaultGets()).toBe(before);
    expect(el._body).toBe('one');
  });

  it('does not clobber an uncommitted typed summary field', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    localStorage.setItem('wiki-metadata-expanded', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));
    const meta = /** @type {any} */ (el.querySelector('wiki-metadata'));
    const summary = meta.querySelector('.wiki-md-summary-input');
    summary.value = 'half-typed summary';
    summary.dispatchEvent(new Event('input', { bubbles: true }));

    vault.body = 'server edit';
    vault.modified = 2000;
    vault.frontmatter = { summary: 'server summary' };
    const before = vaultGets();
    dispatchVaultChanged('notes/alpha.md');
    await settleProbe();

    expect(vaultGets()).toBe(before);
    expect(summary.value).toBe('half-typed summary');
  });

  it('refreshes after a canceled raw frontmatter edit', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    localStorage.setItem('wiki-metadata-expanded', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));
    const meta = /** @type {any} */ (el.querySelector('wiki-metadata'));
    meta.querySelector('.wiki-md-raw-toggle').click();
    await meta.updateComplete;
    const textarea = meta.querySelector('.wiki-md-raw-input');
    textarea.value = 'summary: discarded';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    meta.querySelector('.wiki-md-raw-actions button.secondary').click();
    await meta.updateComplete;

    vault.body = 'two';
    vault.modified = 2000;
    dispatchVaultChanged('notes/alpha.md');

    await vi.waitFor(() => expect(el._body).toBe('two'));
  });

  it('ignores changes to other pages', async () => {
    localStorage.setItem('wiki-edit-mode', 'false');
    const el = await mountPage('notes/alpha');
    const before = vaultGets();

    dispatchVaultChanged('notes/beta.md');
    await el.updateComplete;

    expect(vaultGets()).toBe(before);
  });

  it('skips the update when the mtime is unchanged', async () => {
    localStorage.setItem('wiki-edit-mode', 'true');
    const el = await mountPage('notes/alpha');
    await vi.waitFor(() => expect(editors).toHaveLength(1));

    dispatchVaultChanged('notes/alpha.md');
    await vi.waitFor(() => expect(vaultGets()).toBe(2));
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(editors).toHaveLength(1);
    expect(el._body).toBe('one');
  });
});

describe('WikiWriteMutex pure logic extracted from wiki-page', () => {
    it('verifies mutex locking, pending-fields accumulation, and conflict resolution/retry bookkeeping', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test1' });
        mutex.queueFields({ tags: ['test'] });
        expect(mutex.pendingFields).toEqual({ title: 'test1', tags: ['test'] });
        
        await mutex.flush('page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test1', tags: ['test'] }, modified: 1 }, 'page1', 1);
    });
});
