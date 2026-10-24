import { afterEach, describe, expect, it } from 'vitest';
import './assistant-message.js';

describe('assistant-message thinking block (#154)', () => {
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

  it('renders .thought-block when thinking contains text', async () => {
    const msg = await mount({ content: 'Final answer', thinking: 'Let me think about this step by step.' });
    const block = msg.querySelector('.thought-block');
    expect(block).toBeTruthy();
    expect(block.tagName.toLowerCase()).toBe('details');

    const summary = block.querySelector('summary');
    expect(summary).toBeTruthy();
    expect(summary.textContent).toContain('Thought process');

    const body = block.querySelector('.thought-body');
    expect(body).toBeTruthy();
    expect(body.textContent).toContain('Let me think about this step by step.');
  });

  it('does not render .thought-block when thinking is empty or whitespace', async () => {
    const msg1 = await mount({ content: 'Answer', thinking: '' });
    expect(msg1.querySelector('.thought-block')).toBeNull();

    el?.remove();
    const msg2 = await mount({ content: 'Answer', thinking: '   ' });
    expect(msg2.querySelector('.thought-block')).toBeNull();
  });

  it('keeps details element collapsed by default on completed messages', async () => {
    const msg = await mount({
      content: 'Done',
      thinking: 'Private internal reasoning',
      streaming: false,
    });
    const block = msg.querySelector('details.thought-block');
    expect(block).toBeTruthy();
    expect(block.hasAttribute('open')).toBe(false);
    expect(block.querySelector('.thought-spinner')).toBeNull();
  });

  it('renders markdown inside .thought-body', async () => {
    const msg = await mount({
      content: 'Answer',
      thinking: 'Here is **bold** reasoning and `code`.',
    });
    const body = msg.querySelector('.thought-body');
    expect(body).toBeTruthy();
    expect(body.querySelector('strong')).toBeTruthy();
    expect(body.querySelector('strong').textContent).toBe('bold');
    expect(body.querySelector('code')).toBeTruthy();
    expect(body.querySelector('code').textContent).toBe('code');
  });

  it('shows an active spinner in summary during streaming', async () => {
    const msg = await mount({
      content: '',
      thinking: 'Drafting thoughts...',
      streaming: true,
    });
    const block = msg.querySelector('.thought-block');
    expect(block).toBeTruthy();
    const spinner = block.querySelector('summary .thought-spinner');
    expect(spinner).toBeTruthy();
  });
});
