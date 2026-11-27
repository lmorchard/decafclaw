import { afterEach, describe, expect, it, vi } from 'vitest';

import './model-picker.js';

const GROUPS = [
  { name: 'Anthropic', models: ['anthropic/claude', 'anthropic/haiku'] },
  { name: 'Local', models: ['local/llama'] },
];

/** @param {object} [props] */
async function mount(props = {}) {
  const el = /** @type {any} */ (document.createElement('model-picker'));
  el.groups = GROUPS;
  el.activeModel = 'anthropic/claude';
  el.defaultModel = 'anthropic/claude';
  Object.assign(el, props);
  document.body.append(el);
  await el.updateComplete;
  await popupOf(el).updateComplete;
  return el;
}

/** The nested popup this consumer renders. @param {any} el */
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
function models(el) {
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

describe('model-picker (conversation consumer)', () => {
  it('renders provider groups and marks the default model', async () => {
    const el = await mount();
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.textContent).toContain('anthropic/claude');
    expect(trigger.textContent).toContain('default');

    await open(el);
    const names = [...el.querySelectorAll('.picker-popup-group-name')].map((n) => n.textContent.trim());
    expect(names).toEqual(['Anthropic', 'Local']);
    expect(models(el)).toEqual(['anthropic/claude', 'anthropic/haiku', 'local/llama']);

    const selected = el.querySelector('.picker-popup-option.selected');
    expect(selected.dataset.value).toBe('anthropic/claude');
    expect(selected.querySelector('.picker-popup-badge').textContent).toBe('default');
  });

  it('flags an unconfigured active model and keeps it selectable', async () => {
    const el = await mount({ activeModel: 'legacy-model-v1' });
    expect(el.querySelector('.picker-popup-trigger').textContent).toContain('not configured');

    await open(el);
    expect(el.querySelector('.picker-popup-group-name').textContent.trim()).toBe('Current');
    const current = el.querySelector('.picker-popup-option.selected');
    expect(current.dataset.value).toBe('legacy-model-v1');
    expect(current.textContent).toContain('not configured');

    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    current.click();
    await el.updateComplete;
    document.removeEventListener('set-model', handler);
    expect(handler).not.toHaveBeenCalled();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('filters by model key and by provider name', async () => {
    const el = await mount();
    await open(el);

    await type(el, 'claude');
    expect(models(el)).toEqual(['anthropic/claude']);

    await type(el, 'LOCAL');
    expect(models(el)).toEqual(['local/llama']);

    await type(el, 'zzz');
    expect(models(el)).toEqual([]);
    expect(el.querySelector('.picker-popup-no-match').textContent).toContain('No matches');
  });

  it('dispatches one set-model for a new choice and none for the current one', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    await open(el);

    const current = [...el.querySelectorAll('.picker-popup-option')]
      .find((row) => row.dataset.value === 'anthropic/claude');
    current.click();
    await el.updateComplete;
    expect(handler).not.toHaveBeenCalled();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();

    await open(el);
    const other = [...el.querySelectorAll('.picker-popup-option')]
      .find((row) => row.dataset.value === 'local/llama');
    other.click();
    await el.updateComplete;

    document.removeEventListener('set-model', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'local/llama' });
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const el = await mount();
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));
    search.focus();
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await el.updateComplete;

    expect(el.querySelector('.picker-popup-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.picker-popup-trigger'));
  });

  it('closes on a click outside the picker', async () => {
    const el = await mount();
    await open(el);
    expect(el.querySelector('.picker-popup-panel')).toBeTruthy();

    document.body.click();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('moves the active row with Arrow keys and selects it with Enter', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.picker-popup-search'));

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-option.highlighted').dataset.value).toBe('anthropic/haiku');

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await el.updateComplete;
    document.removeEventListener('set-model', handler);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'anthropic/haiku' });
  });

  it('closes the panel when convId changes', async () => {
    const el = await mount({ convId: 'c1' });
    await open(el);
    expect(el.querySelector('.picker-popup-panel')).toBeTruthy();

    el.convId = 'c2';
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('renders inline without a trigger or panel', async () => {
    const el = await mount({ inline: true });
    expect(el.querySelector('.picker-popup-trigger')).toBeNull();
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
    expect(el.querySelector('.picker-popup-search')).toBeTruthy();
    expect(models(el)).toEqual(['anthropic/claude', 'anthropic/haiku', 'local/llama']);

    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    el.querySelector('[data-value="local/llama"]').click();
    await el.updateComplete;
    document.removeEventListener('set-model', handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('disables the trigger', async () => {
    const el = await mount({ disabled: true });
    const trigger = el.querySelector('.picker-popup-trigger');
    expect(trigger.disabled).toBe(true);
    trigger.click();
    await el.updateComplete;
    expect(el.querySelector('.picker-popup-panel')).toBeNull();
  });

  it('never offers an inherit row and ignores an empty pick', async () => {
    // #2011: `""` is a settings concept. A conversation has no "unset model",
    // so the consumer must not render one and must not forward one.
    const el = await mount();
    await open(el);
    expect(models(el)).not.toContain('');

    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    await open(el);
    const popup = popupOf(el);
    popup.dispatchEvent(new CustomEvent('pick', { detail: { value: '', item: { value: '' } }, bubbles: true }));
    await el.updateComplete;
    document.removeEventListener('set-model', handler);
    expect(handler).not.toHaveBeenCalled();
  });
});
