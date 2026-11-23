"""Stand-alone tests: no editor, no Windows, standard library only.

    python tests/logic_tests.py            # logic tests
    python tests/logic_tests.py --shipped  # also check configs/config.json against config.commented.json
"""

import io
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from arkmcp import config, protocol, typespec  # noqa: E402
from arkmcp.mcp import McpServer  # noqa: E402
from arkmcp.ue_link import LinkError, UnrealLink, call_expression, editor_module_source, installer_script  # noqa: E402

FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
        print("FAIL", label)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    return False


def test_typespec():
    p = typespec.parse
    check(p("int") == ("basic", "int"), "basic int")
    check(p(" Float ") == ("basic", "real"), "float alias")
    check(p("array<string>") == ("array", ("basic", "string")), "array")
    check(p("map< name , int >") == ("map", ("basic", "name"), ("basic", "int")), "map")
    check(p("object:/Script/Engine.Actor") == ("object", "/Script/Engine.Actor"), "object ref")
    check(p("set<class:/Game/A/BP_X.BP_X_C>") == ("set", ("class", "/Game/A/BP_X.BP_X_C")), "set of class")
    check(p("struct:/Script/CoreUObject.Vector") == ("struct", "/Script/CoreUObject.Vector"), "struct")
    for bad in ("", "widget", "array<int", "array<array<int>>", "object:Engine.Actor", "int extra", "map<int>"):
        check(raises(lambda b=bad: p(b), typespec.TypeSpecError), f"rejects {bad!r}")
    for spec in ("map<name,array<int>>",):
        check(raises(lambda s=spec: p(s), typespec.TypeSpecError), f"rejects nested {spec}")
    for spec in ("int", "array<struct:/Script/CoreUObject.Vector>", "map<string,object:/Script/Engine.Actor>"):
        check(typespec.format_spec(p(spec)) == spec, f"round trip {spec}")

    # EdGraphPinType.export_text() as seen in the 5.8.3 DevKit
    d = typespec.describe_pin_type_text
    tail = ("bIsReference=False,bIsConst=False,bIsWeakPointer=False,bIsUObjectWrapper=False,"
            "bSerializeAsSinglePrecisionFloat=False)")
    ref = 'PinSubCategoryMemberReference=(MemberParent=None,MemberName="",MemberGuid=00000000000000000000000000000000),'
    empty_val = ('PinValueType=(TerminalCategory="",TerminalSubCategory="",TerminalSubCategoryObject=None,'
                 'bTerminalIsConst=False,bTerminalIsWeakPointer=False,bTerminalIsUObjectWrapper=False),')
    arr = ('(PinCategory="object",PinSubCategory="",PinSubCategoryObject="/Script/CoreUObject.Class\'/Script/Engine.'
           'AnimMontage\'",' + ref + empty_val + "ContainerType=Array," + tail)
    mp = ('(PinCategory="string",PinSubCategory="",PinSubCategoryObject=None,' + ref +
          'PinValueType=(TerminalCategory="int",TerminalSubCategory="",TerminalSubCategoryObject=None,'
          'bTerminalIsConst=False,bTerminalIsWeakPointer=False,bTerminalIsUObjectWrapper=False),ContainerType=Map,' + tail)
    one = '(PinCategory="int",PinSubCategory="",PinSubCategoryObject=None,' + ref + empty_val + "ContainerType=None," + tail
    check(d(arr) == "array<object:/Script/Engine.AnimMontage>", "describe array of object")
    check(d(mp) == "map<string,int>", "describe map")
    check(d(one) == "int", "describe int")
    check(p(d(arr)) == ("array", ("object", "/Script/Engine.AnimMontage")), "described type parses back")


def test_config():
    text = '{\n // c\n "a": "x // not a comment", /* b */ "b": "\\"q\\"" }'
    check(json.loads(config.strip_json_comments(text)) == {"a": "x // not a comment", "b": '"q"'}, "json comments")
    ini = ("[/Script/Engine.Foo]\nbRemoteExecution=False\n"
           "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\nbRemoteExecution=True\n"
           "RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766\n; comment\n"
           "RemoteExecutionMulticastBindAddress=0.0.0.0\n[Other]\nRemoteExecutionMulticastTtl=9\n")
    vals = config.parse_python_ini(ini)
    check(vals.get("bremoteexecution") == "True", "ini section scoping")
    check(vals.get("remoteexecutionmulticastbindaddress") == "0.0.0.0", "ini bind")
    check("remoteexecutionmulticastttl" not in vals, "ini ignores other sections")
    check(config.parse_endpoint("239.0.0.1:6766") == ("239.0.0.1", 6766), "endpoint")
    check(raises(lambda: config.parse_endpoint("nope"), ValueError), "bad endpoint")


