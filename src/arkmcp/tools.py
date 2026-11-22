"""MCP tool definitions and the dispatcher that forwards them to the editor."""

import json

from . import VERSION, protocol, toon
from .ue_link import LinkError

S = "string"
PATH = {"type": S, "description": "Blueprint asset path, e.g. /Game/Mods/MyMod/BP_MyThing"}
GRAPH = {"type": S, "description": "Graph name (default EventGraph). Function graphs use the function name."}
NODE = {"type": S, "description": "Node id as returned by list_graph / add_node (e.g. K2Node_CallFunction_3)"}


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS = [
    {"name": "editor_status",
     "description": "Check the connection to the DevKit editor: which editors answer, engine version, and whether "
                    "the Blueprint graph scripting API is available. Call this first if anything fails.",
     "inputSchema": _schema({})},
    {"name": "run_python",
     "description": "Run Python inside the DevKit editor (the `unreal` module is available). Use for anything the "
                    "other tools do not cover. print() output is returned. Changes are NOT saved automatically.",
     "inputSchema": _schema({"code": {"type": S, "description": "Python source to execute"}}, ["code"])},
    {"name": "api_search",
     "description": "Search the editor's `unreal` Python module for type names (and graph-library members) "
                    "containing a term.",
     "inputSchema": _schema({"term": {"type": S}, "limit": {"type": "integer"}}, ["term"])},
    {"name": "api_help",
     "description": "Docstring and members of an `unreal` type or function, e.g. 'BlueprintGraphEditor' or "
                    "'BlueprintEditorLibrary.add_member_variable'. Shows exact Python signatures in this DevKit.",
     "inputSchema": _schema({"name": {"type": S}, "max_chars": {"type": "integer"}}, ["name"])},
    {"name": "find_assets",
     "description": "List assets under a content path, optionally filtered by class name (e.g. Blueprint, "
                    "DataTable, PrimalItem...) and a name substring.",
     "inputSchema": _schema({"path": {"type": S, "description": "Default /Game"},
                             "class_name": {"type": S}, "name_contains": {"type": S},
                             "recursive": {"type": "boolean"}, "limit": {"type": "integer"}})},
    {"name": "get_properties",
     "description": "Read editable properties. target is an asset path (for a Blueprint this reads its Class "
                    "Defaults, including Blueprint variables), a class path ending in _C, or 'actor:<label>' for "
                    "an actor in the open level. Omit names to list everything.",
     "inputSchema": _schema({"target": {"type": S}, "names": {"type": "array", "items": {"type": S}},
                             "limit": {"type": "integer"}}, ["target"])},
    {"name": "set_properties",
     "description": "Set editable properties (same targets as get_properties). values maps property name to a "
                    "JSON value; structs accept Unreal text like \"(X=1,Y=2,Z=3)\" or an object of fields; enums "
                    "accept the value name; object refs accept an asset path; {\"py\": \"expr\"} is evaluated in "
                    "the editor. Undoable with Ctrl+Z. Not saved until save_assets.",
     "inputSchema": _schema({"target": {"type": S}, "values": {"type": "object"},
                             "compile": {"type": "boolean", "description": "Compile the Blueprint afterwards"}},
                            ["target", "values"])},
    {"name": "save_assets",
     "description": "Save assets to disk. Nothing else saves; call this when the user is happy with the changes.",
     "inputSchema": _schema({"paths": {"type": "array", "items": {"type": S}}}, ["paths"])},
    {"name": "blueprint_info",
     "description": "Overview of a Blueprint: parent class, graphs, member variables (type, category, default), "
                    "functions, events and event dispatchers.",
     "inputSchema": _schema({"path": PATH}, ["path"])},
    {"name": "compile_blueprint",
     "description": "Compile a Blueprint and report nodes with errors or warnings.",
     "inputSchema": _schema({"path": PATH}, ["path"])},
    {"name": "add_variable",
     "description": "Add a member variable. type: bool, int, byte, real, name, string, text, "
                    "object:/Script/Engine.Actor, class:/Game/.../BP_X.BP_X_C, struct:/Script/CoreUObject.Vector, "
                    "array<T>, set<T>, map<K,V>.",
     "inputSchema": _schema({"path": PATH, "name": {"type": S}, "type": {"type": S},
                             "default": {"type": S, "description": "Default value as Unreal text"},
                             "category": {"type": S}, "instance_editable": {"type": "boolean"},
                             "expose_on_spawn": {"type": "boolean"}}, ["path", "name", "type"])},
    {"name": "remove_variable",
     "description": "Remove a member variable from a Blueprint.",
     "inputSchema": _schema({"path": PATH, "name": {"type": S}}, ["path", "name"])},
    {"name": "create_function",
     "description": "Create a new function graph on a Blueprint and return its entry/result nodes.",
     "inputSchema": _schema({"path": PATH, "name": {"type": S}}, ["path", "name"])},
    {"name": "list_graph",
     "description": "List the nodes of a graph with their ids, titles, positions and pins (name, direction, type, "
                    "literal value, links). Use the ids with the other graph tools.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "include_pins": {"type": "boolean"},
                             "filter": {"type": S, "description": "Only nodes whose id/title/class contains this"}},
                            ["path"])},
    {"name": "list_available_nodes",
     "description": "Names of nodes that can be placed in a graph (the right-click menu), as 'Category|Name'. "
                    "Pass one to add_node with kind 'action'.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "filter": {"type": S}, "limit": {"type": "integer"}},
                            ["path"])},
    {"name": "add_node",
     "description": "Place a node. kind: action (name='Category|Name' from list_available_nodes, optional "
                    "declaring_class), call_function (function_path, e.g. "
                    "/Script/Engine.KismetSystemLibrary.PrintString), branch, custom_event (name), event (name of "
                    "the BlueprintImplementableEvent, e.g. ReceiveBeginPlay), get_variable/set_variable (name, "
                    "optional class_path), get_local/set_local (name), macro (macro_path), return, "
                    "dispatcher_event (name), component_event (component, name), comment (text, width, height). "
                    "x/y set the position.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH,
                             "kind": {"type": S, "enum": ["action", "call_function", "branch", "custom_event", "event",
                                                          "get_variable", "set_variable", "get_local", "set_local",
                                                          "macro", "return", "dispatcher_event", "component_event",
                                                          "comment"]},
                             "name": {"type": S}, "declaring_class": {"type": S}, "function_path": {"type": S},
                             "class_path": {"type": S}, "macro_path": {"type": S}, "component": {"type": S},
                             "text": {"type": S}, "x": {"type": "number"}, "y": {"type": "number"},
                             "width": {"type": "number"}, "height": {"type": "number"}},
                            ["path", "kind"])},
    {"name": "connect_pins",
     "description": "Wire an output pin to an input pin (exec or data). Unreal may insert conversion nodes.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "from_node": NODE, "from_pin": {"type": S},
                             "to_node": NODE, "to_pin": {"type": S}},
                            ["path", "from_node", "from_pin", "to_node", "to_pin"])},
    {"name": "disconnect_pins",
     "description": "Break all links on a pin, or only the link to other_node.other_pin.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "node": NODE, "pin": {"type": S},
                             "direction": {"type": S, "enum": ["in", "out"]},
                             "other_node": NODE, "other_pin": {"type": S}}, ["path", "node", "pin"])},
    {"name": "set_pin_value",
     "description": "Set the literal (unconnected) value of an input pin, as Unreal text (e.g. 5, true, Hello, "
                    "(X=1,Y=0,Z=0)).",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "node": NODE, "pin": {"type": S},
                             "value": {"type": S}}, ["path", "node", "pin", "value"])},
    {"name": "move_node",
     "description": "Move a node to x/y in graph space.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "node": NODE, "x": {"type": "number"},
                             "y": {"type": "number"}}, ["path", "node", "x", "y"])},
    {"name": "remove_nodes",
     "description": "Delete nodes from a graph.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "nodes": {"type": "array", "items": NODE}},
                            ["path", "nodes"])},
    {"name": "tidy_graph",
     "description": "Position nodes for readability. With nodes=[...] (normal use after adding nodes to an existing "
                    "graph): ONLY those nodes move; every other node stays where the user put it and is avoided. New "
                    "exec nodes go on the rail right after their predecessor, data feeders left of and below the pin "
                    "they feed, consumers right of their producer. Without nodes: full re-layout of the whole graph "
                    "(only for graphs built from scratch); refused if the graph has reroute knots unless force=true. "
                    "Undoable as one step.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "nodes": {"type": "array", "items": NODE},
                             "force": {"type": "boolean", "description": "Allow a full re-layout despite knots"},
                             "ladder": {"type": "boolean", "description":
                                        "With nodes: follow the user's ladder conventions. An exec node inserted "
                                        "between two rail nodes goes ON the rail and the downstream rail block (and "
                                        "its feeders) shifts right to make room; a data computation fed from far "
                                        "away is built beside its source below the lowest lane; its result gets a "
                                        "reroute-knot lane to the distant consumer. Moves existing nodes and adds "
                                        "knots, so only use it when the user agrees. Try dry_run first."},
                             "dry_run": {"type": "boolean", "description": "Return positions without moving"},
                             "gap_x": {"type": "number"}, "gap_y": {"type": "number"}}, ["path"])},
    {"name": "add_node_pin",
     "description": "Add a pin to a node that supports it (Sequence, Switch, Make Array, Add/Multiply...).",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "node": NODE}, ["path", "node"])},
]

