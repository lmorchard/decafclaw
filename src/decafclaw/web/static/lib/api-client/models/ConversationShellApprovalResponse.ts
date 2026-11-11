/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationShellApprovalPresetItem } from './ConversationShellApprovalPresetItem';
/**
 * Per-conversation shell auto-approval view (#1159).
 *
 * ``conversation_rules`` are the conversation's editable "remember why"
 * rules; ``presets`` are the active (not disabled) presets with their source
 * and activation path; ``config_guidance`` is the always-on configured
 * guidance; ``cached_patterns`` are the reviewer's auto-approved command
 * patterns for this conversation.
 */
export type ConversationShellApprovalResponse = {
    conv_id: string;
    status?: string;
    conversation_rules?: Array<string>;
    presets?: Array<ConversationShellApprovalPresetItem>;
    config_guidance?: string;
    cached_patterns?: Array<string>;
};

