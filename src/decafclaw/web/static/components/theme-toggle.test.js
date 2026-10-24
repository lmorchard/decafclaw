/**
 * Accessible names and interaction for consolidated theme toggle (#166, #1150).
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
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.removeAttribute('data-palette');
});

describe('theme-toggle consolidated control', () => {
  it('renders a single trigger button when closed', async () => {
    const el = await mount();
    const buttons = [...el.querySelectorAll('button')];
    expect(buttons.length).toBe(1);
    const trigger = buttons[0];
    expect(trigger.getAttribute('aria-label')).toBe('Select theme');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.textContent.trim()).toBe('💻'); // default is system
  });

  it('opens menu with base themes and palettes on click', async () => {
    const el = await mount();
    byLabel(el, 'Select theme').click();
    await el.updateComplete;

    expect(byLabel(el, 'Select theme')?.getAttribute('aria-expanded')).toBe('true');

    for (const t of THEMES.filter((t) => t.kind === 'base')) {
      expect(byLabel(el, `Switch to ${t.label} theme`)).not.toBeNull();
    }

    const palettes = THEMES.filter((t) => t.kind === 'palette');
    for (const p of palettes) {
      expect(byLabel(el, `Select ${p.label} theme`)?.classList.contains('theme-palette-item')).toBe(true);
    }

    const allButtons = [...el.querySelectorAll('button')];
    expect(allButtons.length).toBe(THEMES.length + 1);
    for (const b of allButtons) {
      expect(b.getAttribute('aria-label')?.trim()).toBeTruthy();
    }
  });

  it('selects a base theme, updates icon, applies theme, and closes menu', async () => {
    const el = await mount();
    byLabel(el, 'Select theme').click();
    await el.updateComplete;

    byLabel(el, 'Switch to Dark theme').click();
    await el.updateComplete;

    expect(el.querySelector('.theme-palette-menu')).toBeNull();
    const trigger = byLabel(el, 'Select theme');
    expect(trigger?.textContent.trim()).toBe('🌙');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.hasAttribute('data-palette')).toBe(false);
  });

  it('selects a palette theme, updates icon to palette, applies theme, and closes menu', async () => {
    const el = await mount();
    byLabel(el, 'Select theme').click();
    await el.updateComplete;

    byLabel(el, 'Select Dracula theme').click();
    await el.updateComplete;

    expect(el.querySelector('.theme-palette-menu')).toBeNull();
    const trigger = byLabel(el, 'Select theme');
    expect(trigger?.textContent.trim()).toBe('🎨');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-palette')).toBe('dracula');
  });

  it('closes on Escape or click outside', async () => {
    const el = await mount();
    byLabel(el, 'Select theme').click();
    await el.updateComplete;
    expect(el.querySelector('.theme-palette-menu')).not.toBeNull();

    // Escape
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await el.updateComplete;
    expect(el.querySelector('.theme-palette-menu')).toBeNull();

    // Reopen and outside click
    byLabel(el, 'Select theme').click();
    await el.updateComplete;
    expect(el.querySelector('.theme-palette-menu')).not.toBeNull();

    document.dispatchEvent(new MouseEvent('click'));
    await el.updateComplete;
    expect(el.querySelector('.theme-palette-menu')).toBeNull();
  });
});
