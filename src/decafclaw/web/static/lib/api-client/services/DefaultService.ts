/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationCreateResponse } from '../models/ConversationCreateResponse';
import type { ConversationFolderCreateResponse } from '../models/ConversationFolderCreateResponse';
import type { ConversationFolderResponse } from '../models/ConversationFolderResponse';
import type { ConversationLifecycleResponse } from '../models/ConversationLifecycleResponse';
import type { ConversationListingResponse } from '../models/ConversationListingResponse';
import type { ConversationPatchResponse } from '../models/ConversationPatchResponse';
import type { StickyResponse } from '../models/StickyResponse';
import type { SystemConversationListingResponse } from '../models/SystemConversationListingResponse';
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
     * Metrics Endpoint
     * Prometheus text format metrics endpoint.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static metricsEndpointMetricsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/metrics',
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
     * List Conversations
     * List conversations and subfolders for a specific folder.
     *
     * Query params:
     * folder — folder path (default: top-level)
     *
     * Returns ``{folder, folders, conversations}`` mirroring vault_list pattern.
     * @param folder
     * @returns ConversationListingResponse Successful Response
     * @throws ApiError
     */
    public static listConversationsApiConversationsGet(
        folder: string = '',
    ): CancelablePromise<ConversationListingResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations',
            query: {
                'folder': folder,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Conversation
     * Create a new conversation, optionally in a folder with a model.
     * @param requestBody
     * @returns ConversationCreateResponse Successful Response
     * @throws ApiError
     */
    public static createConversationApiConversationsPost(
        requestBody: {
            title?: string;
            model?: string;
            folder?: string;
            effort?: string;
        },
    ): CancelablePromise<ConversationCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/conversations',
            body: requestBody,
            mediaType: 'application/json',
        });
    }
    /**
     * List Archived Conversations
     * List archived conversations, optionally filtered by folder.
     * @param folder
     * @returns ConversationListingResponse Successful Response
     * @throws ApiError
     */
    public static listArchivedConversationsApiConversationsArchivedGet(
        folder: string = '',
    ): CancelablePromise<ConversationListingResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/archived',
            query: {
                'folder': folder,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List System Conversations
     * List system conversations, grouped by type sub-folders.
     * @param folder
     * @returns SystemConversationListingResponse Successful Response
     * @throws ApiError
     */
    public static listSystemConversationsApiConversationsSystemGet(
        folder: string = '',
    ): CancelablePromise<SystemConversationListingResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/conversations/system',
            query: {
                'folder': folder,
            },
            errors: {
                422: `Validation Error`,
            },
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
     * Rename Conversation
     * Rename and/or move a conversation to a different folder.
     * @param id
     * @param requestBody
     * @returns ConversationPatchResponse Successful Response
     * @throws ApiError
     */
    public static renameConversationApiConversationsIdPatch(
        id: string,
        requestBody: {
            title?: (string | null);
            folder?: (string | null);
        },
    ): CancelablePromise<ConversationPatchResponse>;
    public static renameConversationApiConversationsIdPatch(
        id: string,
        requestBody: {
            title?: (string | null);
            folder?: (string | null);
        },
        discardResponse: true,
    ): CancelablePromise<void>;
    public static renameConversationApiConversationsIdPatch(
        id: string,
        requestBody: {
            title?: (string | null);
            folder?: (string | null);
        },
        discardResponse = false,
    ): CancelablePromise<ConversationPatchResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'PATCH',
            url: '/api/conversations/{id}',
            path: {
                'id': id,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Conversation
     * Permanently delete a conversation and all associated files.
     * @param id
     * @returns ConversationLifecycleResponse Successful Response
     * @throws ApiError
     */
    public static deleteConversationApiConversationsIdDelete(
        id: string,
    ): CancelablePromise<ConversationLifecycleResponse>;
    public static deleteConversationApiConversationsIdDelete(
        id: string,
        discardResponse: true,
    ): CancelablePromise<void>;
    public static deleteConversationApiConversationsIdDelete(
        id: string,
        discardResponse = false,
    ): CancelablePromise<ConversationLifecycleResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'DELETE',
            url: '/api/conversations/{id}',
            path: {
                'id': id,
            },
            errors: {
                422: `Validation Error`,
            },
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
     * Create Conv Folder
     * Create a conversation folder.
     * @param requestBody
     * @returns ConversationFolderCreateResponse Successful Response
     * @throws ApiError
     */
    public static createConvFolderApiConversationsFoldersPost(
        requestBody: {
            path: string;
        },
    ): CancelablePromise<ConversationFolderCreateResponse>;
    public static createConvFolderApiConversationsFoldersPost(
        requestBody: {
            path: string;
        },
        discardResponse: true,
    ): CancelablePromise<void>;
    public static createConvFolderApiConversationsFoldersPost(
        requestBody: {
            path: string;
        },
        discardResponse = false,
    ): CancelablePromise<ConversationFolderCreateResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'POST',
            url: '/api/conversations/folders',
            body: requestBody,
            mediaType: 'application/json',
        });
    }
    /**
     * Delete Conv Folder
     * Delete an empty conversation folder.
     * @param path
     * @returns ConversationFolderResponse Successful Response
     * @throws ApiError
     */
    public static deleteConvFolderApiConversationsFoldersPathDelete(
        path: string,
    ): CancelablePromise<ConversationFolderResponse>;
    public static deleteConvFolderApiConversationsFoldersPathDelete(
        path: string,
        discardResponse: true,
    ): CancelablePromise<void>;
    public static deleteConvFolderApiConversationsFoldersPathDelete(
        path: string,
        discardResponse = false,
    ): CancelablePromise<ConversationFolderResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'DELETE',
            url: '/api/conversations/folders/{path}',
            path: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Conv Folder
     * Rename/move a conversation folder. Merges if target exists.
     * @param path
     * @param requestBody
     * @returns ConversationFolderResponse Successful Response
     * @throws ApiError
     */
    public static renameConvFolderApiConversationsFoldersPathPut(
        path: string,
        requestBody: {
            path: string;
        },
    ): CancelablePromise<ConversationFolderResponse>;
    public static renameConvFolderApiConversationsFoldersPathPut(
        path: string,
        requestBody: {
            path: string;
        },
        discardResponse: true,
    ): CancelablePromise<void>;
    public static renameConvFolderApiConversationsFoldersPathPut(
        path: string,
        requestBody: {
            path: string;
        },
        discardResponse = false,
    ): CancelablePromise<ConversationFolderResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'PUT',
            url: '/api/conversations/folders/{path}',
            path: {
                'path': path,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Archive Conversation
     * Archive a conversation (hide from list, keep data).
     * @param id
     * @returns ConversationLifecycleResponse Successful Response
     * @throws ApiError
     */
    public static archiveConversationApiConversationsIdArchivePost(
        id: string,
    ): CancelablePromise<ConversationLifecycleResponse>;
    public static archiveConversationApiConversationsIdArchivePost(
        id: string,
        discardResponse: true,
    ): CancelablePromise<void>;
    public static archiveConversationApiConversationsIdArchivePost(
        id: string,
        discardResponse = false,
    ): CancelablePromise<ConversationLifecycleResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'POST',
            url: '/api/conversations/{id}/archive',
            path: {
                'id': id,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Unarchive Conversation
     * Unarchive a conversation (restore to active list).
     * @param id
     * @returns ConversationLifecycleResponse Successful Response
     * @throws ApiError
     */
    public static unarchiveConversationApiConversationsIdUnarchivePost(
        id: string,
    ): CancelablePromise<ConversationLifecycleResponse>;
    public static unarchiveConversationApiConversationsIdUnarchivePost(
        id: string,
        discardResponse: true,
    ): CancelablePromise<void>;
    public static unarchiveConversationApiConversationsIdUnarchivePost(
        id: string,
        discardResponse = false,
    ): CancelablePromise<ConversationLifecycleResponse | void> {
        return __request(OpenAPI, {
            discardResponse,
            method: 'POST',
            url: '/api/conversations/{id}/unarchive',
            path: {
                'id': id,
            },
            errors: {
                422: `Validation Error`,
            },
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
     * Get Sticky State
     * Load current sticky-slot state for a conversation (reload recovery).
     * @param convId
     * @returns StickyResponse Successful Response
     * @throws ApiError
     */
    public static getStickyStateApiStickyConvIdGet(
        convId: string,
    ): CancelablePromise<StickyResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/sticky/{conv_id}',
            path: {
                'conv_id': convId,
            },
            errors: {
                422: `Validation Error`,
            },
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
