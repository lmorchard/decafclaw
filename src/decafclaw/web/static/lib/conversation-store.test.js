import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConversationStore, parentConvIdOf } from './conversation-store.js';
import { CHILD_ID_SEPARATOR, SIDE_EFFECT_COMMANDS } from './constants.js';
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

  // #1443: the open handler also runs after a server restart or a network
  // blip. It must refetch the folder the sidebar is showing; defaulting to the
  // top level desynced the store from the sidebar, so a chat created in that
  // folder was created there but refetched into the top-level hierarchy and
  // stayed invisible until a page reload.
  it('refreshes the viewed folder, not the top level, on reconnect', async () => {
    const store = makeStore(ws);
    fetchMock.mockImplementation(async (url) => {
      const folder = new URL(String(url), 'http://test').searchParams.get('folder') || '';
      return { ok: true, json: async () => ({ conversations: [], folders: [], folder, sessions: [] }) };
    });
    await store.listConversations('Work');
    fetchMock.mockClear();

    ws.fireOpen();
    await flush();

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).toContain('/api/conversations?folder=Work');
    expect(urls).toContain('/api/sessions?folder=Work');
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
    fetchMock.mockResolvedValueOnce(new Response(
      JSON.stringify({ folder: '', sessions: [] }),
      { headers: { 'Content-Type': 'application/json' } },
    ));
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
    // selectConversation + the #1443 hierarchy refresh.
    expect(change).toHaveBeenCalledTimes(2);
    expect(log).not.toHaveBeenCalled();
  });

  it('defaults to the current folder and active model (⌘K parity with + Chat)', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ folder: 'Work', folders: [], conversations: [] })));
    await store.listConversations('Work');
    store.setModel('chosen');
    fetchMock.mockClear();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(metadata), { status: 201 }));
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ folder: 'Work', sessions: [] }), { headers: { 'Content-Type': 'application/json' } }));
    await store.createConversation();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: '', model: 'chosen', folder: 'Work' });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sessions?folder=Work');
  });

  it('refreshes the folder it created in, even when the store folder is stale (#1443)', async () => {
    // The sidebar passes the folder it is rendering; the store is still at the
    // top level here. Refetching `#currentFolder` would refetch the wrong
    // listing and leave the new row invisible.
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(metadata), { status: 201 }));
    fetchMock.mockResolvedValueOnce(new Response(
      JSON.stringify({ folder: 'Work', sessions: [] }),
      { headers: { 'Content-Type': 'application/json' } },
    ));
    await store.createConversation('', '', 'Work');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sessions?folder=Work');
  });

  it('flushes queued text and uploads attachments after selection, exactly once', async () => {
    store.setModel('chosen');
    const file = new File(['data'], 'note.txt', { type: 'text/plain' });
    const uploaded = { filename: 'note.txt', path: 'uploads/note.txt', mime_type: 'text/plain' };
    const sent = new Promise(resolve => {
      const original = ws.send.bind(ws);
      ws.send = message => { original(message); if (message.type === MESSAGE_TYPES.SEND) resolve(message); };
    });
    fetchMock.mockImplementation(async (url) => {
      const u = String(url);
      if (u === '/api/upload/created') return new Response(JSON.stringify(uploaded), { status: 201 });
      if (u.startsWith('/api/sessions')) {
        return new Response(
          JSON.stringify({ folder: '', sessions: [] }),
          { headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(JSON.stringify(metadata), { status: 201 });
    });
    store.sendMessage('queued text', [{ file }]);
    expect(await sent).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'created', text: 'queued text', attachments: [uploaded] });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: '', model: 'chosen' });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/upload/created');
    expect(fetchMock.mock.calls[1][1].method).toBe('POST');
    expect(fetchMock.mock.calls[1][1].credentials).toBe('same-origin');
    expect(fetchMock.mock.calls[1][1].body.get('file').name).toBe('note.txt');
    expect(store.currentMessages[0]).toMatchObject({ content: 'queued text', attachments: [uploaded] });
    await store.createConversation();
    expect(ws.sent.filter(m => m.type === MESSAGE_TYPES.SEND)).toHaveLength(1);
    // POST, upload, hierarchy refresh, second POST, second hierarchy refresh.
    expect(fetchMock).toHaveBeenCalledTimes(5);
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
  // `events` counts emitted change events on success; `fetches` the total fetch
  // calls. #1274 added a session-hierarchy refetch to archive/unarchive (flat-list
  // + sessions → two fetches, two emissions). Delete only re-fetches the archived
  // list: a deleted conversation was already out of the active hierarchy (it left
  // when archived), so it needs no sessions refetch — one fetch, one emission.
  ['archiveConversation', 'POST', '/archive', false, true, 2, 3],
  ['unarchiveConversation', 'POST', '/unarchive', true, false, 2, 3],
  ['deleteConversation', 'DELETE', '', true, true, 1, 2],
])('lifecycle %s transport', (method, verb, suffix, archived, clears, events, fetches) => {
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
    if (fetches > 2) {
      // #1274 — archive/unarchive also refresh the session hierarchy (sidebar source).
      expect(new URL(fetchMock.mock.calls[2][0], 'http://test').searchParams.get('folder')).toBe('Work/Nested');
      expect(String(fetchMock.mock.calls[2][0])).toContain('/api/sessions');
    }
    expect(fetchMock).toHaveBeenCalledTimes(fetches);
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

describe('conversation activity status tracking', () => {
  let ws, store, change;

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
    change = vi.fn();
    store.addEventListener('change', change);
  });

  it('initializes status from REST listing items and defaults to idle', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      folder: '',
      folders: [],
      conversations: [
        { conv_id: 'c1', title: 'Chat 1', status: 'busy' },
        { conv_id: 'c2', title: 'Chat 2', status: 'waiting' },
        { conv_id: 'c3', title: 'Chat 3' },
      ],
    })));
    vi.stubGlobal('fetch', fetchMock);

    await store.listConversations();
    expect(store.getConversationStatus('c1')).toBe('busy');
    expect(store.getConversationStatus('c2')).toBe('waiting');
    expect(store.getConversationStatus('c3')).toBe('idle');
    expect(store.getConversationStatus('unknown')).toBe('idle');
    vi.unstubAllGlobals();
  });

  it('updates status on CONVERSATION_STATUS websocket messages', () => {
    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c1', status: 'busy' });
    expect(store.getConversationStatus('c1')).toBe('busy');
    expect(change).toHaveBeenCalled();

    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c1', status: 'waiting' });
    expect(store.getConversationStatus('c1')).toBe('waiting');

    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c1', status: 'idle' });
    expect(store.getConversationStatus('c1')).toBe('idle');
  });

  it('marks background conversation as finished, but active conversation as idle', () => {
    store.selectConversation('c-active');

    // Finished in background -> finished
    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c-bg', status: 'finished' });
    expect(store.getConversationStatus('c-bg')).toBe('finished');

    // Finished while currently active -> idle (already read)
    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c-active', status: 'finished' });
    expect(store.getConversationStatus('c-active')).toBe('idle');
  });

  it('clears finished status when the conversation is selected (read)', () => {
    store.selectConversation('c-other');
    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c-bg', status: 'finished' });
    expect(store.getConversationStatus('c-bg')).toBe('finished');

    // Selecting c-bg clears finished to idle
    store.selectConversation('c-bg');
    expect(store.getConversationStatus('c-bg')).toBe('idle');
  });

  it('marks current conversation as busy when sending a message', () => {
    store.selectConversation('c1');
    expect(store.getConversationStatus('c1')).toBe('idle');

    store.sendMessage('hello');
    expect(store.getConversationStatus('c1')).toBe('busy');
  });

  it('omits steering from an idle send', () => {
    store.selectConversation('c1');

    store.sendMessage('hello');
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'c1', text: 'hello' });
    expect(send).not.toHaveProperty('steering');
  });

  it('steers a second send after turn_start', () => {
    store.selectConversation('c1');
    store.sendMessage('first');
    ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'c1' });

    store.sendMessage('second');
    const sends = ws.sent.filter((m) => m.type === MESSAGE_TYPES.SEND);
    expect(sends).toHaveLength(2);
    expect(sends[1]).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'c1', text: 'second', steering: true });
  });

  it('omits steering after the turn settles', () => {
    store.selectConversation('c1');
    store.sendMessage('first');
    ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'c1' });
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'c1',
      text: 'done',
      final: true,
    });

    store.sendMessage('after');
    const send = ws.sent.filter((m) => m.type === MESSAGE_TYPES.SEND).at(-1);
    expect(send).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'c1', text: 'after' });
    expect(send).not.toHaveProperty('steering');
  });

  it('forwards steering on an attachment send while busy', () => {
    store.selectConversation('c1');
    ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'c1' });
    const attachment = { filename: 'note.txt', path: 'uploads/note.txt', mime_type: 'text/plain' };

    store.sendMessage('with file', [attachment]);
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).toEqual({
      type: MESSAGE_TYPES.SEND,
      conv_id: 'c1',
      text: 'with file',
      attachments: [attachment],
      steering: true,
    });
  });

  it('sends /retitle without engaging busy state or adding user message', () => {
    store.selectConversation('c1');
    expect(store.getConversationStatus('c1')).toBe('idle');

    store.sendMessage('/retitle');
    expect(store.getConversationStatus('c1')).toBe('idle');
    expect(store.currentMessages).toHaveLength(0);
    expect(ws.sent).toContainEqual({
      type: MESSAGE_TYPES.SEND,
      conv_id: 'c1',
      text: '/retitle',
    });
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).not.toHaveProperty('steering');
  });

  it('never adds steering to /retitle while busy', () => {
    store.selectConversation('c1');
    ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'c1' });

    store.sendMessage('/retitle');
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'c1', text: '/retitle' });
  });

  it('retitleConversation sends /retitle for specified conversation', () => {
    store.retitleConversation('c2');
    expect(ws.sent).toContainEqual({
      type: MESSAGE_TYPES.SEND,
      conv_id: 'c2',
      text: '/retitle',
    });
  });

  it('sends /update without engaging busy state or adding user message (#981)', () => {
    store.selectConversation('c1');
    expect(store.getConversationStatus('c1')).toBe('idle');

    store.sendMessage('/update');
    expect(store.getConversationStatus('c1')).toBe('idle');
    expect(store.currentMessages).toHaveLength(0);
    expect(ws.sent).toContainEqual({
      type: MESSAGE_TYPES.SEND,
      conv_id: 'c1',
      text: '/update',
    });
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).not.toHaveProperty('steering');
  });

  it('never adds steering to /update while busy (#981)', () => {
    store.selectConversation('c1');
    ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'c1' });

    store.sendMessage('/update confirm');
    const send = ws.sent.find((m) => m.type === MESSAGE_TYPES.SEND);
    expect(send).toEqual({ type: MESSAGE_TYPES.SEND, conv_id: 'c1', text: '/update confirm' });
  });

  // The guard is built from SIDE_EFFECT_COMMANDS, so every entry — including
  // any added later — must take the side-effect path, with or without
  // arguments. A hardcoded branch would silently start spinning.
  it('treats every SIDE_EFFECT_COMMANDS entry as a side effect', () => {
    store.selectConversation('c1');
    for (const [name, command] of Object.entries(SIDE_EFFECT_COMMANDS)) {
      for (const text of [command, `${command} some argument`]) {
        ws.sent.length = 0;
        store.sendMessage(text);
        expect(store.getConversationStatus('c1'), `${name}: ${text}`).toBe('idle');
        expect(store.currentMessages, `${name}: ${text}`).toHaveLength(0);
        expect(ws.sent, `${name}: ${text}`).toEqual([
          { type: MESSAGE_TYPES.SEND, conv_id: 'c1', text },
        ]);
      }
    }
  });

  it('derives parent ids with CHILD_ID_SEPARATOR', () => {
    const root = 'web-alice-1a2b3c4d';
    expect(parentConvIdOf(`${root}${CHILD_ID_SEPARATOR}abcdef12`)).toBe(root);
    expect(parentConvIdOf(root)).toBe('');
  });
  it('does not overwrite newer websocket transition with delayed REST response', async () => {
    let resolveRest;
    const restPromise = new Promise(resolve => { resolveRest = resolve; });
    const fetchMock = vi.fn(async () => {
      await restPromise;
      return new Response(JSON.stringify({
        folder: '',
        folders: [],
        conversations: [{ conv_id: 'c1', title: 'Chat 1', status: 'idle' }],
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    // Start REST list fetch (snapshots sequence before fetch)
    const listPromise = store.listConversations();

    // While REST fetch is in flight, websocket event arrives
    ws.fireMessage({ type: MESSAGE_TYPES.CONVERSATION_STATUS, conv_id: 'c1', status: 'busy' });
    expect(store.getConversationStatus('c1')).toBe('busy');

    // REST fetch resolves with stale 'idle'
    resolveRest();
    await listPromise;

    // Must remain 'busy' because websocket transition was newer than REST request start
    expect(store.getConversationStatus('c1')).toBe('busy');
    vi.unstubAllGlobals();
  });

  it('rolls back optimistic busy status on ERROR message', () => {
    store.selectConversation('c1');
    store.sendMessage('hello');
    expect(store.getConversationStatus('c1')).toBe('busy');

    ws.fireMessage({ type: MESSAGE_TYPES.ERROR, conv_id: 'c1', message: 'Send failed' });
    expect(store.getConversationStatus('c1')).toBe('idle');
  });

  it('updates activeMode and availableModes on CONV_HISTORY', () => {
    store.selectConversation('c1');
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      context_limit: 1000,
      active_mode: 'dev',
      available_modes: [
        { name: 'default', description: 'Default', presets: [], promoted_tools: [] },
        { name: 'dev', description: 'Dev', presets: ['developer'], promoted_tools: ['shell'] },
      ],
    });
    expect(store.activeMode).toBe('dev');
    expect(store.availableModes).toHaveLength(2);
  });

  it('sends SET_MODE on setMode() and updates activeMode on MODE_CHANGED', () => {
    store.selectConversation('c1');
    store.setMode('research');
    const setModeMsg = ws.sent.find(m => m.type === MESSAGE_TYPES.SET_MODE);
    expect(setModeMsg).toEqual({ type: MESSAGE_TYPES.SET_MODE, conv_id: 'c1', mode: 'research' });
    expect(store.activeMode).toBe('default');

    ws.fireMessage({
      type: MESSAGE_TYPES.MODE_CHANGED,
      conv_id: 'c1',
      mode: 'research',
      presets: [],
      promoted_tools: ['web_fetch'],
    });
    expect(store.activeMode).toBe('research');
  });

  it('updates activeMode on MODE_CHANGED event', () => {
    store.selectConversation('c1');
    const change = vi.fn();
    store.addEventListener('change', change);
    ws.fireMessage({
      type: MESSAGE_TYPES.MODE_CHANGED,
      conv_id: 'c1',
      mode: 'admin',
      presets: [],
      promoted_tools: ['admin_read'],
    });
    expect(store.activeMode).toBe('admin');
    expect(change).toHaveBeenCalled();
  });
});

// #1005 — the context meter must drop after a successful compaction.
// Previously the store read `msg.usage.prompt_tokens`, which on a compacting
// turn is the pre-compaction prompt (>100%) and nothing corrected it. The
// server now carries a dedicated `context_usage` field: the post-compaction
// estimate when the turn compacted, else the real last_prompt. The meter
// prefers `context_usage`; it only falls back to `usage.prompt_tokens` for
// older servers that predate the field.
describe('ConversationStore context meter after compaction (#1005)', () => {
  /** @type {FakeWS} */
  let ws;
  /** @type {ConversationStore} */
  let store;

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
  });

  it('drops the meter to the post-compaction estimate via context_usage', () => {
    store.selectConversation('c1');
    // Turn's last LLM call reported a prompt size above the threshold
    // (>100% in the meter) — this is what `usage.prompt_tokens` still carries.
    // The server computed the post-compaction estimate and forwarded it as
    // context_usage (well under the limit).
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'c1',
      final: true,
      role: 'assistant',
      text: 'done',
      usage: { prompt_tokens: 50_000, completion_tokens: 100 },
      context_usage: 9_000,
      context_limit: 48_000,
    });
    // The meter must reflect the post-compaction value, not the raw LLM usage.
    expect(store.contextUsage).toBe(9_000);
    // The limit is unchanged — only the usage numerator was corrected.
    expect(store.contextLimit).toBe(48_000);
  });

  it('stays at the true value when the turn did not compact', () => {
    store.selectConversation('c1');
    // No compaction: context_usage is the real last_prompt (same as usage).
    // The meter still correctly tracks the actual prompt size.
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'c1',
      final: true,
      role: 'assistant',
      text: 'done',
      usage: { prompt_tokens: 20_000, completion_tokens: 50 },
      context_usage: 20_000,
      context_limit: 48_000,
    });
    expect(store.contextUsage).toBe(20_000);
  });

  it('ignores an older-server message without context_usage (fallback path)', () => {
    store.selectConversation('c1');
    // Older server: no context_usage field. The meter falls back to the
    // historical behavior of reading usage.prompt_tokens.
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'c1',
      final: true,
      role: 'assistant',
      text: 'done',
      usage: { prompt_tokens: 30_000, completion_tokens: 25 },
      context_limit: 48_000,
    });
    expect(store.contextUsage).toBe(30_000);
  });

  it('leaves the meter alone when the conv id does not match', () => {
    store.selectConversation('c1');
    // First set a baseline value on the active conversation.
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'c1',
      final: true,
      role: 'assistant',
      text: 'baseline',
      usage: { prompt_tokens: 10_000, completion_tokens: 10 },
      context_usage: 10_000,
      context_limit: 48_000,
    });
    expect(store.contextUsage).toBe(10_000);

    // A MESSAGE_COMPLETE for another conversation must not affect c1.
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'other',
      final: true,
      role: 'assistant',
      text: 'other done',
      usage: { prompt_tokens: 50_000, completion_tokens: 100 },
      context_usage: 9_000,
      context_limit: 48_000,
    });
    expect(store.contextUsage).toBe(10_000);
  });
});

