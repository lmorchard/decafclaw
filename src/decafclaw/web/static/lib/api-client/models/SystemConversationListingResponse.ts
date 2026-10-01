/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationFolderEntry } from './ConversationFolderEntry';
import type { SystemConversationListingItem } from './SystemConversationListingItem';
export type SystemConversationListingResponse = {
    folder: string;
    folders: Array<ConversationFolderEntry>;
    conversations: Array<SystemConversationListingItem>;
};

