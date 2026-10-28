import { describe, it, expect, vi } from 'vitest';
import { MessageStore } from './message-store.js';
import { MESSAGE_TYPES } from './message-types.js';

describe('MessageStore', () => {
    it('initializes with empty state', () => {
        const store = new MessageStore(() => {});
        expect(store.currentMessages).toEqual([]);
        expect(store.streamingText).toBe('');
        expect(store.hasMore).toBe(false);
    });

    it('handles pushMessage', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'user', content: 'hello' });
        expect(store.currentMessages.length).toBe(1);
        expect(store.currentMessages[0].content).toBe('hello');
    });

    it('handles clear', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'user', content: 'hello' });
        store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', text: 'chunk' }, '1');
        store.clear();
        expect(store.currentMessages.length).toBe(0);
        expect(store.streamingText).toBe('');
        expect(store.hasMore).toBe(false);
    });

    it('handles updateToolCall', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'tool_call', tool_call_id: 'tc1', content: 'initial' });
        store.updateToolCall('tc1', 'new content');
        
        const msg = store.currentMessages[0];
        expect(msg.content).toBe('new content');
        expect(msg.statusHistory).toBeDefined();
        expect(msg.statusHistory.length).toBe(1);
        expect(msg.statusHistory[0].text).toBe('new content');
    });

    it('handles replaceToolCall', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'tool_call', tool_call_id: 'tc1', content: 'initial', statusHistory: [{ text: 'history' }] });
        store.replaceToolCall('tc1', { role: 'tool', tool_call_id: 'tc1', content: 'done' });
        
        const msg = store.currentMessages[0];
        expect(msg.role).toBe('tool');
        expect(msg.content).toBe('done');
        expect(msg.statusHistory).toBeDefined();
        expect(msg.statusHistory[0].text).toBe('history');
    });

    it('handles markToolWidgetSubmitted', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'tool', tool_call_id: 'tc1', content: 'done' });
        store.markToolWidgetSubmitted('tc1', { some: 'data' });
        
        const msg = store.currentMessages[0];
        expect(msg.submitted).toBe(true);
        expect(msg.response).toEqual({ some: 'data' });
    });

    it('handles insertBeforeLastUser', () => {
        const store = new MessageStore(() => {});
        store.pushMessage({ role: 'user', content: 'user msg 1' });
        store.pushMessage({ role: 'user', content: 'user msg 2' });
        store.insertBeforeLastUser({ role: 'system', content: 'inserted' });
        
        expect(store.currentMessages.length).toBe(3);
        expect(store.currentMessages[1].role).toBe('system');
        expect(store.currentMessages[1].content).toBe('inserted');
        expect(store.currentMessages[2].content).toBe('user msg 2');
    });

    describe('handleMessage', () => {
        it('handles CONV_HISTORY and merges tool messages', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({
                type: MESSAGE_TYPES.CONV_HISTORY,
                conv_id: '1',
                has_more: true,
                messages: [
                    { role: 'assistant', content: 'msg', tool_calls: [{ id: 'tc1', function: { name: 'tool_1' } }], timestamp: 't1' },
                    { role: 'tool', tool_call_id: 'tc1', content: 'result', timestamp: 't2' }
                ]
            }, '1');
            
            expect(store.currentMessages.length).toBe(2);
            expect(store.currentMessages[0].role).toBe('assistant');
            expect(store.currentMessages[1].role).toBe('tool');
            expect(store.currentMessages[1].content).toBe('result');
            expect(store.hasMore).toBe(true);
        });

        it('handles CHUNK and clearStreamingText', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', text: 'part1 ' }, '1');
            store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', text: 'part2' }, '1');
            expect(store.streamingText).toBe('part1 part2');
            
            store.clearStreamingText();
            expect(store.streamingText).toBe('');
        });

        it('handles MESSAGE_COMPLETE', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', text: 'part1 ' }, '1');
            store.handleMessage({ type: MESSAGE_TYPES.MESSAGE_COMPLETE, conv_id: '1', role: 'assistant', text: 'part1 part2' }, '1');
            
            expect(store.streamingText).toBe('');
            expect(store.currentMessages.length).toBe(1);
            expect(store.currentMessages[0].content).toBe('part1 part2');
        });

        it('handles CHUNK with thinking and records thinking on MESSAGE_COMPLETE', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', thinking: 'think 1 ' }, '1');
            store.handleMessage({ type: MESSAGE_TYPES.CHUNK, conv_id: '1', thinking: 'think 2', text: 'answer' }, '1');
            expect(store.streamingThinking).toBe('think 1 think 2');
            expect(store.streamingText).toBe('answer');

            store.handleMessage({ type: MESSAGE_TYPES.MESSAGE_COMPLETE, conv_id: '1', role: 'assistant', text: 'answer' }, '1');
            expect(store.streamingThinking).toBe('');
            expect(store.streamingText).toBe('');
            expect(store.currentMessages.length).toBe(1);
            expect(store.currentMessages[0].content).toBe('answer');
            expect(store.currentMessages[0].thinking).toBe('think 1 think 2');
        });

        it('handles USER_MESSAGE and dedups', () => {
            const store = new MessageStore(() => {});
            store.pushMessage({ role: 'user', content: 'hello' });
            
            // Should dedupe
            store.handleMessage({ type: MESSAGE_TYPES.USER_MESSAGE, conv_id: '1', text: 'hello' }, '1');
            expect(store.currentMessages.length).toBe(1);
            
            // Should add
            store.handleMessage({ type: MESSAGE_TYPES.USER_MESSAGE, conv_id: '1', text: 'world' }, '1');
            expect(store.currentMessages.length).toBe(2);
            expect(store.currentMessages[1].content).toBe('world');
        });

        it('handles COMMAND_ACK, COMPACTION_DONE, BACKGROUND_EVENT', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({ type: MESSAGE_TYPES.COMMAND_ACK, conv_id: '1', skill: 'test_skill' }, '1');
            expect(store.currentMessages[0].role).toBe('command');
            
            store.handleMessage({ type: MESSAGE_TYPES.COMPACTION_DONE, conv_id: '1', success: true, before_messages: 10, after_messages: 5 }, '1');
            expect(store.currentMessages[1].role).toBe('compaction');
            expect(store.currentMessages[1].content).toBe('Conversation compacted: 10 → 5 messages');
            
            store.handleMessage({ type: MESSAGE_TYPES.BACKGROUND_EVENT, conv_id: '1', record: { ts: 123 } }, '1');
            expect(store.currentMessages[2].role).toBe('background_event');
        });

        it('marks a failed compaction as failed, not silently compacted (#1005)', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({
                type: MESSAGE_TYPES.COMPACTION_DONE,
                conv_id: '1',
                success: false,
                before_messages: 10,
                after_messages: 10,
            }, '1');
            expect(store.currentMessages[0].role).toBe('compaction');
            expect(store.currentMessages[0].content).toBe('Compaction failed — conversation unchanged');
        });

        it('formats COMPACTION_DONE with elapsed duration and token metrics matching compaction_display.py (#1094)', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({
                type: MESSAGE_TYPES.COMPACTION_DONE,
                conv_id: '1',
                success: true,
                before_messages: 50,
                after_messages: 25,
                elapsed_sec: 12.4,
                estimated_tokens_before: 12345,
                estimated_tokens_after: 6000,
            }, '1');
            expect(store.currentMessages[0].role).toBe('compaction');
            expect(store.currentMessages[0].content).toBe('Conversation compacted (12s, 50 → 25 messages, ~12,345 tokens compacted)');
            expect(store.currentMessages[0].record).toMatchObject({
                before_messages: 50,
                after_messages: 25,
                elapsed_sec: 12.4,
                estimated_tokens_before: 12345,
                estimated_tokens_after: 6000,
                success: true,
            });
        });

        it('retains compaction records and internal context roles in CONV_HISTORY (#1089, #1094)', () => {
            const store = new MessageStore(() => {});
            store.handleMessage({
                type: MESSAGE_TYPES.CONV_HISTORY,
                conv_id: '1',
                has_more: false,
                messages: [
                    { role: 'user', content: 'hello', timestamp: 't1' },
                    { role: 'conversation_notes', content: 'note 1', timestamp: 't2' },
                    { role: 'recent_journal', content: 'journal 1', timestamp: 't3' },
                    { role: 'workspace_references', content: 'file 1', timestamp: 't4' },
                    { role: 'mcp_references', content: 'mcp 1', timestamp: 't5' },
                    {
                        role: 'compaction',
                        content: 'Conversation compacted (10s, 20 → 10 messages)',
                        data: { before_messages: 20, after_messages: 10, success: true },
                        timestamp: 't6',
                    },
                    { role: 'cancel_marker', content: '[User cancelled]', timestamp: 't7' },
                    { role: 'turn_aborted', content: '[Turn failed]', timestamp: 't8' },
                ],
            }, '1');

            expect(store.currentMessages.map(m => m.role)).toEqual([
                'user',
                'conversation_notes',
                'recent_journal',
                'workspace_references',
                'mcp_references',
                'compaction',
                'cancel_marker',
                'turn_aborted',
            ]);
            expect(store.currentMessages[5].record).toEqual({
                before_messages: 20,
                after_messages: 10,
                success: true,
            });
        });

        it('preserves queued user messages in CONV_HISTORY and dedupes on USER_MESSAGE', () => {
            const store = new MessageStore(() => {});
            store.clear();
            store.handleMessage({
                type: MESSAGE_TYPES.CONV_HISTORY,
                conv_id: '1',
                has_more: false,
                messages: [
                    { role: 'user', content: 'prior message', timestamp: 't1' },
                    { role: 'assistant', content: 'prior reply', timestamp: 't2' },
                    { role: 'user', content: 'queued user message', timestamp: 't3' },
                ],
            }, '1');

            expect(store.currentMessages.length).toBe(3);
            expect(store.currentMessages[2].content).toBe('queued user message');

            // When USER_MESSAGE arrives later for the queued turn, it should dedupe
            store.handleMessage({ type: MESSAGE_TYPES.USER_MESSAGE, conv_id: '1', text: 'queued user message' }, '1');
            expect(store.currentMessages.length).toBe(3);
        });
    });
});


