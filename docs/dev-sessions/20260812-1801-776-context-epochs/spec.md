**Concept from opencode:**
`opencode` treats the system prompt not as a monolithic string generated on every turn, but as a collection of independent data sources tracked against a baseline called a **Context Epoch**. It reconciles the new state against the baseline and pushes a mid-conversation system message into the timeline (e.g., *"System update: Skill X is now enabled"*).

**How `decafclaw` could implement this:**
Currently, `ContextComposer` completely regenerates the top-level `<system>` block on every turn. If a dynamic skill is activated or a background schedule changes, the LLM has to implicitly notice the change.

**Proposed Implementation:**
- Introduce a `baseline_system_context` on the `ComposerState`.
- During `ContextComposer.compose()`, if the hash of the active skills or config changes from the baseline, automatically inject a `role: "user"` message (since Claude prefers user messages for steering) that explicitly announces the change: `[System Update: The 'vault' skill tools are now available.]`.

## Verifiable acceptance criteria

- CRITERION: WHEN `config.system_prompt` changes between turns in a single conversation, THE SYSTEM SHALL inject a `role: "user"` message announcing the update (e.g., `[System Update: ...]`) before the user's message.
  CHECK: `pytest tests/test_context_composer.py::test_mid_conversation_system_update` (author a test that calls `compose()` twice with a mutated `config.system_prompt` on the second call and asserts a `[System Update: ...]` message is in the returned `messages`) passes.
  VERIFIED DISCRIMINATING: Fails today (no such message is injected).

- CRITERION: The baseline state SHALL be tracked on `ComposerState` so it is maintained correctly across conversation forks and tool calls.
  CHECK: `pytest tests/test_context_composer.py::test_composer_state_tracks_baseline` (assert that the first turn sets the baseline in `ComposerState`, and subsequent turns only inject the message and update the baseline if it has changed again) passes.
  VERIFIED DISCRIMINATING: Fails today (the test does not exist, and there is no baseline tracking on `ComposerState`).

## Regression guards

- GUARD: `make test` — all existing context composer and agent tests must continue to pass without unintended system messages. Passes today.

## Tier: auto-ok
All criteria are machine-checkable via concrete tests, and this touches the context composer which is central but not risk-gated per se (no credentials, auth, or destructive infra config).