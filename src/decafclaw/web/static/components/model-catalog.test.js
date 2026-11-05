import { beforeEach, describe, expect, it, vi } from 'vitest';

await import('./model-catalog.js');

const CATALOG = {
  default_model: 'local/llama3.2',
  default_pointer: 'local/llama3.2',
  picker_models: ['hand-model', 'local/llama3.2'],
  providers: {
    local: {
      discovered_at: '2026-11-05T12:00:00+00:00',
      error: '',
      models: [
        { key: 'local/llama3.2', id: 'llama3.2', enabled: true, chat: true, tools: true, context_window_size: 131072, owned_by: 'ollama', selectable: true },
        { key: 'local/qwen2.5', id: 'qwen2.5', enabled: false, chat: null, tools: null, context_window_size: null, owned_by: '', selectable: false },
      ],
    },
  },
};

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: new Headers({ 'Content-Type': 'application/json' }),
  json: async () => body,
  text: async () => JSON.stringify(body),
});

async function mountCatalog(initialCatalog = CATALOG) {
  const fetchMock = vi.fn(async (url, options) => {
    const u = new URL(url, 'http://test');
    const method = (options?.method || 'GET').toUpperCase();
    if (u.pathname === '/api/models/catalog' && method === 'GET') {
      return jsonResponse(initialCatalog);
    }
    if (u.pathname === '/api/models/catalog/discover') {
      return jsonResponse(initialCatalog);
    }
    if (u.pathname === '/api/models/catalog/model') {
      const body = JSON.parse(options.body);
      return jsonResponse({ ...initialCatalog, toggled: body });
    }
    if (u.pathname === '/api/models/catalog/default') {
      const body = JSON.parse(options.body);
      return jsonResponse({ ...initialCatalog, default_pointer: body.model });
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);

  const el = /** @type {any} */ (document.createElement('model-catalog'));
  document.body.append(el);
  await el.updateComplete;
  await new Promise(r => setTimeout(r, 10));
  await el.updateComplete;
  return { el, fetchMock };
}

describe('model-catalog component', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    vi.unstubbedGlobals?.();
    vi.unstubAllGlobals();
  });

  it('renders providers, model rows with capability meta, and the default badge', async () => {
    const { el } = await mountCatalog();

    expect(el.querySelector('.model-catalog-provider-name').textContent).toBe('local');
    const rows = el.querySelectorAll('.model-catalog-model');
    expect(rows.length).toBe(2);

    const llama = el.querySelector('[data-model-key="local/llama3.2"]');
    expect(llama.querySelector('input[type="checkbox"]').checked).toBe(true);
    expect(llama.querySelector('.model-catalog-default-badge')).toBeTruthy();
    expect(llama.querySelector('.model-catalog-model-meta').textContent).toContain('chat');
    expect(llama.querySelector('.model-catalog-model-meta').textContent).toContain('131,072 ctx');

    const qwen = el.querySelector('[data-model-key="local/qwen2.5"]');
    expect(qwen.querySelector('input[type="checkbox"]').checked).toBe(false);
    // Unknown capabilities say so instead of implying support.
    expect(qwen.querySelector('.model-catalog-model-meta').textContent).toContain('chat?');
    expect(qwen.querySelector('.model-catalog-model-meta').textContent).toContain('tools?');
  });

  it('toggling a model posts the enable flag', async () => {
    const { el, fetchMock } = await mountCatalog();

    const qwen = el.querySelector('[data-model-key="local/qwen2.5"]');
    qwen.querySelector('input[type="checkbox"]').click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const post = fetchMock.mock.calls.find(([url]) => url.endsWith('/api/models/catalog/model'));
    expect(post).toBeTruthy();
    expect(JSON.parse(post[1].body)).toEqual({ key: 'local/qwen2.5', enabled: true });
  });

  it('changing the default selector posts the pointer', async () => {
    const { el, fetchMock } = await mountCatalog();

    const select = el.querySelector('#catalog-default-select');
    select.value = 'hand-model';
    select.dispatchEvent(new Event('change'));
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const post = fetchMock.mock.calls.find(([url]) => url.endsWith('/api/models/catalog/default'));
    expect(post).toBeTruthy();
    expect(JSON.parse(post[1].body)).toEqual({ model: 'hand-model' });
  });

  it('default selector shows the raw pointer and a clear option', async () => {
    const { el } = await mountCatalog();

    const select = el.querySelector('#catalog-default-select');
    const optionValues = [...select.querySelectorAll('option')].map(o => o.value);
    expect(optionValues).toContain('');
    const selected = select.querySelector('option[selected]');
    expect(selected.value).toBe('local/llama3.2');
  });

  it('refresh button triggers discovery', async () => {
    const { el, fetchMock } = await mountCatalog();

    el.querySelector('.model-catalog-refresh-btn').click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/api/models/catalog/discover'))).toBe(true);
  });

  it('shows an error banner when the catalog request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ error: 'nope' }, 500)));
    const el = /** @type {any} */ (document.createElement('model-catalog'));
    document.body.append(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));
    await el.updateComplete;

    expect(el.querySelector('.model-catalog-error')).toBeTruthy();
  });
});
