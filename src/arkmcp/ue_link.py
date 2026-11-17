"""Connection to a running DevKit editor over Unreal's Python remote execution."""

import json
import os
import socket
import threading
import time
import uuid

from . import VERSION, protocol
from .config import parse_endpoint

PING_SECONDS = 1.0
NODE_TIMEOUT_SECONDS = 5.0
EDITOR_MODULE = "arkmcp_editor"


class LinkError(RuntimeError):
    pass


def editor_module_source():
    """typespec.py + layout.py + editor_side.py, sent into the editor as one module."""
    here = os.path.dirname(os.path.abspath(__file__))
    parts = []
    for name in ("typespec.py", "layout.py", "editor_side.py"):
        with open(os.path.join(here, name), "r", encoding="utf-8") as f:
            parts.append(f.read())
    return "\n\n".join(parts).replace("__ARKMCP_VERSION__", VERSION)


def installer_script(source):
    return (
        "import sys as _s, types as _t\n"
        f"_m = _t.ModuleType({EDITOR_MODULE!r})\n"
        f"_m.__file__ = {EDITOR_MODULE + '.py'!r}\n"
        f"exec(compile({source!r}, {EDITOR_MODULE + '.py'!r}, 'exec'), _m.__dict__)\n"
        f"_s.modules[{EDITOR_MODULE!r}] = _m\n"
        "del _s, _t, _m\n"
    )


def call_expression(tool, args):
    return f"__import__({EDITOR_MODULE!r}).call({tool!r}, {json.dumps(args)!r})"


