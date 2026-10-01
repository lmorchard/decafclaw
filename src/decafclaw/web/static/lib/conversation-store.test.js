import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConversationStore } from './conversation-store.js';
import { MESSAGE_TYPES } from './message-types.js';
// The wire-type manifest, not the generated JS constants: the manifest is the
// only place `direction` is recorded, and reading it directly means a renamed
// or removed type changes this set instead of silently shrinking it (a
// `MESSAGE_TYPES.GONE` reference would just evaluate to `undefined`, and
// tsconfig excludes `**/*.test.js` so nothing would flag it).
import wireManifest from '../../message_types.json';

/**
 * Stand-in for WebSocketClient: same event surface (it is an EventTarget that
 * dispatches `open` / `message`), but `send()` records instead of writing to a
 * socket. `open` is dispatched by hand to simulate a reconnect.
 */
class FakeWS extends EventTarget {
  /** @type {object[]} */
  sent = [];

  /** @param {object} message */
  send(message) {
    this.sent.push(message);
  }

  /** Simulate the underlying socket (re)connecting. */
  fireOpen() {
    this.dispatchEvent(new CustomEvent('open'));
  }

  /** Simulate a server→client frame arriving. @param {object} msg */
  fireMessage(msg) {
    this.dispatchEvent(new CustomEvent('message', { detail: msg }));
  }
}

/**
 * Client→server message types that exist today and are NOT subscription
 * requests. A resubscribe must be something *other* than one of these — e.g.
 * a re-sent `select_conv` or a purpose-built `resubscribe` type. Deliberately a
 * deny-list rather than an allow-list so the assertion stays agnostic about
 * which of those two designs the fix picks. `SELECT_CONV` is absent on purpose.
 */
const NON_SUBSCRIBE_TYPES = new Set([
  MESSAGE_TYPES.LOAD_HISTORY,
  MESSAGE_TYPES.SEND,
  MESSAGE_TYPES.CANCEL_TURN,
  MESSAGE_TYPES.SET_MODEL,
  MESSAGE_TYPES.SET_EFFORT,
  MESSAGE_TYPES.WIDGET_RESPONSE,
  MESSAGE_TYPES.CONFIRM_RESPONSE,
]);

/**
 * Every wire type the *server* actually dispatches on, derived from the
 * manifest's `direction`. A resubscribe has to be one of these: an invented
 * type (`{type: 'resubscribe'}` with no manifest entry and no handler), or a
 * missing `type`, or a server→client type echoed back, all reach the server as
 * `ws: unknown inbound message type` and come back as an error frame — the bug
 * unfixed with a green board.
 */
const CLIENT_TO_SERVER_TYPES = new Set(
  Object.entries(wireManifest.messages)
    .filter(([, spec]) => /** @type {any} */ (spec).direction === 'client_to_server')
    .map(([name]) => name),
);

/** Where `listConversations()` (no folder) fetches — conversation-store.js:181. */
const CONVERSATIONS_URL = '/api/conversations';

/** Let the `open` handler's async `listConversations()` fetch chain settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** @returns {ConversationStore} */
const makeStore = (/** @type {FakeWS} */ ws) =>
  new ConversationStore(/** @type {any} */ (ws));

