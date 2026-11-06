/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogProviderEntry } from './CatalogProviderEntry';
/**
 * GET/POST /api/models/catalog* — discovery state + UI-managed flags.
 */
export type ModelCatalogResponse = {
    default_model: string;
    default_pointer: string;
    picker_models: Array<string>;
    providers: Record<string, CatalogProviderEntry>;
    aux_approval_model?: string;
    aux_approval_pointer?: string;
};

