"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0). No third-party packages."""

import json
import sys

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


class McpServer:
    def __init__(self, name, version, tools, handler, log, instructions=None):
        """tools: list of {"name", "description", "inputSchema"}.
        handler(name, arguments) -> (text, is_error)."""
        self.name = name
        self.version = version
        self.tools = tools
        self.tool_names = {t["name"] for t in tools}
        self.handler = handler
        self.log = log
        self.instructions = instructions

    @staticmethod
    def _result(id_, result):
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    @staticmethod
    def _error(id_, code, message):
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}

    def handle(self, msg):
        """Handle one decoded message. Returns a response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method = msg.get("method")
        id_ = msg.get("id")
        is_notification = "id" not in msg
        if method is None:
            return None  # a response to something we never send; ignore
        params = msg.get("params") or {}

        if is_notification:
            return None

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            result = {"protocolVersion": version,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                result["instructions"] = self.instructions
            return self._result(id_, result)
        if method == "ping":
            return self._result(id_, {})
        if method == "tools/list":
            return self._result(id_, {"tools": self.tools})
        if method == "tools/call":
            name = params.get("name")
            if name not in self.tool_names:
                return self._error(id_, -32602, f"Unknown tool: {name}")
            try:
                text, is_error = self.handler(name, params.get("arguments") or {})
            except Exception as e:  # noqa: BLE001 - report, never kill the server
                self.log(f"tool {name} crashed: {e!r}")
                text, is_error = f"Internal error in {name}: {e}", True
            return self._result(id_, {"content": [{"type": "text", "text": text}], "isError": bool(is_error)})
        if method in ("resources/list", "prompts/list"):
            key = method.split("/")[0]
            return self._result(id_, {key: []})
        return self._error(id_, -32601, f"Method not found: {method}")

    def serve(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin.buffer
        stdout = stdout or sys.stdout.buffer
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                resp = self._error(None, -32700, "Parse error")
            else:
                batch = msg if isinstance(msg, list) else [msg]
                replies = [r for r in (self.handle(m) for m in batch) if r is not None]
                resp = (replies if isinstance(msg, list) else replies[0]) if replies else None
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                stdout.flush()
