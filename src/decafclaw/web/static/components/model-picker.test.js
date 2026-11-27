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
  return el;
}

/** Settings selection mode: a flat option list and an empty/inherit choice. @param {object} [props] */
async function mountSelection(props = {}) {
  const el = /** @type {any} */ (document.createElement('model-picker'));
  el.mode = 'selection';
  el.options = ['hand-model', 'local/llama'];
  el.value = '';
  el.emptyLabel = '(config.json default)';
  Object.assign(el, props);
  document.body.append(el);
  await el.updateComplete;
  return el;
}

/** @param {any} el */
async function open(el) {
  el.querySelector('.model-picker-trigger').click();
  await el.updateComplete;
  return el.querySelector('.model-picker-panel');
}

/** @param {any} el */
function models(el) {
  return [...el.querySelectorAll('.model-picker-option')].map((row) => row.dataset.model);
}

/** @param {any} el @param {string} value */
async function type(el, value) {
  const input = /** @type {HTMLInputElement} */ (el.querySelector('.model-picker-search'));
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await el.updateComplete;
  return input;
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('model-picker', () => {
  it('renders provider groups and marks the default model', async () => {
    const el = await mount();
    const trigger = el.querySelector('.model-picker-trigger');
    expect(trigger.textContent).toContain('anthropic/claude');
    expect(trigger.textContent).toContain('default');

    await open(el);
    const names = [...el.querySelectorAll('.model-picker-group-name')].map((n) => n.textContent.trim());
    expect(names).toEqual(['Anthropic', 'Local']);
    expect(models(el)).toEqual(['anthropic/claude', 'anthropic/haiku', 'local/llama']);

    const selected = el.querySelector('.model-picker-option.selected');
    expect(selected.dataset.model).toBe('anthropic/claude');
    expect(selected.querySelector('.model-picker-default').textContent).toBe('default');
  });

  it('flags an unconfigured active model and keeps it selectable', async () => {
    const el = await mount({ activeModel: 'legacy-model-v1' });
    expect(el.querySelector('.model-picker-trigger').textContent).toContain('not configured');

    await open(el);
    expect(el.querySelector('.model-picker-group-name').textContent.trim()).toBe('Current');
    const current = el.querySelector('.model-picker-option.selected');
    expect(current.dataset.model).toBe('legacy-model-v1');
    expect(current.textContent).toContain('not configured');

    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    current.click();
    await el.updateComplete;
    document.removeEventListener('set-model', handler);
    expect(handler).not.toHaveBeenCalled();
    expect(el.querySelector('.model-picker-panel')).toBeNull();
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
    expect(el.querySelector('.model-picker-no-match').textContent).toContain('No models match');
  });

  it('dispatches one set-model for a new choice and none for the current one', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    await open(el);

    const current = [...el.querySelectorAll('.model-picker-option')]
      .find((row) => row.dataset.model === 'anthropic/claude');
    current.click();
    await el.updateComplete;
    expect(handler).not.toHaveBeenCalled();
    expect(el.querySelector('.model-picker-panel')).toBeNull();

    await open(el);
    const other = [...el.querySelectorAll('.model-picker-option')]
      .find((row) => row.dataset.model === 'local/llama');
    other.click();
    await el.updateComplete;

    document.removeEventListener('set-model', handler);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'local/llama' });
    expect(el.querySelector('.model-picker-panel')).toBeNull();
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const el = await mount();
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.model-picker-search'));
    search.focus();
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await el.updateComplete;

    expect(el.querySelector('.model-picker-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.model-picker-trigger'));
  });

  it('closes on a click outside the picker', async () => {
    const el = await mount();
    await open(el);
    expect(el.querySelector('.model-picker-panel')).toBeTruthy();

    document.body.click();
    await el.updateComplete;
    expect(el.querySelector('.model-picker-panel')).toBeNull();
  });

  it('moves the active row with Arrow keys and selects it with Enter', async () => {
    const el = await mount();
    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    await open(el);
    const search = /** @type {HTMLInputElement} */ (el.querySelector('.model-picker-search'));

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await el.updateComplete;
    expect(el.querySelector('.model-picker-option.highlighted').dataset.model).toBe('anthropic/haiku');

    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await el.updateComplete;
    document.removeEventListener('set-model', handler);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({ model: 'anthropic/haiku' });
  });

  it('closes the panel when convId changes', async () => {
    const el = await mount({ convId: 'c1' });
    await open(el);
    expect(el.querySelector('.model-picker-panel')).toBeTruthy();

    el.convId = 'c2';
    await el.updateComplete;
    expect(el.querySelector('.model-picker-panel')).toBeNull();
  });

  it('renders inline without a trigger or panel', async () => {
    const el = await mount({ inline: true });
    expect(el.querySelector('.model-picker-trigger')).toBeNull();
    expect(el.querySelector('.model-picker-panel')).toBeNull();
    expect(el.querySelector('.model-picker-search')).toBeTruthy();
    expect(models(el)).toEqual(['anthropic/claude', 'anthropic/haiku', 'local/llama']);

    const handler = vi.fn();
    document.addEventListener('set-model', handler);
    el.querySelector('[data-model="local/llama"]').click();
    await el.updateComplete;
    document.removeEventListener('set-model', handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('disables the trigger', async () => {
    const el = await mount({ disabled: true });
    const trigger = el.querySelector('.model-picker-trigger');
    expect(trigger.disabled).toBe(true);
    trigger.click();
    await el.updateComplete;
    expect(el.querySelector('.model-picker-panel')).toBeNull();
  });

  describe('selection mode', () => {
    it('shows the empty label and a flat list with the empty choice', async () => {
      const el = await mountSelection();
      const trigger = el.querySelector('.model-picker-trigger');
      expect(trigger.textContent).toContain('(config.json default)');

      await open(el);
      expect(models(el)).toEqual(['', 'hand-model', 'local/llama']);
      const selected = el.querySelector('.model-picker-option.selected');
      expect(selected.dataset.model).toBe('');
      expect(selected.textContent).toContain('(config.json default)');
    });

    it('marks the selected model and shows it on the trigger', async () => {
      const el = await mountSelection({ value: 'hand-model' });
      expect(el.querySelector('.model-picker-trigger').textContent).toContain('hand-model');

      await open(el);
      const selected = el.querySelector('.model-picker-option.selected');
      expect(selected.dataset.model).toBe('hand-model');
    });

    it('filters the flat option list', async () => {
      const el = await mountSelection();
      await open(el);

      await type(el, 'llama');
      expect(models(el)).toEqual(['local/llama']);

      await type(el, 'HAND');
      expect(models(el)).toEqual(['hand-model']);
    });

    it('emits model-select instead of set-model', async () => {
      const el = await mountSelection();
      const selectHandler = vi.fn();
      const conversationHandler = vi.fn();
      document.addEventListener('model-select', selectHandler);
      document.addEventListener('set-model', conversationHandler);

      await open(el);
      el.querySelector('[data-model="local/llama"]').click();
      await el.updateComplete;

      document.removeEventListener('model-select', selectHandler);
      document.removeEventListener('set-model', conversationHandler);
      expect(selectHandler).toHaveBeenCalledTimes(1);
      expect(selectHandler.mock.calls[0][0].detail).toEqual({ value: 'local/llama' });
      expect(conversationHandler).not.toHaveBeenCalled();
      expect(el.querySelector('.model-picker-panel')).toBeNull();
    });

    it('emits an empty value when the inherit choice is picked', async () => {
      const el = await mountSelection({ value: 'hand-model' });
      const handler = vi.fn();
      document.addEventListener('model-select', handler);

      await open(el);
      el.querySelector('[data-model=""]').click();
      await el.updateComplete;

      document.removeEventListener('model-select', handler);
      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].detail).toEqual({ value: '' });
    });

    it('moves through the flat rows with Arrow keys and selects with Enter', async () => {
      const el = await mountSelection();
      const handler = vi.fn();
      document.addEventListener('model-select', handler);
      await open(el);
      const search = /** @type {HTMLInputElement} */ (el.querySelector('.model-picker-search'));

      // The empty/inherit row is highlighted when the pointer is empty.
      expect(el.querySelector('.model-picker-option.highlighted').dataset.model).toBe('');
      search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      await el.updateComplete;
      expect(el.querySelector('.model-picker-option.highlighted').dataset.model).toBe('hand-model');

      search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await el.updateComplete;
      document.removeEventListener('model-select', handler);

      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].detail).toEqual({ value: 'hand-model' });
    });
  });
});
