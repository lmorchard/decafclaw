import { afterEach, describe, expect, it } from 'vitest';
import './thinking-message.js';

describe('thinking-message (#1627)', () => {
  let el;

  afterEach(() => {
    el?.remove();
    document.body.innerHTML = '';
  });

  async function mount(props = {}) {
    el = document.createElement('thinking-message');
    Object.assign(el, props);
    document.body.appendChild(el);
    await el.updateComplete;
    return el;
  }

  it('renders a collapsed thought block for a completed thought', async () => {
    const msg = await mount({ content: 'Let me think about this step by step.' });
    const block = msg.querySelector('details.thought-block');
    expect(block).toBeTruthy();
    expect(block.hasAttribute('open')).toBe(false);

    const summary = block.querySelector('summary');
    expect(summary.textContent).toContain('Thought process');
    expect(block.querySelector('.thought-spinner')).toBeNull();

    const body = block.querySelector('.thought-body');
    expect(body.textContent).toContain('Let me think about this step by step.');
  });

  it('renders markdown inside the thought body', async () => {
    const msg = await mount({ content: 'Here is **bold** reasoning and `code`.' });
    const body = msg.querySelector('.thought-body');
    expect(body.querySelector('strong')?.textContent).toBe('bold');
    expect(body.querySelector('code')?.textContent).toBe('code');
  });

  it('opens and spins while thinking is streaming', async () => {
    const msg = await mount({ content: 'Drafting thoughts...', streaming: true });
    const block = msg.querySelector('details.thought-block');
    expect(block.hasAttribute('open')).toBe(true);
    expect(block.querySelector('summary .thought-spinner')).toBeTruthy();
  });

  it('renders no thought block for empty content', async () => {
    const msg = await mount({ content: '   ' });
    expect(msg.querySelector('.thought-block')).toBeNull();
  });

  it('renders the provenance capsule row before the thought block (#1563)', async () => {
    const msg = await mount({
      content: 'Thinking.',
      provenance: { facts: [{ fact: 'User prefers espresso.' }] },
    });

    const row = msg.querySelector('turn-provenance-row');
    expect(row).toBeTruthy();
    expect(/** @type {any} */ (row).provenance.facts).toHaveLength(1);

    const block = msg.querySelector('.thought-block');
    expect(row.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