// #1072 — background auto-titling renames conversations after the turn that
// triggered them. The sidebar must pick the new title up in real time from
// the `conv_renamed` frame instead of waiting for a page reload or a manual
// refetch.
describe('ConversationStore conv_renamed (#1072)', () => {
  /** @type {FakeWS} */
  let ws;
  /** @type {ConversationStore} */
  let store;

  beforeEach(async () => {
    ws = new FakeWS();
    store = makeStore(ws);
    const listing = {
      conversations: [
        { conv_id: 'web-alice-1', title: 'Old auto title', created_at: 't0', updated_at: 't1' },
        { conv_id: 'web-alice-2', title: 'Other chat', created_at: 't0', updated_at: 't2' },
      ],
      folders: [],
      folder: '',
    };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(listing))));
    await store.listConversations();
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  it('applies the new title and re-sorts by updated_at, emitting change', () => {
    const change = vi.fn();
    store.addEventListener('change', change);
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-1',
      title: 'Refined conversation title',
      updated_at: 't3',
    });
    // t3 > t2: the renamed conversation must move to the top.
    expect(store.conversations.map(c => c.conv_id)).toEqual(['web-alice-1', 'web-alice-2']);
    expect(store.conversations[0].title).toBe('Refined conversation title');
    expect(store.conversations[0].updated_at).toBe('t3');
    expect(change).toHaveBeenCalled();
    // The patch is in-memory only — no refetch for a title change.
    expect(store.currentMessages).toEqual([]);
  });

  it('keeps list order when the rename does not bump updated_at past the rest', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-2',
      title: 'Other chat refined',
      updated_at: 't0', // older than web-alice-1's t1
    });
    expect(store.conversations.map(c => c.title)).toEqual(['Old auto title', 'Other chat refined']);
  });

  it('ignores a rename for a conversation not in the listing', () => {
    const change = vi.fn();
    store.addEventListener('change', change);
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-9',
      title: 'Ghost title',
      updated_at: 't9',
    });
    expect(store.conversations.map(c => c.title)).toEqual(['Old auto title', 'Other chat']);
    expect(change).toHaveBeenCalled();
  });

  it('ignores a frame with an empty title', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-1',
      title: '',
      updated_at: 't9',
    });
    expect(store.conversations[0].title).toBe('Old auto title');
    expect(store.conversations[0].updated_at).toBe('t1');
  });
});

