import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthClient } from './auth-client.js';

afterEach(() => vi.unstubAllGlobals());

describe('session lookup', () => {
  it('returns the authenticated username and stores it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ username: 'alice' }), { headers: { 'Content-Type': 'application/json' } })));
    const client = new AuthClient();
    expect(await client.checkSession()).toBe('alice');
    expect(client.currentUser).toBe('alice');
    expect(fetch.mock.calls[0][0]).toBe('/api/auth/me');
  });

  it.each(['unauthenticated', 'network failure'])('clears a previously successful session on %s', async (failure) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ username: 'alice' }), { headers: { 'Content-Type': 'application/json' } }));
    if (failure === 'unauthenticated') fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    else fetchMock.mockRejectedValueOnce(new Error('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const client = new AuthClient();
    expect(await client.checkSession()).toBe('alice');
    expect(await client.checkSession()).toBeNull();
    expect(client.currentUser).toBeNull();
  });

  it('returns null without an authenticated session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const client = new AuthClient();
    expect(await client.checkSession()).toBeNull();
    expect(client.currentUser).toBeNull();
  });
});

describe('login', () => {
  it('posts the token, returns and stores username, and emits login detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"username":"alice"}')));
    const client = new AuthClient();
    const listener = vi.fn();
    client.addEventListener('login', listener);
    expect(await client.login('test-token')).toBe('alice');
    expect(client.currentUser).toBe('alice');
    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0].detail).toEqual({ username: 'alice' });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('/api/auth/login');
    expect(options.method).toBe('POST');
    expect(options.headers.get('Content-Type')).toBe('application/json');
    expect(options.body).toBe('{"token":"test-token"}');
    expect(options.credentials).toBe('same-origin');
  });

  it.each(['http', 'network', 'json'])('preserves state and events on %s failure', async (failure) => {
    const error = new TypeError('offline');
    const response = new Response('not JSON', { status: failure === 'http' ? 401 : 200 });
    const json = vi.spyOn(response, 'json');
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('{"username":"alice"}'));
    if (failure === 'network') fetchMock.mockRejectedValueOnce(error);
    else fetchMock.mockResolvedValueOnce(response);
    vi.stubGlobal('fetch', fetchMock);
    const client = new AuthClient();
    await client.login('valid');
    const listener = vi.fn();
    client.addEventListener('login', listener);
    const attempt = client.login('bad');
    if (failure === 'network') await expect(attempt).rejects.toBe(error);
    else if (failure === 'http') await expect(attempt).rejects.toThrow('Invalid token');
    else await expect(attempt).rejects.toBeInstanceOf(SyntaxError);
    expect(json).toHaveBeenCalledTimes(failure === 'json' ? 1 : 0);
    expect(client.currentUser).toBe('alice');
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('logout', () => {
  it.each([200, 204, 401, 500])('ignores status %s and body before clearing state and emitting logout', async (status) => {
    const response = new Response(status === 204 ? null : 'not JSON', { status });
    const json = vi.spyOn(response, 'json');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"username":"alice"}'))
      .mockResolvedValueOnce(response));
    const client = new AuthClient();
    await client.login('valid');
    const listener = vi.fn();
    client.addEventListener('logout', listener);
    await client.logout();
    expect(client.currentUser).toBeNull();
    expect(listener).toHaveBeenCalledOnce();
    expect(json).not.toHaveBeenCalled();
    const [url, options] = fetch.mock.calls[1];
    expect(url).toBe('/api/auth/logout');
    expect(options.method).toBe('POST');
    expect(options.body).toBeUndefined();
    expect(options.credentials).toBe('same-origin');
  });

  it('retains state and emits nothing on transport failure', async () => {
    const error = new TypeError('offline');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"username":"alice"}'))
      .mockRejectedValueOnce(error));
    const client = new AuthClient();
    await client.login('valid');
    const listener = vi.fn();
    client.addEventListener('logout', listener);
    await expect(client.logout()).rejects.toBe(error);
    expect(client.currentUser).toBe('alice');
    expect(listener).not.toHaveBeenCalled();
  });
});
