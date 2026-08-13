import { describe, it, expect } from 'vitest';
import { renderMarkdown } from './markdown.js';

describe('markdown', () => {
    it('returns empty string for empty input', () => {
        expect(renderMarkdown('')).toBe('');
        expect(renderMarkdown(null)).toBe('');
    });

    it('renders basic markdown', () => {
        const html = renderMarkdown('**bold** and *italic*');
        expect(html).toContain('<strong>bold</strong>');
        expect(html).toContain('<em>italic</em>');
    });

    it('rewrites workspace:// image URLs', () => {
        const html = renderMarkdown('![alt](workspace://path/to/img.png)');
        expect(html).toContain('src="/api/workspace/path/to/img.png"');
        expect(html).toContain('alt="alt"');
    });

    it('rewrites workspace:// link URLs', () => {
        const html = renderMarkdown('[link](workspace://path/to/file.txt)');
        expect(html).toContain('href="/api/workspace/path/to/file.txt"');
        expect(html).toContain('link</a>');
    });

    it('handles [[wiki links]]', () => {
        const html = renderMarkdown('Check out [[My Page]]');
        expect(html).toContain('href="/vault/My%20Page"');
        expect(html).toContain('data-wiki-page="My Page"');
        expect(html).toContain('My Page</a>');
    });

    it('handles [[wiki links|with custom display]]', () => {
        const html = renderMarkdown('Check out [[My Page|this page]]');
        expect(html).toContain('href="/vault/My%20Page"');
        expect(html).toContain('data-wiki-page="My Page"');
        expect(html).toContain('this page</a>');
    });

    it('sanitizes dangerous HTML', () => {
        const html = renderMarkdown('Hello <script>alert("xss")</script>');
        expect(html).not.toContain('<script>');
    });
});
