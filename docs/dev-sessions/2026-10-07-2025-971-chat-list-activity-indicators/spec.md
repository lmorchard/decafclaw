# Spec: Activity Indicators on Chat List Rows in Sidebar (#971)

## Context & Problem
When running multiple conversations or kicking off long-running agent tasks, users in the web UI cannot tell what the agent is currently doing across their conversations without clicking into each conversation individually.

Specifically, the user needs to know at a glance in the sidebar:
1. **Busy**: Is the agent actively working / executing a turn?
2. **Waiting**: Is the agent paused and waiting on an answer / approval from the user (such as a shell approval, tool confirmation, or widget input like `ask_user_*`)?
3. **Finished**: Did a conversation running in the background finish its turn? (Acts like an unread completion signal: appears when a background conversation finishes, and clears once the user selects/reads that conversation; past conversations on load are pre-cleared).
4. **Idle**: Normal state (no turn running, no input waiting, already viewed).

## Goals & Invariants
1. **Real-time visibility**: Connected web clients receive global status events over WebSocket whenever any conversation transitions between `busy`, `waiting`, `finished`, or `idle`.
2. **Accurate initial state**: REST endpoints (`/api/conversations`, `/api/conversations/archived`, `/api/conversations/system`) include the live runtime status (`busy`, `waiting`, or `idle`) for each conversation.
3. **Completion lifecycle**: "Finished" only marks conversations that completed a turn while in the background. Opening / selecting that conversation immediately clears the finished indicator to idle.
4. **A11y & Aesthetics**: Indicators must have clear titles and aria-labels, fit within sidebar rows without breaking layout, and preserve existing keyboard navigation contracts (`sidebar-row-a11y.test.js`).