def test_protocol():
    raw = protocol.encode(protocol.TYPE_PING, "me")
    msg = protocol.decode(raw)
    check(msg["type"] == "ping" and "dest" not in msg, "encode/decode")
    check(protocol.decode(b'{"version":2,"magic":"ue_py","type":"x","source":"a"}') is None, "bad version")
    check(protocol.decode(b"not json") is None, "garbage")
    check(not protocol.is_for(msg, "me"), "own message filtered")
    check(protocol.is_for(protocol.decode(protocol.encode("pong", "ed", "me")), "me"), "addressed to me")
    check(not protocol.is_for(protocol.decode(protocol.encode("pong", "ed", "other")), "me"), "addressed elsewhere")

    big = protocol.encode(protocol.TYPE_COMMAND_RESULT, "ed", "me",
                          {"success": True, "result": "é" * 50000 + "}", "output": []})
    dec = protocol.StreamDecoder()
    got = None
    for i in range(0, len(big), 997):  # odd chunk size splits multi-byte characters
        got = dec.feed(big[i:i + 997]) or got
    check(got is not None and got["data"]["result"].endswith("é}"), "stream decoder reassembles")

    check(protocol.unwrap_eval_result("'{\"ok\": true}'") == '{"ok": true}', "eval repr unwrap")
    check(protocol.unwrap_eval_result('{"ok": true}') == '{"ok": true}', "eval plain")


def test_mcp():
    calls = []

    def handler(name, args):
        calls.append((name, args))
        if name == "boom":
            raise RuntimeError("x")
        return "done", False

    tools = [{"name": "t", "description": "", "inputSchema": {"type": "object"}},
             {"name": "boom", "description": "", "inputSchema": {"type": "object"}}]
    srv = McpServer("n", "1", tools, handler, lambda m: None, "hi")
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    check(r["result"]["protocolVersion"] == "2024-11-05" and r["result"]["instructions"] == "hi", "initialize")
    r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    check(r["result"]["protocolVersion"] == "2025-06-18", "initialize fallback version")
    check(srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None, "notification")
    check(len(srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]) == 2, "tools/list")
    r = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "t", "arguments": {"a": 1}}})
    check(r["result"]["content"][0]["text"] == "done" and not r["result"]["isError"], "tools/call")
    r = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "boom"}})
    check(r["result"]["isError"], "tool exception becomes isError")
    check(srv.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "zzz"}})["error"]["code"]
          == -32602, "unknown tool")
    check(srv.handle({"jsonrpc": "2.0", "id": 7, "method": "nope"})["error"]["code"] == -32601, "unknown method")

    out = io.BytesIO()
    srv.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":9,"method":"ping"}\n\nnot json\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    check(lines[0] == {"jsonrpc": "2.0", "id": 9, "result": {}}, "serve ping")
    check(lines[1]["error"]["code"] == -32700, "serve parse error")


# ---------- editor module against a fake `unreal` ----------

