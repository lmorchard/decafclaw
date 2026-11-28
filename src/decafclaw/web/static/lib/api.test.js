import { describe, expect, it } from 'vitest';

import { ApiError } from './api-client/index.js';
import { apiCall, apiErrorMessage } from './api.js';

/** Build an ApiError the way the generated transport does: request options + HTTP result. */
function httpError(status, body) {
  return new ApiError(
    { method: 'GET', url: '/api/conversations' },
    { url: '/api/conversations', ok: false, status, statusText: 'Status', body },
    `HTTP ${status}`,
  );
}

describe('apiCall (#1415)', () => {
  it('resolves ok with the decoded body on success', async () => {
    const data = { conversations: [], folders: [], folder: '' };
    const result = await apiCall(async () => data);
    expect(result).toEqual({ ok: true, data });
  });

  it('resolves not-ok with the ApiError on an HTTP failure', async () => {
    const error = httpError(500, { detail: 'Internal Server Error' });
    const result = await apiCall(async () => { throw error; });
    expect(result.ok).toBe(false);
    expect(/** @type {any} */ (result).error).toBe(error);
  });

  it('resolves not-ok with the original error on a transport failure', async () => {
    const error = new TypeError('Failed to fetch');
    const result = await apiCall(async () => { throw error; });
    expect(result.ok).toBe(false);
    expect(/** @type {any} */ (result).error).toBe(error);
  });

  it('wraps non-Error rejections so callers always get an Error', async () => {
    const result = await apiCall(async () => { throw 'boom'; });
    expect(result.ok).toBe(false);
    const error = /** @type {any} */ (result).error;
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toContain('boom');
  });

  it('never throws, whatever the call rejects with', async () => {
    for (const rejection of [new Error('x'), new TypeError('y'), 'z', 42, null]) {
      const result = await apiCall(async () => { throw rejection; });
      expect(result.ok).toBe(false);
      expect(/** @type {any} */ (result).error).toBeInstanceOf(Error);
    }
  });

  it('awaits the call exactly once', async () => {
    let calls = 0;
    await apiCall(async () => { calls += 1; return 'done'; });
    expect(calls).toBe(1);
  });
});

describe('apiErrorMessage (#1415)', () => {
  it('prefers the server error field with the status', () => {
    const message = apiErrorMessage(httpError(409, { error: 'folder problem' }));
    expect(message).toBe('folder problem (409)');
  });

  it('falls back to FastAPI detail bodies', () => {
    const message = apiErrorMessage(httpError(500, { detail: 'Internal Server Error' }));
    expect(message).toBe('Internal Server Error (500)');
  });

  it('falls back to the status when the body carries no message', () => {
    expect(apiErrorMessage(httpError(503, undefined))).toBe('Request failed (HTTP 503)');
    expect(apiErrorMessage(httpError(418, { ok: true }))).toBe('Request failed (HTTP 418)');
  });

  it('describes transport failures without leaking fetch internals', () => {
    expect(apiErrorMessage(new TypeError('Failed to fetch'))).toBe('Could not reach the server');
  });

  it('describes decode failures without leaking JSON internals', () => {
    expect(apiErrorMessage(new SyntaxError('Unexpected token < in JSON'))).toBe('Invalid response from server');
  });

  it('falls back to the error message for other failures', () => {
    expect(apiErrorMessage(new Error('cancelled'))).toBe('cancelled');
  });

  it('handles an empty message', () => {
    expect(apiErrorMessage(new Error(''))).toBe('Request failed');
  });
});
