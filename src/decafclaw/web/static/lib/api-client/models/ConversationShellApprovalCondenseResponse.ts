/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The condensation proposal (#1267). Never mutates state — the rules land
 * on disk only when the user reviews and saves them through the replace
 * endpoint.
 */
export type ConversationShellApprovalCondenseResponse = {
    ok: boolean;
    conv_id: string;
    rules: Array<string>;
};

