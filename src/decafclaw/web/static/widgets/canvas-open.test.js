import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setActiveConv } from '/static/lib/canvas-state.js';
import { CodeBlockWidget } from './code_block/widget.js';
import { DiffViewWidget } from './diff_view/widget.js';
import { JsonViewWidget } from './json_view/widget.js';
import { MarkdownDocumentWidget } from './markdown_document/widget.js';

describe('widget open-in-canvas callers', () => {
  beforeEach(async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).endsWith('/api/canvas/conv-1')) {
        return new Response(JSON.stringify({
          schema_version: 1, active_tab: null, next_tab_id: 1, tabs: [],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('', { status: 200 });
    }));
    await setActiveConv('conv-1');
  });

  it.each([
    ['code_block', CodeBlockWidget, { code: 'print(1)', language: 'python', filename: 'demo.py' }, 'demo.py'],
    ['diff_view', DiffViewWidget, { before: 'a', after: 'b', filename: 'demo.txt' }, 'demo.txt'],
    ['json_view', JsonViewWidget, { nested: [1, true, null] }, 'JSON View'],
    ['markdown_document', MarkdownDocumentWidget, { content: '# Typed doc\n\nBody' }, 'Typed doc'],
  ])('sends %s data through the generated new-tab operation', async (
    widgetType, WidgetClass, data, label,
  ) => {
    const widget = new WidgetClass();
    widget.data = data;
    await widget._openInCanvas();

    const calls = globalThis.fetch.mock.calls;
    const post = calls.find(c => String(c[0]).includes('/new_tab'));
    expect(post[0]).toBe('/api/canvas/conv-1/new_tab');
    expect(post[1]).toEqual(expect.objectContaining({
      method: 'POST', credentials: 'same-origin',
    }));
    const body = JSON.parse(post[1].body);
    expect(body.widget_type).toBe(widgetType);
    expect(body.label).toBe(label);
    if (widgetType === 'diff_view') expect(body.data.view).toBe('unified');
    else expect(body.data).toEqual(data);
  });
});
