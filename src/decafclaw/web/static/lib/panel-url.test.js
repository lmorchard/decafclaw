import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  PANEL_PARAMS,
  clearPanel,
  getOpenPanel,
  getPanel,
  hideSiblingPanels,
  setPanel,
} from './panel-url.js';

/** Navigate to a query string ('' for the bare path). */
function navigate(search) {
  history.replaceState(null, '', search ? '?' + search : '/');
}

afterEach(() => {
  navigate('');
});

describe('setPanel', () => {
  it('sets the param and pushes a history entry', () => {
    const before = history.length;
    setPanel('wiki', 'My Page');
    expect(new URLSearchParams(location.search).get('vault')).toBe('My Page');
    expect(history.length).toBe(before + 1);
  });

  it('drops the sibling params when opening a panel', () => {
    navigate('file=a.txt&schedule=nightly');
    setPanel('wiki', 'Notes');
    const params = new URLSearchParams(location.search);
    expect(params.get('vault')).toBe('Notes');
    expect(params.has('file')).toBe(false);
    expect(params.has('schedule')).toBe(false);
  });

  it('preserves unrelated query params', () => {
    navigate('conv=abc');
    setPanel('file', 'notes.md');
    const params = new URLSearchParams(location.search);
    expect(params.get('conv')).toBe('abc');
    expect(params.get('file')).toBe('notes.md');
  });

  it('replaces instead of pushing when replace is set', () => {
    const before = history.length;
    setPanel('wiki', 'Notes', { replace: true });
    expect(history.length).toBe(before);
  });

  it('replaces without a history entry when the value is unchanged', () => {
    navigate('vault=Notes');
    const before = history.length;
    setPanel('wiki', 'Notes');
    expect(history.length).toBe(before);
  });

  it('rejects an unknown panel name', () => {
    expect(() => setPanel('config', 'x')).toThrow();
  });
});

describe('clearPanel', () => {
  it('removes only its own param', () => {
    navigate('conv=abc&file=a.txt');
    clearPanel('file');
    const params = new URLSearchParams(location.search);
    expect(params.has('file')).toBe(false);
    expect(params.get('conv')).toBe('abc');
  });

  it('is a no-op when the param is absent', () => {
    const spy = vi.spyOn(history, 'replaceState');
    clearPanel('schedule');
    expect(spy).not.toHaveBeenCalled();
  });

  it('drops the query string entirely when it was the only param', () => {
    navigate('file=a.txt');
    clearPanel('file');
    expect(location.search).toBe('');
  });

  it('rejects an unknown panel name', () => {
    expect(() => clearPanel('config')).toThrow();
  });
});

describe('getPanel / getOpenPanel', () => {
  it('reads the param or null when absent', () => {
    navigate('vault=Notes');
    expect(getPanel('wiki')).toBe('Notes');
    expect(getPanel('file')).toBeNull();
  });

  it.each([
    ['vault=Notes', 'wiki'],
    ['file=a.txt', 'file'],
    ['schedule=nightly', 'schedule'],
  ])('getOpenPanel reports the %s panel from ?%s', (search, name) => {
    navigate(search);
    expect(getOpenPanel()).toEqual({ name, value: new URLSearchParams('?' + search).get(PANEL_PARAMS[name]) });
  });

  it('prefers wiki over file and schedule', () => {
    navigate('vault=Notes&file=a.txt&schedule=nightly');
    expect(getOpenPanel()).toEqual({ name: 'wiki', value: 'Notes' });
  });

  it('returns null when no panel is open', () => {
    navigate('conv=abc');
    expect(getOpenPanel()).toBeNull();
  });

  it('rejects an unknown panel name', () => {
    expect(() => getPanel('config')).toThrow();
  });
});

describe('hideSiblingPanels', () => {
  /** @returns {{el: HTMLDivElement, reset: ReturnType<typeof vi.fn>}} */
  function fakePanel() {
    return { el: document.createElement('div'), reset: vi.fn() };
  }

  it('hides and resets every sibling but not self', () => {
    const wiki = fakePanel();
    const file = fakePanel();
    const schedule = fakePanel();
    const config = { el: document.createElement('div') }; // no reset, like the real config panel
    hideSiblingPanels({ wiki, file, schedule, config }, 'wiki');
    expect(wiki.el.classList.contains('hidden')).toBe(false);
    expect(file.el.classList.contains('hidden')).toBe(true);
    expect(file.reset).toHaveBeenCalledTimes(1);
    expect(schedule.el.classList.contains('hidden')).toBe(true);
    expect(schedule.reset).toHaveBeenCalledTimes(1);
    expect(config.el.classList.contains('hidden')).toBe(true);
  });

  it('skips siblings without an element and a missing self entry', () => {
    const file = fakePanel();
    /** @type {{el: any | null, reset?: () => void}} */
    const wiki = { el: null, reset: vi.fn() };
    // self ('schedule') is absent from the record entirely.
    hideSiblingPanels({ wiki, file }, 'schedule');
    expect(wiki.reset).not.toHaveBeenCalled();
    expect(file.el.classList.contains('hidden')).toBe(true);
  });
});
