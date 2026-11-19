/**
 * Read the conversation id encoded in the page URL: ?conv= first, then the
 * legacy #conv= form. Shared by the app bootstrap and the sidebar, which
 * needs the id before the store delivers its first conversation.
 *
 * @returns {string|null}
 */
export function getConvFromUrl() {
  const fromSearch = new URLSearchParams(location.search).get('conv');
  if (fromSearch) return fromSearch;
  if (location.hash) {
    const raw = location.hash.replace(/^#/, '');
    return new URLSearchParams(raw).get('conv') || null;
  }
  return null;
}
