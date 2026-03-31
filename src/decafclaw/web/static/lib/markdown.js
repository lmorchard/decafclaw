/**
 * Shared markdown rendering — used by assistant messages, wiki pages, etc.
 *
 * Handles workspace:// URL rewriting and [[wiki-link]] detection.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';

// -- Custom renderer: rewrite workspace:// URLs --

const renderer = new marked.Renderer();

const originalImage = renderer.image.bind(renderer);
/** @type {(token: {href: string, title: string|null, text: string}) => string} */
renderer.image = function(token) {
  let href = token.href || '';
  if (href.startsWith('workspace://')) {
    href = '/api/workspace/' + href.slice('workspace://'.length);
    token = { ...token, href };
  }
  return originalImage(token);
};

const originalLink = renderer.link.bind(renderer);
/** @type {(token: {href: string, title?: string|null, tokens: object[]}) => string} */
renderer.link = function(token) {
  let href = token.href || '';
  if (href.startsWith('workspace://')) {
    href = '/api/workspace/' + href.slice('workspace://'.length);
    token = { ...token, href };
  }
  return originalLink(token);
};

// -- Wiki link extension: [[Page Name]] --

const wikiLinkExtension = {
  name: 'wikiLink',
  level: /** @type {const} */ ('inline'),
  start(/** @type {string} */ src) {
    return src.indexOf('[[');
  },
  tokenizer(/** @type {string} */ src) {
    const match = /^\[\[([^\]]+)\]\]/.exec(src);
    if (match) {
      const inner = match[1].trim();
      const pipeIdx = inner.indexOf('|');
      const page = pipeIdx >= 0 ? inner.slice(0, pipeIdx).trim() : inner;
      const display = pipeIdx >= 0 ? inner.slice(pipeIdx + 1).trim() : inner;
      return {
        type: 'wikiLink',
        raw: match[0],
        page,
        display,
      };
    }
    return undefined;
  },
  renderer(/** @type {{page: string, display: string}} */ token) {
    const page = token.page;
    const display = token.display || page;
    const href = '/vault/' + encodeURIComponent(page);
    return `<a href="${href}" class="wiki-link" data-wiki-page="${page.replace(/"/g, '&quot;')}">${display}</a>`;
  },
};

marked.use({ extensions: [wikiLinkExtension] });

/**
 * Render markdown to sanitized HTML.
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdown(text) {
  if (!text) return '';
  const raw = /** @type {string} */ (marked.parse(text, {
    breaks: true,
    async: false,
    renderer,
  }));
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['target', 'data-wiki-page'],
    ADD_TAGS: ['img'],
    ADD_DATA_URI_TAGS: ['img'],
  });
}
