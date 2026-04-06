import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../../lib/markdown.js';
import { formatTime } from '../../lib/utils.js';

/** Assistant message — markdown-rendered, with optional streaming indicator and usage info. */
export class AssistantMessage extends LitElement {
  static properties = {
    content: { type: String },
    streaming: { type: Boolean },
    usage: { type: Object, attribute: false },
    timestamp: { type: String },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.content = '';
    this.streaming = false;
    /** @type {object|null} */
    this.usage = null;
    this.timestamp = '';
  }

  /** @param {object} usage */
  #formatUsage(usage) {
    if (!usage) return '';
    const prompt = (usage.prompt_tokens || 0).toLocaleString();
    const completion = (usage.completion_tokens || 0).toLocaleString();
    return `${prompt} in / ${completion} out`;
  }

  updated() {
    this.querySelectorAll('pre:not(.has-copy)').forEach(pre => {
      pre.classList.add('has-copy');

      // Add language label if the code block specifies a language
      const code = pre.querySelector('code[class*="language-"]');
      if (code) {
        const match = code.className.match(/language-(\S+)/);
        if (match) {
          const label = document.createElement('span');
          label.className = 'code-lang-label';
          label.textContent = match[1];
          pre.style.paddingTop = '2rem';
          pre.appendChild(label);
        }
      }

      const btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.textContent = 'Copy';
      btn.addEventListener('click', () => {
        navigator.clipboard.writeText(/** @type {HTMLElement} */ (pre).innerText).then(() => {
          btn.textContent = 'Copied!';
          setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        });
      });
      pre.appendChild(btn);
    });
  }

  render() {
    return html`
      <div class="message assistant">
        <div class="role">assistant</div>
        <div class="content">
          ${unsafeHTML(renderMarkdown(this.content))}
          ${this.streaming ? html`<span class="streaming-indicator"></span>` : nothing}
        </div>
        ${this.usage || this.timestamp ? html`
          <div class="usage-info">
            ${this.usage ? this.#formatUsage(this.usage) : nothing}
            ${this.timestamp ? html`<span class="msg-time">${formatTime(this.timestamp)}</span>` : nothing}
          </div>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('assistant-message', AssistantMessage);
