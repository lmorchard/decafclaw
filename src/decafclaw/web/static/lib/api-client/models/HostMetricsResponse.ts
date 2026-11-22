/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HostDiskGauge } from './HostDiskGauge';
import type { HostLoadGauge } from './HostLoadGauge';
import type { HostMemoryGauge } from './HostMemoryGauge';
/**
 * Capacity and load for the VM DecafClaw runs on.
 *
 * Every field is optional: each source degrades independently, so an
 * unreadable /proc file nulls its own gauge instead of failing the response.
 */
export type HostMetricsResponse = {
    timestamp: number;
    cpu_count?: (number | null);
    cpu_percent?: (number | null);
    memory?: (HostMemoryGauge | null);
    load?: (HostLoadGauge | null);
    disk_root?: (HostDiskGauge | null);
    disk_tmpfs?: (HostDiskGauge | null);
    process_count?: (number | null);
    uptime_seconds?: (number | null);
};

