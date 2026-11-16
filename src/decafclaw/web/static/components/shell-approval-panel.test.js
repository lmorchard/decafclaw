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
  // Server-side preset list; promote appends to it like ShellPresetStore does,
  // so a test can tell a post-promote store reload from a stale cache.
  /** @type {Array<{name: string, source: string, rules: string[]}>} */
  const serverPresets = [{ name: 'custom_work', source: 'custom', rules: ['Auto-approve git status'] }];
  // Server-side conversation rules; the replace endpoint rewrites them so a
  // post-save refetch shows what was actually saved (#1267).
  const serverRules = [...APPROVAL_RESPONSE.conversation_rules];
  const fetchMock = vi.fn(async (url, options) => {
    const u = new URL(url, 'http://test');
    const method = (options?.method || 'GET').toUpperCase();
    if (u.pathname === '/api/shell-approval/presets') {
      return jsonResponse(serverPresets);
    }
    if (u.pathname === '/api/conversations/c1/shell-approval') {
      return jsonResponse({ ...APPROVAL_RESPONSE, conversation_rules: [...serverRules] });
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
    if (u.pathname === '/api/conversations/c1/shell-approval/rules/replace') {
      if (method === 'PUT') {
        const body = JSON.parse(options.body);
        serverRules.length = 0;
        serverRules.push(...body.rules);
        return jsonResponse({ ok: true, conv_id: 'c1', conversation_rules: [...serverRules] });
      }
    }
    if (u.pathname === '/api/conversations/c1/shell-approval/condense') {
      if (method === 'POST') {
        return jsonResponse({ ok: true, conv_id: 'c1', rules: ['Auto-approve any test command in this repo'] });
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
        const existing = serverPresets.find(p => p.name === body.preset);
        if (existing) {
          if (!existing.rules.includes(body.rule)) existing.rules.push(body.rule);
        } else {
          serverPresets.push({ name: body.preset, source: 'custom', rules: [body.rule] });
        }
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

  it('shows the conversation rule being copied in the promote form', async () => {
    const { el } = await mountPanel();
    el._panelMount.querySelector('.sap-rule .sap-promote-btn').click();
    await el.updateComplete;

    const ruleEl = el._panelMount.querySelector('.sap-promote-rule');
    expect(ruleEl?.textContent).toBe('Auto-approve make test in this repo');
    // The picker offers existing custom presets without another view loading the store.
    await vi.waitFor(() => expect(el._presets.map(p => p.name)).toContain('custom_work'));
    await el.updateComplete;
    const options = Array.from(el._panelMount.querySelectorAll('.sap-preset-select option')).map(o => o.value);
    expect(options).toContain('custom_work');
  });

  it('reloads the preset store after promote and confirms the new preset', async () => {
    const { el } = await mountPanel();
    el._panelMount.querySelector('.sap-rule .sap-promote-btn').click();
    await el.updateComplete;
    const nameInput = el._panelMount.querySelector('.sap-new-preset-input');
    nameInput.value = 'backend_tests';
    nameInput.dispatchEvent(new Event('input'));
    await el.updateComplete;
    el._panelMount.querySelector('.sap-promote-row .sap-promote-btn').click();

    // Success is visible and names both sides of the copy.
    await vi.waitFor(() => expect(el._promoteNotice).toContain('backend_tests'));
    await el.updateComplete;
    const banner = el._panelMount.querySelector('.sap-success-banner');
    expect(banner?.textContent).toContain('Auto-approve make test in this repo');
    expect(banner?.textContent).toContain('backend_tests');
    // The form closes on success, so the banner is the only confirmation.
    expect(el._panelMount.querySelector('.sap-new-preset-input')).toBeNull();

    // The shared preset store reloaded, so the picker can offer it right away.
    await vi.waitFor(() => expect(el._presets.map(p => p.name)).toContain('backend_tests'));
    el._panelMount.querySelector('.sap-rule .sap-promote-btn').click();
    await el.updateComplete;
    const options = Array.from(el._panelMount.querySelectorAll('.sap-preset-select option')).map(o => o.value);
    expect(options).toContain('backend_tests');
  });

  it('enters bulk-edit mode with a draft of the current rules', async () => {
    const { el } = await mountPanel();
    const editBtn = el._panelMount.querySelector('.sap-edit-btn');
    expect(editBtn).toBeTruthy();
    editBtn.click();
    await el.updateComplete;

    expect(el._editMode).toBe(true);
    const inputs = Array.from(el._panelMount.querySelectorAll('.sap-draft-input'));
    expect(inputs.map(i => i.value)).toEqual(['Auto-approve make test in this repo']);
    // The draft offers its own actions; the single-rule add row is gone.
    expect(el._panelMount.querySelector('.sap-condense-btn')).toBeTruthy();
    expect(el._panelMount.querySelector('.sap-save-btn')).toBeTruthy();
    expect(el._panelMount.querySelectorAll('.sap-add-row input.sap-rule-input').length).toBe(0);
  });

  it('saves the draft as a full rule-list replacement', async () => {
    const { el, fetchMock } = await mountPanel();
    el._panelMount.querySelector('.sap-edit-btn').click();
    await el.updateComplete;

    // Edit the existing row and add a new one.
    const input = el._panelMount.querySelector('.sap-draft-input');
    input.value = 'Auto-approve make test';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;
    el._panelMount.querySelector('.sap-add-row .sap-add-btn').click();
    await el.updateComplete;
    const newInput = el._panelMount.querySelectorAll('.sap-draft-input')[1];
    newInput.value = 'Auto-approve cargo test';
    newInput.dispatchEvent(new Event('input'));
    await el.updateComplete;

    el._panelMount.querySelector('.sap-save-btn').click();
    await vi.waitFor(() => expect(el._editMode).toBe(false));

    const replaceCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/rules/replace' && opt?.method === 'PUT',
    );
    expect(replaceCall).toBeTruthy();
    expect(JSON.parse(replaceCall[1].body)).toEqual({ rules: ['Auto-approve make test', 'Auto-approve cargo test'] });
    // The refetched view shows what was saved.
    await el.updateComplete;
    expect(el._panelMount.textContent).toContain('Auto-approve cargo test');
  });

  it('discards draft changes on cancel without saving', async () => {
    const { el, fetchMock } = await mountPanel();
    el._panelMount.querySelector('.sap-edit-btn').click();
    await el.updateComplete;

    const input = el._panelMount.querySelector('.sap-draft-input');
    input.value = 'changed locally';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;

    el._panelMount.querySelector('.sap-edit-actions .sap-cancel-btn').click();
    await el.updateComplete;

    expect(el._editMode).toBe(false);
    const replaceCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/rules/replace' && opt?.method === 'PUT',
    );
    expect(replaceCall).toBeUndefined();
    // The original rule is back, unmodified.
    await el.updateComplete;
    expect(el._panelMount.textContent).toContain('Auto-approve make test in this repo');
  });

  it('condenses the rules into a draft and shows before/after', async () => {
    const { el, fetchMock } = await mountPanel();
    el._panelMount.querySelector('.sap-edit-btn').click();
    await el.updateComplete;

    const condenseBtn = el._panelMount.querySelector('.sap-condense-btn');
    expect(condenseBtn.disabled).toBe(false);
    condenseBtn.click();
    await vi.waitFor(() => expect(el._condensing).toBe(false));

    const condenseCall = fetchMock.mock.calls.find(
      ([url, opt]) => url === '/api/conversations/c1/shell-approval/condense' && opt?.method === 'POST',
    );
    expect(condenseCall).toBeTruthy();

    // The proposal lands in the draft, highlighted as new.
    await el.updateComplete;
    const added = el._panelMount.querySelector('.sap-rule-added');
    expect(added).toBeTruthy();
    expect(added?.querySelector('input')?.value).toBe('Auto-approve any test command in this repo');
    // The superseded rule renders struck-through above the draft.
    const removed = el._panelMount.querySelector('.sap-rule-removed');
    expect(removed?.textContent.trim()).toBe('Auto-approve make test in this repo');
  });

  it('keeps the draft and drops diff markers when condense fails', async () => {
    const fetchMock = vi.fn(async (url, options) => {
      const u = new URL(url, 'http://test');
      const method = (options?.method || 'GET').toUpperCase();
      if (u.pathname === '/api/shell-approval/presets') {
        return jsonResponse([]);
      }
      if (u.pathname === '/api/conversations/c1/shell-approval') {
        return jsonResponse(APPROVAL_RESPONSE);
      }
      if (u.pathname === '/api/conversations/c1/shell-approval/condense' && method === 'POST') {
        return jsonResponse({ error: 'model unavailable' }, 502);
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

    el._panelMount.querySelector('.sap-edit-btn').click();
    await el.updateComplete;
    // Edit the draft so a failed condense must not lose it.
    const input = el._panelMount.querySelector('.sap-draft-input');
    input.value = 'Auto-approve make test (edited)';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;

    el._panelMount.querySelector('.sap-condense-btn').click();
    await vi.waitFor(() => expect(el._condensing).toBe(false));
    await el.updateComplete;

    // The draft survives the failed condense...
    expect(el._draftRules).toEqual(['Auto-approve make test (edited)']);
    // ...and the diff markers are gone.
    expect(el._panelMount.querySelector('.sap-rule-added')).toBeNull();
    expect(el._panelMount.querySelector('.sap-rule-removed')).toBeNull();
  });
});
