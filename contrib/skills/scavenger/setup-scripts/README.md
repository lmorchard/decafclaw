# Bundled Scavenger Native Setup Scripts

Idempotent setup scripts for configuring scoped workspaces on native OS nodes (Apple Silicon under macOS Darwin).

## Guidelines for Native Setup Scripts

- Must be strictly idempotent: safe to run multiple times without re-downloading or erroring if dependencies are already satisfied.
- Must stay contained within the caller's workspace (`$PWD` or `~scavenger/workspaces/<env_id>`).
- Must not touch operator home directories or require `sudo`.
