/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue';
export type WidgetDescriptorResponse = {
    name: string;
    tier: string;
    description: string;
    modes: Array<string>;
    accepts_input: boolean;
    data_schema: Record<string, JsonValue>;
    js_url: string;
};

