/**
 * Auto-focus policy for the chat input.
 *
 * The conversation store emits a `change` event after *every* WebSocket
 * message, so anything that focuses the chat input from that handler has to
 * be explicit about which state transition it reacts to. Focusing on plain
 * "something changed" yanks the caret back to the composer on every streamed
 * chunk, which makes the canvas terminal (or any other input on the page)
 * unusable while the agent is talking.
 */

/**
 * Is the user's focus currently parked somewhere we must not disturb?
 *
 * "Nowhere" (body / documentElement / nothing) and "already inside the chat
 * input" both count as free — re-focusing then is either the point or a
 * no-op. Anything else is a deliberate focus the user placed, including a
 * shadow-DOM widget (whose host element shows up as `activeElement`).
 *
 * @param {Element|null} chatInputEl
 * @returns {boolean}
 */
function isFocusParkedElsewhere(chatInputEl) {
  const active = document.activeElement;
  if (!active || active === document.body || active === document.documentElement) return false;
  if (chatInputEl && chatInputEl.contains(active)) return false;
  return true;
}

/**
 * Decide whether a store `change` should move focus into the chat input.
 *
 * - Conversation switch: always focus. It only happens from an explicit
 *   navigation, and landing in the composer is the whole point.
 * - Agent finished a turn: focus only if it wouldn't steal — the user may
 *   have moved to the canvas terminal, the wiki editor, or a widget mid-turn.
 * - Anything else (a streamed chunk, a tool status, a busy flip): never.
 *
 * @param {object} opts
 * @param {boolean} opts.readOnly     - Conversation is read-only (composer disabled)
 * @param {boolean} opts.convChanged  - The current conversation id just changed
 * @param {boolean} opts.turnFinished - The agent went busy → idle on this change
 * @param {Element|null} [opts.chatInputEl] - The chat-input element, for focus containment
 * @returns {boolean}
 */
export function shouldFocusChatInput({ readOnly, convChanged, turnFinished, chatInputEl = null }) {
  if (readOnly) return false;
  if (convChanged) return true;
  if (!turnFinished) return false;
  return !isFocusParkedElsewhere(chatInputEl);
}
