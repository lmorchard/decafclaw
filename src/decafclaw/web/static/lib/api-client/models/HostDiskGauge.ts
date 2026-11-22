/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One filesystem's capacity, using df's used/(used+available) ratio.
 */
export type HostDiskGauge = {
    path: string;
    total_bytes: number;
    used_bytes: number;
    available_bytes: number;
    used_percent: number;
};

