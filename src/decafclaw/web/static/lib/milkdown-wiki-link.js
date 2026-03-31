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
    const inner = match[1];
    const pipeIdx = inner.indexOf('|');
    const target = pipeIdx >= 0 ? inner.slice(0, pipeIdx).trim() : inner;
    const display = pipeIdx >= 0 ? inner.slice(pipeIdx + 1).trim() : inner;
    parts.push({
      type: 'wiki_link',
      children: [{ type: 'text', value: display }],
      data: { target, display },
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
  attrs: {
    target: { default: null },
  },
  parseDOM: [
    {
      tag: 'a.wiki-link',
      getAttrs: (/** @type {HTMLElement} */ dom) => {
        const t = dom.getAttribute('data-wiki-page');
        return t ? { target: t } : {};
      },
    },
  ],
  toDOM: (/** @type {any} */ mark) => {
    /** @type {Record<string, string>} */
    const attrs = { class: 'wiki-link' };
    if (mark.attrs.target) attrs['data-wiki-page'] = mark.attrs.target;
    return ['a', attrs, 0];
  },
  parseMarkdown: {
    match: (/** @type {{ type: string }} */ node) => node.type === 'wiki_link',
    runner: (/** @type {any} */ state, /** @type {any} */ node, /** @type {any} */ markType) => {
      const target = node.data?.target || '';
      const display = node.children?.[0]?.value || target;
      const attrs = target && target !== display ? { target } : {};
      state.openMark(markType, attrs);
      state.next(node.children);
      state.closeMark(markType);
    },
  },
  toMarkdown: {
    match: (/** @type {{ type: { name: string } }} */ mark) => mark.type.name === 'wiki_link',
    runner: (/** @type {any} */ state, /** @type {any} */ mark, /** @type {any} */ node) => {
      const text = node.text || '';
      const target = mark.attrs.target || '';
      const wiki = target && target !== text ? `[[${target}|${text}]]` : `[[${text}]]`;
      state.addNode('text', undefined, undefined, { value: wiki });
      return true; // prevent default text handler from also emitting the text
    },
  },
}));

// ── 3. Input rule ─────────────────────────────────────────────────────

const wikiLinkInputRule = $inputRule(ctx => {
  const markType = wikiLinkMark.type(ctx);
  return new InputRule(/\[\[([^\]]+)\]\]$/, (state, match, start, end) => {
    const inner = match[1];
    const pipeIdx = inner.indexOf('|');
    const target = pipeIdx >= 0 ? inner.slice(0, pipeIdx).trim() : inner;
    const display = pipeIdx >= 0 ? inner.slice(pipeIdx + 1).trim() : inner;
    return state.tr
      .replaceWith(start, end, state.schema.text(display, [markType.create(target !== display ? { target } : {})]))
      .removeStoredMark(markType);  // don't continue the mark after
  });
});

// ── Export ─────────────────────────────────────────────────────────────

export const wikiLinkPlugin = [remarkWikiLink, wikiLinkMark, wikiLinkInputRule].flat();
