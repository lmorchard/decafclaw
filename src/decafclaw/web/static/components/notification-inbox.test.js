import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import './notification-inbox.js';
let fetchMock;
const record = { id: 'id ?#%é', timestamp: '2026-10-01T00:00:00Z', category: 'background',
  title: 'Finished', body: 'Details', priority: 'normal', link: null, conv_id: 'conversation', read: false };
const json = (data) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  fetchMock = vi.fn((url) => Promise.resolve(json(url.includes('unread-count') ? { count: 2 } : { records: [record], has_more: false })));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); });
async function mount() {
  const el = document.createElement('notification-inbox');
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._count).toBe(2));
  return el;
}
async function open(el) {
  el.querySelector('.notification-bell').click();
  await vi.waitFor(() => expect(el._loading).toBe(false));
  await el.updateComplete;
}
it('loads count and list through generated calls and renders records', async () => {
  const el = await mount(); await open(el);
  expect(fetchMock).toHaveBeenCalledWith('/api/notifications/unread-count', expect.objectContaining({ method: 'GET', credentials: 'same-origin', body: undefined }));
  expect(fetchMock).toHaveBeenCalledWith('/api/notifications?limit=20', expect.objectContaining({ method: 'GET', credentials: 'same-origin', body: undefined }));
  expect(document.querySelector('.notification-panel').textContent).toContain('Finished');
  expect(document.querySelector('.notification-panel').textContent).toContain('Details');
  expect(document.querySelector('.notification-panel').textContent).toContain('background');
});
it.each(['http', 'network', 'parse'])('keeps count failures quiet: %s', async (mode) => {
  const el = await mount();
  fetchMock.mockImplementation(() => mode === 'network' ? Promise.reject(new Error('offline')) :
    Promise.resolve(new Response('invalid', { status: mode === 'http' ? 500 : 200 })));
  window.dispatchEvent(new Event('ws-connected'));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(el._count).toBe(2); expect(el._error).toBe('');
});
it.each(['http', 'network', 'parse'])('shows list failures: %s', async (mode) => {
  const el = await mount();
  fetchMock.mockImplementation(() => mode === 'network' ? Promise.reject(new Error('offline')) :
    Promise.resolve(new Response('invalid', { status: mode === 'http' ? 500 : 200 })));
  await open(el);
  expect(el._error).toBeTruthy();
  if (mode === 'http') expect(el._error).toBe('HTTP 500');
  if (mode === 'network') expect(el._error).toBe('offline');
  expect(document.querySelector('.notification-panel').textContent).toContain('Error:');
});
it('shows loading, empty records, and refreshes from pushed server truth', async () => {
  const el = await mount(); let finish;
  fetchMock.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  el.querySelector('.notification-bell').click();
  await el.updateComplete;
  expect(document.querySelector('.notification-panel').textContent).toContain('Loading');
  await vi.waitFor(() => expect(finish).toBeDefined()); finish(json({ records: [], has_more: false }));
  await vi.waitFor(() => expect(el._loading).toBe(false)); await el.updateComplete;
  expect(document.querySelector('.notification-panel').textContent).toContain('No notifications');
  fetchMock.mockResolvedValue(json({ records: [record], has_more: false }));
  window.dispatchEvent(new CustomEvent('notification-read', { detail: { unread_count: 3 } }));
  await vi.waitFor(() => expect(el._records).toHaveLength(1)); expect(el._count).toBe(3);
});
it.each(['success', 'http', 'network'])('single-read stays optimistic and navigates after %s', async (mode) => {
  const el = await mount(); await open(el);
  const navigate = vi.fn(); el.addEventListener('navigate-conversation', navigate);
  let finish, fail;
  fetchMock.mockImplementation(() => new Promise((resolve, reject) => { finish = resolve; fail = reject; }));
  document.querySelector('.notification-row').click();
  expect(el._records[0].read).toBe(true); expect(el._count).toBe(1); expect(navigate).not.toHaveBeenCalled();
  await vi.waitFor(() => expect(finish).toBeDefined());
  expect(fetchMock).toHaveBeenLastCalledWith('/api/notifications/id%20%3F%23%25%C3%A9/read', expect.objectContaining({ method: 'POST', credentials: 'same-origin', body: undefined }));
  if (mode === 'network') fail(new Error('offline')); else finish(new Response('invalid JSON', { status: mode === 'http' ? 500 : 200 }));
  await vi.waitFor(() => expect(navigate).toHaveBeenCalledOnce());
  expect(navigate.mock.calls[0][0].detail).toEqual({ convId: 'conversation' }); expect(el._open).toBe(false);
  fetchMock.mockResolvedValue(json({ records: [record], has_more: false })); await open(el);
  expect(el._records[0].read).toBe(false);
});
it.each(['success', 'http', 'network'])('mark-all distinguishes HTTP responses from rejected requests: %s', async (mode) => {
  const el = await mount(); await open(el);
  let finish, fail;
  fetchMock.mockImplementation(() => new Promise((resolve, reject) => { finish = resolve; fail = reject; }));
  document.querySelector('.notification-mark-all').click();
  await vi.waitFor(() => expect(finish).toBeDefined());
  expect(el._count).toBe(2); expect(el._records[0].read).toBe(false);
  expect(fetchMock).toHaveBeenLastCalledWith('/api/notifications/read-all', expect.objectContaining({ method: 'POST', credentials: 'same-origin', body: undefined }));
  if (mode === 'network') fail(new Error('offline')); else finish(new Response('invalid JSON', { status: mode === 'http' ? 500 : 200 }));
  if (mode === 'network') {
    await new Promise(resolve => setTimeout(resolve, 0)); expect(el._count).toBe(2); expect(el._records[0].read).toBe(false);
  } else { await vi.waitFor(() => expect(el._count).toBe(0)); expect(el._records[0].read).toBe(true); }
});