class UnrealLink:
    def __init__(self, settings, log):
        self.s = settings
        self.log = log
        self.node_id = str(uuid.uuid4())
        self.group = parse_endpoint(settings["multicast_group_endpoint"])
        self.bind_addr = settings["multicast_bind_address"]
        self._udp = None
        self._udp_lock = threading.Lock()
        self._nodes = {}  # node_id -> (data, last_seen)
        self._nodes_lock = threading.Lock()
        self._pinger = None
        self._running = False
        self._tcp = None
        self._remote = None  # pong data of the connected editor
        self._installed = False

    # ---- discovery (UDP multicast) ----

    def _open_udp(self):
        if self._udp:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        # The editor and other tools share this port, so allow address reuse (same as Epic's client).
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_REUSEPORT", socket.SO_REUSEADDR), 1)
        s.bind((self.bind_addr, self.group[1]))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(self.s["multicast_ttl"]))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.bind_addr))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(self.group[0]) + socket.inet_aton(self.bind_addr))
        s.settimeout(0.1)
        self._udp = s
        self._running = True
        self._pinger = threading.Thread(target=self._udp_loop, name="arkmcp-udp", daemon=True)
        self._pinger.start()

    def _send_udp(self, type_, dest=None, data=None):
        with self._udp_lock:
            if self._udp:
                self._udp.sendto(protocol.encode(type_, self.node_id, dest, data), self.group)

    def _udp_loop(self):
        last_ping = 0.0
        while self._running:
            try:
                raw = self._udp.recv(65535)
                msg = protocol.decode(raw)
                if msg and protocol.is_for(msg, self.node_id) and msg["type"] == protocol.TYPE_PONG:
                    with self._nodes_lock:
                        self._nodes[msg["source"]] = (msg.get("data") or {}, time.time())
            except socket.timeout:
                pass
            except OSError:
                if not self._running:
                    return
                time.sleep(0.2)
            now = time.time()
            if now - last_ping >= PING_SECONDS:
                last_ping = now
                try:
                    self._send_udp(protocol.TYPE_PING)
                except OSError as e:
                    self.log(f"ping failed: {e}")
            with self._nodes_lock:
                for nid in [n for n, (_, seen) in self._nodes.items() if now - seen > NODE_TIMEOUT_SECONDS]:
                    del self._nodes[nid]

    def editors(self, wait=None):
        """Editors currently answering pings, as a list of their pong data (+ node_id)."""
        self._open_udp()
        wait = self.s["discovery_timeout_seconds"] if wait is None else wait
        deadline = time.time() + wait
        while True:
            with self._nodes_lock:
                found = [dict(d, node_id=n) for n, (d, _) in self._nodes.items()]
            if found or time.time() >= deadline:
                return found
            time.sleep(0.1)

    # ---- command connection (TCP, editor connects to us) ----

    @property
    def connected(self):
        return self._tcp is not None

    def describe(self):
        return dict(self._remote) if self._remote else None

    def connect(self):
        if self._tcp:
            return
        editors = self.editors()
        if not editors:
            hint = "" if self.s.get("ini_remote_execution") else (
                " DefaultEngine.ini does not show bRemoteExecution=True for this project.")
            raise LinkError(
                "No DevKit editor answered on " + self.s["multicast_group_endpoint"] +
                f" (bind {self.bind_addr}). Is the editor open with the Python plugin's Remote Execution enabled?" + hint)
        project = (self.s.get("project") or "").lower()
        editors.sort(key=lambda e: 0 if (e.get("project_name") or "").lower() == project else 1)
        target = editors[0]
        if len(editors) > 1:
            self.log(f"{len(editors)} editors answered; using {target.get('project_name')} ({target['node_id']})")

        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            listen.bind((self.s["command_bind_address"], 0))
            listen.listen(1)
            listen.settimeout(2.5)
            ip, port = listen.getsockname()
            deadline = time.time() + float(self.s["connect_timeout_seconds"])
            conn = None
            while conn is None and time.time() < deadline:
                self._send_udp(protocol.TYPE_OPEN_CONNECTION, target["node_id"],
                               {"command_ip": ip, "command_port": port})
                try:
                    conn, _ = listen.accept()
                except socket.timeout:
                    continue
            if conn is None:
                raise LinkError("The editor was found but did not open the command connection in time.")
        finally:
            listen.close()
        conn.setblocking(True)
        self._tcp = conn
        self._remote = target
        self._installed = False
        self.log(f"connected to editor {target.get('project_name')} on {target.get('machine')}")

    def close(self):
        if self._tcp:
            try:
                self._send_udp(protocol.TYPE_CLOSE_CONNECTION, self._remote["node_id"])
            except OSError:
                pass
            try:
                self._tcp.close()
            except OSError:
                pass
        self._tcp = None
        self._remote = None
        self._installed = False

    def shutdown(self):
        self.close()
        self._running = False
        if self._udp:
            try:
                self._udp.close()
            except OSError:
                pass
        self._udp = None

    def run(self, command, mode=protocol.MODE_EXEC_FILE, timeout=None):
        """Run Python in the editor. Returns the command_result data dict
        ({"success", "result", "output": [{"type", "output"}], ...})."""
        self.connect()
        payload = protocol.encode(protocol.TYPE_COMMAND, self.node_id, self._remote["node_id"],
                                  {"command": command, "unattended": True, "exec_mode": mode})
        try:
            self._tcp.sendall(payload)
        except OSError:
            # Connection went stale (editor restarted). Nothing ran, so reconnect and send once more.
            self.close()
            self.connect()
            self._tcp.sendall(payload)
        self._tcp.settimeout(float(timeout or self.s["command_timeout_seconds"]))
        decoder = protocol.StreamDecoder()
        try:
            while True:
                chunk = self._tcp.recv(65536)
                if not chunk:
                    raise LinkError("The editor closed the connection (did it crash or close?).")
                msg = decoder.feed(chunk)
                if msg and protocol.is_for(msg, self.node_id) and msg["type"] == protocol.TYPE_COMMAND_RESULT:
                    return msg.get("data") or {}
        except socket.timeout:
            self.close()
            raise LinkError("Timed out waiting for the editor. The command may still be running or may have "
                            "finished; check the editor before repeating anything that changes assets.")
        except OSError as e:
            self.close()
            raise LinkError(f"Lost the editor connection: {e}")

    def ensure_module(self):
        if self._installed:
            return
        result = self.run(installer_script(editor_module_source()), protocol.MODE_EXEC_FILE)
        if not result.get("success"):
            raise LinkError("Could not load the helper module into the editor: " + str(result.get("result")))
        self._installed = True

    def call_editor(self, tool, args):
        """Call a tool implemented in editor_side.py. Returns the decoded {"ok": ...} dict."""
        self.connect()
        self.ensure_module()
        data = self.run(call_expression(tool, args), protocol.MODE_EVAL_STATEMENT)
        if not data.get("success") and EDITOR_MODULE in str(data.get("result")):
            self._installed = False  # editor's Python was reset; push the module again
            self.ensure_module()
            data = self.run(call_expression(tool, args), protocol.MODE_EVAL_STATEMENT)
        if not data.get("success"):
            return {"ok": False, "error": str(data.get("result")), "output": data.get("output")}
        raw = protocol.unwrap_eval_result(data.get("result"))
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "unreadable reply from editor", "raw": str(raw)[:2000]}
