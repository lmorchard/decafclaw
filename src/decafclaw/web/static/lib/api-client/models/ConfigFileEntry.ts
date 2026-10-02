/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ConfigFileEntry = {
    name: string;
    path: string;
    description: string;
    scope: ConfigFileEntry.scope;
    modified: (number | null);
    exists: boolean;
};
export namespace ConfigFileEntry {
    export enum scope {
        ADMIN = 'admin',
        WORKSPACE = 'workspace',
    }
}

