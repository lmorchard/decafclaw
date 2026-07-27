import { afterEach, describe, expect, it } from 'vitest';

await import('./wiki-metadata.js');

describe('wiki-metadata #raw-editor race', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('keystrokes typed while a raw save is in flight survive the save', async () => {
    // Mount the component
    const el = /** @type {import('./wiki-metadata.js').WikiMetadata} */ (
      document.createElement('wiki-metadata')
    );
    document.body.appendChild(el);

    // (a) Set frontmatterRaw from the server
    el.frontmatterRaw = 'a: 1';
    await el.updateComplete;

    // (b) Open the raw editor — this copies frontmatterRaw into _rawText.
    // First expand the panel (so #renderEditControls runs), then click the
    // raw toggle to open the textarea.
    el._expanded = true;
    await el.updateComplete;
    const rawToggle = /** @type {HTMLButtonElement} */ (
      el.querySelector('.wiki-md-raw-toggle')
    );
    rawToggle.click();
    await el.updateComplete;

    // (c) Simulate the user typing more while a save is in flight.
    // Dispatch an input event on the textarea so the component's @input
    // handler runs (which sets both _rawText and the dirty tracking state).
    const userToken = 'b: user-typed-during-flight';
    const textarea = /** @type {HTMLTextAreaElement} */ (
      el.querySelector('.wiki-md-raw-input')
    );
    textarea.value = `a: 2\n${userToken}`;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await el.updateComplete;

    // (d) The save completes: host calls closeRaw() and then assigns the
    // server's frontmatterRaw (which does NOT contain the user's token)
    el.closeRaw();
    el.frontmatterRaw = 'a: 2';  // Server saved only 'a: 2', not the user's new typing
    await el.updateComplete;

    // (e) Assert the user's token is still present in _rawText
    // This fails today because willUpdate reseeds _rawText from frontmatterRaw
    // when _rawOpen is false (closeRaw sets it to false before the assignment)
    expect(el._rawText).toContain(userToken);
  });
});
