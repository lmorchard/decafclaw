import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

await import('./schedule-page.js');

const SCHEDULE = {
  name: 'dream', schedule: '0 3 * * *', channel: '', model: '',
  enabled: true, pre_script: '', required_skills: [], allowed_tools: [],
  shell_patterns: [], email_recipients: [], unknown_keys: [],
  frontmatter_raw: '', source_tier: 'bundled', has_overlay: false,
  body: 'Body.', modified: 1,
};

describe('schedule-page', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: ['a', 'b'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    }));
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('renders the metadata panel and feeds it the model list', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel).toBeTruthy();
    expect(panel.models).toEqual(['a', 'b']);
  });

  it('PUTs the patch when the panel emits metadata-change', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockClear();
    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { model: 'b' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));

    const [url, init] = /** @type {any} */ (globalThis.fetch).mock.calls[0];
    expect(url).toBe('/api/schedules/dream');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ model: 'b' });
  });

  it('surfaces a 400 on the panel instead of only logging it', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockImplementation(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid cron expression: 'nope'" }),
    }));

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    expect(/** @type {any} */ (el.querySelector('schedule-metadata')).error)
      .toContain('invalid cron');
  });

  it('clears the previous save error when switching to a different schedule', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    // Induce a failed patch on schedule "dream".
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/schedules/dream')) {
        return { ok: false, status: 400, json: async () => ({ error: "invalid cron expression: 'nope'" }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    });

    let panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.error).toContain('invalid cron');

    // Switch to a different schedule; its own GET succeeds.
    /** @type {any} */ (globalThis.fetch).mockImplementation(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: ['a', 'b'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: { ...SCHEDULE, name: 'garden' } }) };
    });
    el.name = 'garden';
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel.error).toBe('');
  });
});