// #1115 — delegated children (`{root}--child-{hex}`) are subagent transcripts:
// viewable read-only; only the parent conversation can be prompted.
describe('delegated child conversations are read-only (#1115)', () => {
  let ws, store;

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
  });

  it('marks a child-shaped conversation read-only immediately on selection', () => {
    store.selectConversation('web-alice-1a2b3c4d--child-abcdef12');
    expect(store.isReadOnly).toBe(true);
    // Selecting a standalone conversation clears the gate again.
    store.selectConversation('web-alice-1a2b3c4d');
    expect(store.isReadOnly).toBe(false);
  });

  it('does not prompt a delegated child (no SEND frame, no optimistic echo)', () => {
    store.selectConversation('web-alice-1a2b3c4d--child-abcdef12');
    store.sendMessage('hello');
    expect(ws.sent).not.toContainEqual(expect.objectContaining({ type: MESSAGE_TYPES.SEND }));
    expect(store.currentMessages).toHaveLength(0);
    expect(store.isBusy).toBe(false);
  });

  it('still allows cancelling a subagent turn (monitoring, not prompting)', () => {
    store.cancelSubagent('web-alice-1a2b3c4d--child-abcdef12');
    expect(ws.sent).toContainEqual({
      type: MESSAGE_TYPES.CANCEL_TURN,
      conv_id: 'web-alice-1a2b3c4d--child-abcdef12',
    });
  });
});

