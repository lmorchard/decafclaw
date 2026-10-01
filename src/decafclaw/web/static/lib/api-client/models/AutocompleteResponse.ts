/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileCompletion } from './FileCompletion';
import type { McpCompletion } from './McpCompletion';
import type { VaultCompletion } from './VaultCompletion';
export type AutocompleteResponse = {
    results: Array<(VaultCompletion | McpCompletion | FileCompletion)>;
};

