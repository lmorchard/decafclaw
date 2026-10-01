/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContextCandidate } from './ContextCandidate';
import type { ContextSource } from './ContextSource';
export type ContextDiagnosticsResponse = {
    sources?: (Array<ContextSource> | null);
    memory_candidates?: (Array<ContextCandidate> | null);
    total_tokens_estimated?: (number | null);
    total_tokens_actual?: (number | null);
    cached_prompt_tokens?: (number | null);
    cache_hit_rate?: (number | null);
    context_window_size?: (number | null);
    compaction_threshold?: (number | null);
};

