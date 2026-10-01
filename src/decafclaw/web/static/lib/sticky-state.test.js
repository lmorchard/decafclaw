import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let sticky;
let fetchMock;
let warn;
const payload = { content: '# Doc', extra: { rows: [1, true, null, { label: 'nested' }] } };
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

beforeEach(async () => {
  vi.resetModules();
  localStorage.clear();
  vi.stubGlobal('window', new EventTarget());
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
  fetchMock = vi.fn().mockResolvedValue(json({ widget_type: null, data: null }));
  vi.stubGlobal('fetch', fetchMock);
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  sticky = await import('./sticky-state.js');
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('sticky lookup through the generated client', () => {
  it('uses its string identifier and session credentials, and publishes the response', async () => {
    const { DefaultService } = await import('./api-client/index.js');
    const generated = vi.spyOn(DefaultService, 'getStickyStateApiStickyConvIdGet');
    fetchMock.mockResolvedValue(json({ widget_type: 'markdown_document', data: payload }));
    const subscriber = vi.fn();
    const unsubscribe = sticky.subscribe(subscriber);
    await sticky.setActiveConv('web-safe_123');
    expect(generated).toHaveBeenCalledWith('web-safe_123');
    expect(fetchMock).toHaveBeenCalledWith('/api/sticky/web-safe_123', expect.objectContaining({
      method: 'GET', credentials: 'same-origin',
    }));
    expect(sticky.currentSnapshot()).toEqual({
      widgetType: 'markdown_document', data: payload, collapsed: false, visible: true,
    });
    expect(subscriber).toHaveBeenCalledExactlyOnceWith(sticky.currentSnapshot());
    unsubscribe();
    await sticky.setActiveConv(null);
    expect(subscriber).toHaveBeenCalledTimes(1);
  });

  it('encodes one path segment without allowing a query or fragment', async () => {
    await sticky.setActiveConv('id /?key=value#fragment%é');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/sticky/id%20%2F%3Fkey%3Dvalue%23fragment%25%C3%A9');
  });

  it('clears a cached widget when the server returns the empty envelope', async () => {
    sticky.applyEvent({ type: 'sticky_set', conv_id: 'a', widget_type: 'markdown_document', data: payload });
    await sticky.setActiveConv('a');
    expect(sticky.currentSnapshot()).toMatchObject({ widgetType: null, data: null, visible: false });
  });

  it('publishes an empty snapshot without requesting when there is no identifier', async () => {
    const subscriber = vi.fn();
    sticky.subscribe(subscriber);
    await sticky.setActiveConv(null);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(subscriber).toHaveBeenCalledExactlyOnceWith({
      widgetType: null, data: null, collapsed: false, visible: false,
    });
  });

  it.each([401, 404, 500])('retains cached state and publishes silently on HTTP %s', async (status) => {
    sticky.applyEvent({ type: 'sticky_set', conv_id: 'a', widget_type: 'markdown_document', data: payload });
    // Even a non-JSON error body must not become a decoding warning.
    fetchMock.mockResolvedValue(new Response('not JSON', { status, headers: { 'Content-Type': 'application/json' } }));
    const subscriber = vi.fn();
    sticky.subscribe(subscriber);
    await sticky.setActiveConv('a');
    expect(sticky.currentSnapshot()).toMatchObject({ widgetType: 'markdown_document', data: payload });
    expect(subscriber).toHaveBeenCalledExactlyOnceWith(sticky.currentSnapshot());
    expect(warn).not.toHaveBeenCalled();
  });

  it.each(['transport', 'JSON'])('retains cached state, warns, and publishes on %s failure', async (failure) => {
    sticky.applyEvent({ type: 'sticky_set', conv_id: 'a', widget_type: 'markdown_document', data: payload });
    if (failure === 'transport') fetchMock.mockRejectedValue(new Error('offline'));
    else fetchMock.mockResolvedValue(new Response('{', { headers: { 'Content-Type': 'application/json' } }));
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const subscriber = vi.fn();
    sticky.subscribe(subscriber);
    await sticky.setActiveConv('a');
    expect(sticky.currentSnapshot()).toMatchObject({ widgetType: 'markdown_document', data: payload });
    expect(subscriber).toHaveBeenCalledExactlyOnceWith(sticky.currentSnapshot());
    expect(warn).toHaveBeenCalledExactlyOnceWith('sticky state load failed', expect.any(Error));
    expect(error).not.toHaveBeenCalled();
  });

  it('keeps per-conversation caches and collapse preferences across switches and WS updates', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 404 }));
    localStorage.setItem('sticky-collapsed.b', 'true');
    await sticky.setActiveConv('a');
    sticky.applyEvent({ type: 'sticky_set', conv_id: 'a', widget_type: 'markdown_document', data: payload });
    sticky.toggleCollapsed();
    expect(localStorage.getItem('sticky-collapsed.a')).toBe('true');
    await sticky.setActiveConv('b');
    expect(sticky.currentSnapshot()).toMatchObject({ visible: false, collapsed: true });
    sticky.toggleCollapsed();
    const subscriber = vi.fn();
    sticky.subscribe(subscriber);
    sticky.applyEvent({ type: 'sticky_set', conv_id: 'a', widget_type: 'markdown_document', data: { content: 'updated' } });
    expect(subscriber).not.toHaveBeenCalled();
    await sticky.setActiveConv('a');
    expect(sticky.currentSnapshot()).toMatchObject({ data: { content: 'updated' }, collapsed: true });
    sticky.applyEvent({ type: 'sticky_clear', conv_id: 'a' });
    expect(subscriber).toHaveBeenLastCalledWith(expect.objectContaining({ visible: false, data: null }));
    await sticky.setActiveConv('b');
    expect(sticky.currentSnapshot().collapsed).toBe(false);
  });

  it('reloads only the active conversation on reconnect', async () => {
    window.dispatchEvent(new Event('ws-connected'));
    expect(fetchMock).not.toHaveBeenCalled();
    await sticky.setActiveConv('a');
    fetchMock.mockResolvedValue(json({ widget_type: 'markdown_document', data: payload }));
    const published = new Promise(resolve => sticky.subscribe(resolve));
    window.dispatchEvent(new Event('ws-connected'));
    expect(await published).toMatchObject({ widgetType: 'markdown_document', data: payload });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sticky/a');
  });
});
