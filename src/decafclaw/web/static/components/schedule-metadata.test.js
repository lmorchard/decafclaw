import { afterEach, describe, expect, it } from 'vitest';

await import('./schedule-metadata.js');

const BASE = {
  name: 'dream',
  schedule: '0 3 * * *',
  channel: '',
  model: 'vertex-gemini-pro',
  enabled: true,
  pre_script: '',
  required_skills: ['dream', 'vault'],
  allowed_tools: [],
  shell_patterns: [],
  email_recipients: [],
  unknown_keys: [],
  frontmatter_raw: 'schedule: "0 3 * * *"',
};

/** @returns {any} */
function mount(overrides = {}) {
  const el = /** @type {any} */ (document.createElement('schedule-metadata'));
  el.data = { ...BASE, ...overrides };
  el.models = ['vertex-gemini-flash', 'vertex-gemini-pro'];
  document.body.appendChild(el);
  return el;
}

/** @param {any} el */
function changes(el) {
  /** @type {any[]} */ const seen = [];
  el.addEventListener('metadata-change', (/** @type {any} */ e) => seen.push(e.detail.fields));
  return seen;
}

describe('schedule-metadata', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('renders a control for every editable field', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-cron')).toBeTruthy();
    expect(el.querySelector('.sched-md-channel')).toBeTruthy();
    expect(el.querySelector('.sched-md-model')).toBeTruthy();
    expect(el.querySelector('.sched-md-enabled')).toBeTruthy();
    expect(el.querySelector('.sched-md-pre-script')).toBeTruthy();
    expect(el.querySelectorAll('chip-list')).toHaveLength(4);
  });

  it('emits metadata-change when the cron field changes', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.sched-md-cron'));
    input.value = '*/5 * * * *';
    input.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ schedule: '*/5 * * * *' }]);
  });

  it('emits metadata-change when a model is picked', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    select.value = 'vertex-gemini-flash';
    select.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ model: 'vertex-gemini-flash' }]);
  });

  it('emits the enabled checkbox as a boolean', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const box = /** @type {HTMLInputElement} */ (el.querySelector('.sched-md-enabled'));
    box.checked = false;
    box.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ enabled: false }]);
  });

  it('forwards chip edits under the right patch key', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const chips = /** @type {any} */ (el.querySelector('chip-list[data-field="shell_patterns"]'));
    chips.dispatchEvent(new CustomEvent('chips-change', {
      detail: { items: ['curl *'] }, bubbles: true, composed: true,
    }));

    expect(seen).toEqual([{ shell_patterns: ['curl *'] }]);
  });

  // Both orderings matter: schedule-page fetches the schedule and the
  // model list independently, so `models` can land before or after
  // `data`. A `.value` binding on the <select> loses in both — lit
  // commits it before the option child part exists, and never
  // re-commits an unchanged string afterwards.
  it('selects the configured model when models arrive before data', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-metadata'));
    el.models = ['vertex-gemini-flash', 'vertex-gemini-pro'];
    el.data = { ...BASE, model: 'vertex-gemini-pro' };
    document.body.appendChild(el);
    await el.updateComplete;

    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    expect(select.value).toBe('vertex-gemini-pro');
  });

  it('selects the configured model when models arrive after data', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-metadata'));
    el.data = { ...BASE, model: 'vertex-gemini-pro' };
    el.models = [];
    document.body.appendChild(el);
    await el.updateComplete;

    el.models = ['vertex-gemini-flash', 'vertex-gemini-pro'];
    await el.updateComplete;

    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    expect(select.value).toBe('vertex-gemini-pro');
  });

  it('flags a stored model that is not configured', async () => {
    // #729: a blank field would read as "no model set", which is the
    // ambiguity that kept the bug invisible.
    const el = mount({ model: 'strong' });
    await el.updateComplete;
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    expect(select.value).toBe('strong');
    expect(select.textContent).toContain('not configured');
  });

  it('names unrecognized keys', async () => {
    const el = mount({ unknown_keys: ['modle', 'efort'] });
    await el.updateComplete;
    const warning = el.querySelector('.sched-md-unknown');
    expect(warning?.textContent).toContain('modle');
    expect(warning?.textContent).toContain('efort');
  });

  it('shows no warning when every key is recognized', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-unknown')).toBeNull();
  });

  it('marks the permissions group', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions')).toBeTruthy();
  });

  it('renders raw frontmatter read-only', async () => {
    const el = mount();
    await el.updateComplete;
    /** @type {HTMLButtonElement} */
    (el.querySelector('.sched-md-raw-toggle')).click();
    await el.updateComplete;
    const raw = el.querySelector('.sched-md-raw-body');
    expect(raw?.textContent).toContain('0 3 * * *');
    expect(el.querySelector('.sched-md-raw-body textarea')).toBeNull();
  });

  it('shows a server error and hides it again when cleared', async () => {
    const el = mount();
    el.error = 'invalid cron expression: \'nope\'';
    await el.updateComplete;
    expect(el.querySelector('.sched-md-error')?.textContent).toContain('invalid cron');

    el.error = '';
    await el.updateComplete;
    expect(el.querySelector('.sched-md-error')).toBeNull();
  });
});
