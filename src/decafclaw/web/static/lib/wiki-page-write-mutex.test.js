import { describe, it, expect, vi } from 'vitest';
import { WikiWriteMutex } from './wiki-page-write-mutex.js';

describe('WikiWriteMutex', () => {
    it('initializes with empty state', () => {
        const mutex = new WikiWriteMutex(vi.fn());
        expect(mutex.pendingFields).toEqual({});
        expect(mutex.metaError).toBeNull();
        expect(mutex.orphanMetaError).toBe('');
    });

    it('queues fields', () => {
        const mutex = new WikiWriteMutex(vi.fn());
        const canFlush = mutex.queueFields({ title: 'test' });
        expect(canFlush).toBe(true);
        expect(mutex.pendingFields).toEqual({ title: 'test' });
    });

    it('blocks queue flush if in conflict', () => {
        const mutex = new WikiWriteMutex(vi.fn());
        mutex.metaError = { status: 'conflict', message: 'test' };
        const canFlush = mutex.queueFields({ title: 'test' });
        expect(canFlush).toBe(false);
    });

    it('flushes pending fields successfully', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test' });
        const res = await mutex.flush('page1', 1);
        
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test' }, modified: 1 }, 'page1', 1);
        expect(res).toEqual({ ok: true, data: { modified: 2 } });
        expect(mutex.pendingFields).toEqual({});
    });

    it('handles flush conflict and sets state', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: false, status: 409, error: 'conflict' });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test' });
        await mutex.flush('page1', 1);
        
        expect(mutex.metaError).toEqual({ status: 'conflict', message: 'Metadata was modified externally.' });
        expect(mutex.lastMetaAttempt).toEqual({ kind: 'patch' });
        expect(mutex.pendingFields).toEqual({ title: 'test' }); // requeued
    });

    it('handles orphan conflict', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: false, status: 409, error: 'conflict' });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test' });
        await mutex.flush('page2', 2, { page: 'page1', modified: 1 });
        
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test' }, modified: 1 }, 'page1', 1);
        expect(mutex.orphanMetaError).toContain('was not saved');
        expect(mutex.orphanMetaError).toContain('modified externally');
        expect(mutex.pendingFields).toEqual({});
    });

    it('handles raw save success', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        const res = await mutex.saveRaw('raw_data', 'page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter_raw: 'raw_data', modified: 1 }, 'page1', 1);
        expect(res).toEqual({ ok: true, data: { modified: 2 } });
    });

    it('handles raw save conflict', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: false, status: 409 });
        const mutex = new WikiWriteMutex(apiPut);
        
        const res = await mutex.saveRaw('raw_data', 'page1', 1);
        expect(res).toEqual({ ok: false, error: 'Metadata was modified externally.' });
        expect(mutex.metaError).toEqual({ status: 'conflict', message: 'Metadata was modified externally.' });
        expect(mutex.lastMetaAttempt).toEqual({ kind: 'raw', raw: 'raw_data' });
    });

    it('blocks raw save if pending conflict', async () => {
        const mutex = new WikiWriteMutex(vi.fn());
        mutex.metaError = { status: 'conflict', message: 'conflict' };
        
        const res = await mutex.saveRaw('raw_data', 'page1', 1);
        expect(res).toEqual({ ok: false, error: 'Resolve the pending metadata conflict above before saving raw YAML.' });
    });

    it('handles overwrite of patch', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.lastMetaAttempt = { kind: 'patch' };
        mutex.pendingFields = { title: 'test' };
        
        const res = await mutex.overwrite('page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test' } }, 'page1', 1);
        expect(res.ok).toBe(true);
    });

    it('handles overwrite of raw', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.lastMetaAttempt = { kind: 'raw', raw: 'raw_data' };
        
        const res = await mutex.overwrite('page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter_raw: 'raw_data' }, 'page1', 1);
        expect(res.ok).toBe(true);
    });

    it('handles retry of patch', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.lastMetaAttempt = { kind: 'patch' };
        mutex.pendingFields = { title: 'test' };
        mutex.metaError = { status: 'error', message: 'fail' };
        
        await mutex.retry('page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test' }, modified: 1 }, 'page1', 1);
        expect(mutex.metaError).toBeNull();
    });

    it('serializes writes', async () => {
        // Implement a delayed apiPut to test serialization
        let resolveFirst;
        const p1 = new Promise(r => resolveFirst = r);
        const apiPut = vi.fn()
            .mockImplementationOnce(() => p1)
            .mockImplementationOnce(() => Promise.resolve({ ok: true, data: { modified: 3 } }));
        
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test' });
        const flushPromise = mutex.flush('page1', 1);
        
        const rawPromise = mutex.saveRaw('raw_data', 'page1', 1);
        
        // At this point apiPut has been called once
        expect(apiPut).toHaveBeenCalledTimes(1);
        
        // Resolve first
        resolveFirst({ ok: true, data: { modified: 2 } });
        
        await flushPromise;
        await rawPromise;
        
        // Now apiPut has been called twice
        expect(apiPut).toHaveBeenCalledTimes(2);
        
        // The second call was delayed until the first finished
        expect(apiPut.mock.calls[1][0]).toEqual({ frontmatter_raw: 'raw_data', modified: 1 });
    });
});
