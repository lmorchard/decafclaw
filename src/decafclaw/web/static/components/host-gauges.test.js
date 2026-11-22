import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DefaultService } from '../lib/api-client/index.js';
import './host-gauges.js';

/**
 * The gauge strip's job is to be honest under bad conditions: unknown values
 * must read as unknown (not as a calm host), pressure must be visible, and a
 * failing endpoint must not spray errors or leak its polling timer.
 */

const FULL_SAMPLE = {
  timestamp: 1700000000,
  cpu_count: 4,
  cpu_percent: 12.5,
  memory: {
    total_bytes: 4 * 1024 ** 3,
    available_bytes: 2.5 * 1024 ** 3,
    used_bytes: 1.5 * 1024 ** 3,
    used_percent: 37.5,
    swap_total_bytes: 4 * 1024 ** 3,
    swap_used_bytes: 1 * 1024 ** 3,
    swap_used_percent: 25,
  },
  load: { one: 0.51, five: 0.62, fifteen: 0.4, running: 1, total: 252 },
  disk_root: {
    path: '/',
    total_bytes: 197 * 1024 ** 3,
    used_bytes: 32 * 1024 ** 3,
    available_bytes: 157 * 1024 ** 3,
    used_percent: 17,
  },
  disk_tmpfs: {
    path: '/tmp',
    total_bytes: 4 * 1024 ** 3,
    used_bytes: 1.3 * 1024 ** 3,
    available_bytes: 2.7 * 1024 ** 3,
    used_percent: 32,
  },
  process_count: 252,
  uptime_seconds: 31204,
};