// #1115 — the sidebar hierarchy renders from `GET /api/sessions`
// (server-grouped sessions, archive-discovered subagent children).
describe('session hierarchy wiring (#1115)', () => {
  let ws, store;
  /** @type {import('vitest').Mock} */
  let fetchMock;

  const sessionBody = {
    folder: '',
    sessions: [
      {
        session_id: 'web-alice-r1',
        title: 'Root chat',
        created_at: '2026-01-01T00:00:00+00:00',
        updated_at: '2026-01-02T00:00:00+00:00',
        status: 'idle',
        conversations: [
          { conv_id: 'web-alice-r1', title: 'Root chat', kind: 'parent', status: 'idle' },
          { conv_id: 'web-alice-r1--child-abcdef12', title: 'Subtask abcdef12', kind: 'subagent', status: 'busy' },
        ],
      },
    ],
  };

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
    fetchMock = vi.fn(async (url) =>
      new Response(JSON.stringify(
        String(url).startsWith('/api/sessions')
          ? sessionBody
          : { folder: '', folders: [], conversations: [] }
      ), { headers: { 'Content-Type': 'application/json' } })
    );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches sessions when the socket opens and syncs subagent statuses', async () => {
    ws.fireOpen();
    await flush();
    expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/sessions'))).toBe(true);
    expect(store.sessions).toHaveLength(1);
    // Subagent statuses come from the session listing like any conv's.
    expect(store.getConversationStatus('web-alice-r1--child-abcdef12')).toBe('busy');
  });

  it('refetches sessions when a turn completes (children may have spawned)', async () => {
    store.selectConversation('web-alice-r1');
    ws.fireMessage({
      type: MESSAGE_TYPES.MESSAGE_COMPLETE,
      conv_id: 'web-alice-r1',
      final: true,
      role: 'assistant',
      text: 'done',
    });
    await flush();
    expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/sessions'))).toBe(true);
  });

  it('refetches sessions (debounced) when a pushed status names an unknown conversation', async () => {
    // A subagent spawning mid-parent-turn pushes status for an id the store
    // has never seen — the hierarchy must refetch so its row appears
    // without a page reload (#1115 follow-up).
    vi.useFakeTimers();
    try {
      ws.fireOpen();
      await vi.advanceTimersByTimeAsync(0);
      const sessionsCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions')).length;
      expect(sessionsCalls()).toBe(1);

      ws.fireMessage({
        type: MESSAGE_TYPES.CONVERSATION_STATUS,
        conv_id: 'web-alice-r1--child-fresh999',
        status: 'busy',
      });
      // Debounce: events inside the window coalesce into one refetch.
      ws.fireMessage({ type: MESSAGE_TYPES.TURN_START, conv_id: 'web-alice-r1--child-fresh999' });
      await vi.advanceTimersByTimeAsync(300);
      expect(sessionsCalls()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not refetch sessions for pushed statuses on known conversations', async () => {
    vi.useFakeTimers();
    try {
      ws.fireOpen();
      await vi.advanceTimersByTimeAsync(0);
      const sessionsCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions')).length;
      expect(sessionsCalls()).toBe(1);

      // The seeded child (sessionBody) is known — badge updates only.
      ws.fireMessage({
        type: MESSAGE_TYPES.CONVERSATION_STATUS,
        conv_id: 'web-alice-r1--child-abcdef12',
        status: 'finished',
      });
      await vi.advanceTimersByTimeAsync(500);
      expect(sessionsCalls()).toBe(1);
      expect(store.getConversationStatus('web-alice-r1--child-abcdef12')).toBe('finished');
    } finally {
      vi.useRealTimers();
    }
  });
});

