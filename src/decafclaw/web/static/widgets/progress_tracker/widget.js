import { LitElement, html, nothing } from 'lit';

/**
 * Progress tracker widget. Props:
 *   data = { steps: [{label, status, note?}], title?: string, summary?: string }
 * Display-only, snapshot-rendered — each update replaces the full step list.
 * Status ∈ pending | in_progress | done | failed | skipped.
 */
const _GLYPHS = {
  pending: '○',
  in_progress: '◐',
  done: '●',
  failed: '✗',
  skipped: '⊘',
};

export class ProgressTrackerWidget extends LitElement {
  static properties = {
    data: { type: Object },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {{steps: {label: string, status: string, note?: string}[], title?: string, summary?: string}|null} */
    this.data = null;
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.steps)) {
      return html`<div class="progress-tracker progress-tracker--empty"><em>no steps</em></div>`;
    }
    return html`
      <div class="progress-tracker">
        ${d.title ? html`<div class="progress-tracker__title">${d.title}</div>` : nothing}
        <ul class="progress-tracker__list">
          ${d.steps.map((s) => {
            const status = s && typeof s.status === 'string' ? s.status : 'pending';
            const glyph = _GLYPHS[status] || _GLYPHS.pending;
            return html`
              <li class="progress-tracker__item progress-tracker__item--${status}">
                <span class="progress-tracker__glyph" aria-hidden="true">${glyph}</span>
                <span class="progress-tracker__label"
                  >${s?.label ?? ''}${s?.note
                    ? html`<span class="progress-tracker__note"> — ${s.note}</span>`
                    : nothing}</span>
              </li>
            `;
          })}
        </ul>
      </div>
    `;
  }
}

customElements.define('dc-widget-progress-tracker', ProgressTrackerWidget);
