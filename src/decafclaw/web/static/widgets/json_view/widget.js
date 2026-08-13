import { LitElement, html } from 'lit';

export class JsonViewWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String }
  };

  render() {
    return html`<div>json_view stub</div>`;
  }
}
customElements.define('dc-widget-json-view', JsonViewWidget);
