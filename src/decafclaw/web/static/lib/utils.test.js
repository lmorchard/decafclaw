import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { encodePagePath, formatTime, formatRelativeTime, copyToClipboard, setupResizeHandle } from './utils.js';

describe('utils', () => {
    describe('encodePagePath', () => {
        it('encodes segments but preserves slashes', () => {
            expect(encodePagePath('a/b/c')).toBe('a/b/c');
            expect(encodePagePath('my page/is cool')).toBe('my%20page/is%20cool');
            expect(encodePagePath('a&b/c?d')).toBe('a%26b/c%3Fd');
        });
    });

    describe('formatTime', () => {
        it('formats ISO timestamp to HH:MM', () => {
            const ts = '2026-08-13T10:00:00Z';
            const d = new Date(ts);
            const expected = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            expect(formatTime(ts)).toBe(expected);
        });

        it('returns empty string for missing ts', () => {
            expect(formatTime('')).toBe('');
            expect(formatTime(null)).toBe('');
        });
    });

    describe('formatRelativeTime', () => {
        beforeEach(() => {
            vi.useFakeTimers();
        });

        afterEach(() => {
            vi.useRealTimers();
        });

        it('formats relative times correctly', () => {
            const now = new Date('2026-08-13T12:00:00Z');
            vi.setSystemTime(now);

            expect(formatRelativeTime('2026-08-13T11:59:30Z')).toBe('just now');
            expect(formatRelativeTime('2026-08-13T11:50:00Z')).toBe('10m ago');
            expect(formatRelativeTime('2026-08-13T09:00:00Z')).toBe('3h ago');
            expect(formatRelativeTime('2026-08-10T12:00:00Z')).toBe('3d ago');
            expect(formatRelativeTime('2026-08-01T12:00:00Z')).toBe(new Date('2026-08-01T12:00:00Z').toLocaleDateString());
        });

        it('returns empty string for invalid ts', () => {
            expect(formatRelativeTime('')).toBe('');
            expect(formatRelativeTime('invalid')).toBe('');
        });
    });

    describe('copyToClipboard', () => {
        afterEach(() => {
            vi.restoreAllMocks();
        });

        it('uses navigator.clipboard if available', async () => {
            Object.assign(navigator, {
                clipboard: {
                    writeText: vi.fn().mockResolvedValue()
                }
            });

            await copyToClipboard('test text');
            expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test text');
        });

        it('falls back to execCommand if clipboard fails', async () => {
            Object.assign(navigator, {
                clipboard: {
                    writeText: vi.fn().mockRejectedValue(new Error('fail'))
                }
            });

            const execCommand = vi.fn().mockReturnValue(true);
            document.execCommand = execCommand;

            await copyToClipboard('test text');
            
            expect(execCommand).toHaveBeenCalledWith('copy');
        });
        
        it('falls back to execCommand if navigator.clipboard is missing', async () => {
            Object.assign(navigator, { clipboard: undefined });

            const execCommand = vi.fn().mockReturnValue(true);
            document.execCommand = execCommand;

            await copyToClipboard('test text');
            
            expect(execCommand).toHaveBeenCalledWith('copy');
        });
    });

    describe('setupResizeHandle', () => {
        beforeEach(() => {
            localStorage.clear();
            document.documentElement.style.cssText = '';
        });

        it('restores from localStorage on init', () => {
            localStorage.setItem('my_width', '300');
            const handle = document.createElement('div');
            const container = document.createElement('div');
            
            setupResizeHandle({
                handle,
                container,
                minWidth: 100,
                maxWidth: 500,
                storageKey: 'my_width',
                cssVar: '--my-width'
            });

            expect(document.documentElement.style.getPropertyValue('--my-width')).toBe('300px');
        });
        
        // Simulating mousedown/mousemove/mouseup in jsdom is complex, but we can test the init part
        // and confirm it adds event listeners.
        it('adds event listeners', () => {
            const handle = document.createElement('div');
            const container = document.createElement('div');
            const addSpy = vi.spyOn(handle, 'addEventListener');
            
            setupResizeHandle({
                handle,
                container,
                minWidth: 100,
                maxWidth: 500,
                storageKey: 'my_width',
                cssVar: '--my-width'
            });

            expect(addSpy).toHaveBeenCalledWith('mousedown', expect.any(Function));
        });
    });
});
