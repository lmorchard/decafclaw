import { beforeEach, describe, expect, it, vi } from 'vitest';

await import('./preset-manager.js');
const { reset } = await import('../lib/presets-store.js');

const BUILTIN_PRESETS = [
  {
    name: 'developer',
    rules: ['Auto-approve pytest', 'Auto-approve ruff check'],
    source: 'builtin',
    shadows_config: false,
  },
  {
    name: 'github',
    rules: ['Auto-approve gh pr list'],
    source: 'builtin',
    shadows_config: false,
  },
  {
    name: 'team_cfg',
    rules: ['Auto-approve make test'],
    source: 'config',
    shadows_config: false,
  },
  {
    name: 'custom_work',
    rules: ['Auto-approve git status'],
    source: 'custom',
    shadows_config: false,
  },
];

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

async function mountManager(initialPresets = BUILTIN_PRESETS) {
  reset();
  const fetchMock = vi.fn(async (url, options) => {
    const u = new URL(url, 'http://test');
    const method = (options?.method || 'GET').toUpperCase();

    if (u.pathname === '/api/shell-approval/presets') {
      return jsonResponse(initialPresets);
    }
    if (u.pathname.startsWith('/api/shell-approval/presets/')) {
      const name = decodeURIComponent(u.pathname.split('/').pop() || '');
      if (method === 'PUT') {
        const body = JSON.parse(options.body);
        return jsonResponse({ ok: true, name, rules: body.rules });
      }
      if (method === 'DELETE') {
        return jsonResponse({ ok: true, name, rules: [] });
      }
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);

  const el = /** @type {any} */ (document.createElement('preset-manager'));
  document.body.append(el);
  await el.updateComplete;
  await new Promise(r => setTimeout(r, 10));
  await el.updateComplete;
  return { el, fetchMock };
}

describe('preset-manager component', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('renders read-only for built-in and config presets, editable for custom', async () => {
    const { el } = await mountManager();

    const items = el.querySelectorAll('.preset-item');
    expect(items.length).toBe(4);

    // Builtin developer
    const devItem = el.querySelector('[data-preset-name="developer"]');
    expect(devItem).toBeTruthy();
    expect(devItem.querySelector('.preset-source-badge').textContent).toBe('builtin');
    expect(devItem.querySelector('.preset-readonly-label')).toBeTruthy();
    expect(devItem.querySelector('.preset-edit-btn')).toBeNull();
    expect(devItem.querySelector('.preset-delete-btn')).toBeNull();

    // Config team_cfg
    const cfgItem = el.querySelector('[data-preset-name="team_cfg"]');
    expect(cfgItem).toBeTruthy();
    expect(cfgItem.querySelector('.preset-source-badge').textContent).toBe('config');
    expect(cfgItem.querySelector('.preset-readonly-label')).toBeTruthy();
    expect(cfgItem.querySelector('.preset-edit-btn')).toBeNull();

    // Custom custom_work
    const customItem = el.querySelector('[data-preset-name="custom_work"]');
    expect(customItem).toBeTruthy();
    expect(customItem.querySelector('.preset-source-badge').textContent).toBe('custom');
    expect(customItem.querySelector('.preset-edit-btn')).toBeTruthy();
    expect(customItem.querySelector('.preset-delete-btn')).toBeTruthy();
  });

  it('allows creating and editing custom preset', async () => {
    const { el, fetchMock } = await mountManager();

    // Click + New Preset
    el.querySelector('.preset-create-btn').click();
    await el.updateComplete;

    expect(el.querySelector('.preset-editor-title').textContent).toContain('New Custom Preset');
    const nameInput = el.querySelector('.preset-name-input');
    const rulesInput = el.querySelector('.preset-rules-input');
    nameInput.value = 'my_new_preset';
    nameInput.dispatchEvent(new Event('input'));
    rulesInput.value = 'Auto-approve cargo test\nAuto-approve cargo check';
    rulesInput.dispatchEvent(new Event('input'));

    el.querySelector('.preset-save-btn').click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const putCall = fetchMock.mock.calls.find(
      ([url, opt]) => url.includes('/api/shell-approval/presets/my_new_preset') && opt?.method === 'PUT'
    );
    expect(putCall).toBeTruthy();
    expect(JSON.parse(putCall[1].body)).toEqual({
      rules: ['Auto-approve cargo test', 'Auto-approve cargo check'],
    });
  });

  it('shows warning when deleting preset referenced by mode or active presets', async () => {
    const { el, fetchMock } = await mountManager();
    el._modes = [{ name: 'dev', presets: ['custom_work'] }];

    const customItem = el.querySelector('[data-preset-name="custom_work"]');
    const deleteBtn = customItem.querySelector('.preset-delete-btn');
    deleteBtn.click();
    await el.updateComplete;

    const warningCard = el.querySelector('.preset-delete-confirm-card');
    expect(warningCard).toBeTruthy();
    expect(warningCard.textContent).toContain("Delete preset 'custom_work'?");
    expect(warningCard.textContent).toContain("mode 'dev'");

    // Confirm delete
    const confirmBtn = warningCard.querySelector('.preset-confirm-delete-btn');
    confirmBtn.click();
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 10));

    const deleteCall = fetchMock.mock.calls.find(
      ([url, opt]) => url.includes('/api/shell-approval/presets/custom_work') && opt?.method === 'DELETE'
    );
    expect(deleteCall).toBeTruthy();
  });
});
