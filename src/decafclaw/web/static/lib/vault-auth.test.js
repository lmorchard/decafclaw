import { afterEach, expect, it, vi } from 'vitest';
import { checkVaultSession } from './vault-auth.js';

afterEach(() => vi.unstubAllGlobals());

it.each([200, 204, 401, 500])('checks vault HTTP status %s without decoding JSON', async (status) => {
  const response = new Response(status === 204 ? null : 'not JSON', { status });
  const json = vi.spyOn(response, 'json');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  const redirect = vi.fn();
  await checkVaultSession(redirect);
  expect(redirect).toHaveBeenCalledTimes(status >= 400 ? 1 : 0);
  expect(json).not.toHaveBeenCalled();
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe('/api/auth/me');
  expect(options.method).toBe('GET');
  expect(options.credentials).toBe('same-origin');
});

it('propagates transport failure without redirecting', async () => {
  const error = new TypeError('offline');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));
  const redirect = vi.fn();
  await expect(checkVaultSession(redirect)).rejects.toBe(error);
  expect(redirect).not.toHaveBeenCalled();
});
