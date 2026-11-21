import { describe, expect, it } from 'vitest';

import { WebFetchResultWidget } from './widget.js';

async function makeWidget(data) {
  const el = new WebFetchResultWidget();
  el.data = data;
  document.body.append(el);
  await el.updateComplete;
  return el;
}

describe('WebFetchResultWidget rendering (#1625)', () => {
  it('renders a 200 outcome card with status, title, url, and facts', async () => {
    const el = await makeWidget({
      status: 200,
      status_text: 'OK',
      title: 'Example Domain',
      url: 'https://example.com/some/long/path?query=1',
      content_type: 'text/html',
      bytes: 1253,
      elapsed_ms: 184,
      truncated: false,
    });

    const statusEl = el.querySelector('.web-fetch-result__status');
    expect(statusEl).not.toBeNull();
    expect(statusEl.textContent).toContain('200 OK');
    expect(statusEl.classList.contains('web-fetch-result__status--ok')).toBe(true);

    const titleEl = el.querySelector('.web-fetch-result__title');
    expect(titleEl).not.toBeNull();
    expect(titleEl.textContent).toBe('Example Domain');

    const urlEl = el.querySelector('.web-fetch-result__url');
    expect(urlEl.textContent).toContain('example.com');

    const factsEl = el.querySelector('.web-fetch-result__facts');
    expect(factsEl.textContent).toContain('text/html');
    expect(factsEl.textContent).toContain('1.2 KB');
    expect(factsEl.textContent).toContain('184 ms');
    expect(factsEl.textContent).not.toContain('truncated');

    el.remove();
  });

  it('marks truncation with a warning fact', async () => {
    const el = await makeWidget({
      status: 200,
      url: 'https://example.com/',
      bytes: 60000,
      truncated: true,
    });

    const warn = el.querySelector('.web-fetch-result__fact--warn');
    expect(warn).not.toBeNull();
    expect(warn.textContent).toBe('truncated');

    el.remove();
  });

  it('renders redirect target when final_url differs from url', async () => {
    const el = await makeWidget({
      status: 301,
      status_text: 'Moved Permanently',
      url: 'http://example.com/',
      final_url: 'https://example.com/',
    });

    const statusEl = el.querySelector('.web-fetch-result__status');
    expect(statusEl.classList.contains('web-fetch-result__status--redirect')).toBe(true);

    const redirectEl = el.querySelector('.web-fetch-result__redirect');
    expect(redirectEl).not.toBeNull();
    expect(redirectEl.textContent).toContain('redirected to');
    expect(redirectEl.textContent).toContain('https://example.com');
    expect(redirectEl.querySelector('span')?.getAttribute('title')).toBe('https://example.com/');

    el.remove();
  });

  it('applies error tone for 4xx/5xx responses', async () => {
    const el = await makeWidget({
      status: 404,
      status_text: 'Not Found',
      url: 'https://example.com/missing',
    });

    const statusEl = el.querySelector('.web-fetch-result__status');
    expect(statusEl.classList.contains('web-fetch-result__status--error')).toBe(true);
    expect(statusEl.textContent).toContain('404 Not Found');

    el.remove();
  });

  it('renders safely when data is empty or missing optional fields', async () => {
    const el = await makeWidget({});

    const statusEl = el.querySelector('.web-fetch-result__status');
    expect(statusEl).not.toBeNull();
    expect(el.querySelector('.web-fetch-result__title')).toBeNull();
    expect(el.querySelector('.web-fetch-result__redirect')).toBeNull();

    el.remove();
  });
});
