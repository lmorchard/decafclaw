import { afterEach, describe, expect, it, vi } from 'vitest';

import './model-select.js';

/**
 * Settings consumer of `<picker-popup>`. These carry the selection-mode
 * coverage that used to live in model-picker.test.js, now that the settings
 * pointer is its own element (#2011).
 */

/** @param {object} [props] */
async function mount(props = {}) {
  const el = /** @type {any} */ (document.createElement('model-select'));
  el.options = ['hand-model', 'local/llama'];
  el.value = '';
  el.emptyLabel = '(config.json default)';
  Object.assign(el, props);
  document.body.append(el);
  await el.updateComplete;
  await popupOf(el).updateComplete;
  return el;
}

/** @param {any} el */
function popupOf(el) {
  return /** @type {any} */ (el.querySelector('picker-popup'));
}

/** @param {any} el */
async function open(el) {
  el.querySelector('.picker-popup-trigger').click();
  await el.updateComplete;
  await popupOf(el).updateComplete;
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
  await popupOf(el).updateComplete;
  return input;
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('model-select (settings consumer)', () => {
  it('shows the empty label and a flat list with the inherit choice pinned', async () => {
    const el = await mount();
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('(config.json default)');

    await open(el);
    expect(values(el)).toEqual(['', 'hand-model', 'local/llama']);
    const selected = el.querySelector('.picker-popup-option.selected');
    expect(selected.dataset.value).toBe('');
    expect(selected.textContent).toContain('(config.json default)');
  });

  it('marks the stored pointer and shows it on the trigger', async () => {
    const el = await mount({ value: 'hand-model' });
    expect(el.querySelector('.picker-popup-trigger').textContent).toContain('hand-model');

    await open(el);
    const selected = el.querySelector('.picker-popup-option.selected');
    expect(selected.dataset.value).toBe('hand-model');
  });

  it('filters the flat option list and drops the inherit row while searching', async () => {
    const el = await mount();
    await open(el);

    await type(el, 'llama');
    expect(values(el)).toEqual(['local/llama']);

    await type(el, 'HAND');
    expect(values(el)).toEqual(['hand-model']);

    // The inherit row is pinned only while the query is empty, so it cannot be
    // matched by a search term.
    await type(el, '');
    expect(values(el)).toEqual(['', 'hand-model', 'local/llama']);
  });

  it('emits model-select instead of set-model', async () => {
    const el = await mount();
    const selectHandler = vi.fn();
    const conversationHandler = vi.fn();
    document.addEventListener('model-select', selectHandler);
    document.addEventListener('set-model', conversationHandler);

    await open(el);
    el.querySelector('[data-value="local/llama"]').click();
    await el.updateComplete;

    document.removeEventListener('model-select', selectHandler);
    document.removeEventListener('set-model', conversationHandler);
    expect(selectHandler).toHaveBeenCalledTimes(1);
    expect(selectHandler.mock.calls[0][0].detail).toEqual({ value: 'local/llama' });
    expect(conversationHandler).not.toHaveBeenCalled();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('emits an empty value when the inherit choice is picked', async () => {
    const el = await mount({ value: 'hand-model' });
    const handler = vi.fn();
    document.addEventListener('model-select', handler);

    await open(el);
    el.querySelector('[data-value=""]').click();
    await el.updateComplete;

    document.removeEventListener('model-select', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ value: '' });
  });

  it('does not emit when the stored pointer is picked again', async () => {
    const el = await mount({ value: 'hand-model' });
    const handler = vi.fn();
    document.addEventListener('model-select', handler);

    await open(el);
    el.querySelector('[data-value="hand-model"]').click();
    await el.updateComplete;

    document.removeEventListener('model-select', handler);
    expect(handler).not.toHaveBeenCalled();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('moves through the flat rows with Arrow keys and selects with Enter', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('model-select', handler);
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));

    // The inherit row is highlighted when the pointer is empty.
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('');
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('hand-model');

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await el.updateComplete;
    document.removeEventListener('model-select', handler);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ value: 'hand-model' });
  });

  it('uses each field\'s own inherit label', async () => {
    const el = await mount({ emptyLabel: '(use conversation model)' });
    expect(el.querySelector('.picker-popup-trigger').textContent)
      .toContain('(use conversation model)');

    await open(el);
    expect(el.querySelector('[data-value=""]').textContent)
      .toContain('(use conversation model)');
  });

  it('disables the trigger', async () => {
    const el = await mount({ disabled: true });
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.disabled).toBe(true);
    trigger.click();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });
});
