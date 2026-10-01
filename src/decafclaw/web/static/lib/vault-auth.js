import { ApiError, DefaultService } from './api-client/index.js';

/** Redirect only an HTTP authentication failure; leave transport errors rejected.
 * @param {() => void} redirect
 * @returns {Promise<void>}
 */
export async function checkVaultSession(redirect = () => { window.location.href = '/'; }) {
  try {
    await DefaultService.authMeApiAuthMeGet(true);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    redirect();
  }
}
