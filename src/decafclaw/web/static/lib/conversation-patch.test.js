import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversationStore } from './conversation-store.js';
import { DefaultService } from './api-client/index.js';

const id = 'web-alice-2026.10_01--abc';
const title = 'Title 日本語 & + #';
const folder = 'Work space/日本語 & + #';
const original = { conv_id: id, title: 'Old', created_at: 'before', updated_at: 'before' };
const other = { ...original, conv_id: 'other' };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function seeded() {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    folder, folders: [], conversations: [original, other],
  })));
  vi.stubGlobal('fetch', fetchMock);
  const store = new ConversationStore(new EventTarget());
  await store.listConversations(folder);
  fetchMock.mockClear();
  const changed = vi.fn();
  store.addEventListener('change', changed);
  return { store, fetchMock, changed };
}
function expectPatch(fetchMock, body) {
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe(`/api/conversations/${id}`);
  expect(options.method).toBe('PATCH');
  expect(new Headers(options.headers).get('Content-Type')).toBe('application/json');
  expect(options.credentials).toBe('same-origin');
  expect(JSON.parse(options.body)).toEqual(body);
}

describe('conversation PATCH generated transport', () => {
  it('merges rename metadata into the matching typed listing and refreshes the hierarchy', async () => {
    const { store, fetchMock, changed } = await seeded();
    const unrelated = store.conversations[1];
    const updated = { ...original, title, updated_at: 'after' };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(updated)));
    // #1274 — rename also refreshes the session hierarchy (the sidebar source).
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ folder, sessions: [] })));
    const generated = vi.spyOn(DefaultService, 'renameConversationApiConversationsIdPatch');
    await store.renameConversation(id, title);
    expect(generated).toHaveBeenCalledWith(id, { title });
    expectPatch(fetchMock, { title });
    expect(store.conversations).toEqual([updated, other]);
    expect(store.conversations[1]).toBe(unrelated);
    // Two emissions: the in-memory flat-list patch, then the sessions refetch.
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it.each(['', 'malformed', '{"title":"unused"}'])('move ignores body %s and refreshes current folder', async (body) => {
    const { store, fetchMock, changed } = await seeded();
    const response = new Response(body);
    const json = vi.spyOn(response, 'json');
    const text = vi.spyOn(response, 'text');
    fetchMock.mockResolvedValueOnce(response)
      .mockResolvedValueOnce(new Response(JSON.stringify({ folder, folders: [], conversations: [other] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ folder, sessions: [] })));
    const generated = vi.spyOn(DefaultService, 'renameConversationApiConversationsIdPatch');
    const moved = await store.moveConversation(id, folder);
    expect(moved).toBe(true);
    expect(generated).toHaveBeenCalledWith(id, { folder }, true);
    expectPatch(fetchMock, { folder });
    expect(json).not.toHaveBeenCalled();
    expect(text).not.toHaveBeenCalled();
    // #1274 — move also refreshes the session hierarchy (3rd fetch).
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [url, options] = fetchMock.mock.calls[1];
    expect(new URL(url, 'http://test').searchParams.get('folder')).toBe(folder);
    expect(options.method).toBe('GET');
    expect(String(fetchMock.mock.calls[2][0])).toContain('/api/sessions');
    expect(store.conversations).toEqual([other]);
    // Two emissions: the flat-list refetch, then the sessions refetch.
    expect(changed).toHaveBeenCalledTimes(2);
  });

  for (const method of ['renameConversation', 'moveConversation']) {
    it.each(['http', 'network', ...(method === 'renameConversation' ? ['json'] : [])])(
      `${method} preserves state on %s failure`, async (failure) => {
        const { store, fetchMock, changed } = await seeded();
        const state = store.conversations;
        const log = vi.spyOn(console, 'error').mockImplementation(() => {});
        if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('offline'));
        else fetchMock.mockResolvedValueOnce(new Response('malformed', { status: failure === 'http' ? 400 : 200 }));
        const result = await store[method](id, method === 'renameConversation' ? title : folder);
        expect(store.conversations).toBe(state);
        expect(store.currentFolder).toBe(folder);
        expect(changed).not.toHaveBeenCalled();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        if (method === 'moveConversation') expect(result).toBe(false);
        expect(log).toHaveBeenCalledTimes(failure === 'http' ? 0 : 1);
        if (failure !== 'http') expect(log.mock.calls[0][0]).toBe(
          `Failed to ${method === 'renameConversation' ? 'rename' : 'move'} conversation:`);
      });
  }
});

describe('conversation folder discovery (#1277)', () => {
  it('walks every level and excludes virtual and reserved folders', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        folder: '',
        folders: [
          { name: 'Work', path: 'Work' },
          { name: 'Archived', path: '_archived', virtual: true },
          { name: 'System', path: '_system', virtual: true },
        ],
        conversations: [],
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        folder: 'Work', folders: [{ name: 'Plans', path: 'Work/Plans' }], conversations: [],
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        folder: 'Work/Plans', folders: [], conversations: [],
      })));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ConversationStore(new EventTarget());

    const folders = await store.listAllFolders();

    expect(folders).toEqual(['Work', 'Work/Plans']);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const foldersQueried = fetchMock.mock.calls.map(
      ([url]) => new URL(url, 'http://test').searchParams.get('folder'),
    );
    expect(foldersQueried).toEqual([null, 'Work', 'Work/Plans']);
    // Reading the tree must not disturb the active listing.
    expect(store.conversations).toEqual([]);
    expect(store.currentFolder).toBe('');
  });
});
