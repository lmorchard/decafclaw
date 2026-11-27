import { afterEach, describe, expect, it, vi } from 'vitest';

import './picker-popup.js';

/**
 * The generic primitive (#2011). Everything asserted here is about the popup
 * mechanism — rows, filtering, keyboard, panel lifecycle — never about models,
 * conversations, or settings. Those belong to the consumers' own tests.
 */

const ITEMS = [
  { value: 'alpha', label: 'alpha' },
  { value: 'beta', label: 'beta', selected: true, badge: 'default' },
  { value: 'gamma', label: 'gamma', warn: '⚠ not configured' },
];

/** @param {object} [props] */
async function mount(props = {}) {
  const el = /** @type {any} */ (document.createElement('picker-popup'));
  el.items = ITEMS;
  el.label = 'Pick one';
  Object.assign(el, props);
  document.body.append(el);
  await el.updateComplete;
  return el;
}

/** @param {any} el */
async function open(el) {
  el.querySelector('.picker-popup-trigger').click();
  await el.updateComplete;
  return el.querySelector('.picker-popup-panel');
}

/** @param {any} el */
function values(el) {
  return [...el.querySelectorAll('.picker-popup-option')].map((row) => row.dataset.value);
}

/** @param {any} el @param {string} value */
async function type(el, value) {
  const input = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await el.updateComplete;
  return input;
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('picker-popup', () => {
  it('renders a flat list with no group headings', async () => {
    const el = await mount();
    await open(el);
    expect(values(el)).toEqual(['alpha', 'beta', 'gamma']);
    expect(el.querySelectorAll('.picker-popup-group-name').length).toBe(0);
  });

  it('renders grouped items when given groups instead of items', async () => {
    const el = await mount({
      items: [],
      groups: [
        { name: 'One', items: [{ value: 'a', label: 'a' }] },
        { name: 'Two', items: [{ value: 'b', label: 'b' }] },
      ],
    });
    await open(el);
    const names = [...el.querySelectorAll('.picker-popup-group-name')].map((n) => n.textContent.trim());
    expect(names).toEqual(['One', 'Two']);
    expect(values(el)).toEqual(['a', 'b']);
  });

  it('shows the selected item on the trigger with its badge and warning', async () => {
    const el = await mount();
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('beta');
    expect(trigger.textContent).toContain('default');

    const warned = await mount({
      items: [{ value: 'w', label: 'w', selected: true, warn: '⚠ not configured' }],
    });
    expect(warned.querySelector('.picker-popup-trigger').textContent).toContain('not configured');
  });

  it('falls back to the placeholder when nothing is selected', async () => {
    const el = await mount({ items: [], placeholder: 'Nothing chosen' });
    expect(el.querySelector('.picker-popup-trigger').textContent).toContain('Nothing chosen');
  });

  it('pins the empty item above the list only while the query is empty', async () => {
    const el = await mount({ emptyItem: { value: '', label: '(inherit)' } });
    await open(el);
    expect(values(el)).toEqual(['', 'alpha', 'beta', 'gamma']);

    await type(el, 'al');
    expect(values(el)).toEqual(['alpha']);
  });

  it('never renders an empty item the caller did not supply', async () => {
    const el = await mount();
    await open(el);
    expect(values(el)).not.toContain('');
  });

  it('filters by value, label, and group name', async () => {
    const el = await mount({
      items: [],
      groups: [
        { name: 'Anthropic', items: [{ value: 'x/one', label: 'First' }] },
        { name: 'Local', items: [{ value: 'y/two', label: 'Second' }] },
      ],
    });
    await open(el);

    await type(el, 'First');
    expect(values(el)).toEqual(['x/one']);

    // A group-name match keeps every item in that group.
    await type(el, 'local');
    expect(values(el)).toEqual(['y/two']);

    await type(el, 'zzz');
    expect(values(el)).toEqual([]);
    expect(el.querySelector('.picker-popup-no-match').textContent).toContain('No matches');
  });

  it('dispatches one pick with the value and item on a click', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('pick', handler);

    await open(el);
    el.querySelector('[data-value="gamma"]').click();
    await el.updateComplete;

    document.removeEventListener('pick', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail.value).toBe('gamma');
    expect(handler.mock.calls[0][0].detail.item.label).toBe('gamma');
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('dispatches pick for the already-selected row too — consumers dedupe', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('pick', handler);

    await open(el);
    el.querySelector('[data-value="beta"]').click();
    await el.updateComplete;

    document.removeEventListener('pick', handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('passes an empty value straight through', async () => {
    const el = await mount({ emptyItem: { value: '', label: '(inherit)' } });
    const handler = vi.fn();
    document.addEventListener('pick', handler);

    await open(el);
    el.querySelector('[data-value=""]').click();
    await el.updateComplete;

    document.removeEventListener('pick', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail.value).toBe('');
  });

  it('highlights the selected row on open and moves with Arrow keys', async () => {
    const el = await mount();
    await open(el);
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('beta');

    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('gamma');

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('beta');
  });

  it('counts the pinned empty row in keyboard navigation', async () => {
    const el = await mount({ emptyItem: { value: '', label: '(inherit)' } });
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));

    // The selected item is `beta`, which now sits at index 2.
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('beta');
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('');
  });

  it('selects the highlighted row with Enter', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('pick', handler);
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await el.updateComplete;

    document.removeEventListener('pick', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail.value).toBe('beta');
  });

  it('clears the query on Escape and returns focus to the trigger', async () => {
    const el = await mount();
    await open(el);
    await type(el, 'al');
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));
    search.focus();
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await el.updateComplete;

    expect(el.querySelector('.picker-popup-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.picker-popup-trigger'));
  });

  it('closes on an outside click', async () => {
    const el = await mount();
    await open(el);
    document.body.click();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('close() lets a caller dismiss the panel', async () => {
    const el = await mount();
    await open(el);
    el.close();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('does not open while disabled', async () => {
    const el = await mount({ disabled: true });
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.disabled).toBe(true);
    trigger.click();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('renders inline without a trigger or panel', async () => {
    const el = await mount({ inline: true });
    expect(el.querySelector('.picker-popup-trigger')).toBeNull();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
    expect(el.querySelector('.picker-popup-search')).toBeTruthy();
    expect(values(el)).toEqual(['alpha', 'beta', 'gamma']);

    const handler = vi.fn();
    document.addEventListener('pick', handler);
    el.querySelector('[data-value="alpha"]').click();
    await el.updateComplete;
    document.removeEventListener('pick', handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('keeps no model or conversation vocabulary', async () => {
    // #2011 guard: the primitive must stay domain-free. If this fails, some
    // consumer's policy leaked into the shared element.
    const el = await mount();
    const markup = el.shadowRoot ? el.shadowRoot.innerHTML : el.innerHTML;
    expect(markup).not.toMatch(/model/i);
    expect(markup).not.toMatch(/conversation/i);
    expect(/** @type {any} */ (el).mode).toBeUndefined();
    expect(/** @type {any} */ (el).convId).toBeUndefined();
    expect(/** @type {any} */ (el).activeModel).toBeUndefined();
  });
});
