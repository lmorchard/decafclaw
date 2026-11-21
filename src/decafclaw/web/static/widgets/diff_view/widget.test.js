import { afterEach, describe, expect, it } from 'vitest';

import { DiffViewWidget } from './widget.js';

const LONG_BEFORE = Array.from({ length: 600 }, (_, i) => `line ${i + 1}`).join('\n') + '\n';
const LONG_AFTER = LONG_BEFORE.replace('line 500', 'CHANGED 500');

const ALL_CHANGED_BEFORE = Array.from({ length: 1200 }, (_, i) => `old ${i}`).join('\n');
const ALL_CHANGED_AFTER = Array.from({ length: 1200 }, (_, i) => `new ${i}`).join('\n');

function mount({ mode = 'inline', data }) {
  const el = new DiffViewWidget();
  el.mode = mode;
  el.data = data;
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('diff_view rendering', () => {
  it('renders one widget card and keeps the changed line for a long file', async () => {
    const el = mount({ data: { before: LONG_BEFORE, after: LONG_AFTER, filename: 'long.md' } });
    await el.updateComplete;

    expect(el.querySelectorAll('.diff-view-widget')).toHaveLength(1);
    expect(el.querySelectorAll('.code-block-header')).toHaveLength(1);
    expect(el.querySelectorAll('.code-block-actions')).toHaveLength(1);

    // The edit is 500 lines into the file. Collapsing unchanged runs keeps it
    // inside the inline preview instead of dumping 500 unchanged lines.
    expect(el.textContent).toContain('CHANGED 500');
    expect(el.querySelectorAll('.diff-line.added')).toHaveLength(1);
    expect(el.querySelectorAll('.diff-line.removed')).toHaveLength(1);
    expect(el.querySelectorAll('.diff-line.gap').length).toBeGreaterThan(0);
  });

  it('renders the first 500 rows with a truncation notice inline', async () => {
    const el = mount({ data: { before: ALL_CHANGED_BEFORE, after: ALL_CHANGED_AFTER } });
    await el.updateComplete;

    expect(el.querySelectorAll('.diff-line')).toHaveLength(500);
    expect(el.textContent).toContain('of 2400 diff lines');
    expect(el.textContent).toContain('Open in Canvas for the full diff');
  });

  it('renders every row in canvas mode without the inline cap', async () => {
    const el = mount({
      mode: 'canvas',
      data: { before: ALL_CHANGED_BEFORE, after: ALL_CHANGED_AFTER },
    });
    await el.updateComplete;

    expect(el.querySelectorAll('.diff-line')).toHaveLength(2400);
    expect(el.textContent).not.toContain('Showing first');
    expect(el.querySelectorAll('.diff-view-widget')).toHaveLength(1);
  });
});
