"""Tests for lib/mcp.py — MCP JSON-RPC protocol shim."""
import io
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.mcp import MCPServer


def make_message(obj):
    """Encode a JSON-RPC message with Content-Length framing."""
    body = json.dumps(obj)
    return f"Content-Length: {len(body)}\r\n\r\n{body}"


def send_and_receive(server, messages):
    """Send framed messages to server, collect responses."""
    input_data = "".join(make_message(m) for m in messages)
    stdin = io.StringIO(input_data)
    stdout = io.StringIO()
    server.run(stdin=stdin, stdout=stdout)
    output = stdout.getvalue()
    responses = []
    while output:
        if not output.startswith("Content-Length:"):
            break
        header_end = output.index("\r\n\r\n")
        length = int(output[len("Content-Length: "):header_end])
        body_start = header_end + 4
        body = output[body_start:body_start + length]
        responses.append(json.loads(body))
        output = output[body_start + length:]
    return responses


class TestMCPServer:
    def setup_method(self):
        self.server = MCPServer("test-server", "0.1.0")

    def test_initialize(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        responses = send_and_receive(self.server, msgs)
        assert len(responses) == 1
        r = responses[0]
        assert r["id"] == 1
        assert r["result"]["serverInfo"]["name"] == "test-server"
        assert "tools" in r["result"]["capabilities"]

    def test_tools_list_empty(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        tools_resp = responses[1]
        assert tools_resp["id"] == 2
        assert tools_resp["result"]["tools"] == []

    def test_tools_list_with_registered_tool(self):
        self.server.register_tool(
            name="echo",
            description="Echo back the input",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=lambda args: args["text"]
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        tools = responses[1]["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert tools[0]["description"] == "Echo back the input"

    def test_tools_call_success(self):
        self.server.register_tool(
            name="greet",
            description="Greet someone",
            input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=lambda args: f"Hello, {args['name']}!"
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "greet", "arguments": {"name": "World"}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["id"] == 2
        assert r["result"]["content"][0]["type"] == "text"
        assert r["result"]["content"][0]["text"] == "Hello, World!"

    def test_tools_call_error(self):
        def fail_handler(args):
            raise ValueError("Something broke")

        self.server.register_tool(
            name="fail", description="Always fails",
            input_schema={"type": "object", "properties": {}},
            handler=fail_handler
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "fail", "arguments": {}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["result"]["isError"] is True
        assert "Something broke" in r["result"]["content"][0]["text"]

    def test_tools_call_unknown_tool(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "nonexistent", "arguments": {}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert "error" in r

    def test_ping(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 99, "method": "ping"},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["id"] == 99
        assert r["result"] == {}

    def test_unknown_method(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "bogus/method", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert "error" in r
        assert r["error"]["code"] == -32601
