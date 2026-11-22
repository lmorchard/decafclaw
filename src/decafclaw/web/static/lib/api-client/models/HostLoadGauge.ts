/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Load averages plus the runnable/total task counts from /proc/loadavg.
 */
export type HostLoadGauge = {
    one: number;
    five: number;
    fifteen: number;
    running?: (number | null);
    total?: (number | null);
};

