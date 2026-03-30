/**
 * Milkdown entry point — re-exports what the wiki editor needs.
 * Bundled by esbuild into vendor/bundle/milkdown.js.
 */

// Core
export { Editor, rootCtx, defaultValueCtx, editorViewCtx } from '@milkdown/kit/core';

// Presets
export { commonmark } from '@milkdown/kit/preset/commonmark';
export { gfm } from '@milkdown/kit/preset/gfm';

// Plugins
export { history } from '@milkdown/kit/plugin/history';
export { listener, listenerCtx } from '@milkdown/kit/plugin/listener';
export { clipboard } from '@milkdown/kit/plugin/clipboard';

// Utils
export { $node, $mark, $remark, $inputRule, $view, callCommand, getMarkdown, replaceAll } from '@milkdown/kit/utils';

// ProseMirror
export { InputRule } from '@milkdown/kit/prose/inputrules';

// Commonmark commands
export {
  toggleStrongCommand,
  toggleEmphasisCommand,
  toggleInlineCodeCommand,
  createCodeBlockCommand,
  wrapInBulletListCommand,
  wrapInOrderedListCommand,
  wrapInBlockquoteCommand,
  wrapInHeadingCommand,
  insertHrCommand,
  toggleLinkCommand,
  insertImageCommand,
} from '@milkdown/kit/preset/commonmark';

// GFM commands
export { toggleStrikethroughCommand } from '@milkdown/kit/preset/gfm';
