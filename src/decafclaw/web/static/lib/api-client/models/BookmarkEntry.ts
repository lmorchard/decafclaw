/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BookmarkEntry = {
    path: string;
    label: string;
    kind: BookmarkEntry.kind;
};
export namespace BookmarkEntry {
    export enum kind {
        FILE = 'file',
        DIR = 'dir',
        PAGE = 'page',
        FOLDER = 'folder',
    }
}

