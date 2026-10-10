/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export { ApiError } from './core/ApiError';
export { CancelablePromise, CancelError } from './core/CancelablePromise';
export { OpenAPI } from './core/OpenAPI';
export type { OpenAPIConfig } from './core/OpenAPI';

export type { AttachmentResponse } from './models/AttachmentResponse';
export type { AutocompleteResponse } from './models/AutocompleteResponse';
export type { CanvasMutationResponse } from './models/CanvasMutationResponse';
export type { CanvasNewTabResponse } from './models/CanvasNewTabResponse';
export type { CanvasStateResponse } from './models/CanvasStateResponse';
export type { CanvasTabResponse } from './models/CanvasTabResponse';
export { ConfigFileEntry } from './models/ConfigFileEntry';
export type { ConfigFileResponse } from './models/ConfigFileResponse';
export type { ConfigWriteResponse } from './models/ConfigWriteResponse';
export type { ContextCandidate } from './models/ContextCandidate';
export type { ContextDiagnosticsResponse } from './models/ContextDiagnosticsResponse';
export type { ContextMatch } from './models/ContextMatch';
export type { ContextRawResponse } from './models/ContextRawResponse';
export type { ContextSource } from './models/ContextSource';
export type { ContextSourceDetails } from './models/ContextSourceDetails';
export type { ConversationCreateResponse } from './models/ConversationCreateResponse';
export type { ConversationFolderCreateResponse } from './models/ConversationFolderCreateResponse';
export type { ConversationFolderEntry } from './models/ConversationFolderEntry';
export type { ConversationFolderResponse } from './models/ConversationFolderResponse';
export type { ConversationLifecycleResponse } from './models/ConversationLifecycleResponse';
export type { ConversationListingItem } from './models/ConversationListingItem';
export type { ConversationListingResponse } from './models/ConversationListingResponse';
export type { ConversationPatchResponse } from './models/ConversationPatchResponse';
export { FileCompletion } from './models/FileCompletion';
export type { HTTPValidationError } from './models/HTTPValidationError';
export type { JsonValue } from './models/JsonValue';
export type { LoginResponse } from './models/LoginResponse';
export type { LogoutResponse } from './models/LogoutResponse';
export { McpCompletion } from './models/McpCompletion';
export type { ModelListResponse } from './models/ModelListResponse';
export type { NotificationCountResponse } from './models/NotificationCountResponse';
export type { NotificationListResponse } from './models/NotificationListResponse';
export type { NotificationReadResponse } from './models/NotificationReadResponse';
export type { NotificationResponse } from './models/NotificationResponse';
export type { ScheduleDetailResponse } from './models/ScheduleDetailResponse';
export type { ScheduleListResponse } from './models/ScheduleListResponse';
export type { ScheduleResetResponse } from './models/ScheduleResetResponse';
export { ScheduleResponse } from './models/ScheduleResponse';
export type { ScheduleRunResponse } from './models/ScheduleRunResponse';
export type { ScheduleUpdateResponse } from './models/ScheduleUpdateResponse';
export type { StickyResponse } from './models/StickyResponse';
export type { SystemConversationListingItem } from './models/SystemConversationListingItem';
export type { SystemConversationListingResponse } from './models/SystemConversationListingResponse';
export type { UserResponse } from './models/UserResponse';
export type { ValidationError } from './models/ValidationError';
export { VaultCompletion } from './models/VaultCompletion';
export type { VaultCreateResponse } from './models/VaultCreateResponse';
export type { VaultDeleteResponse } from './models/VaultDeleteResponse';
export type { VaultFolderCreateResponse } from './models/VaultFolderCreateResponse';
export type { VaultFolderEntry } from './models/VaultFolderEntry';
export type { VaultListingResponse } from './models/VaultListingResponse';
export type { VaultPageListEntry } from './models/VaultPageListEntry';
export type { VaultPageResponse } from './models/VaultPageResponse';
export type { VaultRecentResponse } from './models/VaultRecentResponse';
export type { VaultTagEntry } from './models/VaultTagEntry';
export type { VaultTagsResponse } from './models/VaultTagsResponse';
export type { VaultWriteResponse } from './models/VaultWriteResponse';
export type { VirtualConversationFolderEntry } from './models/VirtualConversationFolderEntry';
export type { WidgetCatalogResponse } from './models/WidgetCatalogResponse';
export type { WidgetDescriptorResponse } from './models/WidgetDescriptorResponse';
export type { WorkspaceDeleteResponse } from './models/WorkspaceDeleteResponse';
export { WorkspaceFileEntry } from './models/WorkspaceFileEntry';
export type { WorkspaceFolderEntry } from './models/WorkspaceFolderEntry';
export type { WorkspaceListingResponse } from './models/WorkspaceListingResponse';
export type { WorkspaceRecentResponse } from './models/WorkspaceRecentResponse';
export type { WorkspaceTextResponse } from './models/WorkspaceTextResponse';
export type { WorkspaceWriteResponse } from './models/WorkspaceWriteResponse';

export { DefaultService } from './services/DefaultService';
import { DefaultService as __DefaultService } from './services/DefaultService';

type NativeWorkspacePath = Parameters<
    typeof __DefaultService.wrapperApiWorkspacePathGet
>[0];

/** Build the native workspace URL while retaining each caller's URL semantics. */
export const buildNativeWorkspaceUrl = (
    path: NativeWorkspacePath,
    mode: 'segments' | 'raw' = 'segments',
): string => {
    const pathText = String(path);
    const suffix = mode === 'raw'
        ? pathText
        : pathText.split('/').map(encodeURIComponent).join('/');
    return `/api/workspace/${suffix}`;
};

