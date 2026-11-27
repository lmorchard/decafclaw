import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { uploadFile, uploadFileWithProgress } from './upload-client.js';

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

describe('uploadFileWithProgress XHR transport', () => {
  /** Minimal XMLHttpRequest double recording the request and driving events. */
  class FakeXHR {
    constructor() {
      FakeXHR.instances.push(this);
      this.uploadListeners = {};
      this.listeners = {};
      this.upload = {
        addEventListener: (type, fn) => {
          this.uploadListeners[type] = fn;
        },
      };
      this.status = 0;
      this.responseText = '';
      this.withCredentials = false;
    }

    open(method, url) {
      this.method = method;
      this.url = url;
    }

    addEventListener(type, fn) {
      this.listeners[type] = fn;
    }

    send(body) {
      this.body = body;
    }

    emitProgress(loaded, total) {
      this.uploadListeners.progress?.({ lengthComputable: true, loaded, total });
    }

    respond(status, responseText) {
      this.status = status;
      this.responseText = responseText;
      this.listeners.load?.();
    }

    fail() {
      this.listeners.error?.();
    }
  }

  beforeEach(() => {
    FakeXHR.instances = [];
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** @returns {FakeXHR} */
  const lastRequest = () => FakeXHR.instances[FakeXHR.instances.length - 1];

  it('posts the multipart file and reports upload progress as a fraction', async () => {
    const attachment = { filename: 'report 日本語.txt', path: 'uploads/report 日本語.txt', mime_type: 'text/plain' };
    const file = new File(['file bytes'], 'report 日本語.txt', { type: 'text/plain' });
    const progress = [];

    const result = uploadFileWithProgress('conv 日本語/#?%', file, (fraction) => progress.push(fraction));
    const xhr = lastRequest();

    expect(xhr.method).toBe('POST');
    expect(xhr.url).toBe('/api/upload/conv%20%E6%97%A5%E6%9C%AC%E8%AA%9E%2F%23%3F%25');
    expect(xhr.withCredentials).toBe(true);
    expect(xhr.body).toBeInstanceOf(FormData);
    const sent = xhr.body.get('file');
    expect(sent.name).toBe('report 日本語.txt');
    await expect(sent.text()).resolves.toBe('file bytes');

    xhr.emitProgress(50, 100);
    xhr.emitProgress(100, 100);
    expect(progress).toEqual([0.5, 1]);

    xhr.respond(201, JSON.stringify(attachment));
    await expect(result).resolves.toEqual(attachment);
  });

  it.each([
    [400, '{"error":"no file in request"}', 'no file in request'],
    [413, 'malformed', 'Upload failed: 413'],
  ])('preserves the established HTTP error message for %s', async (status, body, message) => {
    const result = uploadFileWithProgress('conv', new File(['x'], 'x.txt'));
    const xhr = lastRequest();
    xhr.respond(status, body);

    await expect(result).rejects.toThrow(message);
  });

  it('rejects a network failure', async () => {
    const result = uploadFileWithProgress('conv', new File(['x'], 'x.txt'));
    lastRequest().fail();

    await expect(result).rejects.toBeInstanceOf(TypeError);
  });

  it('rejects a malformed successful response', async () => {
    const result = uploadFileWithProgress('conv', new File(['x'], 'x.txt'));
    lastRequest().respond(201, 'malformed');

    await expect(result).rejects.toBeInstanceOf(SyntaxError);
  });
});
