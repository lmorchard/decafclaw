/**
 * Cross-surface regression guard for issue #1414 ([12.9] dedupe model/mode
 * `<select>` rendering).
 *
 * The three conversation surfaces that host the model selector — `chat-input`
 * (conversation actions menu), `conversation-sidebar`, and `chat-controls` —
 * once each rendered their own native `<select>` with diverging "not
 * configured"/"Custom"/"(default)" adornments, and one bound both `?selected`
 * and `.selected`. #1441 replaced them with the shared `<model-picker>` (the
 * conversation consumer of `<picker-popup>`, #2011), and #1684 retired session
 * modes, so no mode select remains.
 *
 * This case pins that dedup: all three surfaces must render the model selector
 * through the one shared element with identical adornment text, and none may
 * fall back to a per-surface native `<select>`.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import './chat-input.js';
import './conversation-sidebar.js';
import './chat-controls.js';

vi.mock('../lib/toast.js', () => ({ showToast: vi.fn() }));

/** The three surfaces that host the conversation model selector. */
const SURFACES = ['chat-input', 'conversation-sidebar', 'chat-controls'];

/** An active model the catalog does not configure — exercises the warning. */
const UNCONFIGURED_MODEL = 'legacy-model-v1';
/** The configured default model — earns the `default` badge on its row. */
const DEFAULT_MODEL = 'vertex-gemini-flash-3.8';

/**
 * One store shape every surface reads from: an unconfigured active model plus
 * a single configured default. Mirrors the per-surface test mounts (plain
 * EventTarget, fields assigned directly).
 */
function makeStore() {
  return Object.assign(new EventTarget(), {
    currentConvId: 'c1',
    conversations: [{ conv_id: 'c1', title: 'Trip planning' }],
    folders: [],
    archivedConversations: [],
    archivedFolders: [],
    systemConversations: [],
    systemFolders: [],
    contextUsage: 0,
    contextLimit: 100000,
    activeModel: UNCONFIGURED_MODEL,
    availableModels: [DEFAULT_MODEL],
    modelGroups: [{ name: 'Vertex', models: [DEFAULT_MODEL] }],
    defaultModel: DEFAULT_MODEL,
    isBusy: false,
    isReadOnly: false,
    selectConversation: vi.fn(),
    listConversations: vi.fn(),
    listSessions: vi.fn(),
    listArchivedConversations: vi.fn(),
    listSystemConversations: vi.fn(),
    createConversation: vi.fn(),
  });
}

/** @param {HTMLElement|null} node */
function textOf(node) {
  return node ? node.textContent : null;
}

/**
 * Mount one surface with the shared store, assert the structural dedup (the
 * shared `<model-picker>`, no native select), and open its panel so the rows
 * render. The body is emptied first: `conversation-sidebar` snapshots whether
 * a `chat-controls` exists at construction time.
 * @param {string} tag
 */
async function mountSurface(tag) {
  document.body.innerHTML = '';
  const el = /** @type {any} */ (document.createElement(tag));
  document.body.append(el);
  el.store = makeStore();
  await el.updateComplete;
  // The surfaces mirror store fields into reactive state (or call
  // requestUpdate) from updated(), which schedules a follow-up update that the
  // first updateComplete does not cover. Flush it so the DOM is stable.
  await el.updateComplete;

  // chat-input hosts the picker inside its conversation actions menu (#1192).
  if (tag === 'chat-input') {
    el.querySelector('.conversation-actions-btn').click();
    await el.updateComplete;
  }

  const picker = /** @type {any} */ (el.querySelector('model-picker'));
  expect(picker).toBeTruthy(); // the shared element, not a per-surface select
  expect(el.querySelectorAll('model-picker')).toHaveLength(1);

  // #1414/#1684: no native <select> may render the model list (or a mode) —
  // that is what the shared <model-picker> owns. Unrelated selects (the
  // sidebar section switcher, the calendar view) are fine as long as they
  // carry no model options.
  for (const select of [...el.querySelectorAll('select')]) {
    const optionValues = [.../** @type {HTMLSelectElement} */ (select).querySelectorAll('option')]
      .map((option) => option.value);
    const name = /** @type {HTMLSelectElement} */ (select).className || 'unnamed';
    expect(optionValues, `native select "${name}" must not render model options`)
      .not.toContain(UNCONFIGURED_MODEL);
    expect(optionValues, `native select "${name}" must not render model options`)
      .not.toContain(DEFAULT_MODEL);
  }

  // #2011: the trigger and rows live in the nested <picker-popup>, which has
  // its own update cycle. Non-inline pickers hide their rows until the
  // trigger opens the panel, so open it to make the row adornments visible.
  await picker.updateComplete;
  const popup = /** @type {any} */ (picker.querySelector('picker-popup'));
  await popup?.updateComplete;
  const trigger = /** @type {HTMLElement|null} */ (popup?.querySelector('.picker-popup-trigger'));
  if (trigger) {
    trigger.click();
    await popup.updateComplete;
  }

  return el;
}

/**
 * The adornment text a surface actually renders for the shared store: what the
 * selected (unconfigured) row and the default model's row carry, plus the
 * trigger's warning when the surface has one (the inline menu picker does not).
 * @param {any} surfaceEl
 */
async function adornmentSignature(surfaceEl) {
  const picker = /** @type {any} */ (surfaceEl.querySelector('model-picker'));
  await picker.updateComplete;
  const popup = /** @type {any} */ (picker.querySelector('picker-popup'));
  await popup?.updateComplete;

  const trigger = /** @type {HTMLElement|null} */ (popup?.querySelector('.picker-popup-trigger'));
  const selected = /** @type {HTMLElement|null} */ (popup?.querySelector('.picker-popup-option.selected'));
  expect(selected).toBeTruthy(); // the unconfigured model stays visible and selected

  const defaultRow = [...popup.querySelectorAll('.picker-popup-option')]
    .find((row) => row.dataset.value === DEFAULT_MODEL);

  return {
    triggerWarning: textOf(trigger?.querySelector('.picker-popup-warning')),
    selectedValue: selected?.dataset.value ?? null,
    selectedWarning: textOf(selected?.querySelector('.picker-popup-warning')),
    defaultBadge: textOf(defaultRow?.querySelector('.picker-popup-badge')),
  };
}

describe('model/mode select rendering across surfaces (#1414)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('renders the same adornments on all three surfaces', async () => {
    const signatures = [];
    for (const tag of SURFACES) {
      const el = await mountSurface(tag);
      signatures.push(await adornmentSignature(el));
    }

    // One shared element, one consistent behavior: the rendered adornment text
    // must be identical on every surface.
    for (let i = 0; i < SURFACES.length; i++) {
      const tag = SURFACES[i];
      const sig = signatures[i];
      expect(sig.selectedValue, `${tag} selected row`).toBe(UNCONFIGURED_MODEL);
      expect(sig.selectedWarning, `${tag} selected-row warning`).toBe('⚠ not configured');
      expect(sig.defaultBadge, `${tag} default badge`).toBe('default');
    }

    // The two non-inline surfaces carry the same warning on their trigger; the
    // inline menu picker has no trigger (its rows are always visible).
    expect(signatures[0].triggerWarning, 'chat-input is inline').toBeNull();
    expect(signatures[1].triggerWarning, 'conversation-sidebar trigger').toBe('⚠ not configured');
    expect(signatures[2].triggerWarning, 'chat-controls trigger').toBe('⚠ not configured');
  });
});