def _fake_unreal():
    u = types.ModuleType("unreal")

    class Object:
        def __init__(self, name):
            self._name, self.props = name, {}

        def get_name(self):
            return self._name

        def get_path_name(self):
            return "/Game/Test." + self._name

        def get_class(self):
            return types.SimpleNamespace(get_name=lambda: type(self).__name__)

        def get_editor_property(self, k):
            return self.props[k]

        def set_editor_property(self, k, v):
            self.props[k] = v

        def modify(self):
            pass

    class Blueprint(Object):
        pass

    class IntPoint:
        def __init__(self, x=0, y=0):
            self.x, self.y = x, y

    class EdGraphNode(Object):  # 5.8 nodes expose get_node_pos/set_node_pos(IntPoint)
        def get_node_pos(self):
            return IntPoint(self.props["node_pos_x"], self.props["node_pos_y"])

        def set_node_pos(self, pos):
            if not isinstance(pos, IntPoint):
                raise TypeError("set_node_pos expects IntPoint")
            self.props["node_pos_x"], self.props["node_pos_y"] = pos.x, pos.y

    class Pin:
        def __init__(self, node, name, direction):
            self.node, self.name, self.direction, self.links, self.value = node, name, direction, [], ""

    class BlueprintGraphPinLibrary:  # statics only, like a BlueprintFunctionLibrary
        @staticmethod
        def get_pin_name(p):
            return p.name

        @staticmethod
        def get_pin_direction(p):
            return "EdGraphPinDirection.EGPD_OUTPUT" if p.direction == "out" else "EdGraphPinDirection.EGPD_INPUT"

        @staticmethod
        def list_connected_pins(p):
            return list(p.links)

        @staticmethod
        def get_owning_node(p):
            return p.node

        @staticmethod
        def get_pin_value(p):
            return p.value

        @staticmethod
        def can_create_connection(a, b):
            return a.direction != b.direction

        @staticmethod
        def try_create_connection(a, b):
            a.links.append(b)
            b.links.append(a)
            return True

        @staticmethod
        def set_pin_value(p, v):
            p.value = v
            return True

    class GraphEditor:
        def __init__(self):
            self.nodes = []

        def _new(self, name, pins):
            n = EdGraphNode(name)
            n.props.update(node_pos_x=0, node_pos_y=0, node_comment="")
            n.pins = [Pin(n, pn, d) for pn, d in pins]
            self.nodes.append(n)
            return n

        def list_all_nodes(self):
            return list(self.nodes)

        def list_all_pins(self, node):  # instance method taking the node
            return list(node.pins)

        def add_branch_node(self):
            return self._new(f"K2Node_IfThenElse_{len(self.nodes)}",
                             [("execute", "in"), ("Condition", "in"), ("then", "out"), ("else", "out")])

        def get_node_title(self, node):
            return "Title " + node.get_name()

    bp = Blueprint("BP_Test")
    ge = GraphEditor()
    ge._new("K2Node_Event_0", [("then", "out")])

    class BlueprintEditorLibrary:
        @staticmethod
        def get_graph_editor_by_name(b, g):
            return ge if g == "EventGraph" else None

        @staticmethod
        def list_graph_names(b):
            return ["EventGraph"]

    class EditorAssetLibrary:
        @staticmethod
        def load_asset(path):
            return bp if path == "/Game/Test/BP_Test" else None

    class ScopedEditorTransaction:
        def __init__(self, desc):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    u.Object, u.Blueprint, u.Class = Object, Blueprint, type("Class", (Object,), {})
    u.IntPoint = IntPoint
    u.Vector2D = type("Vector2D", (IntPoint,), {})
    u.BlueprintEditorLibrary, u.BlueprintGraphPinLibrary = BlueprintEditorLibrary, BlueprintGraphPinLibrary
    u.EditorAssetLibrary, u.ScopedEditorTransaction = EditorAssetLibrary, ScopedEditorTransaction
    u.load_object = lambda outer, path: None
    u.Array, u.Map, u.Set = list, dict, set
    u.Name = type("Name", (str,), {})
    u.Text = type("Text", (str,), {})
    u.EnumBase = type("EnumBase", (), {})
    u.StructBase = type("StructBase", (), {})
    u.SystemLibrary = types.SimpleNamespace(get_engine_version=lambda: "5.8.3", get_project_directory=lambda: "E:/x")
    return u


