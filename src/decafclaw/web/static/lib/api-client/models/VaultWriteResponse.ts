/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue';
export type VaultWriteResponse = {
    ok: boolean;
    modified: number;
    frontmatter?: (Record<string, JsonValue> | null);
    frontmatter_raw?: (string | null);
    frontmatter_error?: (string | null);
    title?: (string | null);
    path?: (string | null);
    folder?: (string | null);
};

