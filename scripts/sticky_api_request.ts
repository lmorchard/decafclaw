/* Copied into api-client/core/request.ts by gen_api_client.py. */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { OpenAPIConfig } from './OpenAPI';
import { CancelablePromise } from './CancelablePromise';
import { request as generatedRequest, getHeaders, sendRequest, catchErrorCodes } from './generated-request';

/** Keep the sticky caller's path, session, and error semantics operation-local. */
export const request = <T>(config: OpenAPIConfig, options: ApiRequestOptions): CancelablePromise<T> => {
    if (options.method !== 'GET' || options.url !== '/api/sticky/{conv_id}') {
        return generatedRequest<T>(config, options);
    }
    return new CancelablePromise(async (resolve, reject, onCancel) => {
        try {
            const url = `${config.BASE}/api/sticky/${encodeURIComponent(options.path!.conv_id)}`;
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
