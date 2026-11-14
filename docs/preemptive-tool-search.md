# Pre-emptive Tool Search

When a user message arrives, DecafClaw matches its keywords against tool names and descriptions. Deferred matches appear in an archived hint after the user message. The hint directs the agent to `tool_search` before calling a deferred tool.

Keyword matches do not change native tool schemas, the deferred catalog, or tool guidance (#1257). This preserves the prompt prefix across ordinary turns. Explicit `tool_search` fetches still load tools and deliberately change the prefix.

## Matching

`ContextComposer._compose_preempt_matches` uses the raw user message. It excludes prior replies and injected context (#1049).

1. Lowercase the input and split it at non-alphanumeric boundaries.
2. Remove short tokens and common English stopwords.
3. Score each candidate by the count of shared tokens in its name and description.
4. Sort by decreasing score, then tool name. Keep at most `max_matches` candidates.
5. After normal tool classification, render a hint for matches that remain deferred.

The hint excludes disallowed tools and hidden skill tools. Already active tools need no hint. Hidden tools remain discoverable through their owning skill.

The hint uses `<preempt_tool_hint>` and the internal `skill_hint` archive role. It maps to `user` for the LLM, remains at its original history position, and stays hidden in the UI.

## Skill catalog hints

The composer also matches non-activated skills against the user message. `<preempt_skill_hint>` names the strongest matches and directs the agent to `activate_skill`.

The visible skill hint keeps candidates scoring at least `max(2, top_score - 1)`, with at least one result. This keeps weak matches from obscuring a clear winner (#604). Activation still follows the existing trust rules.

Both types of hint are suggestions. They do not activate skills or grant capabilities.

## Configuration

Under `agent.preemptive_search`:

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Enable tool and skill discovery hints |
| `max_matches` | `10` | Limit candidates for each keyword match |

There are no environment aliases for these fields. Set `enabled: false` to disable both hint passes.

Matching runs in composer-driven interactive, heartbeat, scheduled, and child-agent turns. Reflection and compaction use their own tool sets.

## Diagnostics

INFO logs identify suggested tools and matched skills. The context sidecar records `preempt_matches` and `preempt_skill_matches` sources. Tool matches include `input_tokens`, candidate `matches`, and the deferred tools actually `suggested`. The token estimate covers the rendered hint rather than schemas that were never loaded.

Matches reset each turn. Archived hints remain in history. Explicitly fetched tools remain loaded through the existing fetched-tool state.

## Related

- [Prompt prefix stability](context-composer.md#prompt-caching-prefix-stability)
- [Tool priority](tool-priority.md)
- [Tool search](tool-search.md)
- [Skills](skills.md)
