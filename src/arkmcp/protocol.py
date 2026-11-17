"""Unreal Python remote execution wire format (protocol version 1).

Re-implemented from the protocol description; no Epic code is copied. Every message is a
UTF-8 JSON object: {"version": 1, "magic": "ue_py", "type": ..., "source": ..., "dest"?: ..., "data"?: ...}.
UDP multicast carries ping/pong/open_connection/close_connection; the TCP command
connection (the editor connects to us) carries command/command_result.
"""

import ast
import json

PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = "ue_py"

TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_OPEN_CONNECTION = "open_connection"
TYPE_CLOSE_CONNECTION = "close_connection"
TYPE_COMMAND = "command"
TYPE_COMMAND_RESULT = "command_result"

MODE_EXEC_FILE = "ExecuteFile"
MODE_EXEC_STATEMENT = "ExecuteStatement"
MODE_EVAL_STATEMENT = "EvaluateStatement"


def encode(type_, source, dest=None, data=None):
    obj = {"version": PROTOCOL_VERSION, "magic": PROTOCOL_MAGIC, "type": type_, "source": source}
    if dest:
        obj["dest"] = dest
    if data:
        obj["data"] = data
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def decode(raw):
    """Return the message dict, or None if it is not a valid protocol message."""
    try:
        obj = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("version") != PROTOCOL_VERSION or obj.get("magic") != PROTOCOL_MAGIC:
        return None
    if not obj.get("type") or not obj.get("source"):
        return None
    return obj


def is_for(msg, node_id):
    """True if msg was not sent by node_id and is addressed to everyone or to node_id."""
    return msg["source"] != node_id and (not msg.get("dest") or msg["dest"] == node_id)


class StreamDecoder:
    """Collects TCP bytes until they form one complete JSON message (the protocol has no length prefix)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, chunk):
        self.buf += chunk
        try:
            text = self.buf.decode("utf-8")
        except UnicodeDecodeError:
            return None  # split in the middle of a multi-byte character
        stripped = text.strip()
        if not stripped.endswith("}"):
            return None
        msg = decode(stripped)
        if msg is None:
            try:
                json.loads(stripped)
            except ValueError:
                return None  # incomplete
            raise ValueError("editor sent a JSON object that is not a remote execution message")
        self.buf.clear()
        return msg


def unwrap_eval_result(result):
    """EvaluateStatement returns the repr() of the value; undo that for strings."""
    if not isinstance(result, str):
        return result
    s = result.strip()
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        try:
            return ast.literal_eval(s)
        except (ValueError, SyntaxError):
            return result
    return result
