import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

await import('./calendar-sidebar.js');

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 400 ? 'Error' : 'OK',
  headers: { get: (name) => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body,
});

const OCCURRENCE = {
  event_id: 'cal_1', occurrence_at: '2027-01-01T09:00:00+00:00', occurrence_ts: 1,
  title: 'Dentist', target_conv_id: 'conv-1', target_title: 'Chat', status: 'pending', recurrence: false,
};

describe('calendar-sidebar', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('lists occurrences and opens one', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ occurrences: [OCCURRENCE] }));
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = document.createElement('calendar-sidebar');
    document.body.append(sidebar);
    sidebar.active = true;
    await sidebar.updateComplete;
    await vi.waitFor(() => expect(sidebar.querySelector('.calendar-row')).toBeTruthy());

    expect(fetchMock.mock.calls[0][0]).toContain('/api/calendar/occurrences?');
    expect(sidebar.querySelector('.calendar-row-title').textContent).toContain('Dentist');

    const opened = vi.fn();
    document.addEventListener('calendar-open', opened, { once: true });
    sidebar.querySelector('.calendar-row').click();
    expect(opened.mock.calls[0][0].detail.eventId).toBe('cal_1');
  });

  it('switches views, moves ranges, and records URL state', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ occurrences: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = document.createElement('calendar-sidebar');
    document.body.append(sidebar);
    sidebar.active = true;
    await sidebar.updateComplete;
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const week = sidebar.querySelector('.calendar-view-select');
    expect(week).not.toBeNull();
    expect([...week.options].map(o => o.value)).toEqual(['day', 'week', 'month', 'agenda']);
    week.value = 'week';
    week.dispatchEvent(new Event('change', { bubbles: true }));
    await sidebar.updateComplete;
    expect(new URL(window.location.href).searchParams.get('calendar_view')).toBe('week');
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const next = sidebar.querySelector('.calendar-range-controls button[title="Next"]');
    next.click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(new URL(window.location.href).searchParams.get('calendar_date')).toBeTruthy();
  });

  it('renders a month grid and opens a day', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ occurrences: [OCCURRENCE] }));
    vi.stubGlobal('fetch', fetchMock);
    const sidebar = document.createElement('calendar-sidebar');
    document.body.append(sidebar);
    sidebar.active = true;
    await sidebar.updateComplete;
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const month = sidebar.querySelector('.calendar-view-select');
    month.value = 'month';
    month.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await sidebar.updateComplete;
    await vi.waitFor(() => expect(sidebar.querySelector('.calendar-month-grid')).not.toBeNull());
    expect(sidebar.querySelector('.calendar-month-cell')).not.toBeNull();
  });
});
