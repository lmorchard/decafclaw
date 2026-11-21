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
