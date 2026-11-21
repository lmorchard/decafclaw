/**
 * Normalizes a user-entered server address into an http(s) base URL.
 * Returns null when the input is not a usable host.
 */
export function normalizeHost(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) {
    return null;
  }

  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)
    ? trimmed
    : `http://${trimmed}`;
  const match = withScheme.match(/^(https?):\/\/([^/?#\s]+)(\/[^?#\s]*)?$/i);
  if (!match) {
    return null;
  }

  const path = (match[3] ?? '').replace(/\/+$/, '');
  return `${match[1].toLowerCase()}://${match[2]}${path}`;
}

/**
 * Builds the web UI link for a conversation. The web client reads `?conv=`
 * and re-selects the conversation after login.
 */
export function conversationWebUrl(host: string, convId: string | null): string {
  const base = (normalizeHost(host) ?? host).replace(/\/+$/, '');
  return convId ? `${base}/?conv=${encodeURIComponent(convId)}` : `${base}/`;
}
