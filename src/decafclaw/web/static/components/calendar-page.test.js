import { afterEach, describe, expect, it, vi } from 'vitest';

await import('./calendar-page.js');

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 400 ? 'Error' : 'OK',
  headers: { get: (name) => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body,
});

const EVENT = {
  event_id: 'cal_1', conv_id: 'conv-1', target_conv_id: 'conv-1', session_mode: 'current',
  session_key: '', session_title: '', title: 'Dentist', reason: 'Call', fire_at: '2027-01-01T09:00:00+00:00',
  fire_at_ts: 1, created_at: '', updated_at: '', status: 'pending', last_fired_at: '', snooze_until: '',
  recurrence: '', timezone: 'UTC', recurrence_start: '', occurrence_at: '', recurrence_until: '',
  origin_title: '', target_title: '', recurrence_summary: '',
};

function fakeSidebar() {
  const sidebar = document.createElement('conversation-sidebar');
  // @ts-ignore test double
  sidebar.store = { currentConvId: 'conv-1' };
  document.body.append(sidebar);
  return sidebar;
}

describe('calendar-page', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('creates a one-shot event for the current conversation', async () => {
    fakeSidebar();
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ event: EVENT }, 201));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('calendar-page');
    document.body.append(page);
    await page.updateComplete;

    page.querySelector('input[name=title]').value = 'Dentist';
    page.querySelector('textarea[name=reason]').value = 'Call';
    page.querySelector('input[name=fire_at]').value = '2027-01-01T09:00';
    page.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/calendar/events');
    expect(options.method).toBe('POST');
    const body = JSON.parse(options.body);
    expect(body.conv_id).toBe('conv-1');
    expect(body.title).toBe('Dentist');
    expect(body.session_mode).toBe('current');
  });

  it('loads an existing event and snoozes it', async () => {
    fakeSidebar();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ events: [EVENT] }))
      .mockResolvedValueOnce(jsonResponse({ event: { ...EVENT, fire_at_ts: 2 } }));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('calendar-page');
    document.body.append(page);
    page.eventId = 'cal_1';
    await page.updateComplete;
    await vi.waitFor(() => expect(page.querySelector('input[name=title]').value).toBe('Dentist'));

    page.querySelector('.calendar-actions button:nth-of-type(2)').click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe('/api/calendar/events/cal_1/snooze');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ duration_seconds: 900 });
  });

  it('runs a pending event now without touching its schedule', async () => {
    fakeSidebar();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ events: [EVENT] }))
      .mockResolvedValueOnce(jsonResponse({ event: { ...EVENT, last_fired_at: '2027-01-01T09:00:00+00:00' } }));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('calendar-page');
    document.body.append(page);
    page.eventId = 'cal_1';
    await page.updateComplete;
    await vi.waitFor(() => expect(page.querySelector('input[name=title]').value).toBe('Dentist'));

    page.querySelector('.calendar-run-now').click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe('/api/calendar/events/cal_1/run');
    expect(options.method).toBe('POST');
    // The message must not claim the run finished — we only know it started.
    expect(page.textContent).toContain('Run started');
  });

  it('disables Run now for an event that is no longer pending', async () => {
    fakeSidebar();
    const fired = { ...EVENT, status: 'fired' };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ events: [fired] }));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('calendar-page');
    document.body.append(page);
    page.eventId = 'cal_1';
    await page.updateComplete;
    await vi.waitFor(() => expect(page.querySelector('input[name=title]').value).toBe('Dentist'));

    const runButton = page.querySelector('.calendar-run-now');
    expect(runButton).not.toBeNull();
    expect(runButton.disabled).toBe(true);
  });

  it('opens the target conversation from the editor chip', async () => {
    const sidebar = fakeSidebar();
    const selected = [];
    // @ts-ignore test double
    sidebar.store = { currentConvId: 'conv-1', selectConversation: (id) => selected.push(id) };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ events: [EVENT] }));
    vi.stubGlobal('fetch', fetchMock);
    const page = document.createElement('calendar-page');
    document.body.append(page);
    page.eventId = 'cal_1';
    await page.updateComplete;
    await vi.waitFor(() => expect(page.querySelector('.calendar-target-chip')).not.toBeNull());

    page.querySelector('.calendar-target-chip').click();
    expect(selected).toEqual(['conv-1']);
  });
});
