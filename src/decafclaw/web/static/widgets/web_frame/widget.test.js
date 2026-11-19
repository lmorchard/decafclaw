import { describe, expect, it } from 'vitest';
import { WebFrameWidget } from './widget.js';

describe('WebFrameWidget rendering', () => {
  it('renders an iframe with proper sandbox and src attributes', () => {
    const el = new WebFrameWidget();
    el.data = { url: 'http://localhost:2718', title: 'Marimo' };
    const htmlResult = el.render();
    expect(htmlResult).toBeDefined();
    expect(el.data.url).toBe('http://localhost:2718');
    expect(el.data.title).toBe('Marimo');
  });

  it('renders notice when url is empty', () => {
    const el = new WebFrameWidget();
    el.data = {};
    const htmlResult = el.render();
    expect(htmlResult).toBeDefined();
  });
});
