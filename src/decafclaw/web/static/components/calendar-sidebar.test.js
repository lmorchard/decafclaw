import { afterEach, describe, expect, it, vi } from 'vitest';

await import('./calendar-sidebar.js');

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
  recurrence: '', timezone: '', recurrence_start: '', occurrence_at: '', recurrence_until: '',
  origin_title: '', target_title: 'Chat', recurrence_summary: '',
};

describe('calendar-sidebar', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('lists upcoming events and opens one', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ events: [EVENT] }));
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = document.createElement('calendar-sidebar');
    document.body.append(sidebar);
    sidebar.active = true;
    await sidebar.updateComplete;
    await vi.waitFor(() => expect(sidebar.querySelector('.calendar-row')).toBeTruthy());

    expect(fetchMock.mock.calls[0][0]).toBe('/api/calendar/events?status=pending');
    expect(sidebar.querySelector('.calendar-row-title').textContent).toContain('Dentist');

    const opened = vi.fn();
    document.addEventListener('calendar-open', opened, { once: true });
    sidebar.querySelector('.calendar-row').click();
    expect(opened).toHaveBeenCalled();
    expect(opened.mock.calls[0][0].detail.eventId).toBe('cal_1');
  });
});
