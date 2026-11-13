/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One active preset surfaced in the per-conversation approval panel (#1159).
 */
export type ConversationShellApprovalPresetItem = {
    name: string;
    source: ConversationShellApprovalPresetItem.source;
    rules?: Array<string>;
    activated_via?: Array<string>;
    active?: boolean;
};
export namespace ConversationShellApprovalPresetItem {
    export enum source {
        BUILTIN = 'builtin',
        CONFIG = 'config',
        CUSTOM = 'custom',
    }
}