describe('host-gauges', () => {
  /** @type {any} */
  let el;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    el?.remove();
    el = null;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  async function mount(data = FULL_SAMPLE) {
    const spy = vi.spyOn(DefaultService, 'wrapperApiHostGet').mockResolvedValue(/** @type {any} */ (data));
    el = document.createElement('host-gauges');
    document.body.append(el);
    // Let the initial poll's promise settle under fake timers.
    await vi.advanceTimersByTimeAsync(0);
    await el.updateComplete;
    return { el, spy };
  }

  const text = () => el.shadowRoot?.textContent ?? el.textContent ?? '';

  it('renders a reading for each gauge', async () => {
    await mount();
    const labels = [...el.querySelectorAll('.gauge-label')].map((n) => n.textContent.trim());
    expect(labels).toEqual(['MEM', 'CPU', 'LOAD', 'DISK', 'TMP', 'PROC']);
  });

  it('shows available memory rather than used', async () => {
    await mount();
    // 2.5 GiB available, formatted as 2.5G.
    expect(text()).toContain('2.5G');
  });

  it('shows free disk space rather than used', async () => {
    await mount();
    // The number that predicts a failed build is what is left.
    expect(text()).toContain('157.0G');
  });

  it('renders the CPU percentage rounded', async () => {
    await mount();
    expect(text()).toContain('13%');
  });

  it('renders a dash instead of 0% when the CPU delta is not ready', async () => {
    // A cold sampler has no second /proc/stat reading; claiming 0% would read
    // as an idle host and teach the reader to distrust the strip.
    await mount({ ...FULL_SAMPLE, cpu_percent: null });
    const cpu = [...el.querySelectorAll('.gauge')].find(
      (n) => n.querySelector('.gauge-label')?.textContent.trim() === 'CPU',
    );
    expect(cpu?.querySelector('.gauge-value')?.textContent.trim()).toBe('—');
    expect(cpu?.classList.contains('unknown')).toBe(true);
  });

  it('flags high memory pressure', async () => {
    await mount({
      ...FULL_SAMPLE,
      memory: { ...FULL_SAMPLE.memory, used_percent: 94 },
    });
    const mem = [...el.querySelectorAll('.gauge')].find(
      (n) => n.querySelector('.gauge-label')?.textContent.trim() === 'MEM',
    );
    expect(mem?.classList.contains('danger')).toBe(true);
  });

  it('treats load above the core count as danger', async () => {
    // 4.5 on 4 vCPU is saturated; the same 4.5 on 16 cores would be fine, so
    // the band must scale with cpu_count rather than compare against a constant.
    await mount({ ...FULL_SAMPLE, load: { ...FULL_SAMPLE.load, one: 4.5 } });
    const load = [...el.querySelectorAll('.gauge')].find(
      (n) => n.querySelector('.gauge-label')?.textContent.trim() === 'LOAD',
    );
    expect(load?.classList.contains('danger')).toBe(true);
  });

  it('does not flag load that is high only in absolute terms', async () => {
    // 3.0 of 16 vCPU is 19% — should stay neutral.
    await mount({
      ...FULL_SAMPLE,
      cpu_count: 16,
      load: { ...FULL_SAMPLE.load, one: 3.0 },
    });
    const load = [...el.querySelectorAll('.gauge')].find(
      (n) => n.querySelector('.gauge-label')?.textContent.trim() === 'LOAD',
    );
    expect(load?.classList.contains('danger')).toBe(false);
    expect(load?.classList.contains('warn')).toBe(false);
  });

  it('omits the tmpfs gauge when there is no tmpfs', async () => {
    await mount({ ...FULL_SAMPLE, disk_tmpfs: null });
    const labels = [...el.querySelectorAll('.gauge-label')].map((n) => n.textContent.trim());
    expect(labels).not.toContain('TMP');
  });

  it('carries swap detail in the memory tooltip', async () => {
    await mount();
    const mem = [...el.querySelectorAll('.gauge')].find(
      (n) => n.querySelector('.gauge-label')?.textContent.trim() === 'MEM',
    );
    const title = mem?.getAttribute('title') ?? '';
    expect(title).toContain('Swap:');
    expect(title).toContain('25%');
  });

  it('keeps the last reading visible but dimmed when a poll fails', async () => {
    await mount();
    vi.spyOn(DefaultService, 'wrapperApiHostGet').mockRejectedValue(new Error('boom'));
    await vi.advanceTimersByTimeAsync(5000);
    await el.updateComplete;
    expect(el.querySelector('.host-gauges')?.classList.contains('stale')).toBe(true);
    // Numbers from the last good poll survive so the reader keeps orientation.
    expect(text()).toContain('2.5G');
  });

  it('stays silent on 401 rather than showing an error', async () => {
    // app.js owns the redirect to login; the strip should not compete with it.
    const { ApiError } = await import('../lib/api-client/index.js');
    vi.spyOn(DefaultService, 'wrapperApiHostGet').mockRejectedValue(
      new ApiError(
        /** @type {any} */ ({ method: 'GET', url: '/api/host' }),
        /** @type {any} */ ({ url: '/api/host', status: 401, statusText: 'Unauthorized', body: null }),
        'unauthorized',
      ),
    );
    el = document.createElement('host-gauges');
    document.body.append(el);
    await vi.advanceTimersByTimeAsync(0);
    await el.updateComplete;
    expect(el.querySelector('.host-gauges.error')).toBeNull();
    expect(text()).not.toContain('unauthorized');
  });

  it('polls on the configured interval', async () => {
    const { spy } = await mount();
    // The mount already performed the first poll; count from here.
    spy.mockClear();
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('clears its timer on disconnect so a detached strip stops polling', async () => {
    const { spy } = await mount();
    // The mount's initial poll already landed; assert only that no *further*
    // poll fires once the element leaves the document.
    spy.mockClear();
    el.remove();
    el = null;
    await vi.advanceTimersByTimeAsync(20000);
    expect(spy).not.toHaveBeenCalled();
  });

  it('does not stack requests when a poll is still in flight', async () => {
    // On a loaded host a reply can take longer than the poll interval.
    let resolve;
    const pending = new Promise((r) => { resolve = r; });
    const spy = vi
      .spyOn(DefaultService, 'wrapperApiHostGet')
      .mockReturnValue(/** @type {any} */ (pending));

    el = document.createElement('host-gauges');
    document.body.append(el);
    await vi.advanceTimersByTimeAsync(0);
    expect(spy).toHaveBeenCalledTimes(1);

    // Two more ticks arrive while the first is unresolved.
    await vi.advanceTimersByTimeAsync(10000);
    expect(spy).toHaveBeenCalledTimes(1);

    resolve(/** @type {any} */ (FULL_SAMPLE));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
