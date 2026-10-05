/**
 * Keyboard activation for clickable sidebar list rows (#555).
 *
 * The rows stay `<div>`s on purpose: Pico v2 styles `<button>` and
 * `[role="button"]` as primary buttons, which breaks the row look. A row
 * gets `tabindex="0"`, a descriptive `aria-label`, and this handler on
 * `@keydown`, so keyboard users can focus it and activate it with Enter or
 * Space, the same as a click.
 *
 *   <div class="conv-item" tabindex="0" aria-label=${`Open page ${title}`}
 *        @click=${open} @keydown=${activateOnEnterOrSpace(open)}>
 *
 * Only keys pressed on the row itself count. Rows contain their own buttons
 * and checkboxes, and their keydown events bubble up to the row; those
 * controls already handle Enter and Space themselves.
 *
 * @param {() => void} action - the row's click action
 * @returns {(e: KeyboardEvent) => void}
 */
export function activateOnEnterOrSpace(action) {
  return (e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter') {
      action();
    } else if (e.key === ' ') {
      // Space scrolls the sidebar list by default.
      e.preventDefault();
      action();
    }
  };
}
