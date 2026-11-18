import { afterEach, describe, expect, it, vi } from 'vitest';

import './vault-sidebar.js';

const jsonResponse = (body) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
});

// #1524 — the vault create actions sit at the bottom of the list.
describe('vault-sidebar create actions (#1524)', () => {
  let sidebar;

  afterEach(() => {
    sidebar?.remove();
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function mount() {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ files: [], vault: [] })));
    sidebar = /** @type {any} */ (document.createElement('vault-sidebar'));
    document.body.append(sidebar);
    await sidebar.updateComplete;
    return sidebar;
  }

  it('renders + Page / + Folder below the scrollable list in browse view', async () => {
    const el = await mount();
    const actions = [...el.querySelectorAll('.vault-action-btns')]
      .find((node) => node.parentElement === el);
    expect(actions).toBeTruthy();
    expect(actions.previousElementSibling.classList.contains('conv-list')).toBe(true);
    expect(actions.querySelector('.wiki-new-page-btn')).toBeTruthy();
    expect(actions.querySelector('.wiki-new-folder-btn')).toBeTruthy();
    expect(el.querySelector('.conv-list .vault-action-btns')).toBeNull();
  });

  it('omits the create actions in the recent view', async () => {
    const el = await mount();
    el._vaultView = 'recent';
    await el.updateComplete;
    expect(el.querySelector('.vault-action-btns')).toBeNull();
  });
});