INSTRUCTIONS = (
    "Tools for the ARK: Survival Ascended DevKit editor (Unreal 5.8). Nothing is saved until save_assets is "
    "called, and every change is an undoable editor transaction. Typical Blueprint edit: blueprint_info -> "
    "list_graph -> add_node / connect_pins / set_pin_value -> tidy_graph(nodes=[the nodes you added]) -> "
    "compile_blueprint -> save_assets (after the user agrees). "
    "LAYOUT MATTERS to the user. Their graphs are laid out like PLC ladder logic: the exec flow is the rail, one "
    "long row left to right; shared inputs run along buses of reroute knots under the rail and consumers tap up "
    "into their pins; results drop down onto stacked horizontal output lanes that run right to the node collecting "
    "them; modules stack as rows, wrapped with Sequence (Then 1 runs back left and down to start the next row); "
    "Branch alternatives get their own row. Never re-lay out a user's existing graph; only position what you add "
    "(tidy_graph with nodes=...), keep wires running forward, and never put a node on top of another node or wire. "
    "Use refit_comment only on an explicitly named comment and node selection. Use route_bus for shared data "
    "lanes with explicit tap coordinates; existing connected inputs are refused. If a graph tool reports that no signature matched, use api_help on the named function and then "
    "run_python. ARK mods cannot contain C++; everything here is Blueprint/data editing."

    " Replies use a compact TOON-style text format (key: value lines; key[N]{fields}: tables with one row per item; "
    "list cells joined with |) to save tokens. Prefer list_graph with filter=... or include_pins=false on large graphs; "
    "fetch pins only for the nodes you are about to wire.")

