/* Copied into api-client/core/request.ts by gen_api_client.py. */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { OpenAPIConfig } from './OpenAPI';
import { CancelablePromise } from './CancelablePromise';
import { request as generatedRequest, getHeaders, sendRequest, catchErrorCodes, getQueryString } from './generated-request';

/** Preserve the migrated read callers' session, JSON, and HTTP-error semantics. */
export const request = <T>(config: OpenAPIConfig, options: ApiRequestOptions): CancelablePromise<T> => {
    const sticky = options.url === '/api/sticky/{conv_id}';
    const listing = ['/api/conversations', '/api/conversations/archived', '/api/conversations/system'].includes(options.url);
    if (options.method !== 'GET' || (!sticky && !listing)) {
        return generatedRequest<T>(config, options);
    }
    return new CancelablePromise(async (resolve, reject, onCancel) => {
        try {
            // The handwritten listing callers omitted an empty root query.
            const query = listing && options.query?.folder === "" ? undefined : options.query;
            const url = sticky
                ? `${config.BASE}/api/sticky/${encodeURIComponent(options.path!.conv_id)}`
                : `${config.BASE}${options.url}${query ? getQueryString(query) : ""}`;
            const headers = await getHeaders(config, options);
            if (onCancel.isCancelled) return;
            const response = await sendRequest(
                { ...config, WITH_CREDENTIALS: true, CREDENTIALS: 'same-origin' },
                options, url, undefined, undefined, headers, onCancel,
            );
            // Like the original caller, ignore HTTP failures before decoding.
            // Unlike the stock transport, propagate JSON failures to the caller.
            catchErrorCodes(options, {
                url, ok: response.ok, status: response.status,
                statusText: response.statusText, body: undefined,
            });
            resolve(await response.json());
        } catch (error) {
            reject(error);
        }
    });
};
