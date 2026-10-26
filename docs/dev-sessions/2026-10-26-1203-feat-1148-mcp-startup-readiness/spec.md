# Specification: MCP Startup & Readiness Lifecycle (#1148)

## Problem

DecafClaw connects MCP servers concurrently, but `runner.py` awaits initialization of all MCP servers before starting HTTP and Mattermost transports. A slow or hanging MCP handshake delays access to interactive chat and status UI even when other subsystems and tools are healthy.

## Requirements

1. **Non-blocking Interactive Startup**: Start interactive transports (HTTP, chat status, Mattermost) independently of MCP connection progress. Unrelated tools and capabilities must remain usable before slow handshakes complete.
2. **State & Event Tracking**: Track `connecting`, `connected` (ready), `failed`, and `disconnected` states on `MCPServerState`. Expose states via `mcp_server_connecting`, `mcp_server_connected`, `mcp_server_failed`, and `mcp_server_disconnected` events and status tools (`mcp_status`, `get_mcp_data`).
3. **Turn-boundary Late Tool Registration**: A late-connecting MCP server registers its tools at the next eligible turn boundary. In-flight turns freeze their tool availability snapshot to maintain execution consistency.
4. **Required-Server Readiness for Scheduled/Unattended Tasks**: Scheduled tasks can opt into declaring `required-servers` (with optional `server-timeout`). Tasks wait only for their declared dependencies; unrelated slow servers do not block execution. Explicit failure is reported on deadline timeout, connection failure, or unconfigured dependencies.
5. **Lifecycle Ownership & Leak Prevention**: `MCPRegistry` owns all background connection tasks. Shutdown (`shutdown_mcp`, `disconnect_all`) and reload (`_restart`) cancel and drain in-flight connection tasks, prevent late registration from obsolete generations, and clean up transport processes without warnings.
6. **Deterministic Testing**: Implement lifecycle tests using events rather than fixed sleeps, covering fast, slow, failed, canceled, and late-completing connections in `tests/test_mcp_client.py` and `tests/test_runner.py`.
