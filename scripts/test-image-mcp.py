"""Minimal MCP server that returns a test image. For testing media attachments."""

import base64
import io
import json
import sys


def _make_test_png(width=200, height=200):
    """Generate a simple colored PNG using pure Python (no PIL needed)."""
    import struct
    import zlib

    # Build raw pixel data: red/blue gradient
    raw = []
    for y in range(height):
        raw.append(0)  # filter byte: None
        for x in range(width):
            r = int(255 * x / width)
            g = 50
            b = int(255 * y / height)
            raw.extend([r, g, b])

    raw_bytes = bytes(raw)
    compressed = zlib.compress(raw_bytes)

    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")
    return png


TEST_PNG = base64.b64encode(_make_test_png()).decode()


def send(msg):
    out = json.dumps(msg)
    sys.stdout.write(out + "\n")
    sys.stdout.flush()


def main():
    # Read JSON-RPC messages from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "test-image-mcp", "version": "0.1.0"},
                },
            })
        elif method == "notifications/initialized":
            pass  # no response needed
        elif method == "resources/list":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "resources": [
                        {
                            "uri": "test://gradient.png",
                            "name": "Test gradient image",
                            "description": "A 200x200 red-blue gradient PNG for testing",
                            "mimeType": "image/png",
                        },
                    ],
                },
            })
        elif method == "resources/templates/list":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"resourceTemplates": []},
            })
        elif method == "resources/read":
            uri = msg.get("params", {}).get("uri", "")
            if uri == "test://gradient.png":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "contents": [
                            {
                                "uri": "test://gradient.png",
                                "mimeType": "image/png",
                                "blob": TEST_PNG,
                            },
                        ],
                    },
                })
            else:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": f"Unknown resource: {uri}"},
                })
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "generate_test_image",
                            "description": "Generate a test image (a tiny red pixel PNG). Use this to test image attachments.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    ],
                },
            })
        elif method == "tools/call":
            tool_name = msg.get("params", {}).get("name", "")
            if tool_name == "generate_test_image":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": "Here's a test image (200x200 red-blue gradient):"},
                            {"type": "image", "data": TEST_PNG, "mimeType": "image/png"},
                        ],
                    },
                })
            else:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                })
        else:
            if msg_id is not None:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                })


if __name__ == "__main__":
    main()
