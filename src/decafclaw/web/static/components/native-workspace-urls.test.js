import { afterEach, describe, expect, it } from 'vitest';

import { buildNativeWorkspaceUrl } from '../lib/api-client/index.js';
import { renderMarkdown } from '../lib/markdown.js';
import './file-page.js';
import './messages/user-message.js';

afterEach(() => {
  document.body.replaceChildren();
});

describe('generated native workspace URLs', () => {
  it('segment-encodes paths used by file and attachment views', async () => {
    const path = 'nested folder/日本語 #?%.png';
    const expected = '/api/workspace/nested%20folder/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F%25.png';
    expect(buildNativeWorkspaceUrl(path)).toBe(expected);

    const filePage = document.createElement('file-page');
    filePage.path = path;
    filePage.kind = 'image';
    document.body.append(filePage);
    await filePage.updateComplete;
    expect(filePage.querySelector('img').getAttribute('src')).toBe(expected);

    const message = document.createElement('user-message');
    message.attachments = [{ filename: '日本語 #?%.png', path, mime_type: 'image/png' }];
    document.body.append(message);
    await message.updateComplete;
    expect(message.querySelector('.attachment-image').getAttribute('src')).toBe(expected);
    expect(message.querySelector('.attachment-image-link').getAttribute('href')).toBe(expected);
  });

  it('keeps native download links and filenames', async () => {
    const path = 'nested folder/report #1.pdf';
    const expected = '/api/workspace/nested%20folder/report%20%231.pdf';

    const filePage = document.createElement('file-page');
    filePage.path = path;
    filePage.kind = 'binary';
    document.body.append(filePage);
    await filePage.updateComplete;
    const fileLink = filePage.querySelector('.file-download-link');
    expect(fileLink.getAttribute('href')).toBe(expected);
    expect(fileLink.hasAttribute('download')).toBe(true);

    const message = document.createElement('user-message');
    message.attachments = [{ filename: 'report #1.pdf', path, mime_type: 'application/pdf' }];
    document.body.append(message);
    await message.updateComplete;
    const attachmentLink = message.querySelector('.attachment-file-link');
    expect(attachmentLink.getAttribute('href')).toBe(expected);
    expect(attachmentLink.getAttribute('download')).toBe('report #1.pdf');
  });

  it('preserves Markdown percent, query, and fragment semantics', () => {
    const suffix = 'nested/already%20escaped.txt?download=1#section';
    expect(buildNativeWorkspaceUrl(suffix, 'raw')).toBe('/api/workspace/' + suffix);
    const html = renderMarkdown(`[file](workspace://${suffix})`);
    expect(html).toContain(`href="/api/workspace/${suffix}"`);
  });
});
