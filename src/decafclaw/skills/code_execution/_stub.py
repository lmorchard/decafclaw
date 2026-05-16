"""Generator for the in-sandbox `decafclaw_tools.py` proxy module.

The subprocess runs an isolated copy of Python with no access to the host
process. To call back into decafclaw it imports the generated module, which
opens a Unix-domain-socket connection to the host and ships JSON-line RPC
requests for each `dc.<tool>(...)` call.

The accessor list is generated from the allowlist so an LLM running
`dir(dc)` in the sandbox sees exactly the tools it can call.
"""


_STUB_TEMPLATE = '''"""Generated proxy module for the decafclaw code-execution sandbox."""

import json
import os
import socket
import threading
from dataclasses import dataclass

_SOCKET_PATH = os.environ["DECAFCLAW_RPC_SOCKET"]
_lock = threading.Lock()
_sock = None
_rfile = None


def _connect():
    global _sock, _rfile
    if _sock is None:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(_SOCKET_PATH)
        _rfile = _sock.makefile("rb")


@dataclass
class ToolResultProxy:
    text: str = ""
    data: dict | None = None
    error: str | None = None


def _call(tool: str, args: dict) -> ToolResultProxy:
    with _lock:
        _connect()
        _sock.sendall((json.dumps({{"tool": tool, "args": args}}) + "\\n").encode())
        line = _rfile.readline()
        if not line:
            return ToolResultProxy(error="rpc connection closed")
        resp = json.loads(line)
        return ToolResultProxy(
            text=resp.get("text", "") or "",
            data=resp.get("data"),
            error=resp.get("error"),
        )


class _DCNamespace:
    """`dc.<tool_name>(**kwargs)` -> ToolResultProxy."""
    {accessors}


dc = _DCNamespace()
'''


def generate_stub_source(allowed: tuple[str, ...], *, sock_path: str) -> str:
    """Render the proxy module source.

    `sock_path` is informational only; the actual socket path is read from
    `DECAFCLAW_RPC_SOCKET` at runtime so the same generated file can be
    inspected without knowing where it would have connected.
    """
    accessors = "\n    ".join(
        f"def {name}(self, **kwargs): return _call({name!r}, kwargs)"
        for name in allowed
    )
    return _STUB_TEMPLATE.format(accessors=accessors)
