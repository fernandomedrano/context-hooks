"""Minimal MCP stdio server — JSON-RPC 2.0 with Content-Length framing.

Zero external dependencies. Knows nothing about knowledge/memos/etc.
Tools register via register_tool() and the server dispatches calls.
"""
import json
import sys


class MCPServer:
    """MCP protocol handler with tool registry."""

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self._tools = {}

    def register_tool(self, *, name, description, input_schema, handler):
        """Register a tool. handler(args: dict) -> str"""
        self._tools[name] = {
            "description": description,
            "inputSchema": input_schema,
            "handler": handler,
        }

    def run(self, *, stdin=None, stdout=None):
        """Main loop: read JSON-RPC messages, dispatch, respond."""
        _in = stdin or sys.stdin
        _out = stdout or sys.stdout

        while True:
            message = self._read_message(_in)
            if message is None:
                break
            response = self._handle(message)
            if response is not None:
                self._write_message(response, _out)

    def _read_message(self, stream):
        """Read a Content-Length framed JSON-RPC message."""
        headers = {}
        while True:
            line = stream.readline()
            if not line:
                return None
            line = line.strip()
            if line == "":
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()

        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return None

        body = stream.read(content_length)
        if not body:
            return None

        return json.loads(body)

    def _write_message(self, obj, stream):
        """Write a Content-Length framed JSON-RPC response."""
        body = json.dumps(obj)
        stream.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
        stream.flush()

    def _handle(self, message):
        """Dispatch a JSON-RPC message. Returns response dict or None."""
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        if msg_id is None:
            return None

        if method == "initialize":
            return self._respond(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })

        if method == "ping":
            return self._respond(msg_id, {})

        if method == "tools/list":
            tools = [
                {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                for name, t in self._tools.items()
            ]
            return self._respond(msg_id, {"tools": tools})

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in self._tools:
                return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")

            tool = self._tools[tool_name]
            try:
                result_text = tool["handler"](arguments)
                return self._respond(msg_id, {
                    "content": [{"type": "text", "text": str(result_text)}]
                })
            except Exception as e:
                return self._respond(msg_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Error: {e}"}]
                })

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _respond(self, msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _error(self, msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
