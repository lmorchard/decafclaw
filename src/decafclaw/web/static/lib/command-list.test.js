/**
 * Frozen acceptance check for issue #139 C3 — the client wiring.
 *
 * C1 (`components/chat-input.test.js`) and C2 (`tests/test_web_command_list.py`)
 * can both be fully green while the feature is dead in the browser: the
 * component renders a menu from a `commands` property nobody assigns, and the
 * server answers a request nobody sends. This file closes that gap at the two
 * joints between them.
 *
 * Store surface this file defines (no implementation existed when it was
 * written) — deliberately shaped like the existing `availableModels` /
 * `MODELS_AVAILABLE` pair in `conversation-store.js`, which is the store's
 * established idiom for "server pushes a list, components read a getter":
 *
 *   - `selectConversation()` sends `{type: 'list_commands'}`
 *   - a reconnect re-sends it, but only when a conversation is selected
 *   - a `command_list` frame populates `store.commands` and emits `change`
 *   - `store.commands` is `[]` before any frame arrives
 *
 * The request rides on conversation-select rather than on socket `open`
 * (decided by Les, 2026-08-02). Requesting on `open` would have collided with
 * the #704 guard `sends nothing on the socket when no conversation is
 * selected` (`conversation-store.test.js:238`), and narrowing that guard to
 * fit this feature was rejected — it was written for a bug fixed the same day.
 * The accepted cost: the menu is empty for the first message of a fresh
 * session, which is reachable because `sendMessage` creates a conversation
 * when none exists (`conversation-store.js:470`).
 *
 * So `conversation-store.test.js` is untouched, and the last case below
 * actively defends its invariant from this side rather than merely avoiding it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ConversationStore } from './conversation-store.js';
import { MESSAGE_TYPES } from './message-types.js';
// The manifest, not the generated constants: `MESSAGE_TYPES.NOT_A_TYPE`
// silently evaluates to `undefined`, and tsconfig excludes `**/*.test.js`, so
// nothing would flag a wire type that never entered `message_types.json`.
import wireManifest from '../../message_types.json';
// Source text, not an evaluated module: app.js is a top-level DOM script whose
// module scope queries `document` and constructs a live WebSocket. `?raw` also
// sidesteps `import.meta.url` being an http: URL under jsdom, which makes
// fileURLToPath-based reads throw.
import appSource from '../app.js?raw';

/** Wire types as literals — see the manifest test below for why. */
const REQUEST_TYPE = 'list_commands';
const RESPONSE_TYPE = 'command_list';

const COMMANDS = [
  { name: 'dream', description: 'Distill the journal into vault pages', argument_hint: '[topic]' },
  {
    name: 'mcp__demo__summarize',
    description: 'Summarize a block of text',
    argument_hint: '<text> [language]',
  },
];

/** Same shape as the FakeWS in conversation-store.test.js. */
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

/** Let the `open` handler's async listConversations() fetch chain settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const makeStore = (/** @type {FakeWS} */ ws) =>
  new ConversationStore(/** @type {any} */ (ws));

describe('command list transport wiring', () => {
  /** @type {FakeWS} */
  let ws;

  beforeEach(() => {
    // The `open` handler already calls listConversations(), which fetches.
    // Stub it so neither a throw nor an unhandled rejection gets mistaken for
    // the assertion under test.
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ conversations: [], folders: [], folder: '' }),
    })));
    ws = new FakeWS();
  });

  // Harness + manifest guard. The behavioural assertions below compare against
  // string literals on purpose: `m.type === MESSAGE_TYPES.LIST_COMMANDS` would
  // be `undefined === undefined` on a frame with no type, a false pass.
  it('declares both wire types in the manifest', () => {
    expect(/** @type {any} */ (wireManifest.messages)[REQUEST_TYPE]?.direction)
      .toBe('client_to_server');
    expect(/** @type {any} */ (wireManifest.messages)[RESPONSE_TYPE]?.direction)
      .toBe('server_to_client');
    expect(MESSAGE_TYPES.LIST_COMMANDS).toBe(REQUEST_TYPE);
    expect(MESSAGE_TYPES.COMMAND_LIST).toBe(RESPONSE_TYPE);
  });

  it('starts with an empty command list', () => {
    const store = makeStore(ws);

    // Not undefined: app.js assigns this straight onto `chat-input.commands`,
    // and the component iterates it.
    expect(/** @type {any} */ (store).commands).toEqual([]);
  });

  it('requests the command list when a conversation is selected', async () => {
    const store = makeStore(ws);

    store.selectConversation('c1');
    await flush();

    const types = ws.sent.map((m) => /** @type {any} */ (m).type);
    expect(types, `sent ${JSON.stringify(types)}`).toContain(REQUEST_TYPE);
  });

  it('re-requests on every reconnect, not just the first', async () => {
    const store = makeStore(ws);
    store.selectConversation('c1');
    await flush();
    ws.sent = [];

    ws.fireOpen();
    await flush();
    let types = ws.sent.map((m) => /** @type {any} */ (m).type);
    expect(types, `reconnect #1 sent ${JSON.stringify(types)}`).toContain(REQUEST_TYPE);
    ws.sent = [];

    // A one-shot `if (this.#askedForCommands) return;` leaves a reconnected
    // client with a list that can never pick up a newly connected MCP server.
    ws.fireOpen();
    await flush();
    types = ws.sent.map((m) => /** @type {any} */ (m).type);
    expect(types, `reconnect #2 sent ${JSON.stringify(types)}`).toContain(REQUEST_TYPE);
  });

  // The Option B invariant, asserted from this side so the two files agree
  // rather than merely coexist. #704's guard
  // (`conversation-store.test.js:238`) says a reconnect with nothing selected
  // puts nothing on the wire; requesting the command list on `open` would have
  // broken it. This fails loudly if someone later "fixes" the empty-menu-on-
  // first-message cost by moving the request back to the open handler.
  it('stays silent on a reconnect when no conversation is selected', async () => {
    const store = makeStore(ws);
    expect(store.currentConvId).toBeNull();

    ws.fireOpen();
    await flush();

    const types = ws.sent.map((m) => /** @type {any} */ (m).type);
    expect(types, `sent ${JSON.stringify(types)}`).not.toContain(REQUEST_TYPE);
  });

  it('exposes the commands from a command_list frame and notifies subscribers', async () => {
    const store = makeStore(ws);
    const onChange = vi.fn();
    store.addEventListener('change', onChange);

    ws.fireMessage({ type: RESPONSE_TYPE, commands: COMMANDS });

    expect(/** @type {any} */ (store).commands).toEqual(COMMANDS);
    // app.js reads the store off the `change` event; without one, the assignment
    // never runs and chat-input keeps its empty list.
    expect(onChange).toHaveBeenCalled();
  });

  it('keeps the {name, description, argument_hint} entry shape intact', () => {
    const store = makeStore(ws);

    ws.fireMessage({ type: RESPONSE_TYPE, commands: COMMANDS });

    const entry = /** @type {any} */ (store).commands.find(
      (/** @type {any} */ c) => c.name === 'mcp__demo__summarize',
    );
    expect(entry).toBeDefined();
    expect(entry.description).toBe('Summarize a block of text');
    expect(entry.argument_hint).toBe('<text> [language]');
  });
});

describe('app.js hands the store command list to chat-input', () => {
  // TEXT-BASED ASSERTION, not behavioural coverage. `app.js` is a top-level DOM
  // script with module-scope side effects, so it cannot be mounted in a unit
  // test; this reads its source and matches a regex. It is paired with the
  // store checks above — those cover the behaviour, this one only covers the
  // one line of glue between the store and the component. A rename or a reflow
  // can turn it red while the app works, or green while the app is broken.
  // Treat a failure here as "go look at app.js", not as a behavioural verdict.
  it('assigns store.commands onto the chat-input element', () => {
    // Harness guard: if the `?raw` import ever resolves to something else, the
    // regex below would find nothing and blame app.js for it.
    expect(appSource, 'app.js source did not load').toContain('chatInput');

    const glue = appSource
      .split('\n')
      .filter((line) => /chatInput\.commands\s*=/.test(line)
        && /store\.commands/.test(line));

    expect(
      glue,
      'no line in app.js assigns store.commands to chatInput.commands — '
        + 'the server reply never reaches the component',
    ).not.toHaveLength(0);
  });
});
