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
            
            store.handleMessage({ type: MESSAGE_TYPES.COMPACTION_DONE, conv_id: '1', before_messages: 10, after_messages: 5 }, '1');
            expect(store.currentMessages[1].role).toBe('compaction');
            
            store.handleMessage({ type: MESSAGE_TYPES.BACKGROUND_EVENT, conv_id: '1', record: { ts: 123 } }, '1');
            expect(store.currentMessages[2].role).toBe('background_event');
        });
    });
});
