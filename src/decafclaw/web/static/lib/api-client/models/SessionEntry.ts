/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SessionConversationItem } from './SessionConversationItem';
/**
 * A session: one root conversation plus its delegated subagent children.
 */
export type SessionEntry = {
    session_id: string;
    title: string;
    created_at?: string;
    updated_at?: string;
    status?: string;
    conversations?: Array<SessionConversationItem>;
};

