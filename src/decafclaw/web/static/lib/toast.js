/**
 * Show a transient toast message in the floating #toast-container region.
 * No-op if the container element isn't present.
 *
 * @param {string} message
 * @param {number} [duration] Milliseconds before the toast removes itself.
 */
export function showToast(message, duration = 5000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}
