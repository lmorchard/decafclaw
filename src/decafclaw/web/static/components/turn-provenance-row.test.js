import { afterEach, describe, expect, it } from 'vitest';
import './turn-provenance-row.js';

describe('turn-provenance-row (#1563)', () => {
  /** @type {any} */
  let el;

  afterEach(() => {
    el?.remove();
    document.body.innerHTML = '';
  });

  async function mount(provenance) {
    el = document.createElement('turn-provenance-row');
    el.provenance = provenance;
    document.body.appendChild(el);
    await el.updateComplete;
    return el;
  }

  it('renders nothing when there is no provenance or every source is empty', async () => {
    await mount({});
    expect(el.querySelector('.turn-provenance')).toBeNull();

    el.remove();
    await mount({ facts: [], vault: [] });
    expect(el.querySelector('.turn-provenance')).toBeNull();
  });

  it('renders one capsule per non-empty source with counts', async () => {
    await mount({
      facts: [{ fact: 'User prefers espresso.' }, { fact: 'User is in Pacific time.' }],
      journals: [{ timestamp: '2026-11-19 09:00', text: 'Thinking about provenance.' }],
      vault: [{ page: 'agent/pages/decafclaw.md', score: 0.82 }],
      scratchpad: [{ timestamp: '2026-11-19T10:00:00Z', text: 'Ship capsules.' }],
    });

    const capsules = [...el.querySelectorAll('button.turn-provenance-capsule')]
      .map((c) => c.textContent.replace(/\s+/g, ' ').trim());
    expect(capsules).toEqual([
      '🧠 2 facts ▾',
      '📖 1 journal entry ▾',
      '🏛️ 1 vault page ▾',
      '📝 1 note ▾',
    ]);
  });

  it('uses plural labels for multiple items', async () => {
    await mount({
      journals: [
        { timestamp: '2026-11-18 09:00', text: 'One' },
        { timestamp: '2026-11-19 09:00', text: 'Two' },
      ],
      vault: [
        { page: 'a.md', score: 0.9 },
        { page: 'b.md', score: 0.8 },
      ],
      scratchpad: [
        { timestamp: '2026-11-19T10:00:00Z', text: 'One' },
        { timestamp: '2026-11-19T11:00:00Z', text: 'Two' },
      ],
    });

    const labels = [...el.querySelectorAll('.turn-provenance-count')].map((c) => c.textContent);
    expect(labels).toEqual(['2 journal entries', '2 vault pages', '2 notes']);
  });

  it('expands one capsule at a time and shows its items', async () => {
    await mount({
      facts: [{ fact: 'User prefers espresso.' }],
      vault: [
        { page: 'agent/pages/decafclaw.md', score: 0.82, excerpt: 'Agent testbed notes.' },
        { page: 'agent/pages/vault.md', score: 0.71, excerpt: 'Vault retrieval layout.' },
      ],
    });

    const [factsBtn, vaultBtn] = [...el.querySelectorAll('button.turn-provenance-capsule')];

    factsBtn.click();
    await el.updateComplete;
    expect(factsBtn.getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('.turn-provenance-panel').textContent).toContain('User prefers espresso.');

    vaultBtn.click();
    await el.updateComplete;
    expect(factsBtn.getAttribute('aria-expanded')).toBe('false');
    expect(vaultBtn.getAttribute('aria-expanded')).toBe('true');
    const panel = el.querySelector('.turn-provenance-panel');
    expect(panel.textContent).toContain('agent/pages/decafclaw.md');
    expect(panel.textContent).toContain('score 0.82');
    expect(panel.textContent).toContain('Vault retrieval layout.');
    expect(panel.querySelectorAll('li')).toHaveLength(2);

    vaultBtn.click();
    await el.updateComplete;
    expect(el.querySelector('.turn-provenance-panel')).toBeNull();
  });

  it('renders unknown sources with a generic capsule instead of dropping them', async () => {
    await mount({ web_search: [{ text: 'One result' }] });
    const capsule = el.querySelector('button.turn-provenance-capsule');
    expect(capsule.dataset.source).toBe('web_search');
    expect(capsule.textContent).toContain('1 web_search');
  });
});
