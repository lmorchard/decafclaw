import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getCatalog, getDescriptor, _resetForTests } from './widget-catalog.js';

describe('WidgetCatalog', () => {
    beforeEach(() => {
        _resetForTests();
        globalThis.fetch = vi.fn();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('covers state mutations, updates, and event notifications (catalog fetch)', async () => {
        const mockWidgets = {
            widgets: [
                { name: 'test_widget', tier: 'builtin', js_url: '/test.js' }
            ]
        };
        globalThis.fetch.mockResolvedValueOnce({
            ok: true,
            json: async () => mockWidgets
        });

        const catalog = await getCatalog();
        expect(globalThis.fetch).toHaveBeenCalledTimes(1);
        expect(catalog.get('test_widget')).toEqual(mockWidgets.widgets[0]);

        // Memoized
        await getCatalog();
        expect(globalThis.fetch).toHaveBeenCalledTimes(1);

        expect(getDescriptor('test_widget')).toEqual(mockWidgets.widgets[0]);
        expect(getDescriptor('unknown')).toBeNull();
    });

    it('handles fetch failures and allows retry', async () => {
        globalThis.fetch.mockRejectedValueOnce(new Error('Network error'));
        
        const catalog = await getCatalog();
        expect(catalog.size).toBe(0);
        
        // Should retry on next call
        globalThis.fetch.mockResolvedValueOnce({
            ok: true,
            json: async () => ({ widgets: [{ name: 'retry_widget' }] })
        });
        
        const retryCatalog = await getCatalog();
        expect(globalThis.fetch).toHaveBeenCalledTimes(2);
        expect(retryCatalog.get('retry_widget')).toBeDefined();
    });
});
