import { afterEach, describe, expect, it } from 'vitest';

await import('./chip-list.js');

/** @returns {any} */
function mount(items = [], readonly = false) {
  const el = /** @type {any} */ (document.createElement('chip-list'));
  el.label = 'tags';
  el.items = items;
  el.readonly = readonly;
  document.body.appendChild(el);
  return el;
}

describe('chip-list', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('renders one chip per item', async () => {
    const el = mount(['a', 'b']);
    await el.updateComplete;
    expect(el.querySelectorAll('.dc-chip')).toHaveLength(2);
  });

  it('emits chips-change without the removed item', async () => {
    const el = mount(['a', 'b']);
    await el.updateComplete;
    /** @type {any} */ let detail = null;
    el.addEventListener('chips-change', (/** @type {any} */ e) => { detail = e.detail; });

    /** @type {HTMLButtonElement} */
    (el.querySelector('button.dc-chip-x')).click();
    expect(detail.items).toEqual(['b']);
  });

  it('emits chips-change with an appended item on Enter', async () => {
    const el = mount(['a']);
    await el.updateComplete;
    /** @type {any} */ let detail = null;
    el.addEventListener('chips-change', (/** @type {any} */ e) => { detail = e.detail; });

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.dc-chip-input'));
    input.value = 'b';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(detail.items).toEqual(['a', 'b']);
  });

  it('ignores a duplicate', async () => {
    const el = mount(['a']);
    await el.updateComplete;
    let fired = 0;
    el.addEventListener('chips-change', () => { fired += 1; });

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.dc-chip-input'));
    input.value = 'a';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(fired).toBe(0);
  });

  it('hides the input and the remove buttons when readonly', async () => {
    const el = mount(['a'], true);
    await el.updateComplete;
    expect(el.querySelector('.dc-chip-input')).toBeNull();
    expect(el.querySelector('button.dc-chip-x')).toBeNull();
  });
});
