"""Shell command details survive projection into the web event stream."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.web.websocket import _subscribe_to_conv


@pytest.mark.asyncio
async def test_shell_start_forwards_command(config):
    manager = MagicMock()
    ws_send = AsyncMock()
    _subscribe_to_conv({"manager": manager, "ws_send": ws_send, "config": config}, "conv1")
    callback = manager.subscribe.call_args.args[1]
    await callback(
        {
            "type": "tool_start",
            "conv_id": "conv1",
            "tool": "shell",
            "tool_call_id": "tc1",
            "args": {"command": "echo hello"},
        }
    )
    assert ws_send.call_args.args[0]["command"] == "echo hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [None, [], "command", 42, {"command": None}, {"command": []}])
async def test_shell_start_forwards_malformed_arguments_without_command(config, args):
    manager = MagicMock()
    ws_send = AsyncMock()
    _subscribe_to_conv({"manager": manager, "ws_send": ws_send, "config": config}, "conv1")
    callback = manager.subscribe.call_args.args[1]
    await callback({"type": "tool_start", "conv_id": "conv1", "tool": "shell", "tool_call_id": "tc1", "args": args})
    assert ws_send.call_args.args[0]["command"] == ""