describe('ConversationStore reconnect handling', () => {
  /** @type {FakeWS} */
  let ws;
  /** @type {import('vitest').Mock} */
  let fetchMock;

  beforeEach(() => {
    // The `open` handler calls listConversations(), which fetches. Stub it to
    // an empty listing so the handler neither throws nor leaves an unhandled
    // rejection that could be mistaken for the assertion failure below.
    fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ conversations: [], folders: [], folder: '' }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    ws = new FakeWS();
  });

  // Harness guard, not a criterion: if the manifest import or its relative
  // path ever breaks, CLIENT_TO_SERVER_TYPES silently empties and C1's
  // membership assertion below starts failing for a reason that has nothing to
  // do with the store. Fail here instead, where the message says so.
  it('reads client→server wire types from the manifest', () => {
    expect(CLIENT_TO_SERVER_TYPES.size).toBeGreaterThan(0);
    expect(CLIENT_TO_SERVER_TYPES).toContain(MESSAGE_TYPES.SELECT_CONV);
    expect(CLIENT_TO_SERVER_TYPES).not.toContain(MESSAGE_TYPES.CONV_SELECTED);
  });

  // C1: a reconnect must re-subscribe the fresh socket to the selected
  // conversation. Without it the server has no record of this socket's
  // interest, so every broadcast for `c1` (canvas updates, turn events) is
  // delivered to nobody until a full page reload.
  it('resubscribes the reopened socket to the selected conversation', async () => {
    const store = makeStore(ws);

    store.selectConversation('c1');
    await flush();

    /**
     * Assert one reconnect's worth of traffic, then reset the recorders so the
     * next reopen is measured on its own. Called twice: a one-shot guard
     * (`if (done) return;`) resubscribes the *first* reconnect and leaves every
     * later one broken, which is exactly the shape of the bug being fixed.
     * @param {number} round
     */
    const expectResubscribed = (round) => {
      const where = `reconnect #${round}`;

      // Checked first, and passing today: the pre-existing `open` handler
      // refreshes the conversation list (conversation-store.js:128). Rewriting
      // that line instead of adding alongside it would green the resubscribe
      // assertions below while silently killing list refresh on every
      // reconnect. Ordering it here also keeps this test's present-day failure
      // message about the missing resubscribe rather than the fetch stub.
      const listCalls = fetchMock.mock.calls.filter(([url]) => url === CONVERSATIONS_URL);
      expect(
        listCalls,
        `${where}: no fetch of ${CONVERSATIONS_URL} — the open handler's `
          + 'listConversations() refresh was replaced rather than added to',
      ).not.toHaveLength(0);

      const forC1 = ws.sent.filter((m) => /** @type {any} */ (m).conv_id === 'c1');
      expect(forC1, `${where}: nothing sent for c1`).not.toHaveLength(0);

      // Not merely a history refetch that happens to mention c1.
      const candidates = forC1.filter(
        (m) => !NON_SUBSCRIBE_TYPES.has(/** @type {any} */ (m).type),
      );
      const sentTypes = JSON.stringify(ws.sent.map((m) => /** @type {any} */ (m).type));
      expect(
        candidates,
        `${where}: sent ${sentTypes} — none is a subscribe (all are known non-subscribe types)`,
      ).not.toHaveLength(0);

      // The subscribing message has to be a type the server dispatches on.
      // Without this, `send({type: 'resubscribe', conv_id})` with no manifest
      // entry and no handler passes while the server answers with an error.
      const registered = candidates.filter(
        (m) => CLIENT_TO_SERVER_TYPES.has(/** @type {any} */ (m).type),
      );
      expect(
        registered.map((m) => /** @type {any} */ (m).type),
        `${where}: candidate types ${JSON.stringify(candidates.map((m) => /** @type {any} */ (m).type))} `
          + `include no registered client→server type (manifest: ${[...CLIENT_TO_SERVER_TYPES].join(', ')})`,
      ).not.toHaveLength(0);

      ws.sent = [];
      fetchMock.mockClear();
    };

    // Only what the reconnect itself sends is under test.
    ws.sent = [];
    fetchMock.mockClear();

    ws.fireOpen();
    await flush();
    expectResubscribed(1);

    ws.fireOpen();
    await flush();
    expectResubscribed(2);
  });

  // G4a: Web UI resync — a reconnect re-sends SELECT_CONV, LIST_COMMANDS, and LOAD_HISTORY
  // to ensure that any changes made during the outage are fully refetched and reconciled.
  it('refetches history and updates the transcript when the socket reopens', async () => {
    const store = makeStore(ws);

    store.selectConversation('c1');
    await flush();

    // Real path: history arrives as a server→client conv_history frame.
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      has_more: false,
      messages: [
        { role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00Z' },
        { role: 'assistant', content: 'hi there', timestamp: '2026-01-01T00:00:01Z' },
      ],
    });
    expect(store.currentMessages.map((m) => m.content)).toEqual(['hello', 'hi there']);

    // Reopen/Reconnect
    ws.sent = [];
    ws.fireOpen();
    await flush();

    expect(store.currentConvId).toBe('c1');
    const historyRequests = ws.sent.filter(
      (m) => /** @type {any} */ (m).type === MESSAGE_TYPES.LOAD_HISTORY,
    );
    expect(historyRequests).toHaveLength(1);
    expect(/** @type {any} */ (historyRequests[0]).conv_id).toBe('c1');

    // Messages should be cleared to prevent stale rendering
    expect(store.currentMessages).toHaveLength(0);

    // New history arrives (representing what was produced during the outage)
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      has_more: false,
      messages: [
        { role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00Z' },
        { role: 'assistant', content: 'hi there', timestamp: '2026-01-01T00:00:01Z' },
        { role: 'assistant', content: 'new message', timestamp: '2026-01-01T00:00:02Z' },
      ],
    });

    // The transcript should contain all messages including the new one
    expect(store.currentMessages.map((m) => m.content)).toEqual(['hello', 'hi there', 'new message']);
  });

  // G4b: with nothing selected there is nothing to resubscribe to, so the
  // reconnect must stay silent on the socket.
  it('sends nothing on the socket when no conversation is selected', async () => {
    const store = makeStore(ws);
    expect(store.currentConvId).toBeNull();

    ws.fireOpen();
    await flush();

    expect(ws.sent).toEqual([]);
  });
});


