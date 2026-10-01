/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CanvasTabResponse } from './CanvasTabResponse';
export type CanvasStateResponse = {
    schema_version: number;
    active_tab: (string | null);
    next_tab_id: number;
    tabs: Array<CanvasTabResponse>;
};

