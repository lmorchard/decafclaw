/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ShellPresetItem = {
    name: string;
    rules: Array<string>;
    source: ShellPresetItem.source;
    shadows_config?: boolean;
    referenced_by?: Array<string>;
};
export namespace ShellPresetItem {
    export enum source {
        BUILTIN = 'builtin',
        CONFIG = 'config',
        CUSTOM = 'custom',
    }
}

