import { afterEach, describe, expect, it, vi } from 'vitest';

await import('./schedules-sidebar.js');

const SCHEDULE = {
  name: 'Daily #1 日本語', source_tier: 'admin', source_path: 'schedules/Daily #1 日本語.md',
  has_overlay: false, enabled: true, schedule: '0 3 * * *', channel: '', model: '',
  allowed_tools: [], disallowed_tools: [], required_skills: [], shell_patterns: [],
  email_recipients: [], pre_script: '', unknown_keys: [], frontmatter_raw: '',
  body: 'Body.', modified: 1, next_run_iso: null, last_run_iso: null,
};

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 400 ? 'Error' : 'OK',
  json: async () => body,
});

async function mount() {
  const sidebar = document.createElement('schedules-sidebar');
  document.body.append(sidebar);
  await sidebar.updateComplete;
  sidebar.active = true;
  await sidebar.updateComplete;
  await vi.waitFor(() => expect(sidebar.querySelector('.schedule-row')).toBeTruthy());
  return sidebar;
}

describe('schedules-sidebar generated calls', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('lists, toggles, and runs an encoded schedule with same-origin credentials', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ schedules: [SCHEDULE] }))
      // These bodies are deliberately malformed. The old callers ignored
      // successful toggle/run bodies, so generated discard overloads must too.
      .mockResolvedValueOnce({ ...jsonResponse({}, 200), json: async () => {
        throw new SyntaxError('unused toggle body');
      } })
      .mockResolvedValueOnce(jsonResponse({ schedules: [{ ...SCHEDULE, enabled: false }] }))
      .mockResolvedValueOnce({ ...jsonResponse({}, 202), json: async () => {
        throw new SyntaxError('unused run body');
      } });
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = await mount();

    const toggle = /** @type {HTMLInputElement} */ (
      sidebar.querySelector('.schedule-enabled-toggle'));
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    /** @type {HTMLButtonElement} */ (sidebar.querySelector('.schedule-row-run')).click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));

    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/schedules',
      '/api/schedules/Daily%20%231%20%E6%97%A5%E6%9C%AC%E8%AA%9E',
      '/api/schedules',
      '/api/schedules/Daily%20%231%20%E6%97%A5%E6%9C%AC%E8%AA%9E/run',
    ]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options.credentials).toBe('same-origin');
    }
    expect(fetchMock.mock.calls[1][1].method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ enabled: false });
    expect(fetchMock.mock.calls[3][1].method).toBe('POST');
    expect(sidebar._runStatus[SCHEDULE.name]).toBe('started');
  });

  it('keeps HTTP and network failures distinct for run-now', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ schedules: [SCHEDULE] }))
      .mockResolvedValueOnce(jsonResponse({ error: 'denied' }, 403))
      .mockRejectedValueOnce(new TypeError('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = await mount();
    const run = /** @type {HTMLButtonElement} */ (sidebar.querySelector('.schedule-row-run'));

    run.click();
    await vi.waitFor(() => expect(sidebar._runStatus[SCHEDULE.name]).toBe('error'));
    run.click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(sidebar._runStatus[SCHEDULE.name]).toBe('error');
  });
});