TOOLS.append({"name": "refit_comment",
              "description": "Resize one named comment around explicitly selected nodes after layout. Only the comment moves; graph nodes and wires are preserved.",
              "inputSchema": _schema({"path": PATH, "graph": GRAPH, "node": NODE,
                                       "nodes": {"type": "array", "items": NODE, "minItems": 1},
                                       "padding": {"type": "integer", "minimum": 0}},
                                      ["path", "node", "nodes"])})

TOOLS.extend([
    {"name": "create_blueprint", "description": "Create an unsaved Blueprint in a full mod/package path. Refuses to replace an existing asset. Parent defaults to Actor.",
     "inputSchema": _schema({"path": PATH, "parent_class": {"type": S}}, ["path"])},
    {"name": "add_reroute", "description": "Add one reroute knot at explicit x/y coordinates. Use connect_pins for data or execution wires.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["path", "x", "y"])},
    {"name": "route_bus", "description": "Route one shared data output along a horizontal bus with explicit left-to-right tap positions. Only new reroutes and wires are created. Existing connected destinations are refused. Does not move existing nodes. Execution flow requires Sequence and explicit reroutes.",
     "inputSchema": _schema({"path": PATH, "graph": GRAPH, "from_node": NODE, "from_pin": {"type": S},
                              "y": {"type": "integer"},
                              "taps": {"type": "array", "minItems": 1, "maxItems": 64,
                                       "items": _schema({"node": NODE, "pin": {"type": S}, "x": {"type": "integer"}}, ["node", "pin", "x"])}},
                             ["path", "from_node", "from_pin", "y", "taps"])}
])

EDITOR_TOOLS = {t["name"] for t in TOOLS} - {"editor_status", "run_python"}


def _clip(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} characters]"


def make_handler(link, settings, log):
    limit = int(settings["max_output_chars"])

    def dispatch(name, args):
        try:
            if name == "editor_status":
                return _clip(toon.dumps(_status(link, settings)), limit), False
            if name == "run_python":
                data = link.run(args["code"], protocol.MODE_EXEC_FILE)
                lines = [o.get("output", "") if o.get("type") == "Info" else f"[{o.get('type')}] {o.get('output', '')}"
                         for o in (data.get("output") or [])]
                text = "\n".join(lines).rstrip()
                if not data.get("success"):
                    text = (text + "\n\n" if text else "") + "FAILED:\n" + str(data.get("result", "")).rstrip()
                return _clip(text or "(no output)", limit), not data.get("success")
            if name in EDITOR_TOOLS:
                reply = link.call_editor(name, args)
                if reply.get("ok"):
                    return _clip(toon.dumps(reply.get("result")), limit), False
                # error details stay compact JSON (tracebacks/docstrings are free text anyway)
                return _clip(json.dumps({k: v for k, v in reply.items() if k != "ok"}, separators=(",", ":"),
                                        default=str, ensure_ascii=False), limit), True
            return f"Unknown tool {name}", True
        except LinkError as e:
            return str(e), True
        except KeyError as e:
            return f"Missing argument {e}", True

    def handler(name, args):
        try:
            with link.session():
                return dispatch(name, args)
        except LinkError as e:
            return str(e), True

    return handler


def _status(link, settings):
    out = {"server_version": VERSION,
           "multicast": settings["multicast_group_endpoint"], "bind": settings["multicast_bind_address"],
           "ini": settings["ini_path"], "ini_remote_execution": settings["ini_remote_execution"]}
    editors = link.editors()
    out["editors"] = [{k: e.get(k) for k in ("project_name", "engine_version", "machine", "user", "node_id")}
                      for e in editors]
    if not editors:
        out["problem"] = ("No editor answered. Open the DevKit, and check Edit > Project Settings > Plugins > "
                          "Python > Enable Remote Execution.")
        return out
    reply = link.call_editor("ping", {})
    out["connected_to"] = (link.describe() or {}).get("project_name")
    out["editor"] = reply.get("result") if reply.get("ok") else reply
    return out