def test_editor_module():
    src = editor_module_source()
    check("__ARKMCP_VERSION__" not in src, "version substituted")
    compile(installer_script(src), "installer", "exec")
    compile(call_expression("ping", {"a": "'\"\\"}), "expr", "eval")

    sys.modules["unreal"] = _fake_unreal()
    try:
        ns = {}
        exec(compile(installer_script(src), "installer", "exec"), ns)
        mod = sys.modules["arkmcp_editor"]
        call = lambda tool, args: json.loads(eval(call_expression(tool, args), {}))  # noqa: E731

        r = call("ping", {})
        check(r["ok"] and r["result"]["engine_version"] == "5.8.3", "editor ping")
        r = call("add_node", {"path": "/Game/Test/BP_Test", "kind": "branch", "x": 300, "y": 40})
        check(r["ok"] and r["result"]["node"]["pos"] == [300, 40], "add_node + position")
        bid = r["result"]["node"]["id"]
        r = call("connect_pins", {"path": "/Game/Test/BP_Test", "from_node": "K2Node_Event_0", "from_pin": "then",
                                  "to_node": bid, "to_pin": "execute"})
        check(r["ok"] and r["result"]["connected"], "connect exec pins")
        r = call("set_pin_value", {"path": "/Game/Test/BP_Test", "node": bid, "pin": "condition", "value": "true"})
        check(r["ok"] and r["result"]["pin"]["value"] == "true", "set pin value (case-insensitive pin)")
        r = call("list_graph", {"path": "/Game/Test/BP_Test"})
        nodes = {n["id"]: n for n in r["result"]["nodes"]}
        links = [p.get("links") for p in nodes[bid]["pins"] if p["name"] == "execute"][0]
        check(links == ["K2Node_Event_0.then"], "list_graph shows links")
        check(nodes[bid]["title"] == "Title " + bid, "node title via graph editor method")
        r = call("connect_pins", {"path": "/Game/Test/BP_Test", "from_node": bid, "from_pin": "nope",
                                  "to_node": bid, "to_pin": "execute"})
        check(not r["ok"] and "pins:" in r["error"], "missing pin lists pins")
        r = call("add_node", {"path": "/Game/Test/BP_Test", "kind": "return"})
        check(not r["ok"] and "add_return_node" in r["error"], "missing API reported by name")
        r = call("blueprint_info", {"path": "/Game/Nope"})
        check(not r["ok"] and "not found" in r["error"], "missing asset")
        r = call("add_variable", {"path": "/Game/Test/BP_Test", "name": "X", "type": "widget"})
        check(not r["ok"] and "unknown type" in r["error"], "typespec error surfaces")
        check(call("nope", {})["ok"] is False, "unknown editor tool")
        check(mod.ARKMCP_VERSION != "__ARKMCP_VERSION__", "module version")
    finally:
        sys.modules.pop("unreal", None)
        sys.modules.pop("arkmcp_editor", None)


def test_tool_table():
    from arkmcp import tools
    names = [t["name"] for t in tools.TOOLS]
    check(len(names) == len(set(names)), "unique tool names")
    src = editor_module_source()
    for n in tools.EDITOR_TOOLS:
        check(f'"{n}":' in src, f"editor implements {n}")
    for t in tools.TOOLS:
        for r in t["inputSchema"].get("required", []):
            check(r in t["inputSchema"]["properties"], f"{t['name']} required {r} is declared")


