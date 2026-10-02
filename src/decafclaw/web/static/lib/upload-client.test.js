import { afterEach, describe, expect, it, vi } from 'vitest';

import { uploadFile } from './upload-client.js';

describe('uploadFile generated multipart transport', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('sends the real file and consumes the typed attachment result', async () => {
    const attachment = {
      filename: 'report 日本語.txt',
      path: 'conversations/conv/uploads/report 日本語.txt',
      mime_type: 'text/plain',
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(attachment), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['file bytes'], 'report 日本語.txt', { type: 'text/plain' });

    await expect(uploadFile('conv 日本語/#?%', file)).resolves.toEqual(attachment);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/upload/conv%20%E6%97%A5%E6%9C%AC%E8%AA%9E%2F%23%3F%25');
    expect(options.method).toBe('POST');
    expect(options.credentials).toBe('same-origin');
    expect(options.body).toBeInstanceOf(FormData);
    const sent = options.body.get('file');
    expect(sent).toBeInstanceOf(File);
    expect(sent.name).toBe('report 日本語.txt');
    expect(sent.type).toBe('text/plain');
    await expect(sent.text()).resolves.toBe('file bytes');
  });

  it.each([
    [400, '{"error":"no file in request"}', 'no file in request'],
    [413, 'malformed', 'Upload failed: 413'],
  ])('preserves the established HTTP error message for %s', async (status, body, message) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status })));
    await expect(uploadFile('conv', new File(['x'], 'x.txt'))).rejects.toThrow(message);
  });

  it('preserves network failures', async () => {
    const offline = new Error('offline');
    vi.stubGlobal('fetch', vi.fn(async () => { throw offline; }));
    await expect(uploadFile('conv', new File(['x'], 'x.txt'))).rejects.toBe(offline);
  });

  it('rejects a malformed successful response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('malformed', { status: 201 })));
    await expect(uploadFile('conv', new File(['x'], 'x.txt'))).rejects.toBeInstanceOf(SyntaxError);
  });
});
