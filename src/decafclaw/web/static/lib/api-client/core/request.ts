/* Copied into api-client/core/request.ts by gen_api_client.py. */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { OpenAPIConfig } from './OpenAPI';
import { CancelablePromise } from './CancelablePromise';
import { request as generatedRequest, getHeaders, sendRequest, catchErrorCodes, getQueryString, getRequestBody } from './generated-request';

/** Preserve migrated callers' session, JSON, and HTTP-error semantics. */
export const request = <T>(config: OpenAPIConfig, options: ApiRequestOptions & { discardResponse?: boolean }): CancelablePromise<T> => {
    const login = options.method === 'POST' && options.url === '/api/auth/login';
    const logout = options.method === 'POST' && options.url === '/api/auth/logout';
    // Keep the main session check on its existing transport; only the vault
    // guard selects the response-discarding overload.
    const vaultGuard = options.method === 'GET' && options.url === '/api/auth/me' && options.discardResponse;
    const sticky = options.url === '/api/sticky/{conv_id}';
    const listing = ['/api/conversations', '/api/conversations/archived', '/api/conversations/system'].includes(options.url);
    const patch = options.method === 'PATCH' && options.url === '/api/conversations/{id}';
    const create = options.method === 'POST' && options.url === '/api/conversations';
    const lifecycle = (options.method === 'DELETE' && options.url === '/api/conversations/{id}')
        || (options.method === 'POST' && ['/api/conversations/{id}/archive', '/api/conversations/{id}/unarchive'].includes(options.url));
    const folder = (options.method === 'POST' && options.url === '/api/conversations/folders')
        || (['PUT', 'DELETE'].includes(options.method) && options.url === '/api/conversations/folders/{path}');
    if (!login && !logout && !vaultGuard && !create && !lifecycle && !folder && !patch && (options.method !== 'GET' || (!sticky && !listing))) {
        return generatedRequest<T>(config, options);
    }
    return new CancelablePromise(async (resolve, reject, onCancel) => {
        try {
            // The handwritten listing callers omitted an empty root query.
            const query = listing && options.query?.folder === "" ? undefined : options.query;
            const url = folder
                ? `${config.BASE}/api/conversations/folders${options.path ? '/' + String(options.path.path).split('/').map(encodeURIComponent).join('/') : ''}`
                : patch || lifecycle
                ? `${config.BASE}${options.url.replace('{id}', encodeURIComponent(options.path!.id))}`
                : sticky
                ? `${config.BASE}/api/sticky/${encodeURIComponent(options.path!.conv_id)}`
                : `${config.BASE}${options.url}${query ? getQueryString(query) : ""}`;
            const headers = await getHeaders(config, options);
            if (onCancel.isCancelled) return;
            const response = await sendRequest(
                { ...config, WITH_CREDENTIALS: true, CREDENTIALS: 'same-origin' },
                options, url, getRequestBody(options), undefined, headers, onCancel,
            );
            // Folder mutations decode error JSON for the existing error log.
            // Logout's discard caller accepts any HTTP response. Other callers
            // reject HTTP errors before decoding. Propagate JSON failures.
            if (!(logout && options.discardResponse)) catchErrorCodes(options, {
                url, ok: response.ok, status: response.status,
                statusText: response.statusText, body: folder && !response.ok ? await response.json() : undefined,
            });
            if (options.discardResponse) {
                // The generated void overload makes the caller's unused body explicit.
                resolve(undefined);
            } else {
                resolve(await response.json());
            }
        } catch (error) {
            reject(error);
        }
    });
};
