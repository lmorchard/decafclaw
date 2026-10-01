import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversationStore } from './conversation-store.js';

const operations = [
  ['listConversations', '/api/conversations', 'conversations', 'folders', 'currentFolder'],
  ['listArchivedConversations', '/api/conversations/archived', 'archivedConversations', 'archivedFolders', 'archivedCurrentFolder'],
  ['listSystemConversations', '/api/conversations/system', 'systemConversations', 'systemFolders', 'systemCurrentFolder'],
];
const nested = 'Work space/日本語 & plus+ #hash';
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe.each(operations)('%s generated transport', (method, route, itemsKey, foldersKey, folderKey) => {
  const folders = method === 'listSystemConversations'
    ? [undefined, '', 'heartbeat', 'schedule', 'delegated'] : [undefined, '', nested];
  it.each(folders)('publishes returned state for folder %s', async (folder) => {
    const item = { conv_id: 'c1', title: 'A chat', updated_at: '2026-01-02',
      ...(method === 'listSystemConversations' ? { conv_type: folder || 'heartbeat' } : { created_at: '2026-01-01' }) };
    const body = { folder: folder || '', folders: [{ name: 'Child', path: 'child' }], conversations: [item] };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = new ConversationStore(new EventTarget());
    const changed = vi.fn(() => {
      expect(store[itemsKey]).toEqual(body.conversations);
      expect(store[foldersKey]).toEqual(body.folders);
      expect(store[folderKey]).toBe(body.folder);
    });
    store.addEventListener('change', changed);
    await store[method](folder);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    const parsed = new URL(url, 'http://test');
    expect(parsed.pathname).toBe(route);
    expect(parsed.searchParams.getAll('folder')).toEqual(folder ? [folder] : []);
    expect([...parsed.searchParams.keys()]).toEqual(folder ? ['folder'] : []);
    expect(parsed.hash).toBe('');
    expect(options.method).toBe('GET');
    expect(options.credentials).toBe('same-origin');
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it.each(['http', 'network', 'json'])('retains state after %s failure', async (failure) => {
    const body = { folder: 'old', folders: [{ name: 'Old', path: 'old' }], conversations: [{ conv_id: 'old' }] };
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(body)));
    vi.stubGlobal('fetch', fetchMock);
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const store = new ConversationStore(new EventTarget());
    await store[method]('old');
    const items = store[itemsKey];
    const entries = store[foldersKey];
    const changed = vi.fn();
    store.addEventListener('change', changed);
    if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('offline'));
    else fetchMock.mockResolvedValueOnce(new Response('not json', { status: failure === 'http' ? 400 : 200 }));
    await store[method]('new');
    expect(store[itemsKey]).toBe(items);
    expect(store[foldersKey]).toBe(entries);
    expect(store[folderKey]).toBe('old');
    expect(changed).not.toHaveBeenCalled();
    expect(log).toHaveBeenCalledTimes(failure === 'http' ? 0 : 1);
    if (failure !== 'http') expect(log.mock.calls[0][0]).toBe(
      `Failed to list ${method === 'listConversations' ? '' : method === 'listArchivedConversations' ? 'archived ' : 'system '}conversations:`);
  });
});
