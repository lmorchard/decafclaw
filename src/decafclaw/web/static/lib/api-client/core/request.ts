/* Copied into api-client/core/request.ts by gen_api_client.py. */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { OpenAPIConfig } from './OpenAPI';
import { CancelablePromise } from './CancelablePromise';
import { request as generatedRequest, getHeaders, sendRequest, catchErrorCodes, getQueryString, getRequestBody } from './generated-request';

/** Preserve migrated callers' session, JSON, and HTTP-error semantics. */
export const request = <T>(config: OpenAPIConfig, options: ApiRequestOptions & { discardResponse?: boolean }): CancelablePromise<T> => {
    const notificationRead = options.method === 'POST'
        && ['/api/notifications/{id}/read', '/api/notifications/read-all'].includes(options.url);
    const notification = notificationRead || (options.method === 'GET'
        && ['/api/notifications', '/api/notifications/unread-count'].includes(options.url));
    const login = options.method === 'POST' && options.url === '/api/auth/login';
    const logout = options.method === 'POST' && options.url === '/api/auth/logout';
    // Keep the main session check on its existing transport; only the vault
    // guard selects the response-discarding overload.
    const vaultGuard = options.method === 'GET' && options.url === '/api/auth/me' && options.discardResponse;
    const diagnostics = options.method === 'GET' && options.url === '/api/conversations/{id}/context';
    const sticky = options.url === '/api/sticky/{conv_id}';
    const widgetCatalog = options.method === 'GET' && options.url === '/api/widgets';
    const canvasState = options.method === 'GET' && options.url === '/api/canvas/{conv_id}';
    const canvasNewTab = options.method === 'POST' && options.url === '/api/canvas/{conv_id}/new_tab';
    const canvasIgnoredMutation = options.method === 'POST'
        && ['/api/canvas/{conv_id}/active_tab', '/api/canvas/{conv_id}/close_tab'].includes(options.url);
    const canvas = canvasState || canvasNewTab || canvasIgnoredMutation;
    const workspaceRead = options.method === 'GET'
        && ['/api/workspace', '/api/workspace/recent', '/api/workspace-file/{path}', '/api/autocomplete'].includes(options.url);
    const vaultRead = options.method === 'GET'
        && ['/api/vault', '/api/vault/recent', '/api/vault/tags', '/api/vault/{page}'].includes(options.url);
    const vaultMutation = ['POST', 'PUT', 'DELETE'].includes(options.method)
        && ['/api/vault', '/api/vault/folders', '/api/vault/{page}'].includes(options.url);
    const configFile = options.url === '/api/config/files'
        || options.url === '/api/config/files/{path}';
    const schedule = options.url === '/api/models'
        || options.url === '/api/schedules'
        || ['/api/schedules/{name}', '/api/schedules/{name}/run',
            '/api/schedules/{name}/overlay'].includes(options.url);
    const scheduleMutation = options.method === 'PUT'
        && options.url === '/api/schedules/{name}';
    const workspaceMutation = options.url === '/api/workspace/{path}'
        && ['PUT', 'DELETE'].includes(options.method);
    const listing = ['/api/conversations', '/api/conversations/archived', '/api/conversations/system'].includes(options.url);
    const patch = options.method === 'PATCH' && options.url === '/api/conversations/{id}';
    const create = options.method === 'POST' && options.url === '/api/conversations';
    const lifecycle = (options.method === 'DELETE' && options.url === '/api/conversations/{id}')
        || (options.method === 'POST' && ['/api/conversations/{id}/archive', '/api/conversations/{id}/unarchive'].includes(options.url));
    const folder = (options.method === 'POST' && options.url === '/api/conversations/folders')
        || (['PUT', 'DELETE'].includes(options.method) && options.url === '/api/conversations/folders/{path}');
    if (!notification && !diagnostics && !login && !logout && !vaultGuard && !create && !lifecycle && !folder && !patch && !widgetCatalog && !canvas && !workspaceRead && !vaultRead && !vaultMutation && !configFile && !schedule && !workspaceMutation && (options.method !== 'GET' || (!sticky && !listing))) {
        return generatedRequest<T>(config, options);
    }
    return new CancelablePromise(async (resolve, reject, onCancel) => {
        try {
            // The handwritten listing callers omitted an empty root query.
            const query = listing && options.query?.folder === "" ? undefined : options.query;
            const url = workspaceMutation
                ? `${config.BASE}/api/workspace/${String(options.path!.path).split('/').map(encodeURIComponent).join('/')}${options.query ? getQueryString(options.query) : ''}`
                : schedule && options.path
                ? `${config.BASE}${options.url.replace('{name}', encodeURIComponent(options.path.name))}`
                : schedule
                ? `${config.BASE}${options.url}`
                : configFile && options.path
                ? `${config.BASE}/api/config/files/${String(options.path.path).split('/').map(encodeURIComponent).join('/')}`
                : configFile
                ? `${config.BASE}/api/config/files`
                : vaultMutation && options.url === '/api/vault/{page}'
                ? `${config.BASE}/api/vault/${String(options.path!.page).split('/').map(encodeURIComponent).join('/')}`
                : vaultRead && options.url === '/api/vault/{page}'
                ? `${config.BASE}/api/vault/${String(options.path!.page).split('/').map(encodeURIComponent).join('/')}`
                : vaultRead
                ? `${config.BASE}${options.url}${options.query ? getQueryString(options.query) : ''}`
                : workspaceRead && options.url === '/api/workspace-file/{path}'
                ? `${config.BASE}/api/workspace-file/${String(options.path!.path).split('/').map(encodeURIComponent).join('/')}`
                : workspaceRead
                ? `${config.BASE}${options.url}${options.query ? getQueryString(options.query) : ''}`
                : folder
                ? `${config.BASE}/api/conversations/folders${options.path ? '/' + String(options.path.path).split('/').map(encodeURIComponent).join('/') : ''}`
                : canvas
                ? `${config.BASE}${options.url.replace('{conv_id}', encodeURIComponent(options.path!.conv_id))}`
                : patch || lifecycle || diagnostics || (notificationRead && options.path)
                ? `${config.BASE}${options.url.replace('{id}', encodeURIComponent(options.path!.id))}${query ? getQueryString(query) : ''}`
                : sticky
                ? `${config.BASE}/api/sticky/${encodeURIComponent(options.path!.conv_id)}`
                : `${config.BASE}${options.url}${query ? getQueryString(query) : ""}`;
            const headers = await getHeaders(config, options);
            if (onCancel.isCancelled) return;
            const response = await sendRequest(
                { ...config, WITH_CREDENTIALS: true,
                  CREDENTIALS: widgetCatalog ? 'include' : 'same-origin' },
                options, url, getRequestBody(options), undefined, headers, onCancel,
            );
            // Folder and workspace file actions decode error JSON for their
            // existing UI messages. Workspace saves retain raw error text.
            // Logout and notification read callers accept any HTTP response when
            // discarding the body. Other callers
            // reject HTTP errors before decoding. Propagate JSON failures.
            const workspaceSave = workspaceMutation && options.method === 'PUT'
                && options.query?.rename_to === undefined;
            const workspaceRename = workspaceMutation && options.method === 'PUT'
                && options.query?.rename_to !== undefined;
            if (!((logout || notificationRead || canvasIgnoredMutation) && options.discardResponse)) catchErrorCodes(options, {
                url, ok: response.ok, status: response.status,
                statusText: response.statusText,
                body: folder && !response.ok ? await response.json()
                    : vaultMutation && !response.ok ? await response.json().catch(() => undefined)
                    : scheduleMutation && !response.ok
                        ? await response.json().catch(() => undefined)
                    : workspaceMutation && !response.ok
                        ? workspaceSave ? await response.text()
                            : await response.json().catch(() => undefined)
                    : canvasNewTab && !response.ok ? await response.text() : undefined,
            });
            if (options.discardResponse || workspaceRename) {
                // Void overloads and the legacy rename caller do not decode
                // response bodies that their callers never consumed.
                resolve(undefined);

            } else {
                // The old file editor treated an empty or malformed success
                // body as a successful save and retained its previous mtime.
                resolve(workspaceSave
                    ? await response.json().catch(() => ({}))
                    : await response.json());
            }
        } catch (error) {
            reject(error);
        }
    });
};

/** Text responses stay text, including newline-delimited JSON exports. */
export const textRequest = (config: OpenAPIConfig, options: ApiRequestOptions): CancelablePromise<string> => {
    return new CancelablePromise(async (resolve, reject, onCancel) => {
        try {
            const url = `${config.BASE}${options.url.replace('{id}', encodeURIComponent(options.path!.id))}${options.query ? getQueryString(options.query) : ''}`;
            const headers = await getHeaders(config, options);
            if (onCancel.isCancelled) return;
            const response = await sendRequest(
                { ...config, WITH_CREDENTIALS: true, CREDENTIALS: 'same-origin' },
                options, url, undefined, undefined, headers, onCancel,
            );
            catchErrorCodes(options, {
                url, ok: response.ok, status: response.status,
                statusText: response.statusText, body: undefined,
            });
            resolve(await response.text());
        } catch (error) {
            reject(error);
        }
    });
};