// #1274 — the sidebar's active section renders from the session hierarchy
// (`GET /api/sessions`), but conversation mutations only refreshed the flat
// listing, so archive/rename/unarchive/move left the hierarchy stale until a
// full page reload. Each of those must refresh the hierarchy too. Delete is the
// exception: it is reachable only from the archived section, where the
// conversation was already out of the active hierarchy (it left when archived),
// so it needs no refresh.
describe('session hierarchy follows conversation mutations (#1274)', () => {
  let ws, store;
  /** @type {import('vitest').Mock} */
  let fetchMock;

  // One session containing the conversation under test, so a hierarchy refresh
  // is observable. The mock returns this for every `/api/sessions` call — the
  // assertion is that a refresh *happens* (the server would reflect the change),
  // not that this fixed body models the removal.
  const sessionBody = {
    folder: '',
    sessions: [
      {
        session_id: 'web-alice-r1',
        title: 'Root chat',
        created_at: 't0',
        updated_at: 't1',
        status: 'idle',
        conversations: [
          { conv_id: 'web-alice-r1', title: 'Root chat', kind: 'parent', status: 'idle' },
        ],
      },
    ],
  };

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
    fetchMock = vi.fn(async (url) => {
      const u = String(url);
      let body;
      if (u.startsWith('/api/sessions')) {
        body = sessionBody;
      } else if (u === '/api/conversations') {
        // create — a fresh conversation; creation must also refresh sessions.
        body = { conv_id: 'web-alice-new', title: 'New conversation', created_at: 't3', updated_at: 't3' };
      } else if (u.startsWith('/api/conversations/')) {
        // rename/move/delete/archive/unarchive — a conv-shaped ack.
        body = { conv_id: 'web-alice-r1', title: 'Renamed', created_at: 't0', updated_at: 't2' };
      } else {
        body = { folder: '', folders: [], conversations: [] };
      }
      return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  const sessionsCalls = () =>
    fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions')).length;

  it('refreshes the session hierarchy after archiving a conversation', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    await store.archiveConversation('web-alice-r1');
    expect(sessionsCalls()).toBe(2);
  });

  it('refreshes the session hierarchy after renaming a conversation', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    await store.renameConversation('web-alice-r1', 'Renamed');
    expect(sessionsCalls()).toBe(2);
  });

  it('refreshes the session hierarchy after unarchiving a conversation', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    await store.unarchiveConversation('web-alice-r1');
    expect(sessionsCalls()).toBe(2);
  });

  it('does not refresh the session hierarchy after deleting a conversation', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    // Delete is reachable only from the archived section; the conversation was
    // already out of the active hierarchy when archived, so no refetch is needed.
    await store.deleteConversation('web-alice-r1');
    expect(sessionsCalls()).toBe(1);
  });

  it('refreshes the session hierarchy after moving a conversation to another folder', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    await store.moveConversation('web-alice-r1', 'Work');
    expect(sessionsCalls()).toBe(2);
  });

  it('refreshes the session hierarchy after creating a conversation', async () => {
    await store.listSessions();
    expect(sessionsCalls()).toBe(1);
    await store.createConversation();
    expect(store.conversations.some((c) => c.conv_id === 'web-alice-new')).toBe(true);
    expect(sessionsCalls()).toBe(2);
  });

  it('schedules a debounced hierarchy refresh on a background rename of a listed conversation', async () => {
    vi.useFakeTimers();
    try {
      ws.fireOpen();
      await vi.advanceTimersByTimeAsync(0);
      expect(sessionsCalls()).toBe(1);

      // A background auto-title (#1072) for a conversation present in the
      // hierarchy must refresh it (debounced) rather than wait for a reload.
      ws.fireMessage({
        type: MESSAGE_TYPES.CONV_RENAMED,
        conv_id: 'web-alice-r1',
        title: 'Refined title',
        updated_at: 't9',
      });
      await vi.advanceTimersByTimeAsync(300);
      expect(sessionsCalls()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not schedule a hierarchy refresh for a background rename of an unlisted conversation', async () => {
    vi.useFakeTimers();
    try {
      ws.fireOpen();
      await vi.advanceTimersByTimeAsync(0);
      expect(sessionsCalls()).toBe(1);

      // A rename for a conversation the store has never loaded is not in the
      // hierarchy — no refetch (avoids a stray request for an unknown id).
      ws.fireMessage({
        type: MESSAGE_TYPES.CONV_RENAMED,
        conv_id: 'web-alice-ghost',
        title: 'Ghost title',
        updated_at: 't9',
      });
      await vi.advanceTimersByTimeAsync(500);
      expect(sessionsCalls()).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('conversation model groups (#1441)', () => {
  let ws;
  let store;

  beforeEach(() => {
    ws = new FakeWS();
    store = new ConversationStore(ws);
    store.selectConversation('c1');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('stores model_groups from CONV_HISTORY', () => {
    const groups = [
      { name: 'Anthropic', models: ['anthropic/claude'] },
      { name: 'Custom', models: ['legacy'] },
    ];
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      context_limit: 1000,
      available_models: ['anthropic/claude', 'legacy'],
      model_groups: groups,
    });

    expect(store.modelGroups).toEqual(groups);
  });

  it('falls back to one group from available_models when model_groups is absent', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      context_limit: 1000,
      available_models: ['m1', 'm2'],
    });

    expect(store.modelGroups).toEqual([{ name: 'Models', models: ['m1', 'm2'] }]);
  });

  it('stores model_groups from MODELS_AVAILABLE', () => {
    const groups = [{ name: 'Local', models: ['local/llama'] }];
    ws.fireMessage({
      type: MESSAGE_TYPES.MODELS_AVAILABLE,
      available_models: ['local/llama'],
      model_groups: groups,
      default_model: 'local/llama',
    });

    expect(store.modelGroups).toEqual(groups);
  });

  it('falls back when MODELS_AVAILABLE has no model_groups', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.MODELS_AVAILABLE,
      available_models: ['m1'],
      default_model: 'm1',
    });

    expect(store.modelGroups).toEqual([{ name: 'Models', models: ['m1'] }]);
  });
});

