import { EditorView } from 'codemirror';
import { afterEach, describe, expect, it } from 'vitest';

import { FileEditor, editorHighlightStyle } from './file-editor.js';
import { applyTheme } from '../lib/theme.js';

afterEach(() => {
  document.body.replaceChildren();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.removeAttribute('data-palette');
});

/**
 * @param {string} path
 * @param {string} content
 */
async function mountEditor(path, content) {
  const editor = new FileEditor();
  editor.path = path;
  editor.content = content;
  editor.kind = 'text';
  document.body.append(editor);
  await editor.updateComplete;
  const mount = editor.querySelector('.cm-editor');
  if (!(mount instanceof HTMLElement)) throw new Error('CodeMirror did not mount');
  const view = EditorView.findFromDOM(mount);
  return { editor, mount, view };
}

describe('editorHighlightStyle', () => {
  it('defines syntax highlighting rules backed by CSS variables', () => {
    expect(editorHighlightStyle).toBeDefined();
    expect(editorHighlightStyle.module).toBeDefined();
    const rules = editorHighlightStyle.module.rules.join('\n');
    expect(rules).toContain('var(--cm-keyword');
    expect(rules).toContain('var(--cm-string');
    expect(rules).toContain('var(--cm-number');
    expect(rules).toContain('var(--cm-comment');
    expect(rules).toContain('var(--cm-variable');
    expect(rules).toContain('var(--cm-type');
  });
});

describe('FileEditor theming and mounting', () => {
  it('mounts CodeMirror with gutters and line numbers', async () => {
    const { mount, view } = await mountEditor('sample.txt', 'line 1\nline 2');
    expect(view).not.toBeNull();
    const gutters = mount.querySelector('.cm-gutters');
    expect(gutters).toBeInstanceOf(HTMLElement);
    const lines = mount.querySelectorAll('.cm-line');
    expect(lines.length).toBe(2);
  });

  it('tokenizes and applies syntax highlighting spans for python code', async () => {
    const code = 'def compute():\n    return 42 # answer\n';
    const { mount } = await mountEditor('test.py', code);
    const spans = mount.querySelectorAll('.cm-line span');
    expect(spans.length).toBeGreaterThan(0);
    // Find keyword span ("def" or "return")
    const keywordSpan = Array.from(spans).find((s) => s.textContent === 'def' || s.textContent === 'return');
    expect(keywordSpan).toBeDefined();
    expect(keywordSpan?.className).toMatch(/^ͼ/);
  });

  it('tokenizes and applies syntax highlighting spans for javascript code', async () => {
    const code = 'const answer = 42;\n// comment\n';
    const { mount } = await mountEditor('test.js', code);
    const spans = mount.querySelectorAll('.cm-line span');
    expect(spans.length).toBeGreaterThan(0);
    const keywordSpan = Array.from(spans).find((s) => s.textContent === 'const');
    expect(keywordSpan).toBeDefined();
    expect(keywordSpan?.className).toMatch(/^ͼ/);
  });

  it('updates html attributes dynamically when theme changes without remounting', async () => {
    const { mount } = await mountEditor('theme-test.md', '# Heading\nSome content');
    expect(mount.isConnected).toBe(true);

    applyTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-palette')).toBeNull();

    applyTheme('dracula');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-palette')).toBe('dracula');

    applyTheme('solarized-light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.getAttribute('data-palette')).toBe('solarized-light');

    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.getAttribute('data-palette')).toBeNull();

    // Editor remains mounted throughout theme transitions
    expect(mount.isConnected).toBe(true);
  });
});
