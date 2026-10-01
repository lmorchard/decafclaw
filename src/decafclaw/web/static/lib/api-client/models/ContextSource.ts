/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContextSourceDetails } from './ContextSourceDetails';
export type ContextSource = {
    source: string;
    tokens_estimated?: (number | null);
    items_included?: (number | null);
    items_truncated?: (number | null);
    details?: (ContextSourceDetails | null);
};