def test_layout():
    from arkmcp.layout import layout_graph, pin_offset as o
    nodes = {"E0": dict(w=200, h=80, x=0, y=0, exec=True), "E1": dict(w=200, h=96, x=0, y=200, exec=True),
             "Print": dict(w=250, h=224, x=600, y=0, exec=True), "Get": dict(w=200, h=48, x=60, y=200, exec=False),
             "Branch": dict(w=200, h=96, x=300, y=0, exec=True), "Gt": dict(w=250, h=116, x=300, y=180, exec=False),
             "CE": dict(w=200, h=80, x=0, y=600, exec=True)}
    edges = [("E0", o(0), "Branch", o(0), True), ("Branch", o(0), "Print", o(0), True),
             ("Get", o(0), "Gt", o(0), False), ("Gt", o(0), "Branch", o(1), False)]
    pos = layout_graph(nodes, edges)
    for s_, _, d_, _, _ in edges:
        check(pos[s_][0] + nodes[s_]["w"] < pos[d_][0], f"wire {s_}->{d_} runs forward")
    check(pos["E0"][1] == pos["Branch"][1] == pos["Print"][1], "exec chain on one row")
    check(pos["E0"][0] < pos["Gt"][0] < pos["Branch"][0], "data node sits between source and consumer")
    ids = list(pos)
    for i, a_ in enumerate(ids):
        for b_ in ids[i + 1:]:
            ax, ay = pos[a_]; bx, by = pos[b_]
            apart = (ax + nodes[a_]["w"] <= bx or bx + nodes[b_]["w"] <= ax or
                     ay + nodes[a_]["h"] <= by or by + nodes[b_]["h"] <= ay)
            check(apart, f"{a_} and {b_} do not overlap")
    check(pos["E1"][1] > pos["Print"][1] + nodes["Print"]["h"], "unconnected event gets its own band below")
    check(all(v[0] % 8 == 0 and v[1] % 8 == 0 for v in pos.values()), "grid snapped")
    # calibrated against the user's hand layout of BP_McpTest (sizes measured from a screenshot)
    from arkmcp.layout import estimate_node_size as est
    check([round(v) for v in est("K2Node_Event", "Event BeginPlay", [], [("then", "Exec", None, True)])] == [154, 56],
          "event size estimate")
    check(est("K2Node_PromotableOperator", "integer > integer", [("A", "Integer", "", True), ("B", "Integer", "5", False)],
              [("ReturnValue", "Boolean", "", True)]) == (142.0, 68.0), "operator size estimate")
    real = {"E0": (152, 57), "Branch": (172, 86), "Print": (318, 175), "Get": (127, 30), "Gt": (142, 68)}
    hand = {"E0": (0, 0), "Get": (24, 72), "Gt": (192, 72), "Branch": (400, 0), "Print": (600, 0)}
    nodes2 = {k: dict(w=w, h=h, x=0, y=0, exec=k in ("E0", "Branch", "Print")) for k, (w, h) in real.items()}
    pos2 = layout_graph(nodes2, edges, origin=(0, 0))
    check(all(abs(pos2[k][0] - hand[k][0]) <= 32 and abs(pos2[k][1] - hand[k][1]) <= 32 for k in hand),
          f"matches the hand layout within 32 units: {pos2}")
    check(pos2["Gt"][1] >= pos2["E0"][1] + 56, "data nodes stay below the exec wire lane")

    # place-only mode: existing nodes never move, new ones avoid them
    from arkmcp.layout import place_nodes
    world = {"Ev": dict(w=150, h=56, x=0, y=0, exec=True), "Old": dict(w=180, h=80, x=600, y=0, exec=True),
             "Wall": dict(w=200, h=100, x=232, y=0, exec=False),          # something already sits where New wants to go
             "New": dict(w=160, h=80, x=0, y=0, exec=True), "Feed": dict(w=120, h=32, x=0, y=0, exec=False)}
    ed = [("Ev", o(0), "New", o(0), True), ("New", o(0), "Old", o(0), True), ("Feed", o(0), "New", o(1), False)]
    got = place_nodes(world, ed, ["New", "Feed"])
    check(set(got) == {"New", "Feed"}, "only the listed nodes are placed")
    rects = {k: (world[k]["x"], world[k]["y"]) for k in ("Ev", "Old", "Wall")}
    rects.update(got)
    ids2 = list(rects)
    for i, a_ in enumerate(ids2):
        for b_ in ids2[i + 1:]:
            ax, ay = rects[a_]; bx, by = rects[b_]
            apart = (ax + world[a_]["w"] <= bx or bx + world[b_]["w"] <= ax or
                     ay + world[a_]["h"] <= by or by + world[b_]["h"] <= ay)
            check(apart, f"placed {a_}/{b_} do not overlap")
    check(got["New"][0] > world["Ev"]["x"] + world["Ev"]["w"], "new exec node goes after its predecessor")
    check(got["Feed"][0] + world["Feed"]["w"] <= got["New"][0], "feeder sits left of the pin it feeds")
    check(got["Feed"][1] > got["New"][1], "feeder sits below the rail")

    cyc = layout_graph({"A": dict(w=100, h=50, x=0, y=0, exec=True), "B": dict(w=100, h=50, x=0, y=0, exec=True)},
                       [("A", 40, "B", 40, True), ("B", 40, "A", 40, True)])
    check(set(cyc) == {"A", "B"}, "cycles do not hang the layout")


