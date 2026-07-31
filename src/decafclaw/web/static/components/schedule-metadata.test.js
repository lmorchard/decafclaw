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
  source_tier: 'admin',
};

/** @returns {any} */
function mount(overrides = {}, props = {}) {
  const el = /** @type {any} */ (document.createElement('schedule-metadata'));
  el.data = { ...BASE, ...overrides };
  el.models = ['vertex-gemini-flash', 'vertex-gemini-pro'];
  Object.assign(el, props);
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

  // Every chip field, not just one: a typo in a patch key produces a
  // PUT the server answers 200 to with the field untouched, _saveError
  // clears, and the edit silently evaporates — this branch's own thesis
  // bug, on the write path.
  it.each([
    ['required_skills', 'Required skills'],
    ['allowed_tools', 'Allowed tools'],
    ['shell_patterns', 'Shell patterns'],
    ['email_recipients', 'Email recipients'],
  ])('forwards %s chip edits under that exact patch key', async (field, label) => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const chips = /** @type {any} */ (el.querySelector(`chip-list[data-field="${field}"]`));
    expect(chips, `no chip-list rendered for ${field}`).toBeTruthy();
    expect(chips.label).toBe(label);
    chips.dispatchEvent(new CustomEvent('chips-change', {
      detail: { items: ['probe-value'] }, bubbles: true, composed: true,
    }));

    expect(seen).toEqual([{ [field]: ['probe-value'] }]);
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

  it('falls back to a text input when the model list is unavailable', async () => {
    // An expired session or a 500 on /api/models must not leave the
    // field uneditable with no explanation.
    const el = mount({ model: 'vertex-gemini-pro' }, { models: [], modelsUnavailable: true });
    await el.updateComplete;

    expect(el.querySelector('.sched-md-model')).toBeNull();
    const input = /** @type {HTMLInputElement} */ (el.querySelector('.sched-md-model-text'));
    expect(input).toBeTruthy();
    expect(input.value).toBe('vertex-gemini-pro');

    const seen = changes(el);
    input.value = 'vertex-gemini-flash';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(seen).toEqual([{ model: 'vertex-gemini-flash' }]);
  });

  it('keeps the dropdown when the model list loaded but is empty', async () => {
    // A fresh agent with no model_configs is a real state, not an
    // error — it must not look like the fetch failed.
    const el = mount({ model: '' }, { models: [] });
    await el.updateComplete;

    expect(el.querySelector('.sched-md-model-text')).toBeNull();
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    expect(select).toBeTruthy();
    expect(select.querySelectorAll('option')).toHaveLength(1);
    expect(el.querySelector('.sched-md-model-note')).toBeNull();
  });

  it('names unrecognized keys and warns they will be removed', async () => {
    const el = mount({ unknown_keys: ['modle', 'efort'] });
    await el.updateComplete;
    const warning = /** @type {string} */ (
      el.querySelector('.sched-md-unknown')?.textContent.replace(/\s+/g, ' '));
    expect(warning).toContain('modle');
    expect(warning).toContain('efort');
    // "ignored" understates it: the next save rewrites the file through
    // serialize_to_markdown, which drops them.
    expect(warning).toContain('2 keys are not recognized');
    expect(warning).toContain('will remove them');
    expect(warning).not.toContain('ignored');
  });

  it('phrases the unrecognized-key warning in the singular for one key', async () => {
    const el = mount({ unknown_keys: ['modle'] });
    await el.updateComplete;
    const warning = /** @type {string} */ (
      el.querySelector('.sched-md-unknown')?.textContent.replace(/\s+/g, ' '));
    expect(warning).toContain('1 key is not recognized');
    expect(warning).toContain('will remove it');
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

  it('groups pre_script with the permissions, not the plain form', async () => {
    // pre_script executes arbitrary Python as the bot process on the
    // next fire — the most powerful field on the panel. Unmarked in the
    // plain form it read as an ordinary path setting.
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions .sched-md-pre-script')).toBeTruthy();
    expect(el.querySelector('.sched-md-form .sched-md-pre-script')).toBeNull();
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

  it('notes that permissions do not pre-approve at workspace tier', async () => {
    const el = mount({ source_tier: 'workspace' });
    await el.updateComplete;
    const note = el.querySelector('.sched-md-permissions-note');
    expect(note).toBeTruthy();
    // Load-bearing on content, not just presence: a note that renders but
    // misnames the trust boundary or omits pre_script would still pass an
    // existence-only check.
    expect(note?.textContent).toMatch(/admin/i);
    expect(note?.textContent).toMatch(/bundled/i);
    expect(note?.textContent).toMatch(/pre_script/i);
  });

  it('scopes the email claim to this field, not to send_email as a whole', async () => {
    // check_email_approval unions config.email.allowed_recipients with the
    // per-task list. Gating the per-task half does NOT mean email always
    // requires confirmation — a globally-allowlisted recipient still
    // bypasses it at any tier. The note must not claim otherwise.
    const el = mount({ source_tier: 'workspace' });
    await el.updateComplete;
    const text = el.querySelector('.sched-md-permissions-note')?.textContent ?? '';
    expect(text).not.toMatch(/email recipients still require confirmation/i);
    expect(text).toMatch(/this list/i);
  });

  it('notes that permissions do not pre-approve at extra tier', async () => {
    const el = mount({ source_tier: 'extra' });
    await el.updateComplete;
    const note = el.querySelector('.sched-md-permissions-note');
    expect(note).toBeTruthy();
    expect(note?.textContent).toMatch(/admin/i);
    expect(note?.textContent).toMatch(/bundled/i);
    expect(note?.textContent).toMatch(/pre_script/i);
  });

  // Documents the fail-closed intent: the condition is an allowlist of
  // trusted tiers (mirroring _PREAPPROVAL_TIERS), not an enumeration of
  // untrusted ones, so a source_tier this list doesn't recognize still
  // gets the note rather than silently reading as trusted.
  it('shows the note for an unrecognized source_tier', async () => {
    const el = mount({ source_tier: 'some-future-tier' });
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions-note')).toBeTruthy();
  });

  it('shows no such note at admin tier', async () => {
    const el = mount({ source_tier: 'admin' });
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions-note')).toBeNull();
  });

  it('shows no such note at bundled tier', async () => {
    const el = mount({ source_tier: 'bundled' });
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions-note')).toBeNull();
  });
});
