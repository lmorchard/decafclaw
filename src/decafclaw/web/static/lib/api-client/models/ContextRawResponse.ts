/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContextSource } from './ContextSource';
import type { JsonValue } from './JsonValue';
export type ContextRawResponse = {
    model?: (string | null);
    messages?: (Array<Record<string, JsonValue>> | null);
    tools?: (Array<Record<string, JsonValue>> | null);
    sources?: (Array<ContextSource> | null);
};

