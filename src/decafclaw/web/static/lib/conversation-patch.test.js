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
  it('merges rename metadata into the matching typed listing and emits once', async () => {
    const { store, fetchMock, changed } = await seeded();
    const unrelated = store.conversations[1];
    const updated = { ...original, title, updated_at: 'after' };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(updated)));
    const generated = vi.spyOn(DefaultService, 'renameConversationApiConversationsIdPatch');
    await store.renameConversation(id, title);
    expect(generated).toHaveBeenCalledWith(id, { title });
    expectPatch(fetchMock, { title });
    expect(store.conversations).toEqual([updated, other]);
    expect(store.conversations[1]).toBe(unrelated);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it.each(['', 'malformed', '{"title":"unused"}'])('move ignores body %s and refreshes current folder', async (body) => {
    const { store, fetchMock, changed } = await seeded();
    const response = new Response(body);
    const json = vi.spyOn(response, 'json');
    const text = vi.spyOn(response, 'text');
    fetchMock.mockResolvedValueOnce(response).mockResolvedValueOnce(new Response(JSON.stringify({
      folder, folders: [], conversations: [other],
    })));
    const generated = vi.spyOn(DefaultService, 'renameConversationApiConversationsIdPatch');
    await store.moveConversation(id, folder);
    expect(generated).toHaveBeenCalledWith(id, { folder }, true);
    expectPatch(fetchMock, { folder });
    expect(json).not.toHaveBeenCalled();
    expect(text).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, options] = fetchMock.mock.calls[1];
    expect(new URL(url, 'http://test').searchParams.get('folder')).toBe(folder);
    expect(options.method).toBe('GET');
    expect(store.conversations).toEqual([other]);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  for (const method of ['renameConversation', 'moveConversation']) {
    it.each(['http', 'network', ...(method === 'renameConversation' ? ['json'] : [])])(
      `${method} preserves state on %s failure`, async (failure) => {
        const { store, fetchMock, changed } = await seeded();
        const state = store.conversations;
        const log = vi.spyOn(console, 'error').mockImplementation(() => {});
        if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('offline'));
        else fetchMock.mockResolvedValueOnce(new Response('malformed', { status: failure === 'http' ? 400 : 200 }));
        await store[method](id, method === 'renameConversation' ? title : folder);
        expect(store.conversations).toBe(state);
        expect(store.currentFolder).toBe(folder);
        expect(changed).not.toHaveBeenCalled();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(log).toHaveBeenCalledTimes(failure === 'http' ? 0 : 1);
        if (failure !== 'http') expect(log.mock.calls[0][0]).toBe(
          `Failed to ${method === 'renameConversation' ? 'rename' : 'move'} conversation:`);
      });
  }
});
