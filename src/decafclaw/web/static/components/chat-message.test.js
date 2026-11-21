import { describe, it, expect, vi } from 'vitest';
import './chat-message.js';

describe('chat-message role rendering', () => {
  it('renders workspace_references as a tool message', async () => {
    const el = document.createElement('chat-message');
    el.role = 'workspace_references';
    el.content = 'file content';
    document.body.append(el);
    await el.updateComplete;

    const toolMsg = el.querySelector('tool-message');
    expect(toolMsg).not.toBeNull();
    expect(toolMsg?.tool).toBe('workspace_references');
    expect(toolMsg?.icon).toBe('📁');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

  it('renders mcp_references as a tool message', async () => {
    const el = document.createElement('chat-message');
    el.role = 'mcp_references';
    el.content = 'resource text';
    document.body.append(el);
    await el.updateComplete;

    const toolMsg = el.querySelector('tool-message');
    expect(toolMsg).not.toBeNull();
    expect(toolMsg?.tool).toBe('mcp_references');
    expect(toolMsg?.icon).toBe('🔌');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

  it('forwards provenance to the assistant message (#1563)', async () => {
    const el = document.createElement('chat-message');
    el.role = 'assistant';
    el.content = 'Answer';
    el.provenance = { facts: [{ fact: 'User prefers espresso.' }] };
    document.body.append(el);
    await el.updateComplete;

    const assistant = el.querySelector('assistant-message');
    expect(assistant).not.toBeNull();
    expect(assistant?.provenance).toEqual({ facts: [{ fact: 'User prefers espresso.' }] });
    el.remove();
  });

  it('renders cancel_marker as a distinct event banner', async () => {
    const el = document.createElement('chat-message');
    el.role = 'cancel_marker';
    el.content = '[User cancelled this turn.]';
    document.body.append(el);
    await el.updateComplete;

    const banner = el.querySelector('.chat-event.cancel-marker');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('Turn cancelled');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

  it('renders turn_aborted as a distinct event banner', async () => {
    const el = document.createElement('chat-message');
    el.role = 'turn_aborted';
    el.content = '[The previous turn failed unexpectedly.]';
    document.body.append(el);
    await el.updateComplete;

    const banner = el.querySelector('.chat-event.turn-aborted');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('Turn aborted');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

  it('renders compaction notice with metrics', async () => {
    const el = document.createElement('chat-message');
    el.role = 'compaction';
    el.content = 'Conversation compacted (10s, 50 → 20 messages, ~5,000 tokens compacted)';
    el.record = {
      before_messages: 50,
      after_messages: 20,
      estimated_tokens_before: 10000,
      estimated_tokens_after: 5000,
      elapsed_sec: 10.2,
      success: true,
    };
    document.body.append(el);
    await el.updateComplete;

    const card = el.querySelector('.compaction-notice');
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain('Conversation compacted');
    expect(card?.textContent).toContain('50 → 20 msgs');
    expect(card?.textContent).toContain('-50% tokens');
    expect(card?.textContent).toContain('10.2s');
    el.remove();
  });

  it('computes token reduction using whole-history estimates when present', async () => {
    const el = document.createElement('chat-message');
    el.role = 'compaction';
    el.content = 'Conversation compacted (10s, 50 → 20 messages, ~10,000 tokens compacted)';
    el.record = {
      before_messages: 50,
      after_messages: 20,
      estimated_tokens_before: 10000, // summarizer input
      history_tokens_before: 15000,   // whole history before
      history_tokens_after: 6000,     // whole history after
      elapsed_sec: 10.0,
      success: true,
    };
    document.body.append(el);
    await el.updateComplete;

    const card = el.querySelector('.compaction-notice');
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain('-60% tokens');
    el.remove();
  });

  it('renders thinking rows as their own transcript widget (#1627)', async () => {
    const el = document.createElement('chat-message');
    el.role = 'thinking';
    el.content = 'Reasoning before tool';
    el.provenance = { facts: [{ fact: 'User prefers espresso.' }] };
    el.streaming = true;
    document.body.append(el);
    await el.updateComplete;

    const thinking = el.querySelector('thinking-message');
    expect(thinking).not.toBeNull();
    expect(thinking?.content).toBe('Reasoning before tool');
    expect(thinking?.provenance).toEqual({ facts: [{ fact: 'User prefers espresso.' }] });
    expect(thinking?.streaming).toBe(true);
    expect(el.querySelector('assistant-message')).toBeNull();
    expect(el.querySelector('tool-call-message')).toBeNull();
    el.remove();
  });

  it('renders assistant tool-call history without owning thinking (#1627)', async () => {
    const el = document.createElement('chat-message');
    el.role = 'assistant';
    el.content = 'Calling tool...';
    el.toolCalls = [{ function: { name: 'shell' } }];
    document.body.append(el);
    await el.updateComplete;

    const toolCallMsg = el.querySelector('tool-call-message');
    expect(toolCallMsg).not.toBeNull();
    expect(toolCallMsg?.content).toBe('Calling tool...');
    expect(toolCallMsg?.thinking).toBeUndefined();
    el.remove();
  });
});

describe('chat-message tool failure tags (#1569)', () => {
  it('forwards isError/timedOut/timeoutSec to tool-message', async () => {
    const el = document.createElement('chat-message');
    el.role = 'tool';
    el.tool = 'slow_tool';
    el.content = '[error: tool slow_tool timed out after 180s]';
    el.isError = true;
    el.timedOut = true;
    el.timeoutSec = 180;
    document.body.append(el);
    await el.updateComplete;

    const toolMsg = el.querySelector('tool-message');
    expect(toolMsg.isError).toBe(true);
    expect(toolMsg.timedOut).toBe(true);
    expect(toolMsg.timeoutSec).toBe(180);
    el.remove();
  });
});

describe('chat-message branch affordance (#1581)', () => {
  function mount(role, chatView, props = {}) {
    const el = document.createElement('chat-message');
    el.role = role;
    el.content = 'hello';
    Object.assign(el, props);
    chatView.append(el);
    return el;
  }

  it('branches from the message timestamp via the chat-view store', async () => {
    const store = { forkConversation: vi.fn() };
    const host = document.createElement('chat-view');
    host.store = store;
    document.body.append(host);
    const el = mount('assistant', host, { timestamp: '2026-11-20T03:31:22.123456' });
    await el.updateComplete;

    const button = el.querySelector('button.branch-btn');
    expect(button).not.toBeNull();
    expect(button.textContent).toContain('Branch');
    button.click();
    expect(store.forkConversation).toHaveBeenCalledWith('2026-11-20T03:31:22.123456');
    host.remove();
  });

  it('renders only for user and assistant rows', async () => {
    const host = document.createElement('chat-view');
    host.store = { forkConversation: vi.fn() };
    document.body.append(host);
    const user = mount('user', host, { timestamp: 't1' });
    const tool = mount('tool', host, { timestamp: 't1' });
    const compaction = mount('compaction', host, { timestamp: 't1' });
    await Promise.all([user.updateComplete, tool.updateComplete, compaction.updateComplete]);

    expect(user.querySelector('button.branch-btn')).not.toBeNull();
    expect(tool.querySelector('button.branch-btn')).toBeNull();
    expect(compaction.querySelector('button.branch-btn')).toBeNull();
    host.remove();
  });

  it('omits the button when the message has no archive timestamp', async () => {
    const host = document.createElement('chat-view');
    host.store = { forkConversation: vi.fn() };
    document.body.append(host);
    const el = mount('assistant', host);
    await el.updateComplete;
    expect(el.querySelector('button.branch-btn')).toBeNull();
    host.remove();
  });
});
