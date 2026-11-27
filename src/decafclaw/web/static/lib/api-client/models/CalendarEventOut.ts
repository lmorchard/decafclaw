/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CalendarEventOut = {
    event_id: string;
    conv_id: string;
    target_conv_id: string;
    session_mode: string;
    session_key?: string;
    session_title?: string;
    title: string;
    reason: string;
    fire_at: string;
    fire_at_ts: number;
    created_at: string;
    updated_at: string;
    status: string;
    last_fired_at?: string;
    snooze_until?: string;
    recurrence?: string;
    timezone?: string;
    recurrence_start?: string;
    occurrence_at?: string;
    recurrence_until?: string;
    allowed_tools?: Array<string>;
    disallowed_tools?: Array<string>;
    shell_patterns?: Array<string>;
    required_skills?: Array<string>;
    policy_granted?: boolean;
    model?: string;
    origin_title?: string;
    target_title?: string;
    recurrence_summary?: string;
};

