# decafclaw-tui

A Text User Interface (TUI) client for `decafclaw`. It connects to a running agent instance via WebSockets and allows interacting with the agent from a terminal.

## Features

- **Conversation Picker:** Shows a list of recent conversations, allowing you to select one or start a new one.
- **Chat Interface:** Provides a simple chat layout with message history and a text input prompt.
- **Confirmation Modals:** Securely prompts the user for tool approvals (e.g., executing commands) via modal overlays that block other input.
- **Tab Switching:** Use `Tab` to switch focus between the conversation picker (sidebar) and the chat log. (Auto-hides the conversation picker once you've selected a conversation; type `/resume` or press `Tab` in the chat to summon the picker back.)

## Prerequisites

- Node.js (v18+)
- `decafclaw` server running (`make dev` or `make run`, starts on `http://localhost:18880`)

## Installation

Run `npm install` in this directory to install the dependencies.

## Running the TUI

You can run the TUI in development mode:

```bash
npm run dev -- --token <your-session-token>
```

Alternatively, you can provide the token via the `DECAFCLAW_TOKEN` environment variable.

The TUI connects to `http://localhost:18880` by default. If your server is running on a different port/host, you can provide the `--host` parameter or the `DECAFCLAW_HOST` environment variable:

```bash
npm run dev -- --token <your-token> --host http://localhost:8088
```

## Shortcuts

- `Up` / `Down`: Navigate the conversation list.
- `Enter`: Select a conversation (or create a new one if `[new]` is selected).
- `n` / `N`: Create a new conversation (when conversation picker is focused).
- `Tab`: Toggle between the conversation picker and the chat log.
- `Ctrl+C`: Abort a running turn, or exit if no turn is running.

## Development

The UI is built with [React](https://reactjs.org/) and [Ink](https://github.com/vadimdemedes/ink).

- Run `npm run test` to run the component and unit tests using Vitest.
