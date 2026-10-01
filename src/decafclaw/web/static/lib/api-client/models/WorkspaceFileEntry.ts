/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WorkspaceFileEntry = {
    name: string;
    path: string;
    size: number;
    modified: number;
    kind: WorkspaceFileEntry.kind;
    readonly: boolean;
    secret: boolean;
};
export namespace WorkspaceFileEntry {
    export enum kind {
        TEXT = 'text',
        IMAGE = 'image',
        BINARY = 'binary',
    }
}

