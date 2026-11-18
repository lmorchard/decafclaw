import { afterEach, describe, expect, it } from 'vitest';

import { DEFAULT_PAGE_TITLE, applyPageTitle, buildPageTitle } from './page-title.js';

describe('buildPageTitle (#1503)', () => {
  it('uses the plain app name when no conversation is selected', () => {
    expect(buildPageTitle()).toBe(DEFAULT_PAGE_TITLE);
    expect(buildPageTitle('')).toBe(DEFAULT_PAGE_TITLE);
    expect(buildPageTitle('   ', 'idle')).toBe(DEFAULT_PAGE_TITLE);
  });

  it('names the selected conversation while idle', () => {
    expect(buildPageTitle('Fix the login flow')).toBe('Fix the login flow — DecafClaw');
    expect(buildPageTitle('Fix the login flow', 'idle')).toBe('Fix the login flow — DecafClaw');
  });

  it.each([
    ['busy', '◐'],
    ['waiting', '●'],
  ])('marks the %s status with its own glyph', (status, marker) => {
    expect(buildPageTitle('Fix the login flow', status)).toBe(`${marker} Fix the login flow — DecafClaw`);
    expect(buildPageTitle('', status)).toBe(`${marker} DecafClaw`);
  });

  it('leaves the title unmarked for finished and unknown statuses', () => {
    // `finished` belongs to conversations that are not selected; the store
    // resets the selected one to idle, so a marker here would never render.
    expect(buildPageTitle('Chat', 'finished')).toBe('Chat — DecafClaw');
    expect(buildPageTitle('Chat', 'something-else')).toBe('Chat — DecafClaw');
  });
});

describe('applyPageTitle (#1503)', () => {
  afterEach(() => {
    document.title = DEFAULT_PAGE_TITLE;
  });

  it('writes the composed title to the document for each transition', () => {
    applyPageTitle('Release notes', 'busy');
    expect(document.title).toBe('◐ Release notes — DecafClaw');

    applyPageTitle('Release notes', 'waiting');
    expect(document.title).toBe('● Release notes — DecafClaw');

    applyPageTitle('', 'idle');
    expect(document.title).toBe(DEFAULT_PAGE_TITLE);
  });
});
