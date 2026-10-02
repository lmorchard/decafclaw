/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ScheduleResponse = {
    name: string;
    source_tier: ScheduleResponse.source_tier;
    source_path: string;
    has_overlay: boolean;
    enabled: boolean;
    schedule: string;
    channel: string;
    model: string;
    allowed_tools: Array<string>;
    disallowed_tools: Array<string>;
    required_skills: Array<string>;
    shell_patterns: Array<string>;
    email_recipients: Array<string>;
    pre_script: string;
    unknown_keys: Array<string>;
    frontmatter_raw: string;
    body: string;
    modified: number;
    next_run_iso: (string | null);
    last_run_iso: (string | null);
};
export namespace ScheduleResponse {
    export enum source_tier {
        ADMIN = 'admin',
        WORKSPACE = 'workspace',
        BUNDLED = 'bundled',
        EXTRA = 'extra',
    }
}

