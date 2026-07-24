import { describe, it, expect, beforeEach } from 'vitest';
import {
  THEMES, STORAGE_KEY, DEFAULT_THEME,
  resolveTheme, applyTheme, loadStoredTheme, storeTheme,
} from './theme.js';

describe('THEMES registry', () => {
  it('exposes the five expected themes in order', () => {
    expect(THEMES.map((t) => t.name)).toEqual([
      'light', 'dark', 'system', 'dracula', 'solarized-light',
    ]);
  });
  it('classifies base vs palette themes', () => {
    expect(THEMES.filter((t) => t.kind === 'base').map((t) => t.name))
      .toEqual(['light', 'dark', 'system']);
    expect(THEMES.filter((t) => t.kind === 'palette').map((t) => t.name))
      .toEqual(['dracula', 'solarized-light']);
  });
});

describe('resolveTheme', () => {
  it('resolves base themes to data-theme with no palette', () => {
    expect(resolveTheme('light')).toMatchObject({ dataTheme: 'light', dataPalette: null });
    expect(resolveTheme('dark')).toMatchObject({ dataTheme: 'dark', dataPalette: null });
  });
  it('resolves system to no attributes', () => {
    expect(resolveTheme('system')).toMatchObject({ dataTheme: null, dataPalette: null });
  });
  it('resolves palette themes to base + palette', () => {
    expect(resolveTheme('dracula')).toMatchObject({ dataTheme: 'dark', dataPalette: 'dracula' });
    expect(resolveTheme('solarized-light'))
      .toMatchObject({ dataTheme: 'light', dataPalette: 'solarized-light' });
  });
  it('falls back to the default theme for unknown or null names', () => {
    expect(resolveTheme('nope').name).toBe(DEFAULT_THEME);
    expect(resolveTheme(null).name).toBe(DEFAULT_THEME);
  });
});

describe('applyTheme', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-palette');
  });
  it('sets data-theme for a base theme and leaves palette absent', () => {
    applyTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.hasAttribute('data-palette')).toBe(false);
  });
  it('clears data-theme for system', () => {
    document.documentElement.setAttribute('data-theme', 'dark');
    applyTheme('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
  it('sets both attributes for a palette theme', () => {
    applyTheme('dracula');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-palette')).toBe('dracula');
  });
  it('switching from a palette back to a base clears the palette', () => {
    applyTheme('dracula');
    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.hasAttribute('data-palette')).toBe(false);
  });
  it('returns the resolved theme name (default for bogus input)', () => {
    expect(applyTheme('dracula')).toBe('dracula');
    expect(applyTheme('bogus')).toBe(DEFAULT_THEME);
  });
});

describe('persistence', () => {
  beforeEach(() => localStorage.clear());
  it('stores and loads a theme name', () => {
    storeTheme('dracula');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dracula');
    expect(loadStoredTheme()).toBe('dracula');
  });
  it('defaults to system when nothing is stored', () => {
    expect(loadStoredTheme()).toBe('system');
  });
  it('is backward compatible with legacy base values', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    expect(loadStoredTheme()).toBe('dark');
  });
  it('falls back to system for an unknown stored value', () => {
    localStorage.setItem(STORAGE_KEY, 'winamp');
    expect(loadStoredTheme()).toBe('system');
  });
});