// Exercise the real store and generated transport, never a service mock.
describe.each([
  ['createFolder', 'POST', false],
  ['renameFolder', 'PUT', true],
  ['deleteFolder', 'DELETE', true],
])('folder operation %s', (method, verb, hasPath) => {
  const path = 'Work space/日本語 & plus+ #hash%?';
  const destination = 'Destination/renamed + %';
  const encoded = path.split('/').map(encodeURIComponent).join('/');
  const invoke = (store) => method === 'renameFolder'
    ? store.renameFolder(path, destination) : store[method](path);
  let store;
  let fetchMock;
  let log;
  beforeEach(async () => {
    log = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock = vi.fn(async () => new Response(JSON.stringify({
      folder: 'Current folder', folders: [], conversations: [],
    })));
    vi.stubGlobal('fetch', fetchMock);
    store = makeStore(new FakeWS());
    await store.listConversations('Current folder');
    fetchMock.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each(['{"ok":true}', '', 'malformed'])('sends inputs and refreshes after HTTP success (%s)', async (body) => {
    fetchMock.mockResolvedValueOnce(new Response(body));
    expect(await invoke(store)).toBe(true);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations/folders' + (hasPath ? '/' + encoded : ''));
    expect(options.method).toBe(verb);
    expect(options.credentials).toBe('same-origin');
    if (verb !== 'DELETE') {
      expect(new Headers(options.headers).get('Content-Type')).toBe('application/json');
      expect(JSON.parse(options.body)).toEqual({ path: method === 'renameFolder' ? destination : path });
    } else {
      expect(options.body).toBeUndefined();
    }
    expect(fetchMock.mock.calls[1][0]).toBe('/api/conversations?folder=Current%20folder');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(log).not.toHaveBeenCalled();
  });

  it.each([400, 401, 404, 409, 500])('returns false and preserves server error (%s)', async (status) => {
    fetchMock.mockResolvedValueOnce(new Response('{"error":"folder problem"}', { status }));
    expect(await invoke(store)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith(expect.any(String), 'folder problem');
  });

  it('handles malformed error JSON without refreshing', async () => {
    fetchMock.mockResolvedValueOnce(new Response('broken', { status: 400 }));
    expect(await invoke(store)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith(expect.any(String), expect.any(SyntaxError));
  });

  it('handles network rejection without refreshing', async () => {
    const error = new Error('offline');
    fetchMock.mockRejectedValueOnce(error);
    expect(await invoke(store)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith(expect.any(String), error);
  });
});

// Lifecycle tests exercise actual store callers through the emitted client.
describe('conversation creation transport', () => {
  let store, ws, fetchMock, log;
  const metadata = { conv_id: 'created', title: '日本語 & + #', created_at: 'then', updated_at: 'now', model: 'chosen' };
  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    log = vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each([false, true])('sends optional inputs (%s), inserts metadata, selects and emits', async (optional) => {
    const existing = { ...metadata, conv_id: 'existing', title: 'Existing' };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ conversations: [existing], folders: [], folder: '' })));
    await store.listConversations();
    store.selectConversation('existing');
    store.sendMessage('old message');
    ws.sent = [];
    fetchMock.mockClear();
    const change = vi.fn();
    store.addEventListener('change', change);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(metadata), { status: 201 }));
    await store.createConversation(metadata.title, optional ? 'chosen' : '', optional ? 'Work/日本語 & + #' : '');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations');
    expect(options.method).toBe('POST');
    expect(options.credentials).toBe('same-origin');
    expect(new Headers(options.headers).get('content-type')).toBe('application/json');
    expect(JSON.parse(options.body)).toEqual(optional
      ? { title: metadata.title, model: 'chosen', folder: 'Work/日本語 & + #' } : { title: metadata.title });
    expect(store.conversations).toEqual([metadata, existing]);
    expect(store.currentMessages).toEqual([]);
    expect(store.currentConvId).toBe('created');
    // Selection retains the established reset; conv_selected later supplies the model.
    expect(store.activeModel).toBe('');
    expect(ws.sent.map(m => m.type)).toEqual([MESSAGE_TYPES.SELECT_CONV, MESSAGE_TYPES.LOAD_HISTORY, MESSAGE_TYPES.LIST_COMMANDS]);
    expect(change).toHaveBeenCalledTimes(1);
    expect(log).not.toHaveBeenCalled();
  });

  it('flushes queued text and uploads attachments after selection, exactly once', async () => {
    store.setModel('chosen');
    const file = new File(['data'], 'note.txt', { type: 'text/plain' });
    const uploaded = { filename: 'note.txt', path: 'uploads/note.txt', mime_type: 'text/plain' };
    const sent = new Promise(resolve => {
      const original = ws.send.bind(ws);
      ws.send = message => { original(message); if (message.type === MESSAGE_TYPES.SEND) resolve(message); };
    });
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(metadata), { status: 201 }));
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(uploaded)));
    store.sendMessage('queued text', [{ file }]);
    expect(await sent).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'created', text: 'queued text', attachments: [uploaded] });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: '', model: 'chosen' });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/upload/created');
    expect(fetchMock.mock.calls[1][1].body.get('file').name).toBe('note.txt');
    expect(store.currentMessages[0]).toMatchObject({ content: 'queued text', attachments: [uploaded] });
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...metadata, conv_id: 'second' }), { status: 201 }));
    await store.createConversation();
    expect(ws.sent.filter(m => m.type === MESSAGE_TYPES.SEND)).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(['http', 'network', 'json'])('preserves state on %s failure', async failure => {
    store.selectConversation('existing');
    store.sendMessage('existing message');
    const change = vi.fn();
    store.addEventListener('change', change);
    const messages = store.currentMessages;
    if (failure === 'network') fetchMock.mockRejectedValueOnce(new Error('offline'));
    else fetchMock.mockResolvedValueOnce(new Response('malformed', { status: failure === 'http' ? 403 : 201 }));
    await store.createConversation();
    expect(store.conversations).toEqual([]);
    expect(store.currentConvId).toBe('existing');
    expect(store.currentMessages).toBe(messages);
    expect(change).not.toHaveBeenCalled();
    if (failure === 'http') expect(log).not.toHaveBeenCalled();
    else expect(log).toHaveBeenCalledWith('Failed to create conversation:', expect.any(Error));
  });
});

