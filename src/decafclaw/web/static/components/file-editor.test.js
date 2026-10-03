import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { EditorView } from 'codemirror';
import { beforeAll, afterEach, describe, expect, it } from 'vitest';

import { FileEditor, editorHighlightStyle } from './file-editor.js';
import { applyTheme } from '../lib/theme.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Calculate WCAG relative luminance of a #rrggbb color. */
function luminance(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const a = [r, g, b].map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
}

/** Calculate contrast ratio between two hex colors. */
function contrast(hex1, hex2) {
  const l1 = luminance(hex1);
  const l2 = luminance(hex2);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

/** Alpha-composite an rgba color over a background hex color. */
function composite(bgHex, rgbaStr) {
  const [br, bg, bb] = [parseInt(bgHex.slice(1, 3), 16), parseInt(bgHex.slice(3, 5), 16), parseInt(bgHex.slice(5, 7), 16)];
  const m = rgbaStr.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
  if (!m) return bgHex;
  const [fr, fg, fb, a] = [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10), m[4] ? parseFloat(m[4]) : 1];
  const r = Math.round(a * fr + (1 - a) * br);
  const g = Math.round(a * fg + (1 - a) * bg);
  const b = Math.round(a * fb + (1 - a) * bb);
  return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
}

let styleEl;

