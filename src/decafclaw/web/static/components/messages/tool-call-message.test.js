import { afterEach, describe, expect, it } from 'vitest';
import './tool-call-message.js';

describe('tool-call-message thinking block (#154)', () => {
  let el;

  afterEach(() => {
    el?.remove();
    document.body.innerHTML = '';
  });

  async function mount(props = {}) {
    el = document.createElement('tool-call-message');
    Object.assign(el, props);
    document.body.appendChild(el);
    await el.updateComplete;
    return el;
  }

  it('renders .thought-block and tool summary when thinking is present in tool_calls_history', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: 'I will check the files now.',
      thinking: 'Need to inspect the directory structure first.',
      toolCalls: [{ function: { name: 'workspace_list' } }],
    });

    const thoughtBlock = msg.querySelector('.thought-block');
    expect(thoughtBlock).toBeTruthy();
    expect(thoughtBlock.tagName.toLowerCase()).toBe('details');
    expect(thoughtBlock.hasAttribute('open')).toBe(false);

    const summary = thoughtBlock.querySelector('summary');
    expect(summary).toBeTruthy();
    expect(summary.textContent).toContain('Thought process');

    const body = thoughtBlock.querySelector('.thought-body');
    expect(body).toBeTruthy();
    expect(body.textContent).toContain('Need to inspect the directory structure first.');

    // Assistant content is rendered
    const content = msg.querySelector('.message.assistant .content');
    expect(content).toBeTruthy();
    expect(content.textContent).toContain('I will check the files now.');

    // Tool header is retained
    const toolHeader = msg.querySelector('.message.tool .tool-result-header');
    expect(toolHeader).toBeTruthy();
    expect(toolHeader.querySelector('.tool-name')?.textContent).toBe('workspace_list');
  });

  it('renders .thought-block even when assistant content is empty in tool_calls_history', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: '',
      thinking: 'Autonomous decision to invoke shell.',
      toolCalls: [{ function: { name: 'shell' } }],
    });

    const thoughtBlock = msg.querySelector('.thought-block');
    expect(thoughtBlock).toBeTruthy();

    const body = thoughtBlock.querySelector('.thought-body');
    expect(body.textContent).toContain('Autonomous decision to invoke shell.');

    // No empty content div
    expect(msg.querySelector('.message.assistant .content')).toBeNull();

    // Tool header is still rendered
    const toolHeader = msg.querySelector('.message.tool .tool-result-header');
    expect(toolHeader).toBeTruthy();
    expect(toolHeader.querySelector('.tool-name')?.textContent).toBe('shell');
  });

  it('does not render .thought-block when thinking is empty in tool_calls_history', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: 'Calling tool without thinking.',
      thinking: '',
      toolCalls: [{ function: { name: 'shell' } }],
    });

    expect(msg.querySelector('.thought-block')).toBeNull();

    const content = msg.querySelector('.message.assistant .content');
    expect(content).toBeTruthy();
    expect(content.textContent).toContain('Calling tool without thinking.');
  });

  it('renders markdown inside .thought-body in tool-call-message', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: '',
      thinking: 'Plan: **step 1** and `run_check()`.',
      toolCalls: [{ function: { name: 'check' } }],
    });

    const body = msg.querySelector('.thought-body');
    expect(body).toBeTruthy();
    expect(body.querySelector('strong')?.textContent).toBe('step 1');
    expect(body.querySelector('code')?.textContent).toBe('run_check()');
  });
});