describe('stream state on return (#1439)', () => {
  let ws;
  let store;

  beforeEach(() => {
    ws = new FakeWS();
    store = new ConversationStore(ws);
    store.selectConversation('c1');
  });

  it('leaves streaming cleared after CONV_HISTORY and seeds it from STREAM_STATE', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      turn_active: true,
    });
    expect(store.streamingText).toBe('');
    expect(store.streamingThinking).toBe('');

    ws.fireMessage({
      type: MESSAGE_TYPES.STREAM_STATE,
      conv_id: 'c1',
      text: 'Partial answer ',
      thinking: 'First thought. ',
    });
    expect(store.streamingText).toBe('Partial answer ');
    expect(store.streamingThinking).toBe('First thought. ');

    // New chunks append to the restored segment.
    ws.fireMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: 'c1', text: 'continues.', thinking: 'Second thought.' });
    expect(store.streamingText).toBe('Partial answer continues.');
    expect(store.streamingThinking).toBe('First thought. Second thought.');
  });

  it('ignores STREAM_STATE for another conversation or before a turn is active', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.STREAM_STATE,
      conv_id: 'c1',
      text: 'stale',
      thinking: 'stale',
    });
    expect(store.streamingText).toBe('');
    expect(store.streamingThinking).toBe('');

    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      turn_active: true,
    });
    ws.fireMessage({
      type: MESSAGE_TYPES.STREAM_STATE,
      conv_id: 'c2',
      text: 'other conversation',
      thinking: 'other conversation',
    });
    expect(store.streamingText).toBe('');
    expect(store.streamingThinking).toBe('');
  });

  it('clears streaming buffers on TURN_COMPLETE', () => {
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_HISTORY,
      conv_id: 'c1',
      messages: [],
      has_more: false,
      turn_active: true,
    });
    ws.fireMessage({
      type: MESSAGE_TYPES.STREAM_STATE,
      conv_id: 'c1',
      text: 'Partial answer ',
      thinking: 'First thought. ',
    });
    expect(store.streamingText).toBe('Partial answer ');

    ws.fireMessage({ type: MESSAGE_TYPES.TURN_COMPLETE, conv_id: 'c1', reason: 'completed' });
    expect(store.streamingText).toBe('');
    expect(store.streamingThinking).toBe('');
  });
});