beforeAll(() => {
  // Load real application stylesheets in production order to test cascade and variables
  const stylesDir = path.resolve(__dirname, '../styles');
  const draculaCss = fs.readFileSync(path.join(stylesDir, 'palettes/dracula.css'), 'utf8');
  const solarizedCss = fs.readFileSync(path.join(stylesDir, 'palettes/solarized-light.css'), 'utf8');
  const wikiCss = fs.readFileSync(path.join(stylesDir, 'wiki-editor.css'), 'utf8');

  styleEl = document.createElement('style');
  styleEl.textContent = [draculaCss, solarizedCss, wikiCss].join('\n\n');
  document.head.appendChild(styleEl);
});

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
  // Ensure app stylesheet sits after style-mod injected styles, mirroring browser link order
  if (styleEl && document.head.contains(styleEl)) {
    document.head.appendChild(styleEl);
  }
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

  it('updates CSS variables and satisfies WCAG AA contrast across themes', async () => {
    const { mount } = await mountEditor('theme-test.md', '# Heading\nSome content');
    expect(mount.isConnected).toBe(true);

    const getProp = (name) => getComputedStyle(mount).getPropertyValue(name).trim();

    // 1. Dark theme
    applyTheme('dark');
    expect(getProp('--cm-keyword')).toBe('#c678dd');
    expect(getProp('--cm-comment')).toBe('#8590a4');
    expect(getProp('--cm-background')).toBe('#13171f');
    expect(getProp('--cm-foreground')).toBe('#c2c7d0');
    expect(getProp('--cm-gutter-bg')).toBe('#1b202b');
    expect(getProp('--cm-gutter-color')).toBe('#8590a4');
    expect(getProp('--cm-gutter-active-color')).toBe('#c2c7d0');
    expect(getProp('--cm-match-bg')).toBe('#44475a');
    expect(getProp('--cm-match-color')).toBe('#f8f8f2');
    expect(contrast('#13171f', getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#13171f', getProp('--cm-cursor-color'))).toBeGreaterThanOrEqual(3.0);
    expect(contrast('#1b202b', getProp('--cm-gutter-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#1b202b', getProp('--cm-gutter-active-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-match-bg'), getProp('--cm-match-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#13171f', getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#13171f', getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);
    const darkSelection = composite('#13171f', getProp('--cm-selection-bg'));
    expect(contrast(darkSelection, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(darkSelection, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(darkSelection, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);

    // 2. Dracula palette (verifying Dracula overrides base dark in cascade)
    applyTheme('dracula');
    expect(getProp('--cm-keyword')).toBe('#ff79c6');
    expect(getProp('--cm-comment')).toBe('#95a7db');
    expect(getProp('--cm-background')).toBe('#282a36');
    expect(getProp('--cm-foreground')).toBe('#f8f8f2');
    expect(getProp('--cm-gutter-bg')).toBe('#2f313f');
    expect(getProp('--cm-gutter-color')).toBe('#95a7db');
    expect(getProp('--cm-gutter-active-color')).toBe('#f8f8f2');
    expect(getProp('--cm-match-bg')).toBe('#44475a');
    expect(getProp('--cm-match-color')).toBe('#f8f8f2');
    const draculaBg = getProp('--cm-background');
    expect(contrast(draculaBg, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(draculaBg, getProp('--cm-cursor-color'))).toBeGreaterThanOrEqual(3.0);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-active-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-match-bg'), getProp('--cm-match-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(draculaBg, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(draculaBg, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);
    const draculaSelection = composite(draculaBg, getProp('--cm-selection-bg'));
    expect(contrast(draculaSelection, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(draculaSelection, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(draculaSelection, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);

    // 3. Solarized Light palette
    applyTheme('solarized-light');
    expect(getProp('--cm-keyword')).toBe('#5b6c00');
    expect(getProp('--cm-comment')).toBe('#4f636a');
    expect(getProp('--cm-meta')).toBe('#4f636a');
    expect(getProp('--cm-background')).toBe('#fdf6e3');
    expect(getProp('--cm-foreground')).toBe('#073642');
    expect(getProp('--cm-gutter-bg')).toBe('#eee8d5');
    expect(getProp('--cm-gutter-color')).toBe('#4f636a');
    expect(getProp('--cm-gutter-active-color')).toBe('#073642');
    expect(getProp('--cm-active-line-bg')).toBe('#eee8d5');
    expect(getProp('--cm-match-bg')).toBe('#eee8d5');
    expect(getProp('--cm-match-color')).toBe('#073642');
    const solarizedBg = getProp('--cm-background');
    const solarizedActiveBg = getProp('--cm-active-line-bg');
    expect(contrast(solarizedBg, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedBg, getProp('--cm-cursor-color'))).toBeGreaterThanOrEqual(3.0);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-active-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-match-bg'), getProp('--cm-match-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedBg, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedBg, getProp('--cm-atom'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedBg, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedActiveBg, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedActiveBg, getProp('--cm-meta'))).toBeGreaterThanOrEqual(4.5);
    const solarizedSelection = composite(solarizedBg, getProp('--cm-selection-bg'));
    expect(contrast(solarizedSelection, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedSelection, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(solarizedSelection, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);

    // 4. Base Light theme
    applyTheme('light');
    expect(getProp('--cm-keyword')).toBe('#a626a4');
    expect(getProp('--cm-comment')).toBe('#5c6370');
    expect(getProp('--cm-background')).toBe('#ffffff');
    expect(getProp('--cm-foreground')).toBe('#13171f');
    expect(getProp('--cm-gutter-bg')).toBe('#f6f8fa');
    expect(getProp('--cm-gutter-color')).toBe('#5c6370');
    expect(getProp('--cm-gutter-active-color')).toBe('#13171f');
    expect(getProp('--cm-match-bg')).toBe('#ffeeba');
    expect(getProp('--cm-match-color')).toBe('#13171f');
    const lightBg = getProp('--cm-background');
    expect(contrast(lightBg, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightBg, getProp('--cm-cursor-color'))).toBeGreaterThanOrEqual(3.0);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-gutter-bg'), getProp('--cm-gutter-active-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(getProp('--cm-match-bg'), getProp('--cm-match-color'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightBg, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightBg, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightBg, getProp('--cm-string'))).toBeGreaterThanOrEqual(4.5);
    const lightSelection = composite(lightBg, getProp('--cm-selection-bg'));
    expect(contrast(lightSelection, getProp('--cm-foreground'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightSelection, getProp('--cm-comment'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightSelection, getProp('--cm-keyword'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(lightSelection, getProp('--cm-string'))).toBeGreaterThanOrEqual(4.5);

    expect(mount.isConnected).toBe(true);
  });

  it('verifies mounted editor chrome elements receive theme styles', async () => {
    const { mount, view } = await mountEditor('chrome-test.py', 'def foo():\n    return 1');
    view.focus();

    const gutters = mount.querySelector('.cm-gutters');
    expect(gutters).toBeInstanceOf(HTMLElement);
    const gutterEl = mount.querySelector('.cm-gutterElement');
    expect(gutterEl).toBeInstanceOf(HTMLElement);
    const activeLine = mount.querySelector('.cm-activeLine');
    expect(activeLine).toBeInstanceOf(HTMLElement);
    const activeLineGutter = mount.querySelector('.cm-activeLineGutter');
    expect(activeLineGutter).toBeInstanceOf(HTMLElement);
    const content = mount.querySelector('.cm-content');
    expect(content).toBeInstanceOf(HTMLElement);

    // Directly verify cursor element styling inside .cm-editor
    const cursor = document.createElement('div');
    cursor.className = 'cm-cursor';
    mount.appendChild(cursor);
    expect(getComputedStyle(cursor).borderLeftColor).toContain('--cm-cursor-color');

    // Directly verify search match element styling inside .cm-editor
    const selectionMatch = document.createElement('span');
    selectionMatch.className = 'cm-selectionMatch';
    mount.appendChild(selectionMatch);
    expect(getComputedStyle(selectionMatch).backgroundColor).toContain('--cm-match-bg');
    expect(getComputedStyle(selectionMatch).color).toContain('--cm-match-color');

    expect(getComputedStyle(gutters).backgroundColor).toContain('--cm-gutter-bg');
    expect(getComputedStyle(gutterEl).color).toContain('--cm-gutter-color');
    expect(getComputedStyle(activeLine).backgroundColor).toContain('--cm-active-line-bg');
    expect(getComputedStyle(activeLineGutter).color).toContain('--cm-gutter-active-color');

    // Focused state preserves themed outline for keyboard accessibility
    mount.classList.add('cm-focused');
    expect(getComputedStyle(mount).outline).toContain('--pico-primary');
  });
});
