import { describe, it, expect } from 'vitest';
import { renderMarkdown } from './markdown.js';

/** @param {string} html @returns {HTMLAnchorElement} */
function parseAnchor(html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    return /** @type {HTMLAnchorElement} */ (template.content.querySelector('a'));
}

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

    it('rewrites workspace:// link URLs and marks them for in-session opening', () => {
        const link = parseAnchor(renderMarkdown('[link](workspace://path/to/file.txt)'));
        expect(link.getAttribute('href')).toBe('/api/workspace/path/to/file.txt');
        expect(link.classList.contains('workspace-link')).toBe(true);
        expect(link.getAttribute('data-workspace-path')).toBe('path/to/file.txt');
        expect(link.textContent).toBe('link');
    });

    it('marks vault:// links as wiki links for the in-session editor', () => {
        const link = parseAnchor(renderMarkdown('[Notes](vault://Notes/My%20Page)'));
        expect(link.getAttribute('href')).toBe('/vault/Notes/My%20Page');
        expect(link.classList.contains('wiki-link')).toBe(true);
        expect(link.getAttribute('data-wiki-page')).toBe('Notes/My Page');
        expect(link.textContent).toBe('Notes');
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