describe('shell command details', () => {
    it('keeps each live command alongside its result after progress updates', async () => {
        const { ToolStatusStore } = await import('./tool-status-store.js');
        const messages = new MessageStore(() => {});
        const tools = new ToolStatusStore(() => {}, {}, messages);
        for (const id of ['a', 'b']) {
            tools.handleMessage({ type: MESSAGE_TYPES.TOOL_START, conv_id: '1', tool: 'shell', tool_call_id: id, command: `echo ${id}` }, '1');
            tools.handleMessage({ type: MESSAGE_TYPES.TOOL_STATUS, conv_id: '1', tool: 'shell', tool_call_id: id, message: 'Running' }, '1');
        }
        for (const id of ['b', 'a']) {
            tools.handleMessage({ type: MESSAGE_TYPES.TOOL_END, conv_id: '1', tool: 'shell', tool_call_id: id, result_text: id }, '1');
        }
        expect(messages.currentMessages.map(m => [m.command, m.content])).toEqual([['echo a', 'a'], ['echo b', 'b']]);
    });

    it('restores the command from archived arguments', () => {
        const store = new MessageStore(() => {});
        store.handleMessage({ type: MESSAGE_TYPES.CONV_HISTORY, conv_id: '1', messages: [
            { role: 'assistant', tool_calls: [{ id: 'tc1', function: { name: 'shell', arguments: JSON.stringify({ command: 'echo hello' }) } }], timestamp: 't1' },
            { role: 'tool', tool_call_id: 'tc1', content: 'hello', timestamp: 't2' },
        ] }, '1');
        expect(store.currentMessages[0].command).toBe('echo hello');
        expect(store.currentMessages[0].content).toBe('hello');
    });
});

it.each(['not json', undefined, '{}'])('renders shell results with unavailable arguments (%s)', (argumentsValue) => {
    const store = new MessageStore(() => {});
    store.handleMessage({ type: MESSAGE_TYPES.CONV_HISTORY, conv_id: '1', messages: [
        { role: 'assistant', tool_calls: [{ id: 'tc1', function: { name: 'shell', arguments: argumentsValue } }], timestamp: 't1' },
        { role: 'tool', tool_call_id: 'tc1', content: 'output', timestamp: 't2' },
    ] }, '1');
    expect(store.currentMessages[0].command).toBe('');
    expect(store.currentMessages[0].content).toBe('output');
});