def test_connection_failures():
    settings = dict(config.DEFAULTS, multicast_group_endpoint='239.0.0.1:6766', multicast_bind_address='127.0.0.1')
    class Socket:
        sends = 0
        closed = False
        def sendall(self, payload):
            self.sends += 1
            if self.fail_send:
                raise OSError('delivery uncertain')
        def close(self):
            self.closed = True
        def settimeout(self, timeout):
            pass
        def recv(self, count):
            return b''
    for fail_send in (True, False):
        link = UnrealLink(settings, lambda msg: None)
        sock = Socket()
        sock.fail_send = fail_send
        link._tcp, link._remote, link._installed = sock, {'node_id':'test-editor'}, True
        link.connect = lambda: None
        link._send_udp = lambda *args: None
        try:
            link.run('mutate_once()')
            check(False, 'lost reply must be reported')
        except LinkError as exc:
            check('may have run' in str(exc), 'uncertain completion is explicit')
        check(sock.sends == 1, 'a mutation is never replayed after a transport failure')
        check(sock.closed and not link.connected and not link._installed, 'lost connection clears socket and helper state')


def test_layout_cases():
    """Hand layouts from tests/layout_cases.json: the full layout must reproduce them within tolerance."""
    from arkmcp.layout import estimate_node_size, layout_graph, pin_offset
    with open(os.path.join(ROOT, "tests", "layout_cases.json"), encoding="utf-8") as f:
        data = json.load(f)
    for case in data["cases"]:
        nodes = {}
        for k, spec in case["nodes"].items():
            w, h = estimate_node_size(spec["cls"], spec["title"], [tuple(p) for p in spec["ins"]],
                                      [tuple(p) for p in spec["outs"]], spec.get("advanced_hidden", False),
                                      spec.get("dev_only", False))
            nodes[k] = dict(w=w, h=h, x=0, y=0, exec=spec["exec"])
        edges = [(a, pin_offset(ai), b, pin_offset(bi), ex) for a, ai, b, bi, ex in case["edges"]]
        pos = layout_graph(nodes, edges, origin=(0, 0))
        tx, ty = case["tolerance"]
        for k, (hx, hy) in case["hand"].items():
            if k in case.get("known_gaps", {}):
                print(f"  known gap [{case['name']}] {k}: rules {pos[k]} vs hand {(hx, hy)}")
                continue
            check(abs(pos[k][0] - hx) <= tx and abs(pos[k][1] - hy) <= ty,
                  f"[{case['name']}] {k}: rules {pos[k]} vs hand {(hx, hy)}")


def test_ladder_insert_case():
    """Sean's arrangement of the ADD-path dedup insertion (tests/captures/*_fixture.json): make room on the rail,
    build the check beside its source, carry the result on a knot lane."""
    from arkmcp.layout import place_ladder
    with open(os.path.join(ROOT, "tests", "captures", "cloudbshelf_training_add_dedup_fixture.json"), encoding="utf-8") as f:
        fx = json.load(f)
    r = place_ladder(fx["nodes"], [tuple(e) for e in fx["edges"]], fx["movable"])
    hand = fx["hand"]
    check(not r["fallback"], "ladder: no node fell back to plain placement")
    check(r["decisions"] and r["decisions"][0]["placement"] == "at source",
          f"ladder: inputs only exist far away, so the check is built at the source: {r['decisions']}")
    for k in fx["movable"]:
        x, y = r["positions"][k]
        check(abs(x - hand[k][0]) <= 64 and abs(y - hand[k][1]) <= 32, f"ladder: new node {k} {(x, y)} vs hand {hand[k]}")
    for k in ("K2Node_CallFunction_23", "K2Node_VariableSet_1", "K2Node_VariableSet_3", "K2Node_CallFunction_19",
              "K2Node_Self_2", "K2Node_VariableGet_7", "K2Node_VariableGet_6"):
        check(k in r["shifted"], f"ladder: {k} moves with the block")
        if k in r["positions"]:
            check(abs(r["positions"][k][0] - hand[k][0]) <= 96, f"ladder: shifted {k} {r['positions'][k]} vs hand {hand[k]}")
    for k in ("K2Node_CallFunction_23", "K2Node_VariableSet_1", "K2Node_VariableSet_3", "K2Node_CallFunction_19"):
        check(abs(r["positions"][k][1] - hand[k][1]) <= 16, f"ladder: rail re-levelled {k} y={r['positions'][k][1]} vs hand {hand[k][1]}")
    check(all(not fx["nodes"][k].get("knot") for k in r["shifted"]), "ladder: lane knots are never shifted")
    knot = fx["hand_added_knots"]["K2Node_Knot_3"]
    check(len(r["lanes"]) == 1 and abs(r["lanes"][0]["x"] - knot["pos"][0]) <= 32 and abs(r["lanes"][0]["y"] - knot["pos"][1]) <= 24,
          f"ladder: result lane knot {r['lanes']} vs hand {knot['pos']}")
    check(r["lanes"] and [r["lanes"][0][k] for k in ("src", "src_pin", "dst", "dst_pin")] == knot["between"],
          "ladder: lane splices the same wire Sean routed")


