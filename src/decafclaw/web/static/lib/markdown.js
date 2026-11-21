/**
 * Shared markdown rendering — used by assistant messages, wiki pages, etc.
 *
 * Handles workspace:// and vault:// link rewriting, [[wiki-link]] detection,
 * and the data attributes that route link clicks inside the current session.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { buildNativeWorkspaceUrl } from './api-client/index.js';

// -- Custom renderer: rewrite workspace:// URLs --

const renderer = new marked.Renderer();

const originalImage = renderer.image.bind(renderer);
/** @type {(token: {href: string, title: string|null, text: string}) => string} */
renderer.image = function(token) {
  let href = token.href || '';
  if (href.startsWith('workspace://')) {
    href = buildNativeWorkspaceUrl(href.slice('workspace://'.length), 'raw');
    token = { ...token, href };
  }
  return originalImage(token);
};

/** Escape a value for a double-quoted HTML attribute. */
function escapeAttr(/** @type {string} */ value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** Site path for a vault page, encoding each path segment. */
function vaultHref(/** @type {string} */ page) {
  return '/vault/' + page.split('/').map(encodeURIComponent).join('/');
}

/** Decode a percent-encoded vault path, tolerating literal percent signs. */
function decodeVaultPage(/** @type {string} */ page) {
  try {
    return decodeURIComponent(page);
  } catch {
    return page;
  }
}

const originalLink = renderer.link.bind(renderer);
/** @type {(token: {href: string, title?: string|null, tokens: object[]}) => string} */
renderer.link = function(token) {
  const href = token.href || '';
  // The raw /api/workspace href stays as the fallback for modified clicks,
  // which open the file in a new tab. The data attribute lets app.js route an
  // ordinary click to the in-session file editor instead (#1485).
  if (href.startsWith('workspace://')) {
    const path = href.slice('workspace://'.length);
    const html = originalLink({
      ...token,
      href: buildNativeWorkspaceUrl(path, 'raw'),
    });
    return html.replace(
      '<a ',
      `<a class="workspace-link" data-workspace-path="${escapeAttr(path)}" `,
    );
  }
  if (href.startsWith('vault://')) {
    const page = decodeVaultPage(href.slice('vault://'.length));
    const html = originalLink({ ...token, href: vaultHref(page) });
    return html.replace(
      '<a ',
      `<a class="wiki-link" data-wiki-page="${escapeAttr(page)}" `,
    );
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
    const href = vaultHref(page);
    return `<a href="${escapeAttr(href)}" class="wiki-link" data-wiki-page="${escapeAttr(page)}">${display}</a>`;
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
    ADD_ATTR: ['target', 'data-wiki-page', 'data-workspace-path'],
    ADD_TAGS: ['img'],
    ADD_DATA_URI_TAGS: ['img'],
  });
}
