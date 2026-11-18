// Browser-tab title for the selected conversation (#1503).
//
// The sidebar shows per-conversation activity, but several DecafClaw tabs read
// identically in the tab strip, and a background approval or running turn is
// invisible from another tab. The title therefore carries the selected
// conversation's name plus a leading status marker. The waiting (`●`) marker
// matches the sidebar's glyph; `◐` stands in for the sidebar's CSS spinner,
// which a title string cannot animate.
//
// Only states the selected conversation can hold are marked. `finished` is a
// sidebar state for conversations that are *not* selected: the store resets a
// selected conversation to `idle` when its turn ends or when it is selected,
// so an unmarked title is already the correct "nothing needs attention" title.

/** App name used when no conversation is selected. */
export const DEFAULT_PAGE_TITLE = 'DecafClaw';

/**
 * Marker per conversation activity status that the selected conversation can
 * hold. Keys are the server's `conversation_status` values the client keeps
 * for the active conversation.
 * @type {Readonly<Record<string, string>>}
 */
const STATUS_MARKERS = Object.freeze({
  idle: '',
  busy: '\u25D0',
  waiting: '\u25CF',
});

/**
 * Compose the document title for a conversation and its activity status.
 * Unmarked statuses (including `finished`, which never sticks to the selected
 * conversation) produce the plain title.
 * @param {string} [sessionTitle] selected conversation's display title, or ''
 * @param {string} [status] 'idle' | 'busy' | 'waiting'
 * @returns {string}
 */
export function buildPageTitle(sessionTitle, status = 'idle') {
  const title = String(sessionTitle || '').trim();
  const marker = STATUS_MARKERS[status] || '';
  const name = title ? `${title} \u2014 ${DEFAULT_PAGE_TITLE}` : DEFAULT_PAGE_TITLE;
  return marker ? `${marker} ${name}` : name;
}

/**
 * Write the composed title to the document, skipping no-op assignments.
 * @param {string} [sessionTitle]
 * @param {string} [status]
 */
export function applyPageTitle(sessionTitle, status = 'idle') {
  const next = buildPageTitle(sessionTitle, status);
  if (document.title !== next) document.title = next;
}
