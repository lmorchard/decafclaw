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
});
