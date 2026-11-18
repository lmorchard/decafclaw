import { afterEach, describe, expect, it } from 'vitest';
import './chat-recalled-facts.js';

describe('chat-recalled-facts (#1514)', () => {
  /** @type {any} */
  let el;

  afterEach(() => {
    el?.remove();
    document.body.innerHTML = '';
  });

  /** @param {string[]} facts */
  async function mount(facts) {
    el = document.createElement('chat-recalled-facts');
    el.facts = facts;
    document.body.appendChild(el);
    await el.updateComplete;
    return el;
  }

  it('renders nothing when there are no facts or only blanks', async () => {
    await mount([]);
    expect(el.querySelector('.recalled-facts')).toBeNull();

    el.remove();
    await mount(['', '   ']);
    expect(el.querySelector('.recalled-facts')).toBeNull();
  });

  it('renders a collapsed chip with the fact count', async () => {
    await mount(['User prefers espresso.', 'User is in Pacific time.']);

    const toggle = el.querySelector('.recalled-facts-toggle');
    expect(toggle).toBeTruthy();
    expect(toggle.textContent).toContain('🧠');
    expect(toggle.textContent).toContain('Recalled 2 facts');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(el.querySelector('.recalled-facts-list')).toBeNull();
  });

  it('uses the singular label for one fact', async () => {
    await mount(['User prefers espresso.']);
    expect(el.querySelector('.recalled-facts-toggle').textContent).toContain('Recalled 1 fact');
  });

  it('expands and collapses the fact list on click', async () => {
    await mount(['User prefers espresso.', 'User is in Pacific time.']);

    el.querySelector('.recalled-facts-toggle').click();
    await el.updateComplete;

    const list = el.querySelector('.recalled-facts-list');
    expect(list).toBeTruthy();
    expect([...list.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'User prefers espresso.',
      'User is in Pacific time.',
    ]);
    expect(el.querySelector('.recalled-facts-toggle').getAttribute('aria-expanded')).toBe('true');

    el.querySelector('.recalled-facts-toggle').click();
    await el.updateComplete;

    expect(el.querySelector('.recalled-facts-list')).toBeNull();
    expect(el.querySelector('.recalled-facts-toggle').getAttribute('aria-expanded')).toBe('false');
  });
});