def test_toon():
    from arkmcp import toon
    out = toon.dumps({"graph": "EventGraph", "count": 2, "ok": True, "none": None,
                      "nodes": [{"id": "A", "pos": [0, 16], "title": "Get, Item"},
                                {"id": "B", "pos": [8, 24], "title": "", "extra": "x|y"}],
                      "names": ["a", "b c"], "nested": {"k": 1},
                      "mixed": [{"id": "N", "pins": [{"name": "then", "dir": "out"}]}]})
    lines = out.splitlines()
    check("graph: EventGraph" in lines and "count: 2" in lines and "ok: true" in lines and "none: " in lines, "toon scalars")
    check("nodes[2]{id,pos,title,extra}:" in lines, "toon table header with union of keys")
    check('  A,0|16,"Get, Item",' in lines, "toon row: list cell, quoted comma, empty missing field")
    check('  B,8|24,"","x|y"' in lines, "toon row: empty string quoted, pipe quoted")
    check("names[2]: a,b c" in lines and "nested:" in lines and "  k: 1" in lines, "toon scalar list and nested object")
    check("mixed[1]:" in lines and "  - id: N" in lines and "    pins[1]{name,dir}:" in lines and "      then,out" in lines,
          "toon list of nested objects")
    check(toon.dumps([]) == "items[0]:" and toon.dumps("x") == "x", "toon edge cases")


def test_placement_choice_cases():
    """Sean's reworked graph: nodes he placed are removed and re-placed; the fewest-new-long-wires rule must choose
    'beside consumer' here and land within tolerance. (The first ADD pass, where it must choose 'at source', is
    covered by test_ladder_insert_case.)"""
    from arkmcp.layout import place_ladder
    with open(os.path.join(ROOT, "tests", "captures", "cloudbshelf_training_rework_fixture.json"), encoding="utf-8") as f:
        fx = json.load(f)
    for case in fx["cases"]:
        nodes = {k: dict(v) for k, v in fx["nodes"].items()}
        hand = {k: (nodes[k]["x"], nodes[k]["y"]) for k in case["movable"]}
        for k in case["movable"]:
            nodes[k]["x"] = nodes[k]["y"] = 0
        r = place_ladder(nodes, [tuple(e) for e in fx["edges"]], case["movable"])
        check(all(d["placement"] == "beside consumer" for d in r["decisions"]),
              f"[{case['name']}] chooses beside consumer: {r['decisions']}")
        for k in case["movable"]:
            x, y = r["positions"][k]
            check(abs(x - hand[k][0]) <= 64 and abs(y - hand[k][1]) <= 40,
                  f"[{case['name']}] {k} {(x, y)} vs hand {hand[k]}")


def test_shipped():
    plain = config.load_json_with_comments(os.path.join(ROOT, "configs", "config.json"))
    commented = config.load_json_with_comments(os.path.join(ROOT, "configs", "config.commented.json"))
    check(plain == commented, "config.json matches config.commented.json")
    check(set(plain) == set(config.DEFAULTS), "config keys match defaults")
    check(all(plain[k] == v for k, v in config.DEFAULTS.items()), "shipped values are the defaults")


if __name__ == "__main__":
    for t in (test_typespec, test_config, test_protocol, test_mcp, test_editor_module, test_tool_table, test_layout, test_connection_failures, test_layout_cases, test_ladder_insert_case, test_toon, test_placement_choice_cases):
        t()
    if "--shipped" in sys.argv:
        test_shipped()
    print("OK" if not FAILS else f"{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)
