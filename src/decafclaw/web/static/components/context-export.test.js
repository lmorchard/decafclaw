import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import './context-inspector.js';
import './copy-conversation-menu.js';
import { copyToClipboard } from '../lib/utils.js';
import { showToast } from '../lib/toast.js';
vi.mock('../lib/utils.js', () => ({ copyToClipboard: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));
let fetchMock;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
});
async function inspector(response) {
  fetchMock.mockResolvedValue(response);
  const el = document.createElement('context-inspector');
  el.convId = 'id ?#%é';
  el.open = true;
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._loading).toBe(false));
  await el.updateComplete;
  return el;
}
it('renders typed source details, cache statistics, and candidates', async () => {
  const el = await inspector(json({
    total_tokens_estimated: 120, total_tokens_actual: 100, cached_prompt_tokens: 50,
    cache_hit_rate: 0.5, context_window_size: 1000, compaction_threshold: 800,
    sources: [
      { source: 'memory', tokens_estimated: 60, items_included: 1, items_truncated: 2,
        details: { top_score: 0.9, min_score: 0.2, budget_source: 'dynamic' } },
      { source: 'tools', details: { deferred_mode: true } },
      { source: 'preempt_matches', details: { matches: [{ name: 'vault_read', score: 3 }], input_tokens: ['vault'] } },
    ],
    memory_candidates: [{ file_path: 'agent/pages/Test', source_type: 'page', composite_score: 0.8,
      tokens_estimated: 20, similarity: 0.7, recency: 0.6, importance: 0.5, linked_from: 'agent/pages/Origin' }],
  }));
  expect(fetchMock).toHaveBeenCalledWith('/api/conversations/id%20%3F%23%25%C3%A9/context',
    expect.objectContaining({ method: 'GET', credentials: 'same-origin' }));
  for (const text of ['50 (50%)', 'dynamic budget', 'deferred mode', 'vault_read(3)', 'Test', 'Origin', '+2 dropped']) {
    expect(el.textContent).toContain(text);
  }
  expect(el.querySelectorAll('.waffle-cell').length).toBeGreaterThan(0);
});
it.each([{}, { sources: [], memory_candidates: [] }])('preserves absent diagnostics: %j', async (data) => {
  const el = await inspector(json(data));
  expect(el.textContent).toContain('Estimated');
  expect(el.textContent).toContain('—');
  expect(el.querySelector('.source-table')).toBeNull();
});
it.each([404, 401, 500])('preserves diagnostics HTTP %s', async (status) => {
  const el = await inspector(new Response('not JSON', { status }));
  expect(el.textContent).toContain(status === 404 ? 'No context data yet' : `Error: HTTP ${status}`);
});
it('shows loading and retains network failures', async () => {
  let reject;
  fetchMock.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
  const el = document.createElement('context-inspector');
  el.convId = 'test'; el.open = true; document.body.append(el);
  await vi.waitFor(() => expect(el.textContent).toContain('Loading...'));
  reject(new Error('offline'));
  await vi.waitFor(() => expect(el.textContent).toContain('Error: offline'));
});
it('retains JSON parse failures', async () => {
  const el = await inspector(new Response('broken JSON'));
  expect(el._error).not.toBe('');
  expect(el._data).toBeNull();
});
it.each(['markdown', 'jsonl'])('copies exact %s text through generated client', async (format) => {
  const body = format === 'jsonl' ? '{"role":"user"}\n{"role":"assistant"}\n' : '# Title\n\nHello\n';
  fetchMock.mockResolvedValue(new Response(body, { headers: { 'Content-Type': format === 'jsonl' ? 'application/x-ndjson' : 'text/markdown' } }));
  const el = document.createElement('copy-conversation-menu'); el.convId = 'id ?#%é';
  await el._copy(format);
  expect(fetchMock).toHaveBeenCalledWith(`/api/conversations/id%20%3F%23%25%C3%A9/export?format=${format}`,
    expect.objectContaining({ method: 'GET', credentials: 'same-origin' }));
  expect(copyToClipboard).toHaveBeenCalledWith(body);
  expect(showToast).toHaveBeenCalledWith(`Copied as ${format}`);
});
it.each([400, 401, 404, 500])('preserves export HTTP %s messages', async (status) => {
  fetchMock.mockResolvedValue(new Response('not JSON', { status }));
  const el = document.createElement('copy-conversation-menu'); el.convId = 'id';
  await el._copy('jsonl');
  expect(copyToClipboard).not.toHaveBeenCalled();
  expect(showToast).toHaveBeenCalledWith(`Copy failed: server returned ${status}`);
});
it.each(['network', 'clipboard', 'body'])('reports %s failures', async (failure) => {
  const response = new Response('text');
  if (failure === 'body') vi.spyOn(response, 'text').mockRejectedValue(new Error('body failed'));
  fetchMock.mockResolvedValue(response);
  if (failure === 'network') fetchMock.mockRejectedValue(new Error('network failed'));
  if (failure === 'clipboard') copyToClipboard.mockRejectedValueOnce(new Error('clipboard failed'));
  const el = document.createElement('copy-conversation-menu'); el.convId = 'id';
  await el._copy('markdown');
  expect(showToast).toHaveBeenCalledWith(`Copy failed: ${failure} failed`);
});

it('renders raw view and copies request JSON', async () => {
  const rawData = {
    model: 'test-model-4',
    messages: [{ role: 'user', content: 'hello' }],
    tools: [{ type: 'function', function: { name: 'test_tool' } }],
  };
  fetchMock.mockResolvedValue(json(rawData));
  const el = document.createElement('context-inspector');
  el.convId = 'id ?#%é';
  el.open = true;
  el._tab = 'raw';
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._rawLoading).toBe(false));
  await el.updateComplete;

  expect(fetchMock).toHaveBeenCalledWith('/api/conversations/id%20%3F%23%25%C3%A9/context/raw',
    expect.objectContaining({ method: 'GET', credentials: 'same-origin' }));
  expect(el.textContent).toContain('test-model-4');
  expect(el.textContent).toContain('Messages (1)');
  expect(el.textContent).toContain('Tools (1)');
  expect(el.textContent).toContain('"hello"');
  expect(el.textContent).toContain('test_tool');

  const copyBtn = el.querySelector('.copy-raw-btn');
  expect(copyBtn).not.toBeNull();
  copyBtn.click();
  await vi.waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(JSON.stringify(rawData, null, 2)));
  expect(showToast).toHaveBeenCalledWith('Copied raw context');
});

it('shows empty message when raw context is 404', async () => {
  fetchMock.mockResolvedValue(new Response('not found', { status: 404 }));
  const el = document.createElement('context-inspector');
  el.convId = 'id';
  el.open = true;
  el._tab = 'raw';
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._rawLoading).toBe(false));
  await el.updateComplete;

  expect(el.textContent).toContain('No raw request data yet');
});

it('shows error message when raw context returns 500', async () => {
  fetchMock.mockResolvedValue(new Response('server error', { status: 500 }));
  const el = document.createElement('context-inspector');
  el.convId = 'id';
  el.open = true;
  el._tab = 'raw';
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._rawLoading).toBe(false));
  await el.updateComplete;

  expect(el.textContent).toContain('Error: HTTP 500');
});
