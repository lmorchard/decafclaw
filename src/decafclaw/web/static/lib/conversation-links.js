/**
 * Conversation link routing (#1485).
 *
 * Rendered conversation links carry data attributes for vault pages and
 * workspace files. An ordinary click opens the target in the current session's
 * side panel; modified clicks keep the browser's behavior (new tab), using the
 * link's href.
 */

/** @typedef {{ showWikiPage: (page: string) => void, showFilePage: (path: string) => void }} LinkHandlers */

/**
 * True when the click asks the browser for a new tab or window.
 * @param {MouseEvent} event
 * @returns {boolean}
 */
export function isModifiedClick(event) {
  return event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey;
}

/**
 * Route a click on a rendered conversation link.
 * @param {MouseEvent} event
 * @param {LinkHandlers} handlers
 * @returns {boolean} whether the click was handled in-session
 */
export function handleConversationLinkClick(event, handlers) {
  if (isModifiedClick(event)) return false;
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return false;

  const wikiLink = target.closest('a.wiki-link');
  if (wikiLink) {
    // Inside the wiki pane, <wiki-page> owns link navigation.
    if (wikiLink.closest('#wiki-main')) return false;
    const page = wikiLink.getAttribute('data-wiki-page');
    if (!page) return false;
    event.preventDefault();
    handlers.showWikiPage(page);
    return true;
  }

  const fileLink = target.closest('a.workspace-link');
  if (fileLink) {
    const path = fileLink.getAttribute('data-workspace-path');
    if (!path) return false;
    event.preventDefault();
    handlers.showFilePage(path);
    return true;
  }

  return false;
}
