/**
 * Milkdown plugin for [[wiki-link]] syntax.
 *
 * Uses a mark (like bold/italic) so link text is editable inline.
 *
 * Three parts:
 * 1. Remark plugin — parse [[target]] in markdown AST into wiki_link nodes
 * 2. ProseMirror mark — renders as <a class="wiki-link">, serializes as [[text]]
 * 3. Input rule — transform [[text]] as user types closing ]]
 *
 * Usage: editor.use(wikiLinkPlugin)
 */

import { $mark, $remark, $inputRule, InputRule } from '@milkdown/kit';

// ── 1. Remark plugin ──────────────────────────────────────────────────

const WIKI_LINK_RE = /\[\[([^\]]+)\]\]/g;

/**
 * Split a text string into text and wiki_link mdast nodes.
 * @param {string} value
 * @returns {any[]}
 */
function splitTextNode(value) {
  /** @type {any[]} */
  const parts = [];
  let lastIndex = 0;
  let match;
  WIKI_LINK_RE.lastIndex = 0;
  while ((match = WIKI_LINK_RE.exec(value)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', value: value.slice(lastIndex, match.index) });
    }
    parts.push({
      type: 'wiki_link',
      children: [{ type: 'text', value: match[1] }],
      data: { target: match[1] },
    });
    lastIndex = WIKI_LINK_RE.lastIndex;
  }
  if (lastIndex < value.length) {
    parts.push({ type: 'text', value: value.slice(lastIndex) });
  }
  return parts;
}

/**
 * @param {any} node
 */
function walkTree(node) {
  if (!node.children) return;
  /** @type {any[]} */
  const newChildren = [];
  for (const child of node.children) {
    if (child.type === 'text' && typeof child.value === 'string' && child.value.includes('[[')) {
      const parts = splitTextNode(child.value);
      if (parts.some(p => p.type === 'wiki_link')) {
        newChildren.push(...parts);
        continue;
      }
    }
    walkTree(child);
    newChildren.push(child);
  }
  node.children = newChildren;
}

const remarkWikiLink = $remark('remarkWikiLink', () => () =>
  /** @param {any} tree */ (tree) => {
    walkTree(tree);
    return tree;
  }
);

// ── 2. ProseMirror mark ──────────────────────────────────────────────

const wikiLinkMark = $mark('wiki_link', () => ({
  inclusive: false,
  parseDOM: [
    {
      tag: 'a.wiki-link',
      getAttrs: (/** @type {HTMLElement} */ _dom) => ({}),
    },
  ],
  toDOM: (/** @type {any} */ _mark) => [
    'a',
    { class: 'wiki-link' },
    0,
  ],
  parseMarkdown: {
    match: (/** @type {{ type: string }} */ node) => node.type === 'wiki_link',
    runner: (/** @type {any} */ state, /** @type {any} */ node, /** @type {any} */ markType) => {
      state.openMark(markType);
      state.next(node.children);
      state.closeMark(markType);
    },
  },
  toMarkdown: {
    match: (/** @type {{ type: { name: string } }} */ mark) => mark.type.name === 'wiki_link',
    runner: (/** @type {any} */ state, /** @type {any} */ _mark, /** @type {any} */ node) => {
      // Serialize as [[text]] — produce a text node with the delimiters
      const text = node.text || '';
      state.addNode('text', undefined, undefined, { value: `[[${text}]]` });
    },
  },
}));

// ── 3. Input rule ─────────────────────────────────────────────────────

const wikiLinkInputRule = $inputRule(ctx => {
  const markType = wikiLinkMark.type(ctx);
  return new InputRule(/\[\[([^\]]+)\]\]$/, (state, match, start, end) => {
    const target = match[1];
    return state.tr
      .replaceWith(start, end, state.schema.text(target, [markType.create()]))
      .removeStoredMark(markType);  // don't continue the mark after
  });
});

// ── Export ─────────────────────────────────────────────────────────────

export const wikiLinkPlugin = [remarkWikiLink, wikiLinkMark, wikiLinkInputRule].flat();
