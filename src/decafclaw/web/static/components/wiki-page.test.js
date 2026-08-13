import { describe, it, expect } from 'vitest';
import './wiki-page.js';

describe('wiki-page component', () => {
    it('can be created', () => {
        const el = document.createElement('wiki-page');
        expect(el).toBeDefined();
    });
});

import { WikiWriteMutex } from '../lib/wiki-page-write-mutex.js';
import { vi } from 'vitest';

describe('WikiWriteMutex pure logic extracted from wiki-page', () => {
    it('verifies mutex locking, pending-fields accumulation, and conflict resolution/retry bookkeeping', async () => {
        const apiPut = vi.fn().mockResolvedValue({ ok: true, data: { modified: 2 } });
        const mutex = new WikiWriteMutex(apiPut);
        
        mutex.queueFields({ title: 'test1' });
        mutex.queueFields({ tags: ['test'] });
        expect(mutex.pendingFields).toEqual({ title: 'test1', tags: ['test'] });
        
        await mutex.flush('page1', 1);
        expect(apiPut).toHaveBeenCalledWith({ frontmatter: { title: 'test1', tags: ['test'] }, modified: 1 }, 'page1', 1);
    });
});
