import { describe, it, expect } from 'vitest';
import './chat-message.js';

describe('chat-message role rendering', () => {
  it('renders conversation_notes as a tool message', async () => {
    const el = document.createElement('chat-message');
    el.role = 'conversation_notes';
    el.content = 'note body';
    document.body.append(el);
    await el.updateComplete;

    const toolMsg = el.querySelector('tool-message');
    expect(toolMsg).not.toBeNull();
    expect(toolMsg?.tool).toBe('conversation_notes');
    expect(toolMsg?.icon).toBe('📝');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

  it('renders recent_journal as a tool message', async () => {
    const el = document.createElement('chat-message');
    el.role = 'recent_journal';
    el.content = 'journal entry';
    document.body.append(el);
    await el.updateComplete;

    const toolMsg = el.querySelector('tool-message');
    expect(toolMsg).not.toBeNull();
    expect(toolMsg?.tool).toBe('recent_journal');
    expect(toolMsg?.icon).toBe('📓');
    expect(el.querySelector('user-message')).toBeNull();
    el.remove();
  });

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

  it('forwards thinking to tool-call-message when assistant message has tool_calls', async () => {
    const el = document.createElement('chat-message');
    el.role = 'assistant';
    el.content = 'Calling tool...';
    el.thinking = 'Reasoning before tool';
    el.toolCalls = [{ function: { name: 'shell' } }];
    document.body.append(el);
    await el.updateComplete;

    const toolCallMsg = el.querySelector('tool-call-message');
    expect(toolCallMsg).not.toBeNull();
    expect(toolCallMsg.thinking).toBe('Reasoning before tool');
    expect(toolCallMsg.content).toBe('Calling tool...');
    el.remove();
  });
});
