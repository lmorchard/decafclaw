import { afterEach, describe, expect, it } from 'vitest';
import {
  LEGACY_STORAGE_KEY,
  STORAGE_KEY,
  loadShowContextProvenance,
  storeShowContextProvenance,
} from './context-provenance-preference.js';

describe('context provenance preference (#1563)', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults on', () => {
    expect(loadShowContextProvenance()).toBe(true);
  });

  it('honors the current key', () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    expect(loadShowContextProvenance()).toBe(false);
  });

  it('migrates the pre-#1563 recalled-facts key when the new key is absent', () => {
    localStorage.setItem(LEGACY_STORAGE_KEY, 'false');
    expect(loadShowContextProvenance()).toBe(false);

    storeShowContextProvenance(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true');
    expect(loadShowContextProvenance()).toBe(true);
  });
});
