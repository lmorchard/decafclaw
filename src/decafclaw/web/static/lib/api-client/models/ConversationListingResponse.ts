/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationFolderEntry } from './ConversationFolderEntry';
import type { ConversationListingItem } from './ConversationListingItem';
import type { VirtualConversationFolderEntry } from './VirtualConversationFolderEntry';
export type ConversationListingResponse = {
    folder: string;
    folders: Array<(ConversationFolderEntry | VirtualConversationFolderEntry)>;
    conversations: Array<ConversationListingItem>;
};

