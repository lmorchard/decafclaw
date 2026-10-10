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
