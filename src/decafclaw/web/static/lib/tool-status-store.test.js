import { describe, it, expect, vi } from 'vitest';
import { ToolStatusStore } from './tool-status-store.js';
import { MESSAGE_TYPES } from './message-types.js';

describe('ToolStatusStore', () => {
    it('initializes with empty state', () => {
        const store = new ToolStatusStore(() => {}, {}, {});
        expect(store.toolStatus).toBeNull();
        expect(store.pendingConfirms).toEqual([]);
    });

    it('handles TOOL_START, TOOL_STATUS, TOOL_END', () => {
        const ws = {};
        const msgStore = { pushMessage: vi.fn(), updateToolCall: vi.fn(), replaceToolCall: vi.fn() };
        const store = new ToolStatusStore(() => {}, ws, msgStore);
        
        store.handleMessage({ type: MESSAGE_TYPES.TOOL_START, tool_call_id: 'tc1', tool: 'test', conv_id: '1' }, '1');
        expect(store.toolStatus).toBe('Running test...');
        expect(msgStore.pushMessage).toHaveBeenCalled();

        store.handleMessage({ type: MESSAGE_TYPES.TOOL_STATUS, tool_call_id: 'tc1', tool: 'test', message: 'status update', conv_id: '1' }, '1');
        expect(store.toolStatus).toBe('test: status update');
        expect(msgStore.updateToolCall).toHaveBeenCalledWith('tc1', 'test: status update');

        store.handleMessage({ type: MESSAGE_TYPES.TOOL_END, tool_call_id: 'tc1', tool: 'test', result_text: 'done', conv_id: '1' }, '1');
        expect(store.toolStatus).toBeNull();
        expect(msgStore.replaceToolCall).toHaveBeenCalled();
    });

    it('handles CONFIRM_REQUEST and deduplicates', () => {
        const ws = {};
        const msgStore = {};
        const store = new ToolStatusStore(() => {}, ws, msgStore);

        // Wrong conv_id
        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '2', confirmation_id: 'c1' }, '1');
        expect(store.pendingConfirms.length).toBe(0);

        // Correct conv_id
        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '1', confirmation_id: 'c1' }, '1');
        expect(store.pendingConfirms.length).toBe(1);

        // Deduplicate
        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '1', confirmation_id: 'c1' }, '1');
        expect(store.pendingConfirms.length).toBe(1);
    });

    it('handles CONFIRMATION_RESPONSE', () => {
        const ws = {};
        const msgStore = { markToolWidgetSubmitted: vi.fn() };
        const store = new ToolStatusStore(() => {}, ws, msgStore);

        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '1', confirmation_id: 'c1', action_type: 'widget_response', tool_call_id: 'tc1' }, '1');
        expect(store.pendingConfirms.length).toBe(1);

        store.handleMessage({ type: MESSAGE_TYPES.CONFIRMATION_RESPONSE, confirmation_id: 'c1', data: { ok: true } }, '1');
        expect(store.pendingConfirms.length).toBe(0);
        expect(msgStore.markToolWidgetSubmitted).toHaveBeenCalledWith('tc1', { ok: true });
    });

    it('handles respondToConfirm', () => {
        let sent = null;
        const ws = { send: vi.fn((data) => sent = data) };
        let onChangeCalled = false;
        const store = new ToolStatusStore(() => { onChangeCalled = true; }, ws, {});

        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '1', confirmation_id: 'c1', tool_call_id: 'tc1', context_id: 'ctx1', tool: 'test' }, '1');
        
        store.respondToConfirm('ctx1', 'test', 'tc1', true, { extra: 'data' });
        
        expect(sent.type).toBe(MESSAGE_TYPES.CONFIRM_RESPONSE);
        expect(sent.approved).toBe(true);
        expect(sent.extra).toBe('data');
        expect(store.pendingConfirms.length).toBe(0);
        expect(onChangeCalled).toBe(true);
    });

    it('handles respondToWidget', () => {
        let sent = null;
        const ws = { send: vi.fn((data) => sent = data) };
        const msgStore = { markToolWidgetSubmitted: vi.fn() };
        let onChangeCalled = false;
        const store = new ToolStatusStore(() => { onChangeCalled = true; }, ws, msgStore);

        store.handleMessage({ type: MESSAGE_TYPES.CONFIRM_REQUEST, conv_id: '1', confirmation_id: 'c1', tool_call_id: 'tc1' }, '1');
        
        store.respondToWidget('tc1', { input: 'value' });
        
        expect(sent.type).toBe(MESSAGE_TYPES.WIDGET_RESPONSE);
        expect(sent.data).toEqual({ input: 'value' });
        expect(msgStore.markToolWidgetSubmitted).toHaveBeenCalledWith('tc1', { input: 'value' });
        expect(store.pendingConfirms.length).toBe(0);
        expect(onChangeCalled).toBe(true);
    });
});
