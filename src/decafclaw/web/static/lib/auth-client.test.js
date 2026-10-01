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
