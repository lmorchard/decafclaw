/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UserResponse } from '../models/UserResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Health
     * Liveness probe — returns the static health snapshot.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static healthHealthGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * Handle Confirm
     * Handle Mattermost interactive button callbacks for tool confirmation.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static handleConfirmActionsConfirmPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/actions/confirm',
        });
    }
    /**
     * Handle Cancel
     * Handle Mattermost interactive button callback for stop/cancel.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static handleCancelActionsCancelPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/actions/cancel',
        });
    }
    /**
     * Auth Login
     * Validate a one-time login token, then set the session cookie.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authLoginApiAuthLoginPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/login',
        });
    }
    /**
     * Auth Logout
     * Clear the session cookie.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authLogoutApiAuthLogoutPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/logout',
        });
    }
    /**
     * Auth Me
     * Return the current authenticated user.
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static authMeApiAuthMeGet(): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/me',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/conversations',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsArchivedGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/archived',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsSystemGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/system',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/{id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdDelete(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/conversations/{id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdPatch(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/conversations/{id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdHistoryGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/{id}/history',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdContextGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/{id}/context',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdExportGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/{id}/export',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsFoldersPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/conversations/folders',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsFoldersPathPut(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/conversations/folders/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsFoldersPathDelete(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/conversations/folders/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdArchivePost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/conversations/{id}/archive',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConversationsIdUnarchivePost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/conversations/{id}/unarchive',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiNotificationsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/notifications',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiNotificationsUnreadCountGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/notifications/unread-count',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiNotificationsReadAllPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/notifications/read-all',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiNotificationsIdReadPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/notifications/{id}/read',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiUploadConvIdPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/upload/{conv_id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspaceGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspace',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspacePost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workspace',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspaceRecentGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspace/recent',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiAutocompleteGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/autocomplete',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspaceFilePathGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspace-file/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspacePathGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspace/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspacePathPut(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/workspace/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWorkspacePathDelete(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/workspace/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConfigFilesGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/config/files',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConfigFilesPathGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/config/files/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiConfigFilesPathPut(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/config/files/{path}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiModelsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/models',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiSchedulesGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/schedules',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiSchedulesNameRunPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/schedules/{name}/run',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiSchedulesNameOverlayDelete(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/schedules/{name}/overlay',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiSchedulesNameGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/schedules/{name}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiSchedulesNamePut(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/schedules/{name}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/vault',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/vault',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultFoldersPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/vault/folders',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultRecentGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/vault/recent',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultTagsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/vault/tags',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultPageGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/vault/{page}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultPagePut(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/vault/{page}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiVaultPageDelete(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/vault/{page}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperVaultPageGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/vault/{page}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWikiGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/wiki',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWikiPageGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/wiki/{page}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiWidgetsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/widgets',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperWidgetsTierNameWidgetJsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/widgets/{tier}/{name}/widget.js',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiCanvasConvIdGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/canvas/{conv_id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiStickyConvIdGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/sticky/{conv_id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiCanvasConvIdNewTabPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/canvas/{conv_id}/new_tab',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiCanvasConvIdActiveTabPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/canvas/{conv_id}/active_tab',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperApiCanvasConvIdCloseTabPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/canvas/{conv_id}/close_tab',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperCanvasConvIdGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/canvas/{conv_id}',
        });
    }
    /**
     * Wrapper
     * @returns any Successful Response
     * @throws ApiError
     */
    public static wrapperCanvasConvIdTabIdGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/canvas/{conv_id}/{tab_id}',
        });
    }
    /**
     * Serve Index
     * @returns any Successful Response
     * @throws ApiError
     */
    public static serveIndexGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/',
        });
    }
}
