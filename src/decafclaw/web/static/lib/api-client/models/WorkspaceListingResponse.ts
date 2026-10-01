/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceFileEntry } from './WorkspaceFileEntry';
import type { WorkspaceFolderEntry } from './WorkspaceFolderEntry';
export type WorkspaceListingResponse = {
    folder: string;
    folders: Array<WorkspaceFolderEntry>;
    files: Array<WorkspaceFileEntry>;
};

