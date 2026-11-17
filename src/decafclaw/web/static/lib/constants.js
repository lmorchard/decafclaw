// UI-local constants shared by the Web client's store and components.
//
// These are deliberately not in `message_types.json`: nothing here travels on
// the WebSocket wire, so `make gen-message-types` has no business generating
// them. The server's `conversation_status` values are wire values, but the
// labels below are presentation, and the side-effect commands are client-side
// guards for commands the user types.

/**
 * Conversation activity statuses mapped to the short labels shown beside a
 * subagent row. Keys mirror the server's `conversation_status` values.
 * @type {Readonly<Record<string, string>>}
 */
export const STATUS_LABELS = Object.freeze({
  idle: 'Idle',
  busy: 'Running',
  waiting: 'Waiting',
  finished: 'Done',
});

/**
 * Longer status wording used in conversation-row `title` tooltips. Separate
 * from {@link STATUS_LABELS} because the badge and the tooltip deliberately
 * read differently ("Running" vs. "busy").
 * @type {Readonly<Record<string, string>>}
 */
export const STATUS_TITLE_LABELS = Object.freeze({
  busy: 'busy',
  waiting: 'waiting for answer',
  finished: 'finished',
});

/**
 * Human-only chat commands handled as side effects: they run no agent turn, so
 * the client sends them without the busy flag or an optimistic user echo.
 * Named so callers can reference a specific command (e.g. the terminal button)
 * without repeating its literal.
 * @type {Readonly<Record<string, string>>}
 */
export const SIDE_EFFECT_COMMANDS = Object.freeze({
  TERMINAL: '/terminal',
  RETITLE: '/retitle',
  BANG_RETITLE: '!retitle',
  UPDATE: '/update',
  BANG_UPDATE: '!update',
});

/** Separator between a root conversation id and a delegated child's id. */
export const CHILD_ID_SEPARATOR = '--child-';

/** Command literals in match order; derived once from the named map. */
const SIDE_EFFECT_COMMAND_VALUES = Object.freeze(Object.values(SIDE_EFFECT_COMMANDS));

/**
 * Whether `text` invokes a side-effect command, with or without arguments.
 * @param {string} text
 * @returns {boolean}
 */
export function isSideEffectCommand(text) {
  const trimmed = String(text || '').trim();
  return SIDE_EFFECT_COMMAND_VALUES.some(
    (command) => trimmed === command || trimmed.startsWith(`${command} `),
  );
}
