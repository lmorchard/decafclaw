/**
 * Dialog semantics for confirmation prompts (#166).
 */

import { afterEach, describe, expect, it } from 'vitest';

import './confirm-view.js';

/** @param {object[]} confirms @returns {Promise<any>} */
async function mount(confirms) {
  const el = /** @type {any} */ (document.createElement('confirm-view'));
  el.confirms = confirms;
  document.body.appendChild(el);
  await el.updateComplete;
  return el;
}

afterEach(() => {
  document.body.innerHTML = '';
});

/** @param {Element} card */
function expectDialog(card) {
  expect(card.getAttribute('role')).toBe('dialog');
  // Inline cards with no focus trap: aria-modal would hide the rest of the page.
  expect(card.hasAttribute('aria-modal')).toBe(false);
  expect(card.getAttribute('aria-label')?.trim()).toBeTruthy();
}

describe('confirm-view dialog semantics', () => {
  it('marks a tool confirmation card as a labelled non-modal dialog', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'shell', tool_call_id: 'tc1', command: 'ls',
    }]);

    const card = el.querySelector('.confirm-card');
    expectDialog(card);
    expect(card.getAttribute('aria-label')).toBe('Confirm shell');
  });

  it('shows the working directory when a shell command targets a subdirectory', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'shell', tool_call_id: 'tc-cwd', command: 'pytest tests',
      action_data: { cwd: '/ws/worktrees/issue-1' },
    }]);

    const cwd = el.querySelector('.confirm-cwd');
    expect(cwd?.textContent).toContain('/ws/worktrees/issue-1');
  });

  it('omits the working directory line when no cwd is set', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'shell', tool_call_id: 'tc-root', command: 'ls',
    }]);

    expect(el.querySelector('.confirm-cwd')).toBeNull();
  });

  it('marks a workflow free-text input card as a labelled non-modal dialog', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'workflow', tool_call_id: 'tc2',
      confirmation_id: 'c2', action_type: 'workflow_user_input',
      message: 'What is your name?', action_data: {},
    }]);

    const card = el.querySelector('.confirm-card');
    expectDialog(card);
    expect(card.getAttribute('aria-label')).toBe('What is your name?');
  });

  it('marks a workflow choice card as a labelled non-modal dialog', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'workflow', tool_call_id: 'tc3',
      confirmation_id: 'c3', action_type: 'workflow_user_input',
      message: 'Pick one', action_data: { choices: ['a', 'b'] },
    }]);

    const card = el.querySelector('.confirm-card');
    expectDialog(card);
    expect(card.getAttribute('aria-label')).toBe('Pick one');
  });

  it('renders countdown badge when timeout is configured', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'shell', tool_call_id: 'tc4', command: 'git status',
      timeout: 60, timestamp: new Date().toISOString(),
    }]);

    const badge = el.querySelector('.confirm-timeout');
    expect(badge).toBeTruthy();
    expect(badge.textContent).toMatch(/⏱ \d+s/);
  });

  it('renders no timeout badge when timeout is disabled', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'shell', tool_call_id: 'tc5', command: 'git status',
      timeout: null,
    }]);

    const badge = el.querySelector('.confirm-timeout');
    expect(badge).toBeTruthy();
    expect(badge.textContent).toBe('⏱ No timeout');
  });

  it('renders timeout badge in free-text workflow input card', async () => {
    const el = await mount([{
      context_id: 'ctx', tool: 'workflow', tool_call_id: 'tc6',
      confirmation_id: 'c6', action_type: 'workflow_user_input',
      message: 'Enter branch name', action_data: {},
      timeout: null,
    }]);

    const badge = el.querySelector('.confirm-timeout');
    expect(badge).toBeTruthy();
    expect(badge.textContent).toBe('⏱ No timeout');
  });

  it('renders decline reason, editable rule, and Approve + remember why button (#982)', async () => {
    let responded = null;
    const fakeStore = {
      respondToConfirm: (contextId, tool, toolCallId, approved, extra) => {
        responded = { contextId, tool, toolCallId, approved, extra };
      },
    };

    const el = /** @type {any} */ (document.createElement('confirm-view'));
    el.store = fakeStore;
    el.confirms = [{
      context_id: 'ctx-982',
      confirmation_id: 'c-982',
      tool: 'shell',
      tool_call_id: 'tc-982',
      command: 'gh pr create',
      suggested_pattern: 'gh pr create *',
      decline_reason: 'Remote PR creation modifies external state',
      suggested_rule: 'Auto-approve gh pr create in this repo',
    }];
    document.body.appendChild(el);
    await el.updateComplete;

    // Check decline reason display
    const declineEl = el.querySelector('.confirm-decline-reason');
    expect(declineEl).toBeTruthy();
    expect(declineEl.textContent).toContain('Remote PR creation modifies external state');

    // Check rule input
    const input = el.querySelector('.confirm-rule-input');
    expect(input).toBeTruthy();
    expect(input.value).toBe('Auto-approve gh pr create in this repo');

    // Check Approve + remember why button
    const buttons = Array.from(el.querySelectorAll('.confirm-buttons button'));
    const rememberBtn = buttons.find(b => b.textContent?.includes('Approve + remember why'));
    expect(rememberBtn).toBeTruthy();

    // Edit input and click
    input.value = 'Auto-approve gh pr create in this repo (edited)';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;

    rememberBtn.click();

    expect(responded).toEqual({
      contextId: 'ctx-982',
      tool: 'shell',
      toolCallId: 'tc-982',
      approved: true,
      extra: {
        add_rule: true,
        rule: 'Auto-approve gh pr create in this repo (edited)',
      },
    });
  });
});

