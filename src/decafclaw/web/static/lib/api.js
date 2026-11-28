import { ApiError } from './api-client/index.js';

/**
 * Shared REST result wrapper (#1415).
 *
 * The generated API client rejects with `ApiError` on an HTTP error status,
 * and with a TypeError / SyntaxError on transport or body-decode failure.
 * Callers historically handled those three classes inconsistently — some
 * silently swallowed an `ApiError`, others logged it differently — so a 500
 * on a listing left the UI empty with no feedback. This module normalizes
 * every generated client call into one result shape so callers branch on
 * `ok` and surface the failure uniformly (error state, toast, …).
 */

/**
 * @template T
 * @typedef {object} ApiCallOk<T>
 * @property {true} ok - the call resolved with a decoded body
 * @property {T} data - the decoded response body
 */

/**
 * @template [T=undefined]
 * @typedef {object} ApiCallFail<T>
 * @property {false} ok - the call rejected (HTTP, transport, or decode failure)
 * @property {Error} error - the ApiError for HTTP status failures; the original rejection otherwise
 */

/**
 * @template T
 * @typedef {ApiCallOk<T> | ApiCallFail<T>} ApiCallResult<T>
 */

/**
 * Run a generated client call, converting any rejection into a uniform result.
 * Never throws: every failure mode resolves to `{ok: false, error}`.
 *
 * @template T
 * @param {() => Promise<T>} call - zero-argument thunk returning the generated client's promise
 * @returns {Promise<ApiCallResult<T>>}
 */
export async function apiCall(call) {
  try {
    const data = await call();
    return { ok: true, data };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return { ok: false, error };
  }
}

/**
 * Human-readable text for a failed call's error, suitable for UI display.
 * Prefers the server's own message (the `error` or FastAPI `detail` JSON
 * field) with the HTTP status, then a transport-level description.
 *
 * @param {Error} err - the error from a failed apiCall()
 * @returns {string}
 */
export function apiErrorMessage(err) {
  if (err instanceof ApiError) {
    const body = err.body;
    const serverMessage = (body && typeof body === 'object' && !Array.isArray(body))
      ? /** @type {any} */ (body).error ?? /** @type {any} */ (body).detail
      : undefined;
    if (typeof serverMessage === 'string' && serverMessage) return `${serverMessage} (${err.status})`;
    return `Request failed (HTTP ${err.status})`;
  }
  if (err instanceof TypeError) return 'Could not reach the server';
  if (err instanceof SyntaxError) return 'Invalid response from server';
  return err.message || 'Request failed';
}