// A listing response can arrive after a newer one: folder clicks, a mutation
// refresh, and a reconnect each start their own request. Only the last
// requested listing may change state or `#currentFolder`. Otherwise the
// sidebar renders the wrong folder and later mutations refresh that folder.
describe('listing response ordering', () => {
  let store;
  /** @type {import('vitest').Mock} */
  let fetchMock;

  beforeEach(() => {
    store = makeStore(new FakeWS());
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const defer = () => {
    /** @type {(response: Response) => void} */
    let resolve = () => {};
    const promise = new Promise((r) => { resolve = /** @type {any} */ (r); });
    return { promise, resolve };
  };
  const json = (body) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

  it('keeps the newer conversation listing and folder when an older response resolves last', async () => {
    const older = defer();
    const newer = defer();
    fetchMock.mockImplementationOnce(() => older.promise).mockImplementationOnce(() => newer.promise);
    const first = store.listConversations('First');
    const second = store.listConversations('Second');
    newer.resolve(json({
      folder: 'Second', folders: [], conversations: [{ conv_id: 'b', title: 'B' }],
    }));
    await second;
    older.resolve(json({
      folder: 'First', folders: [], conversations: [{ conv_id: 'a', title: 'A' }],
    }));
    await first;
    expect(store.currentFolder).toBe('Second');
    expect(store.conversations.map((c) => c.conv_id)).toEqual(['b']);
  });

  it('keeps the newer session listing when an older response resolves last', async () => {
    const older = defer();
    const newer = defer();
    fetchMock.mockImplementationOnce(() => older.promise).mockImplementationOnce(() => newer.promise);
    const first = store.listSessions('First');
    const second = store.listSessions('Second');
    newer.resolve(json({
      folder: 'Second',
      sessions: [{ session_id: 'b', conversations: [{ conv_id: 'b' }] }],
    }));
    await second;
    older.resolve(json({
      folder: 'First',
      sessions: [{ session_id: 'a', conversations: [{ conv_id: 'a' }] }],
    }));
    await first;
    expect(store.sessions.map((s) => s.session_id)).toEqual(['b']);
  });
});

// #1503 — the tab title names the selected conversation, so the store must
// expose that title from whichever listing holds it. Delegated children exist
// only in the session hierarchy; archived conversations only in the archived
// listing.
describe('current conversation title (#1503)', () => {
  /** @type {FakeWS} */
  let ws;
  /** @type {ConversationStore} */
  let store;
  /** @type {import('vitest').Mock} */
  let fetchMock;

  const json = (body) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

  beforeEach(() => {
    ws = new FakeWS();
    store = makeStore(ws);
    fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.startsWith('/api/sessions')) {
        return json({
          folder: '',
          sessions: [
            {
              session_id: 'web-alice-s2',
              title: 'Session root',
              conversations: [
                { conv_id: 'web-alice-s2', title: 'Session root', kind: 'parent' },
                { conv_id: 'web-alice-s2--child-ab', title: 'Child research', kind: 'child' },
              ],
            },
          ],
        });
      }
      if (u.startsWith('/api/conversations/archived')) {
        return json({
          folder: '',
          folders: [],
          conversations: [{ conv_id: 'web-alice-arch', title: 'Archived chat' }],
        });
      }
      if (u.startsWith('/api/conversations')) {
        return json({
          folder: '',
          folders: [],
          conversations: [{ conv_id: 'web-alice-r1', title: 'Flat chat' }],
        });
      }
      return json({ folder: '', folders: [], conversations: [] });
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  it('is empty before a conversation is selected', async () => {
    await store.listConversations();
    expect(store.currentConversationTitle).toBe('');
  });

  it('reads the selected title from the active listing', async () => {
    await store.listConversations();
    store.selectConversation('web-alice-r1');
    expect(store.currentConversationTitle).toBe('Flat chat');
  });

  it('falls back to the session hierarchy for a delegated child', async () => {
    await store.listSessions();
    store.selectConversation('web-alice-s2--child-ab');
    expect(store.currentConversationTitle).toBe('Child research');
  });

  it('falls back to the archived listing', async () => {
    await store.listArchivedConversations();
    store.selectConversation('web-alice-arch');
    expect(store.currentConversationTitle).toBe('Archived chat');
  });

  it('follows a background rename pushed for the selected conversation', async () => {
    await store.listConversations();
    store.selectConversation('web-alice-r1');
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-r1',
      title: 'Renamed in the background',
      updated_at: 't9',
    });
    expect(store.currentConversationTitle).toBe('Renamed in the background');
  });

  it('is empty for an id no listing knows', () => {
    store.selectConversation('web-alice-unknown');
    expect(store.currentConversationTitle).toBe('');
  });

  it('takes the title from the server when no listing holds the conversation', () => {
    store.selectConversation('web-alice-deep');
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_SELECTED,
      conv_id: 'web-alice-deep',
      title: 'Deep linked chat',
    });
    expect(store.currentConversationTitle).toBe('Deep linked chat');
  });

  it('keeps the remembered title when a folder listing drops the conversation', async () => {
    await store.listConversations();
    store.selectConversation('web-alice-r1');
    expect(store.currentConversationTitle).toBe('Flat chat');
    // The sidebar moved to another folder; the open conversation is not in it.
    fetchMock.mockImplementationOnce(async () => json({ folder: 'Work', folders: [], conversations: [] }));
    await store.listConversations('Work');
    expect(store.currentConversationTitle).toBe('Flat chat');
  });

  it('does not let a late conv_selected for another conversation rename the tab', () => {
    store.selectConversation('web-alice-deep');
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_SELECTED,
      conv_id: 'web-alice-r1',
      title: 'Someone else',
    });
    expect(store.currentConversationTitle).toBe('');
  });

  it('applies a pushed rename to the remembered title', () => {
    store.selectConversation('web-alice-deep');
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_SELECTED,
      conv_id: 'web-alice-deep',
      title: 'First title',
    });
    ws.fireMessage({
      type: MESSAGE_TYPES.CONV_RENAMED,
      conv_id: 'web-alice-deep',
      title: 'Retitled',
      updated_at: 't9',
    });
    expect(store.currentConversationTitle).toBe('Retitled');
  });
});
