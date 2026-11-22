/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Memory and swap in bytes.
 *
 * ``available_bytes`` is ``MemAvailable`` — reclaimable cache included —
 * rather than ``MemFree``, which reads alarmingly low on a healthy box.
 */
export type HostMemoryGauge = {
    total_bytes: number;
    available_bytes: number;
    used_bytes: number;
    used_percent: number;
    swap_total_bytes: number;
    swap_used_bytes: number;
    swap_used_percent: number;
};

