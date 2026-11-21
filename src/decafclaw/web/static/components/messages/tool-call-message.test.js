import { afterEach, describe, expect, it } from 'vitest';
import './tool-call-message.js';

describe('tool-call-message', () => {
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

  it('renders tool_calls_history content and tool names without a thought block (#1627)', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: 'I will check the files now.',
      thinking: 'Legacy thinking field.',
      toolCalls: [{ function: { name: 'workspace_list' } }],
    });

    const content = msg.querySelector('.message.assistant .content');
    expect(content).toBeTruthy();
    expect(content.textContent).toContain('I will check the files now.');
    expect(msg.querySelector('.thought-block')).toBeNull();

    const toolHeader = msg.querySelector('.message.tool .tool-result-header');
    expect(toolHeader).toBeTruthy();
    expect(toolHeader.querySelector('.tool-name')?.textContent).toBe('workspace_list');
  });

  it('renders only the tool header when the history row has no content', async () => {
    const msg = await mount({
      variant: 'tool_calls_history',
      content: '',
      toolCalls: [{ function: { name: 'shell' } }],
    });

    expect(msg.querySelector('.message.assistant')).toBeNull();
    const toolHeader = msg.querySelector('.message.tool .tool-result-header');
    expect(toolHeader).toBeTruthy();
    expect(toolHeader.querySelector('.tool-name')?.textContent).toBe('shell');
  });

  it('renders the live tool_call summary and progress history', async () => {
    const msg = await mount({
      variant: 'tool_call',
      content: 'Running checks',
      statusHistory: [
        { text: 'Starting', timestamp: '2026-11-21T10:00:00Z' },
        { text: 'Running checks', timestamp: '2026-11-21T10:00:01Z' },
      ],
    });

    expect(msg.querySelector('.tool-summary').textContent).toBe('Running checks');
    expect(msg.querySelector('.status-history-badge').textContent).toBe('2');
  });
});
