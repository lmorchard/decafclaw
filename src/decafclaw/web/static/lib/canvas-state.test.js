import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyEvent, closeTabById, currentSnapshot, setActiveConv,
} from './canvas-state.js';

/**
 * `closeTabById` must remove the tab from local state on its own.
 *
 * The server confirms a close by broadcasting `canvas_update` over /ws/chat,
 * but the terminal widget calls this when it discovers the server restarted —
 * exactly when that socket is least able to deliver. The reconnect path does
 * re-send SELECT_CONV now (#704), so the broadcast eventually reaches a
 * subscribed socket, but only once the reconnect backoff has elapsed — long
 * after this close completed. Relying on the push left dead tabs on screen
 * until a full page reload.
 */
describe('closeTabById', () => {
  beforeEach(async () => {
    // setActiveConv fetches; stub it so state comes from applyEvent below.
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
    await setActiveConv('c1');
    // The store is module-level and survives between cases; the stubbed fetch
    // above means setActiveConv won't overwrite it. Reset explicitly.
    applyEvent({ conv_id: 'c1', kind: 'clear' });
    applyEvent({
      conv_id: 'c1', kind: 'new_tab', active_tab: 't1',
      tab: { id: 't1', label: 'One', widget_type: 'terminal', data: {} },
    });
    applyEvent({
      conv_id: 'c1', kind: 'new_tab', active_tab: 't2',
      tab: { id: 't2', label: 'Two', widget_type: 'terminal', data: {} },
    });
  });

  it('removes the tab locally without waiting for the server broadcast', async () => {
    expect(currentSnapshot().tabs.map(t => t.id)).toEqual(['t1', 't2']);

    await closeTabById('c1', 't2');

    // No canvas_update was applied — only the optimistic path ran.
    expect(currentSnapshot().tabs.map(t => t.id)).toEqual(['t1']);
    expect(currentSnapshot().activeTabId).toBe('t1');
  });

  it('still POSTs to the server', async () => {
    await closeTabById('c1', 't2');
    const calls = /** @type {any} */ (globalThis.fetch).mock.calls;
    const post = calls.find((/** @type {any[]} */ c) => String(c[0]).includes('close_tab'));
    expect(post).toBeTruthy();
    expect(JSON.parse(post[1].body)).toEqual({ tab_id: 't2' });
  });

  it('is idempotent when the server echo arrives afterwards', async () => {
    await closeTabById('c1', 't2');
    applyEvent({
      conv_id: 'c1', kind: 'close_tab', closed_tab_id: 't2', active_tab: 't1',
    });
    expect(currentSnapshot().tabs.map(t => t.id)).toEqual(['t1']);
  });

  it('ignores a tab id it does not know about', async () => {
    await closeTabById('c1', 'nope');
    expect(currentSnapshot().tabs.map(t => t.id)).toEqual(['t1', 't2']);
  });
});
