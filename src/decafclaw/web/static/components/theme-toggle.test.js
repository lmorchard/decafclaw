/**
 * Accessible names for the theme toggle buttons (#166).
 */

import { afterEach, describe, expect, it } from 'vitest';

import { THEMES } from '../lib/theme.js';
import './theme-toggle.js';

/** @returns {Promise<any>} */
async function mount() {
  const el = /** @type {any} */ (document.createElement('theme-toggle'));
  document.body.appendChild(el);
  await el.updateComplete;
  return el;
}

/** @param {any} el @param {string} label */
const byLabel = (el, label) => el.querySelector(`button[aria-label="${label}"]`);

afterEach(() => {
  document.body.innerHTML = '';
  localStorage.clear();
});

describe('theme-toggle accessible button names', () => {
  it('labels each base theme button with the theme it switches to', async () => {
    const el = await mount();

    for (const t of THEMES.filter((t) => t.kind === 'base')) {
      expect(byLabel(el, `Switch to ${t.label} theme`)).not.toBeNull();
    }
  });

  it('labels the palette toggle button', async () => {
    const el = await mount();

    expect(byLabel(el, 'Select color palette')).not.toBeNull();
  });

  it('labels each palette item once the palette is open', async () => {
    const el = await mount();
    byLabel(el, 'Select color palette').click();
    await el.updateComplete;

    const palettes = THEMES.filter((t) => t.kind === 'palette');
    expect(palettes.length).toBeGreaterThan(0);
    for (const p of palettes) {
      expect(byLabel(el, `Select ${p.label} theme`)?.classList.contains('theme-palette-item')).toBe(true);
    }
  });

  it('gives every theme button a non-empty aria-label', async () => {
    const el = await mount();
    byLabel(el, 'Select color palette').click();
    await el.updateComplete;

    const buttons = [...el.querySelectorAll('button')];
    expect(buttons.length).toBe(THEMES.length + 1);
    for (const b of buttons) {
      expect(b.getAttribute('aria-label')?.trim()).toBeTruthy();
    }
  });
});
