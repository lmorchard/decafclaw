import { beforeEach, describe, expect, it, vi } from 'vitest';

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

  // G4a: rules out the naive `addEventListener('open', () => this.select(id))`
  // fix — selectConversation() clears the message store, so a transient blip
  // would blank the transcript the user is reading.
  it('keeps already-loaded messages when the socket reopens', async () => {
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

    /**
     * The transcript, the selection, and the absence of a refetch all have to
     * survive a reopen. Content alone is not enough: a
     * `selectConversation(id, {resubscribeOnly: true})` that skips
     * `messageStore.clear()` but still fires LOAD_HISTORY leaves the array
     * intact while re-pulling 50 messages on every transient blip, and one that
     * nulls `#currentConvId` leaves the array intact while detaching every
     * subsequent `msg.conv_id === this.#currentConvId` check.
     * @param {number} round
     */
    const expectUndisturbed = (round) => {
      const where = `reconnect #${round}`;
      expect(store.currentMessages.map((m) => m.content), where).toEqual(['hello', 'hi there']);
      expect(store.currentConvId, `${where}: selection lost`).toBe('c1');
      const historyRequests = ws.sent.filter(
        (m) => /** @type {any} */ (m).type === MESSAGE_TYPES.LOAD_HISTORY,
      );
      expect(
        historyRequests,
        `${where}: refetched history — the transcript is already loaded`,
      ).toHaveLength(0);
      ws.sent = [];
    };

    // Drop the initial select_conv/load_history pair from selectConversation().
    ws.sent = [];

    ws.fireOpen();
    await flush();
    expectUndisturbed(1);

    ws.fireOpen();
    await flush();
    expectUndisturbed(2);
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
