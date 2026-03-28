/**
 * Format an ISO timestamp to a locale time string (HH:MM).
 * @param {string} ts
 * @returns {string}
 */
export function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