describe.each([
  ['archiveConversation', 'POST', '/archive', false, true, 1],
  ['unarchiveConversation', 'POST', '/unarchive', true, false, 1],
  ['deleteConversation', 'DELETE', '', true, true, 2],
])('lifecycle %s transport', (method, verb, suffix, archived, clears, events) => {
  const id = 'web-user-日本語 & + #%?';
  let store, fetchMock, log, change;
  beforeEach(async () => {
    store = makeStore(new FakeWS());
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ folder: 'Work/Nested', folders: [], conversations: [] })));
    vi.stubGlobal('fetch', fetchMock);
    await store.listConversations('Work/Nested');
    await store.listArchivedConversations('Work/Nested');
    store.selectConversation(id);
    store.sendMessage('existing');
    change = vi.fn();
    store.addEventListener('change', change);
    log = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockClear();
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each(['{"ok":true}', '', 'malformed'])('ignores success acknowledgement (%s) and refreshes state', async body => {
    fetchMock.mockResolvedValueOnce(new Response(body));
    const refreshed = { folder: 'Work/Nested', folders: [{ name: 'Child', path: 'Work/Nested/Child', count: 2 }], conversations: [] };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(refreshed)));
    await store[method](id);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/conversations/${encodeURIComponent(id)}${suffix}`);
    expect(options.method).toBe(verb);
    expect(options.credentials).toBe('same-origin');
    expect(options.body).toBeUndefined();
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/conversations${archived ? '/archived' : ''}?folder=Work%2FNested`);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(store.currentConvId).toBe(clears ? null : id);
    expect(store.currentMessages).toHaveLength(clears ? 0 : 1);
    expect(archived ? store.archivedFolders : store.folders).toEqual(refreshed.folders);
    expect(change).toHaveBeenCalledTimes(events);
    expect(log).not.toHaveBeenCalled();
  });

  it('preserves an unrelated selection on success', async () => {
    fetchMock.mockResolvedValueOnce(new Response(''));
    await store[method]('another');
    expect(store.currentConvId).toBe(id);
    expect(store.currentMessages).toHaveLength(1);
  });

  it.each([401, 404, 500, 'network'])('preserves state and refresh count on failure (%s)', async status => {
    const messages = store.currentMessages;
    if (status === 'network') fetchMock.mockRejectedValueOnce(new Error('offline'));
    else fetchMock.mockResolvedValueOnce(new Response('malformed', { status }));
    await store[method](id);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.currentConvId).toBe(id);
    expect(store.currentMessages).toBe(messages);
    expect(change).not.toHaveBeenCalled();
    if (status === 'network') expect(log).toHaveBeenCalledWith(`Failed to ${method.replace('Conversation', '')} conversation:`, expect.any(Error));
    else expect(log).not.toHaveBeenCalled();
  });
});
