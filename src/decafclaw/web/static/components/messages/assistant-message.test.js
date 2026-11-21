import { afterEach, describe, expect, it } from 'vitest';
import './assistant-message.js';

describe('assistant-message', () => {
  let el;

  afterEach(() => {
    el?.remove();
    document.body.innerHTML = '';
  });

  async function mount(props = {}) {
    el = document.createElement('assistant-message');
    Object.assign(el, props);
    document.body.appendChild(el);
    await el.updateComplete;
    return el;
  }

  it('renders markdown content and no thought block (#1627)', async () => {
    const msg = await mount({ content: 'Final **answer**.', thinking: 'Legacy thinking field.' });
    const content = msg.querySelector('.content');
    expect(content.textContent).toContain('Final answer.');
    expect(content.querySelector('strong')).toBeTruthy();
    expect(msg.querySelector('.thought-block')).toBeNull();
  });

  it('shows a streaming indicator on the response while streaming', async () => {
    const msg = await mount({ content: 'Partial answer', streaming: true });
    expect(msg.querySelector('.streaming-indicator')).toBeTruthy();
  });

  it('renders the provenance capsule row above the response (#1563)', async () => {
    const msg = await mount({
      content: 'Final answer',
      provenance: { facts: [{ fact: 'User prefers espresso.' }, { fact: 'User is in Pacific time.' }] },
    });

    const row = msg.querySelector('turn-provenance-row');
    expect(row).toBeTruthy();
    expect(/** @type {any} */ (row).provenance.facts).toHaveLength(2);

    const content = msg.querySelector('.content');
    expect(row.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders no provenance row for an empty payload (#1563)', async () => {
    const msg = await mount({ content: 'Answer', provenance: {} });
    const row = /** @type {any} */ (msg.querySelector('turn-provenance-row'));
    await row.updateComplete;
    expect(row.querySelector('.turn-provenance')).toBeNull();
  });
});
