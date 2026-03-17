import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

// Custom marked renderer to rewrite workspace image paths
const renderer = new marked.Renderer();
const originalImage = renderer.image.bind(renderer);
/** @type {(token: {href: string, title: string|null, text: string}) => string} */
renderer.image = function(token) {
  let href = token.href || '';
  if (href && !href.startsWith('http') && !href.startsWith('/')) {
    href = '/api/workspace/' + href;
    token = { ...token, href };
  }
  return originalImage(token);
};

/**
 * Render markdown to sanitized HTML.
 * @param {string} text
 * @returns {string}
 */
function renderMarkdown(text) {
  if (!text) return '';
  const raw = /** @type {string} */ (marked.parse(text, {
    breaks: true,
    async: false,
    renderer,
  }));
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['target'],
    ADD_TAGS: ['img'],
    ADD_DATA_URI_TAGS: ['img'],
  });
}

/**
 * @param {string} ts
 * @returns {string}
 */
function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

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
