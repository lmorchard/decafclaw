import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './shell-approval-panel.js';
import { reset } from '../lib/presets-store.js';

const APPROVAL_RESPONSE = {
  conv_id: 'c1',
  status: 'idle',
  conversation_rules: ['Auto-approve make test in this repo'],
  presets: [
    { name: 'developer', source: 'builtin', rules: ['Auto-approve pytest'], activated_via: ['mode dev'] },
    { name: 'custom_work', source: 'custom', rules: ['Auto-approve git status'] },
  ],
  config_guidance: 'Always ask before destructive commands.',
  cached_patterns: ['Bash(make test:*)'],
};

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

async function mountPanel() {
  const fetchMock = vi.fn(async (url, options) => {
    const u = new URL(url, 'http://test');
    const method = (options?.method || 'GET').toUpperCase();
    if (u.pathname === '/api/conversations/c1/shell-approval') {
      return jsonResponse(APPROVAL_RESPONSE);
    }
    if (u.pathname === '/api/conversations/c1/shell-approval/rules') {
      if (method === 'PUT') {
        const body = JSON.parse(options.body);
        return jsonResponse({ ok: true });
      }
      if (method === 'DELETE') {
        return jsonResponse({ ok: true });
      }
    }
    if (u.pathname === '/api/conversations/c1/shell-approval/patterns') {
      if (method === 'DELETE') {
        return jsonResponse({ ok: true });
      }
    }
    if (u.pathname === '/api/conversations/c1/shell-approval/promote') {
      if (method === 'POST') {
        const body = JSON.parse(options.body);
        return jsonResponse({ ok: true });
      }
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  const el = /** @type {any} */ (document.createElement('shell-approval-panel'));
  el.convId = 'c1';
  el.open = true;
  document.body.append(el);
  await el.updateComplete;
  await vi.waitFor(() => expect(el._loading).toBe(false));
  await el.updateComplete;
  return { el, fetchMock };
}

describe('shell-approval-panel component', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    reset();
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the per-conversation approval view from the GET response', async () => {
    const { el, fetchMock } = await mountPanel();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/conversations/c1/shell-approval',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    );
    const text = el._panelMount.textContent;
    expect(text).toContain('Shell Auto-Approval');
    expect(text).toContain('Auto-approve make test in this repo');
    expect(text).toContain('Always ask before destructive commands.');
    expect(text).toContain('Bash(make test:*)');
    expect(text).toContain('developer');
    expect(text).toContain('Auto-approve pytest');
  });

  it('shows the empty conversation-rules state when none are set', async () => {
    const fetchMock = vi.fn(async (url, options) => {
      const u = new URL(url, 'http://test');
      if (u.pathname === '/api/conversations/c1/shell-approval') {
        return jsonResponse({ conv_id: 'c1', status: 'idle', conversation_rules: [], presets: [], config_guidance: '', cached_patterns: [] });
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);
    const el = document.createElement('shell-approval-panel');
    el.convId = 'c1';
    el.open = true;
    document.body.append(el);
    await el.updateComplete;
    await vi.waitFor(() => expect(el._loading).toBe(false));
    await el.updateComplete;
    expect(el._panelMount.textContent).toContain('No conversation rules yet');
  });

  it('adds a conversation rule via PUT and refreshes the view', async () => {
    const { el, fetchMock } = await mountPanel();
    const input = el._panelMount.querySelector('.sap-rule-input');
    input.value = 'Auto-approve cargo test';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;
    const addBtn = el._panelMount.querySelector('.sap-add-btn');
    expect(addBtn.disabled).toBe(false);
    addBtn.click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const putCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/rules' && opt?.method === 'PUT',
    );
    expect(putCall).toBeTruthy();
    expect(JSON.parse(putCall[1].body)).toEqual({ rule: 'Auto-approve cargo test' });
    // The add clears the input + re-fetches the (now longer) conversation rules.
    expect(el._newRuleText).toBe('');
  });

  it('deletes a conversation rule via DELETE', async () => {
    const { el, fetchMock } = await mountPanel();
    const delBtn = el._panelMount.querySelector('.sap-delete-btn');
    delBtn.click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const deleteCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/rules' && opt?.method === 'DELETE',
    );
    expect(deleteCall).toBeTruthy();
    expect(JSON.parse(deleteCall[1].body)).toEqual({ rule: 'Auto-approve make test in this repo' });
  });

  it('revokes a cached approval pattern via DELETE patterns', async () => {
    const { el, fetchMock } = await mountPanel();
    const delBtn = el._panelMount.querySelector('.sap-delete-pattern-btn');
    delBtn.click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const deleteCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/patterns' && opt?.method === 'DELETE',
    );
    expect(deleteCall).toBeTruthy();
    expect(JSON.parse(deleteCall[1].body)).toEqual({ pattern: 'Bash(make test:*)' });
  });

  it('promotes a conversation rule into a custom preset via POST promote', async () => {
    const { el, fetchMock } = await mountPanel();
    const promoteBtn = el._panelMount.querySelector('.sap-promote-btn');
    promoteBtn.click();
    await el.updateComplete;
    const sections = el._panelMount.querySelectorAll('.sap-section');
    const promoteSection = Array.from(sections).find(s => s.textContent.includes('Promote Rule to Custom Preset'));
    expect(promoteSection).toBeTruthy();

    const presetInput = el._panelMount.querySelector('.sap-new-preset-input');
    presetInput.value = 'backend_tests';
    presetInput.dispatchEvent(new Event('input'));
    await el.updateComplete;
    const promoteSubmit = el._panelMount.querySelector('.sap-promote-btn');
    promoteSubmit.click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const promoteCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/promote' && opt?.method === 'POST',
    );
    expect(promoteCall).toBeTruthy();
    expect(JSON.parse(promoteCall[1].body)).toEqual({
      rule: 'Auto-approve make test in this repo',
      preset: 'backend_tests',
    });
  });
});