describe('shell purpose and denial feedback (#1559)', () => {
  /** @param {object[]} confirms @returns {Promise<{el: any, responses: object[]}>} */
  async function mountWithStore(confirms) {
    const responses = [];
    const el = /** @type {any} */ (document.createElement('confirm-view'));
    el.store = {
      respondToConfirm: (contextId, tool, toolCallId, approved, extra) => {
        responses.push({ contextId, tool, toolCallId, approved, extra });
      },
    };
    el.confirms = confirms;
    document.body.appendChild(el);
    await el.updateComplete;
    return { el, responses };
  }

  function denyButton(el) {
    return Array.from(el.querySelectorAll('.confirm-buttons button'))
      .find(b => b.textContent?.trim() === 'Deny');
  }

  it('renders the stated purpose under the command', async () => {
    const { el } = await mountWithStore([{
      context_id: 'ctx-p', tool: 'shell', tool_call_id: 'tc-p', command: 'pytest tests',
      action_data: { purpose: 'Run the test suite before committing' },
    }]);

    const purpose = el.querySelector('.confirm-purpose');
    expect(purpose).toBeTruthy();
    expect(purpose.textContent).toContain('Run the test suite before committing');
  });

  it('omits the purpose line when none was stated', async () => {
    const { el } = await mountWithStore([{
      context_id: 'ctx-np', tool: 'shell', tool_call_id: 'tc-np', command: 'ls',
    }]);

    expect(el.querySelector('.confirm-purpose')).toBeNull();
  });

  it('sends typed feedback with a denial', async () => {
    const { el, responses } = await mountWithStore([{
      context_id: 'ctx-fb', confirmation_id: 'c-fb', tool: 'shell', tool_call_id: 'tc-fb',
      command: 'grep -rn TODO src/', action_data: { purpose: 'Find TODOs' },
    }]);

    const input = el.querySelector('.confirm-feedback-input');
    input.value = 'Use workspace_search instead.';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;

    denyButton(el).click();

    expect(responses).toHaveLength(1);
    expect(responses[0].approved).toBe(false);
    expect(responses[0].extra).toEqual({ feedback: 'Use workspace_search instead.' });
  });

  it('omits feedback when the field is empty', async () => {
    const { el, responses } = await mountWithStore([{
      context_id: 'ctx-empty', confirmation_id: 'c-empty', tool: 'shell', tool_call_id: 'tc-empty',
      command: 'ls',
    }]);

    denyButton(el).click();

    expect(responses[0].approved).toBe(false);
    expect(responses[0].extra).toEqual({});
  });

  it('flags a missing native tool on denial', async () => {
    const { el, responses } = await mountWithStore([{
      context_id: 'ctx-flag', confirmation_id: 'c-flag', tool: 'shell', tool_call_id: 'tc-flag',
      command: 'systemctl restart nginx', action_data: { purpose: 'Restart the server' },
    }]);

    const flag = el.querySelector('.confirm-feedback-flag input');
    flag.checked = true;
    flag.dispatchEvent(new Event('change'));
    await el.updateComplete;

    denyButton(el).click();

    expect(responses[0].extra).toEqual({ data: { flag_missing_tool: true } });
  });

  it('does not offer the missing-tool flag for non-shell tools', async () => {
    const { el } = await mountWithStore([{
      context_id: 'ctx-skill', tool: 'activate_skill', tool_call_id: 'tc-skill',
      command: 'project', message: 'Activate project?',
    }]);

    expect(el.querySelector('.confirm-feedback-flag')).toBeNull();
    expect(el.querySelector('.confirm-feedback-input')).toBeNull();
  });
});
