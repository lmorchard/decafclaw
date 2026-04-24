/**
 * CodeMirror 6 entry point — re-exports what file-editor needs.
 * Bundled by esbuild into vendor/bundle/codemirror.js.
 */

// Core
export { EditorState } from '@codemirror/state';
export { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';

// Commands / keymaps
export { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';

// Language support
export {
  bracketMatching,
  defaultHighlightStyle,
  syntaxHighlighting,
  foldGutter,
  foldKeymap,
  indentOnInput,
} from '@codemirror/language';

// Search
export { searchKeymap, highlightSelectionMatches } from '@codemirror/search';

// Language packs
export { markdown } from '@codemirror/lang-markdown';
export { python } from '@codemirror/lang-python';
export { json } from '@codemirror/lang-json';
export { yaml } from '@codemirror/lang-yaml';
export { javascript } from '@codemirror/lang-javascript';
