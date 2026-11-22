# ---------------------------------------------------------------------------------------------
# editor_side.py - runs INSIDE the DevKit editor's Python (sent over remote execution).
# It is concatenated after typespec.py, so parse()/TypeSpecError from there are in scope.
# Entry point: call(tool_name, args_json) -> JSON string {"ok": bool, "result"|"error": ...}
#
# The 5.8 BlueprintEditorLibrary graph API is reached through _try(), which looks a function up
# by its Python name on the graph editor object, on the node/pin, and on the library classes,
# so small differences in where Epic put a function do not break the tools. When nothing fits,
# the error lists the candidate docstrings so the caller can adapt with run_python.
# ---------------------------------------------------------------------------------------------

import json as _json
import re as _re
import traceback as _traceback

import unreal

ARKMCP_VERSION = "__ARKMCP_VERSION__"

_HOST_NAMES = ("BlueprintEditorLibrary", "BlueprintGraphPinLibrary", "BlueprintGraphEditor")


class ApiMismatch(Exception):
    def __init__(self, name, errors, docs):
        super().__init__(f"no matching signature for {name}")
        self.name, self.errors, self.docs = name, errors, docs


class ToolError(Exception):
    pass


# ---------- API resolution ----------

def _hosts():
    return [getattr(unreal, n) for n in _HOST_NAMES if hasattr(unreal, n)]


def _try(name, attempts):
    """attempts: list of (owner, args). owner None = every library class in _HOST_NAMES."""
    errors, docs = [], {}
    for owner, args in attempts:
        owners = _hosts() if owner is None else [owner]
        for o in owners:
            fn = getattr(o, name, None)
            if not callable(fn):
                continue
            label = getattr(o, "__name__", type(o).__name__) + "." + name
            try:
                return fn(*args)
            except TypeError as e:
                errors.append(f"{label}{_argtypes(args)}: {e}")
                docs.setdefault(label, (getattr(fn, "__doc__", "") or "")[:600])
    if not docs:
        errors.append(f"{name} was not found on the graph editor, node, pin or {', '.join(_HOST_NAMES)}")
    raise ApiMismatch(name, errors, docs)


def _argtypes(args):
    return "(" + ", ".join(type(a).__name__ for a in args) + ")"


def _ge_fn(ge, name, *args):
    return _try(name, [(ge, args)])


def _node_fn(ge, name, node, *rest):
    return _try(name, [(ge, (node,) + rest), (node, rest), (None, (node,) + rest)])


def _pin_fn(name, pin, *rest):
    return _try(name, [(pin, rest), (None, (pin,) + rest)])


def _lib_fn(name, *args):
    return _try(name, [(None, args)])


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, tuple):
        for v in value:
            if isinstance(v, (list, unreal.Array)):
                return list(v)
        return [v for v in value if v is not None and not isinstance(v, bool)]
    if isinstance(value, (list, unreal.Array)):
        return list(value)
    return [value]


def _safe(fn, default=None, warnings=None):
    try:
        return fn()
    except ApiMismatch as e:
        if warnings is not None and e.name not in warnings:
            warnings[e.name] = e.errors[:3]
        return default
    except Exception as e:  # noqa: BLE001 - listing should survive one bad field
        if warnings is not None:
            warnings.setdefault("errors", []).append(str(e)[:300])
        return default


# ---------- values ----------

def _to_json(v, depth=0):
    if depth > 5:
        return str(v)
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (unreal.Name, unreal.Text)):
        return str(v)
    if isinstance(v, unreal.EnumBase):
        return getattr(v, "name", str(v))
    if isinstance(v, unreal.Object):
        try:
            return v.get_path_name()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, unreal.StructBase):
        try:
            return v.export_text()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, (unreal.Map, dict)):
        return {str(_to_json(k, depth + 1)): _to_json(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, unreal.Array, unreal.Set)):
        return [_to_json(x, depth + 1) for x in v]
    return str(v)


def _from_json(value, current):
    """Convert a JSON value for set_editor_property, using the current value as a type hint.
    {"py": "<expression>"} is evaluated in the editor with `unreal` in scope."""
    if isinstance(value, dict) and set(value) == {"py"}:
        return eval(value["py"], {"unreal": unreal})  # noqa: S307 - the user asked for exactly this
    if isinstance(current, unreal.EnumBase) and isinstance(value, str):
        enum_type = type(current)
        for cand in (value, value.upper()):
            if hasattr(enum_type, cand):
                return getattr(enum_type, cand)
        raise ToolError(f"{value!r} is not a value of {enum_type.__name__}")
    if isinstance(current, unreal.StructBase):
        if isinstance(value, str):
            new = type(current)()
            if hasattr(new, "import_text"):
                new.import_text(value)
                return new
            raise ToolError("this struct cannot be set from text; use {\"py\": \"unreal.X(...)\"}")
        if isinstance(value, dict):
            new = type(current)()
            try:
                new.import_text(current.export_text())
            except Exception:  # noqa: BLE001
                pass
            for k, v in value.items():
                new.set_editor_property(k, _from_json(v, new.get_editor_property(k)))
            return new
    if isinstance(value, str) and value.startswith("/") and (current is None or isinstance(current, unreal.Object)):
        if isinstance(current, unreal.Class) or value.endswith("_C"):
            obj = unreal.load_class(None, value)
        else:
            obj = unreal.EditorAssetLibrary.load_asset(value) or unreal.load_object(None, value)
        if obj is None:
            raise ToolError(f"could not load {value}")
        return obj
    return value


# ---------- assets, blueprints, graphs ----------

def _load(path):
    obj = None
    try:
        obj = unreal.EditorAssetLibrary.load_asset(path)
    except Exception:  # noqa: BLE001
        obj = None
    if obj is None:
        obj = unreal.load_object(None, path)
    if obj is None:
        raise ToolError(f"asset not found: {path}")
    return obj


def _blueprint(path):
    obj = _load(path)
    if isinstance(obj, unreal.Blueprint):
        return obj
    bp = _safe(lambda: _lib_fn("get_blueprint_asset", obj))
    if isinstance(bp, unreal.Blueprint):
        return bp
    raise ToolError(f"{path} is not a Blueprint (it is a {type(obj).__name__})")


def _graph_editor(bp, graph):
    graph = graph or "EventGraph"
    ge = _safe(lambda: _lib_fn("get_graph_editor_by_name", bp, graph))
    if ge is None:
        g = _safe(lambda: _lib_fn("find_graph", bp, graph))
        if g is None and graph == "EventGraph":
            g = _safe(lambda: _lib_fn("find_event_graph", bp))
        if g is not None:
            ge = _lib_fn("get_graph_editor", g)
    if ge is None:
        names = _safe(lambda: _graph_names(bp), [])
        raise ToolError(f"graph {graph!r} not found on {bp.get_name()}; graphs: {names}")
    return ge


def _any_graph_editor(bp):
    """A graph editor for member-level operations: the EventGraph, else the first graph."""
    try:
        return _graph_editor(bp, "EventGraph")
    except ToolError:
        names = _graph_names(bp)
        if not names:
            raise ToolError(f"{bp.get_name()} has no graphs to edit through")
        return _graph_editor(bp, names[0])


def _graph_names(bp):
    for fn in ("list_graph_names", "list_graphs"):
        try:
            return [g if isinstance(g, str) else (g.get_name() if isinstance(g, unreal.Object) else str(g))
                    for g in _as_list(_lib_fn(fn, bp))]
        except ApiMismatch:
            continue
    return []


def _nodes(ge):
    nodes = [n for n in _as_list(_ge_fn(ge, "list_all_nodes")) if isinstance(n, unreal.Object)]
    # The 5.8 graph API lists K2 nodes and comments separately.
    comments = _safe(lambda: _ge_fn(ge, "list_comment_nodes"), [])
    seen = {n.get_name() for n in nodes}
    nodes.extend(n for n in _as_list(comments) if isinstance(n, unreal.Object) and n.get_name() not in seen)
    return nodes


def _node(ge, node_id):
    nodes = _nodes(ge)
    for n in nodes:
        if n.get_name() == node_id:
            return n
    for n in nodes:
        if n.get_name().lower() == str(node_id).lower():
            return n
    raise ToolError(f"node {node_id!r} not in graph; nodes: {[n.get_name() for n in nodes][:80]}")


def _pins(ge, node):
    return _as_list(_node_fn(ge, "list_all_pins", node))


def _pin_name(pin):
    return str(_pin_fn("get_pin_name", pin))


def _pin_dir(pin):
    d = str(_pin_fn("get_pin_direction", pin)).upper()
    return "out" if "OUTPUT" in d else "in"


def _find_pin(ge, node, name, direction=None):
    pins = _pins(ge, node)
    want = str(name).lower()
    matches = [p for p in pins if _pin_name(p).lower() == want]
    if direction:
        matches = [p for p in matches if _pin_dir(p) == direction]
    if len(matches) == 1:
        return matches[0]
    listing = [f"{_pin_name(p)} ({_pin_dir(p)})" for p in pins]
    if not matches:
        raise ToolError(f"pin {name!r}{' (' + direction + ')' if direction else ''} not on {node.get_name()}; pins: {listing}")
    raise ToolError(f"pin name {name!r} is ambiguous on {node.get_name()}; give a direction. Pins: {listing}")


def _pin_info(pin, warnings):
    info = {"name": _safe(lambda: _pin_name(pin), "?", warnings),
            "dir": _safe(lambda: _pin_dir(pin), "?", warnings)}
    t = _safe(lambda: _pin_fn("get_pin_type_display_string", pin), None, warnings)
    if t is None:
        t = _safe(lambda: _to_json(_pin_fn("get_pin_type", pin)), None, warnings)
    if t is not None:
        info["type"] = str(t)
    val = _safe(lambda: _pin_fn("get_pin_value", pin), None, warnings)
    if val not in (None, ""):
        info["value"] = str(val)
    links = []
    for other in _as_list(_safe(lambda: _pin_fn("list_connected_pins", pin), [], warnings)):
        owner = _safe(lambda: _pin_fn("get_owning_node", other), None, warnings)
        links.append(f"{owner.get_name() if owner else '?'}.{_safe(lambda: _pin_name(other), '?', warnings)}")
    if links:
        info["links"] = links
    return info


def _is_k2(node):
    k2 = getattr(unreal, "K2Node", None)
    return k2 is None or isinstance(node, k2)


def _node_info(ge, node, include_pins, warnings):
    info = {"id": node.get_name(), "class": node.get_class().get_name()}
    if not _is_k2(node):
        # Comment boxes and other non-K2 nodes: the pin/title/position API only accepts K2Nodes.
        text = _safe(lambda: _lib_fn("get_comment_text", node), None)
        if text is None:
            text = _safe(lambda: node.get_editor_property("node_comment"), None)
        if text:
            info["title"] = str(text)
        inside = _safe(lambda: _as_list(_lib_fn("get_nodes_in_comment", node)), None)
        if inside:
            info["contains"] = [n.get_name() for n in inside if isinstance(n, unreal.Object)]
        info["pos"] = _safe(lambda: [int(node.wc_get_property_value("NodePosX")),
                                     int(node.wc_get_property_value("NodePosY"))], None, warnings)
        size = _safe(lambda: node.get_node_size(), None, warnings)
        if size is not None:
            info["size"] = [int(size.x), int(size.y)]
        guid = _safe(lambda: node.wc_get_property_value("NodeGuid"), None)
        if guid is not None:
            info["guid_valid"] = _safe(lambda: unreal.GuidLibrary.is_valid_guid(guid), None)
            info["guid"] = _safe(lambda: guid.export_text(), None)
        return info
    title = _safe(lambda: _node_fn(ge, "get_node_title", node), None, warnings)
    if title is not None:
        info["title"] = str(title)
    info["pos"] = _safe(lambda: _get_pos(ge, node), None, warnings)
    comment = _safe(lambda: node.get_editor_property("node_comment"), "")
    if comment:
        info["comment"] = str(comment)
    if include_pins:
        info["pins"] = [_pin_info(p, warnings) for p in _safe(lambda: _pins(ge, node), [], warnings)]
    return info


def _get_pos(ge, node):
    try:
        p = _node_fn(ge, "get_node_pos", node)
        if hasattr(p, "x"):
            return [int(p.x), int(p.y)]
    except ApiMismatch:
        pass
    return [int(node.get_editor_property("node_pos_x")), int(node.get_editor_property("node_pos_y"))]


def _set_pos(ge, node, x, y):
    if not _is_k2(node):
        node.modify()
        cdo = unreal.get_default_object(unreal.SystemLibrary)
        for prop, value in (("NodePosX", int(x)), ("NodePosY", int(y))):
            cdo.call_method("SetIntPropertyByName", args=(node, prop, value))
        return
    for pos in (unreal.IntPoint(int(x), int(y)), unreal.Vector2D(float(x), float(y))):
        try:
            _node_fn(ge, "set_node_pos", node, pos)
            return
        except ApiMismatch:
            continue
    try:
        node.modify()
        node.set_editor_property("node_pos_x", int(x))
        node.set_editor_property("node_pos_y", int(y))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"could not set the node position: {e}")


def _type_str(ptype):
    try:
        return describe_pin_type_text(ptype.export_text())  # from typespec.py
    except Exception:  # noqa: BLE001
        return str(_to_json(ptype))


def _member_vars(bp):
    """The Blueprint's own variables (not every inherited native property)."""
    try:
        return [str(v) for v in _as_list(_lib_fn("list_member_variable_names", bp, False))]
    except ApiMismatch:
        return [str(v) for v in _as_list(_lib_fn("list_member_variable_names", bp))]


def _pin_type(spec):
    tree = parse(spec)  # from typespec.py

    def build(t):
        kind = t[0]
        if kind == "basic":
            return _lib_fn("get_basic_type_by_name", t[1])
        if kind == "struct":
            st = unreal.load_object(None, t[1])
            if st is None:
                raise ToolError(f"struct not found: {t[1]}")
            return _lib_fn("get_struct_type", st)
        if kind in ("object", "class"):
            cls = unreal.load_class(None, t[1])
            if cls is None:
                raise ToolError(f"class not found: {t[1]}")
            return _lib_fn("get_object_reference_type" if kind == "object" else "get_class_reference_type", cls)
        if kind == "array":
            return _lib_fn("get_array_type", build(t[1]))
        if kind == "set":
            return _lib_fn("get_set_type", build(t[1]))
        if kind == "map":
            return _lib_fn("get_map_type", build(t[1]), build(t[2]))
        raise ToolError(f"unsupported type {t!r}")
    return build(tree)


def _target(spec):
    """Resolve the object whose properties are read/written. Returns (object, blueprint or None)."""
    if spec.startswith("actor:"):
        label = spec[len("actor:"):]
        sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for a in sub.get_all_level_actors():
            if a.get_actor_label() == label or a.get_name() == label:
                return a, None
        raise ToolError(f"no actor labelled {label!r} in the open level")
    obj = _load(spec)
    if isinstance(obj, unreal.Blueprint):
        gen = obj.generated_class()
        if gen is None:
            raise ToolError("Blueprint has no generated class yet; compile it first")
        return unreal.get_default_object(gen), obj
    if isinstance(obj, unreal.Class):
        return unreal.get_default_object(obj), None
    return obj, None


_DOC_PROP = _re.compile(r"^\s*-\s*``(\w+)``\s*\(([^)]*)\):\s*\[(Read-Write|Read-Only)\]", _re.M)


def _doc_properties(obj):
    found = {}
    for cls in type(obj).__mro__:
        for m in _DOC_PROP.finditer(cls.__doc__ or ""):
            found.setdefault(m.group(1), {"type": m.group(2), "access": m.group(3)})
    return found


# ---------- tools ----------

def t_ping(a):
    return {"version": ARKMCP_VERSION,
            "engine_version": _safe(lambda: unreal.SystemLibrary.get_engine_version()),
            "project_dir": _safe(lambda: unreal.SystemLibrary.get_project_directory()),
            "graph_api": {n: hasattr(unreal, n) for n in _HOST_NAMES}}


def t_api_search(a):
    term = a["term"].lower()
    names = [n for n in dir(unreal) if term in n.lower() and not n.startswith("_")]
    members = []
    if a.get("include_members", True):
        for cls_name in _HOST_NAMES:
            cls = getattr(unreal, cls_name, None)
            if cls:
                members += [f"{cls_name}.{m}" for m in dir(cls) if term in m.lower() and not m.startswith("_")]
    limit = int(a.get("limit", 200))
    return {"types": names[:limit], "graph_library_members": members[:limit]}


def t_api_help(a):
    path = a["name"]
    obj = unreal
    for part in path.replace("unreal.", "", 1).split("."):
        obj = getattr(obj, part)
    out = {"name": path, "doc": (getattr(obj, "__doc__", "") or "")[: int(a.get("max_chars", 12000))]}
    if isinstance(obj, type):
        out["members"] = [m for m in dir(obj) if not m.startswith("_")]
    return out


def t_find_assets(a):
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    datas = ar.get_assets_by_path(a.get("path", "/Game"), recursive=a.get("recursive", True))
    cls = (a.get("class_name") or "").lower()
    needle = (a.get("name_contains") or "").lower()
    limit = int(a.get("limit", 200))
    out, total = [], 0
    for d in datas:
        cname = str(d.asset_class_path.asset_name) if hasattr(d, "asset_class_path") else str(d.asset_class)
        aname = str(d.asset_name)
        if cls and cname.lower() != cls:
            continue
        if needle and needle not in aname.lower():
            continue
        total += 1
        if len(out) < limit:
            out.append({"path": f"{d.package_name}.{aname}", "class": cname})
    return {"count": total, "assets": out, "truncated": total > len(out)}


def t_get_properties(a):
    obj, bp = _target(a["target"])
    names = a.get("names")
    listed = {}
    if not names:
        listed = _doc_properties(obj)
        names = list(listed)
        if bp is not None:
            bp_vars = _safe(lambda: _member_vars(bp), [])
            for v in bp_vars:
                listed.setdefault(v, {"type": "blueprint variable", "access": "Read-Write"})
            names = bp_vars + [n for n in names if n not in bp_vars]
    values, errors = {}, {}
    for n in names[: int(a.get("limit", 400))]:
        try:
            values[n] = _to_json(obj.get_editor_property(n))
        except Exception as e:  # noqa: BLE001
            errors[n] = str(e)[:200]
    out = {"object": obj.get_path_name(), "values": values}
    if listed:
        out["info"] = {k: v for k, v in listed.items() if k in values}
    if errors:
        out["errors"] = errors
    return out


def t_set_properties(a):
    obj, bp = _target(a["target"])
    done, errors = {}, {}
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: set properties"):
        if bp is not None:
            bp.modify()
        obj.modify()
        for name, value in a["values"].items():
            try:
                current = _safe(lambda: obj.get_editor_property(name))
                obj.set_editor_property(name, _from_json(value, current))
                done[name] = _to_json(obj.get_editor_property(name))
            except Exception as e:  # noqa: BLE001
                errors[name] = str(e)[:300]
    if bp is not None and a.get("compile"):
        _lib_fn("compile_blueprint", bp)
    out = {"set": done, "saved": False}
    if errors:
        out["errors"] = errors
    return out


def t_save_assets(a):
    saved, failed = [], []
    for p in a["paths"]:
        (saved if unreal.EditorAssetLibrary.save_asset(p, only_if_is_dirty=False) else failed).append(p)
    return {"saved": saved, "failed": failed}


def t_blueprint_info(a):
    bp = _blueprint(a["path"])
    w = {}
    info = {"path": bp.get_path_name()}
    parent = _safe(lambda: _lib_fn("get_blueprint_parent_class", bp), None, w)
    if parent is None:
        parent = _safe(lambda: bp.get_editor_property("parent_class"), None)
    info["parent_class"] = parent.get_path_name() if parent else None
    info["graphs"] = _safe(lambda: _graph_names(bp), [], w)
    cdo = _safe(lambda: unreal.get_default_object(bp.generated_class()), None, w)
    variables = []
    for v in _safe(lambda: _member_vars(bp), [], w):
        entry = {"name": v}
        ptype = _safe(lambda: _lib_fn("get_member_variable_type", bp, v), None, w)
        if ptype is not None:
            entry["type"] = _type_str(ptype)
        cat = _safe(lambda: _lib_fn("get_blueprint_variable_category", bp, v), None)
        if cat:
            entry["category"] = str(cat)
        if cdo is not None:
            entry["default"] = _safe(lambda: _to_json(cdo.get_editor_property(v)), None)
        variables.append(entry)
    info["variables"] = variables
    for key, fn in (("functions", "list_functions"), ("events", "list_events")):
        vals = _safe(lambda: _as_list(_lib_fn(fn, bp)), None, w)
        if vals is not None:
            # BlueprintFunctionInfo{name, description, is_implemented}; list what this Blueprint implements
            done = [str(getattr(x, "name", x)) for x in vals if getattr(x, "is_implemented", True)]
            info[key] = done
            if len(done) < len(vals):
                info[key + "_overridable_not_implemented"] = len(vals) - len(done)
    disp = _safe(lambda: _as_list(_lib_fn("list_event_dispatchers", bp)), None, w)
    if disp is not None:
        info["event_dispatchers"] = [str(x) for x in disp]
    if w:
        info["warnings"] = w
    return info


def t_compile_blueprint(a):
    bp = _blueprint(a["path"])
    _lib_fn("compile_blueprint", bp)
    w, problems = {}, {}
    for g in _graph_names(bp):
        ge = _safe(lambda: _graph_editor(bp, g), None, w)
        if ge is None:
            continue
        for kind, fn in (("errors", "list_nodes_with_errors"), ("warnings", "list_nodes_with_warnings")):
            for n in _as_list(_safe(lambda: _ge_fn(ge, fn), [], w)):
                if isinstance(n, unreal.Object):
                    problems.setdefault(kind, []).append({
                        "graph": g, "node": n.get_name(),
                        "message": str(_safe(lambda: n.get_editor_property("error_msg"), ""))})
    status = _safe(lambda: _to_json(bp.get_editor_property("status")), None)
    out = {"status": status, "problems": problems, "saved": False}
    if w:
        out["api_warnings"] = w
    return out


def t_add_variable(a):
    bp = _blueprint(a["path"])
    name, ptype = a["name"], _pin_type(a["type"])
    default = a.get("default")
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: add variable"):
        if default is None:
            # BlueprintEditorLibrary.add_member_variable(blueprint, member_name, variable_type) -> bool
            ok = _lib_fn("add_member_variable", bp, name, ptype)
        else:
            # Only the graph editor's method takes a default: ge.add_member_variable(member_name, pin_type, value)
            ge = _any_graph_editor(bp)
            ok = _try("add_member_variable", [(ge, (name, ptype, str(default))),
                                              (None, (bp, name, ptype, str(default)))])
        if ok is False:
            raise ToolError(f"Unreal refused to add {name!r} (name already used, or invalid default?)")
        for key, fn in (("category", "set_blueprint_variable_category"),
                        ("instance_editable", "set_blueprint_variable_instance_editable"),
                        ("expose_on_spawn", "set_blueprint_variable_expose_on_spawn")):
            if key in a:
                _lib_fn(fn, bp, name, a[key])
    return {"added": name, "type": a["type"], "note": "compile_blueprint, then save_assets to keep it"}


def t_remove_variable(a):
    bp = _blueprint(a["path"])
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: remove variable"):
        ok = _try("remove_member_variable", [(_any_graph_editor(bp), (a["name"],)), (None, (bp, a["name"]))])
    if ok is False:
        raise ToolError(f"Unreal could not remove {a['name']!r} (not a variable of this Blueprint?)")
    return {"removed": a["name"]}


def t_create_function(a):
    bp = _blueprint(a["path"])
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: create function"):
        ge = _lib_fn("create_and_edit_function_graph", bp, a["name"])
    w = {}
    graph = _safe(lambda: _ge_fn(ge, "get_graph"), None, w)
    return {"graph": graph.get_name() if isinstance(graph, unreal.Object) else a["name"],
            "nodes": [_node_info(ge, n, True, w) for n in _safe(lambda: _nodes(ge), [], w)]}


def t_list_graph(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    w = {}
    flt = (a.get("filter") or "").lower()
    nodes = []
    for n in _nodes(ge):
        info = _node_info(ge, n, a.get("include_pins", True), w)
        if flt and flt not in (info["id"] + " " + info.get("title", "") + " " + info["class"]).lower():
            continue
        nodes.append(info)
    out = {"graph": a.get("graph") or "EventGraph", "nodes": nodes}
    if w:
        out["api_warnings"] = w
    return out


def t_list_available_nodes(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    # ge.list_available_nodes(context_pins) -> Array[str]; no context pins = the full palette
    names = [str(x) for x in _as_list(_try("list_available_nodes", [(ge, ([],)), (ge, ())]))]
    flt = (a.get("filter") or "").lower()
    if flt:
        names = [n for n in names if flt in n.lower()]
    limit = int(a.get("limit", 200))
    return {"count": len(names), "nodes": names[:limit], "truncated": len(names) > limit}


def _positions(x, y):
    return [unreal.Vector2D(float(x), float(y)), unreal.IntPoint(int(x), int(y))]


def _component_template(bp, name):
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    matches, available = {}, set()
    for handle in subsystem.k2_gather_subobject_data_for_blueprint(bp):
        data = library.get_data(handle)
        component = library.get_associated_object(data)
        if not isinstance(component, unreal.ActorComponent):
            continue
        variable = str(library.get_variable_name(data))
        available.add(variable)
        aliases = (variable, component.get_name(), component.get_path_name(),
                   component.get_name().removesuffix("_GEN_VARIABLE"))
        if str(name).casefold() in {s.casefold() for s in aliases}:
            # Gather can expose the same template through multiple handles.
            matches[component.get_path_name()] = component
    if len(matches) != 1:
        raise ToolError(f"component {name!r} was not uniquely found; components: {sorted(available)}")
    return next(iter(matches.values()))


def _make_node(ge, bp, a):
    kind = a["kind"]
    x, y = a.get("x", 0), a.get("y", 0)
    if kind == "action":
        # ge.create_node_from_name(node_with_category, location, context_pins, declaring_class=None)
        extra = ()
        if a.get("declaring_class"):
            cls = unreal.load_class(None, a["declaring_class"])
            if cls is None:
                raise ToolError(f"class not found: {a['declaring_class']}")
            extra = (cls,)
        return _try("create_node_from_name", [(ge, (a["name"], pos, []) + extra) for pos in _positions(x, y)])
    if kind == "call_function":
        return _ge_fn(ge, "add_call_function_node", a["function_path"])
    if kind == "branch":
        return _ge_fn(ge, "add_branch_node")
    if kind == "custom_event":
        return _ge_fn(ge, "add_custom_event_node", a["name"])
    if kind == "event":
        existing = _safe(lambda: _ge_fn(ge, "find_event_node", a["name"]))
        if isinstance(existing, unreal.Object):
            return existing
        # BlueprintEditorLibrary.add_event_override(blueprint, event_name, position)
        return _try("add_event_override", [(None, (bp, a["name"], pos)) for pos in _positions(x, y)])
    if kind in ("get_variable", "set_variable"):
        fn = "add_get_member_variable_node" if kind == "get_variable" else "add_set_member_variable_node"
        if a.get("class_path"):
            return _ge_fn(ge, fn, a["name"], a["class_path"])
        return _ge_fn(ge, fn, a["name"])
    if kind == "get_local":
        return _ge_fn(ge, "add_get_local_variable_node", a["name"])
    if kind == "set_local":
        return _ge_fn(ge, "add_set_local_variable_node", a["name"])
    if kind == "macro":
        return _ge_fn(ge, "add_macro_node", a["macro_path"])
    if kind == "return":
        return _ge_fn(ge, "add_return_node")
    if kind == "dispatcher_event":
        return _ge_fn(ge, "add_dispatcher_event_node", a["name"])
    if kind == "component_event":
        return _ge_fn(ge, "add_component_bound_event_node", _component_template(bp, a["component"]), a["name"])
    if kind == "comment":
        x, y = a.get("x", 0), a.get("y", 0)
        w, h = a.get("width", 400), a.get("height", 200)
        attempts = [(ge, (a.get("text", ""), unreal.Vector2D(float(x), float(y)), unreal.Vector2D(float(w), float(h)))),
                    (ge, (a.get("text", ""), unreal.IntPoint(int(x), int(y)), unreal.IntPoint(int(w), int(h))))]
        return _try("add_comment_node", attempts)
    raise ToolError(f"unknown node kind {kind!r}")


def t_add_node(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: add node"):
        node = _make_node(ge, bp, a)
        if isinstance(node, tuple):
            node = next((n for n in node if isinstance(n, unreal.Object)), None)
        if not isinstance(node, unreal.Object):
            raise ToolError("the editor did not create a node (check the name/path)")
        if a["kind"] == "comment":
            # This DevKit's AddCommentNode omits CreateNewGuid. The Wildcard
            # getter returns a live struct wrapper; assign through that wrapper
            # because NodeGuid is protected from set_editor_property.
            guid = node.wc_get_property_value("NodeGuid")
            if not unreal.GuidLibrary.is_valid_guid(guid):
                node.modify()
                guid.assign(unreal.GuidLibrary.new_guid())
                if not unreal.GuidLibrary.is_valid_guid(node.wc_get_property_value("NodeGuid")):
                    _ge_fn(ge, "remove_comment_node", node)
                    raise ToolError("comment GUID could not be initialized; comment removed")
        if a["kind"] != "comment" and ("x" in a or "y" in a):
            _set_pos(ge, node, a.get("x", 0), a.get("y", 0))
    w = {}
    return {"node": _node_info(ge, node, True, w), **({"api_warnings": w} if w else {})}


def t_connect(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    src = _find_pin(ge, _node(ge, a["from_node"]), a["from_pin"], "out")
    dst = _find_pin(ge, _node(ge, a["to_node"]), a["to_pin"], "in")
    allowed = _safe(lambda: _pin_fn("can_create_connection", src, dst), None)
    if allowed is False:
        raise ToolError("Unreal will not allow this connection (incompatible pin types or directions)")
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: connect pins"):
        ok = _pin_fn("try_create_connection", src, dst)
    w = {}
    return {"connected": bool(ok), "from": _pin_info(src, w), "to": _pin_info(dst, w)}


def t_disconnect(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    pin = _find_pin(ge, _node(ge, a["node"]), a["pin"], a.get("direction"))
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: break pin links"):
        if a.get("other_node"):
            other = _find_pin(ge, _node(ge, a["other_node"]), a["other_pin"], None)
            _pin_fn("break_single_pin_link", pin, other)
        else:
            _pin_fn("break_pin_links", pin)
    return {"pin": _pin_info(pin, {})}


def t_set_pin_value(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    pin = _find_pin(ge, _node(ge, a["node"]), a["pin"], "in")
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: set pin value"):
        ok = _pin_fn("set_pin_value", pin, str(a["value"]))
    if ok is False:
        raise ToolError(f"Unreal rejected value {a['value']!r} for this pin")
    return {"pin": _pin_info(pin, {})}


def t_move_node(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    node = _node(ge, a["node"])
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: move node"):
        _set_pos(ge, node, a["x"], a["y"])
    return {"node": node.get_name(), "pos": [a["x"], a["y"]]}


def t_remove_nodes(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    nodes = [_node(ge, n) for n in a["nodes"]]
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: remove nodes"):
        k2 = [n for n in nodes if _is_k2(n)]
        if k2:
            _ge_fn(ge, "remove_nodes", k2)
        for node in nodes:
            if not _is_k2(node):
                _ge_fn(ge, "remove_comment_node", node)
    return {"removed": [n for n in a["nodes"]]}


def _estimated_bounds(ge, node):
    info = _node_info(ge, node, True, {})
    pins = {d: [(p["name"], p.get("type", ""), p.get("value"), bool(p.get("links")))
                for p in info.get("pins", []) if p["dir"] == d and p["name"] != "OutputDelegate"]
            for d in ("in", "out")}
    adv = str(_safe(lambda: node.wc_get_property_value("AdvancedPinDisplay"), "")).upper()
    state = str(_safe(lambda: node.wc_get_property_value("EnabledState"), "")).upper()
    w, h = estimate_node_size(info["class"], info.get("title", info["id"]), pins["in"], pins["out"],
                              advanced_hidden="HIDDEN" in adv, dev_only="DEVELOPMENT_ONLY" in state)
    x, y = info["pos"]
    return x, y, x+w, y+h


def t_refit_comment(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    comment = _node(ge, a["node"])
    if comment.get_class().get_name() != "EdGraphNode_Comment":
        raise ToolError("node must be a comment box")
    members = [_node(ge, name) for name in a["nodes"]]
    if not members or any(not _is_k2(n) for n in members):
        raise ToolError("nodes must contain at least one K2 node")
    padding = int(a.get("padding", 50))
    if padding < 0:
        raise ToolError("padding must be nonnegative")
    boxes = [_estimated_bounds(ge, node) for node in members]
    x, y = int(min(b[0] for b in boxes))-padding, int(min(b[1] for b in boxes))-padding-32
    right, bottom = int(max(b[2] for b in boxes))+padding, int(max(b[3] for b in boxes))+padding
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: refit comment"):
        comment.modify()
        cdo = unreal.get_default_object(unreal.SystemLibrary)
        for prop, value in (("NodePosX", x), ("NodePosY", y), ("NodeWidth", right-x), ("NodeHeight", bottom-y)):
            # These reflected engine setters have no generated Python glue.
            cdo.call_method("SetIntPropertyByName", args=(comment, prop, value))
        if any(int(comment.wc_get_property_value(prop)) != value for prop, value in
               (("NodePosX", x), ("NodePosY", y), ("NodeWidth", right-x), ("NodeHeight", bottom-y))):
            raise ToolError("comment bounds did not match the requested bounds")
    return {"comment": _node_info(ge, comment, False, {}), "nodes": a["nodes"], "saved": False}


def t_create_blueprint(a):
    path = a["path"].rstrip("/")
    if not _re.fullmatch(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)+", path):
        raise ToolError("path must be a full package path, without an object or file suffix")
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        raise ToolError(f"asset already exists: {path}; refusing to replace it")
    parent = unreal.load_class(None, a.get("parent_class", "/Script/Engine.Actor"))
    if parent is None:
        raise ToolError("parent class was not found")
    folder, name = path.rsplit("/", 1)
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", parent)
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: create Blueprint"):
        bp = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Blueprint, factory)
    if bp is None:
        raise ToolError("Unreal refused to create the Blueprint")
    return {"created": bp.get_path_name(), "saved": False}


def _reroute_action(ge):
    """The palette name of 'Add Reroute Node' differs by graph ('|AddRerouteNode...' or '||AddRerouteNode...')."""
    names = [str(x) for x in _as_list(_try("list_available_nodes", [(ge, ([],)), (ge, ())]))]
    for n in names:
        if n.rstrip(".").endswith("AddRerouteNode"):
            return n
    raise ToolError("this graph's palette has no 'Add Reroute Node' action")


def t_add_reroute(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    return t_add_node({**a, "kind": "action", "name": _reroute_action(ge)})


def t_route_bus(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    source = _find_pin(ge, _node(ge, a["from_node"]), a["from_pin"], "out")
    source_type = str(_pin_fn("get_pin_type_display_string", source)).lower()
    if source_type == "exec" or "delegate" in source_type:
        raise ToolError("route_bus supports data pins; use Sequence and explicit reroutes for execution flow")
    taps = a["taps"]
    if not taps or len(taps) > 64:
        raise ToolError("taps must contain between 1 and 64 destinations")
    destinations, seen = [], set()
    for tap in taps:
        key = (tap["node"], tap["pin"])
        if key in seen:
            raise ToolError("duplicate bus destination")
        seen.add(key)
        pin = _find_pin(ge, _node(ge, tap["node"]), tap["pin"], "in")
        if _as_list(_pin_fn("list_connected_pins", pin)):
            raise ToolError(f"destination {key} is already connected; existing wiring was preserved")
        destination_type = str(_pin_fn("get_pin_type_display_string", pin)).lower()
        if destination_type not in (source_type, "wildcard"):
            raise ToolError(f"bus types must match without implicit conversion: {source_type} -> {destination_type}")
        if _pin_fn("can_create_connection", source, pin) is False:
            raise ToolError(f"incompatible bus destination: {key}")
        destinations.append((tap, pin))
    if any(int(taps[i]["x"]) >= int(taps[i+1]["x"]) for i in range(len(taps)-1)):
        raise ToolError("tap x positions must increase from left to right")
    obstacles = [_estimated_bounds(ge, n) for n in _nodes(ge) if _is_k2(n)]
    for tap in taps:
        x, y = int(tap["x"]), int(a["y"])
        if any(x < r and x+32 > l and y < b and y+16 > t for l,t,r,b in obstacles):
            raise ToolError("a bus tap would overlap an existing node; choose another x/y position")
        obstacles.append((x,y,x+32,y+16))
    made = []
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: route data bus"):
        try:
            previous = source
            for tap, destination in destinations:
                knot = _make_node(ge, bp, {"kind": "action", "name": _reroute_action(ge),
                                           "x": int(tap["x"]), "y": int(a["y"])})
                if not isinstance(knot, unreal.Object):
                    raise ToolError("Unreal did not create the reroute")
                made.append(knot)
                _set_pos(ge, knot, tap["x"], a["y"])
                inlet = _find_pin(ge, knot, "InputPin", "in")
                outlet = _find_pin(ge, knot, "OutputPin", "out")
                if not _pin_fn("try_create_connection", previous, inlet):
                    raise ToolError("could not connect bus segment")
                if not _pin_fn("try_create_connection", outlet, destination):
                    raise ToolError("could not connect bus tap")
                previous = outlet
        except Exception:
            if made:
                _ge_fn(ge, "remove_nodes", made)
            raise
    return {"reroutes": [_node_info(ge, n, True, {}) for n in made], "taps": taps,
            "saved": False, "note": "existing node positions and existing connections were preserved"}


def t_add_pin(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    node = _node(ge, a["node"])
    with unreal.ScopedEditorTransaction("ArkDevkitMcp: add pin"):
        ok = _node_fn(ge, "add_node_pin", node)
    if ok is False:
        raise ToolError("this node type does not support adding pins")
    return {"node": _node_info(ge, node, True, {})}




def _gather_geometry(ge):
    """Every K2 node's estimated size/position and every wire, for layout.py.
    Edges: (src, src_pin_offset, dst, dst_pin_offset, is_exec, src_pin_name, dst_pin_name)."""
    all_nodes = [n for n in _nodes(ge) if _is_k2(n)]
    by_name = {n.get_name(): n for n in all_nodes}
    geo, pin_index, raw_links = {}, {}, []
    for n in all_nodes:
        name = n.get_name()
        pins = _as_list(_node_fn(ge, "list_all_pins", n))
        counters = {"in": 0, "out": 0}
        has_exec = False
        size_pins = {"in": [], "out": []}
        for p in pins:
            pname, d = _pin_name(p), _pin_dir(p)
            ptype = str(_safe(lambda: _pin_fn("get_pin_type_display_string", p), ""))
            if pname == "OutputDelegate" and ptype.lower() == "delegate":
                pin_index[(name, d, pname)] = 0   # drawn in the title bar, not a pin row
            else:
                pin_index[(name, d, pname)] = counters[d]
                counters[d] += 1
            if ptype.lower() == "exec":
                has_exec = True
            if pname != "OutputDelegate":
                connected = bool(_as_list(_safe(lambda: _pin_fn("list_connected_pins", p), [])))
                value = _safe(lambda: _pin_fn("get_pin_value", p), None) if d == "in" else None
                size_pins[d].append((pname, ptype, value, connected))
            if d == "out":
                for other in _as_list(_safe(lambda: _pin_fn("list_connected_pins", p), [])):
                    owner = _safe(lambda: _pin_fn("get_owning_node", other), None)
                    if owner is not None:
                        raw_links.append((name, pname, owner.get_name(), _pin_name(other), ptype.lower() == "exec"))
        pos = _get_pos(ge, n)
        cls = n.get_class().get_name()
        title = str(_safe(lambda: _node_fn(ge, "get_node_title", n), name))
        adv = str(_safe(lambda: n.wc_get_property_value("AdvancedPinDisplay"), "")).upper()
        state = str(_safe(lambda: n.wc_get_property_value("EnabledState"), "")).upper()
        w, h = estimate_node_size(cls, title, size_pins["in"], size_pins["out"],   # layout.py
                                  advanced_hidden="HIDDEN" in adv, dev_only="DEVELOPMENT_ONLY" in state)
        geo[name] = {"w": w, "h": h, "x": pos[0], "y": pos[1], "exec": has_exec, "knot": cls == "K2Node_Knot"}
    edges = []
    for src, sp, dst, dp, is_exec in raw_links:
        if dst in geo:
            edges.append((src, pin_offset(pin_index.get((src, "out", sp), 0)),
                          dst, pin_offset(pin_index.get((dst, "in", dp), 0)), is_exec, sp, dp))
    return geo, edges, by_name


def t_tidy_graph(a):
    bp = _blueprint(a["path"])
    ge = _graph_editor(bp, a.get("graph"))
    geo, edges, by_name = _gather_geometry(ge)
    if a.get("export_geometry"):
        return {"nodes": geo, "edges": [list(e) for e in edges]}

    wanted = a.get("nodes")
    gx, gy = a.get("gap_x", 48), a.get("gap_y", 16)
    if wanted:
        missing = [w for w in wanted if w not in geo]
        if missing:
            raise ToolError(f"nodes not in graph: {missing}")
        lanes = []
        if a.get("ladder"):
            # ladder conventions (layout.py place_ladder): may shift the downstream rail block right and adds
            # knot lanes for results that travel far. Opt-in only.
            plan = place_ladder(geo, edges, wanted, make_room=True, result_lanes=True, gap_x=a.get("gap_x", 96), gap_y=gy)
            positions, lanes = plan["positions"], plan["lanes"]
            mode = "ladder"
        else:
            # place only these; every other node stays put and is an obstacle  (layout.py)
            positions = place_nodes(geo, edges, wanted, gap_x=a.get("gap_x", 96), gap_y=gy)
            mode = "place"
    else:
        knots = [k for k, n in by_name.items() if n.get_class().get_name() == "K2Node_Knot"]
        if knots and not a.get("force"):
            raise ToolError(
                f"this graph has {len(knots)} reroute knots (hand routing). A full re-layout would destroy it, so it "
                "was not done. Pass nodes=[...] to place only new nodes around the existing layout, or force=true "
                "to re-lay everything.")
        positions = layout_graph(geo, edges, gap_x=gx, gap_y=gy)   # from layout.py
        mode = "full"
        lanes = []
    moved, knots = {}, []
    if not a.get("dry_run"):
        if lanes:
            _reroute_action(ge)   # fail before changing anything if knots cannot be made here
        with unreal.ScopedEditorTransaction("ArkDevkitMcp: tidy graph"):
            for name, (x, y) in positions.items():
                if [x, y] != [geo[name]["x"], geo[name]["y"]]:
                    _set_pos(ge, by_name[name], x, y)
                    moved[name] = [x, y]
            for lane in lanes:   # splice a reroute knot into the result wire
                src = _find_pin(ge, by_name[lane["src"]], lane["src_pin"], "out")
                dst = _find_pin(ge, by_name[lane["dst"]], lane["dst_pin"], "in")
                knot = _make_node(ge, bp, {"kind": "action", "name": _reroute_action(ge), "x": lane["x"], "y": lane["y"]})
                if isinstance(knot, tuple):
                    knot = next((n for n in knot if isinstance(n, unreal.Object)), None)
                if not isinstance(knot, unreal.Object):
                    raise ToolError("could not create a reroute knot for the result lane")
                _set_pos(ge, knot, lane["x"], lane["y"])
                _pin_fn("break_single_pin_link", src, dst)
                k_in = _find_pin(ge, knot, "InputPin", "in")
                k_out = _find_pin(ge, knot, "OutputPin", "out")
                if not (_pin_fn("try_create_connection", src, k_in) and _pin_fn("try_create_connection", k_out, dst)):
                    raise ToolError(f"could not route {lane['src']}.{lane['src_pin']} through the lane knot")
                knots.append({"id": knot.get_name(), "pos": [lane["x"], lane["y"]],
                              "carries": f"{lane['src']}.{lane['src_pin']} -> {lane['dst']}.{lane['dst_pin']}"})
    else:
        moved = {k: list(v) for k, v in positions.items()}
        knots = [{"pos": [l["x"], l["y"]], "carries": f"{l['src']}.{l['src_pin']} -> {l['dst']}.{l['dst_pin']}"}
                 for l in lanes]
    return {"mode": mode, "moved": moved, "lane_knots": knots, "dry_run": bool(a.get("dry_run")),
            "sizes_used": {k: [round(geo[k]["w"]), round(geo[k]["h"])] for k in moved if k in geo},
            "note": "comment boxes are not moved; Ctrl+Z undoes the whole tidy"}


TOOLS = {
    "ping": t_ping,
    "api_search": t_api_search,
    "api_help": t_api_help,
    "find_assets": t_find_assets,
    "get_properties": t_get_properties,
    "set_properties": t_set_properties,
    "save_assets": t_save_assets,
    "blueprint_info": t_blueprint_info,
    "compile_blueprint": t_compile_blueprint,
    "add_variable": t_add_variable,
    "remove_variable": t_remove_variable,
    "create_function": t_create_function,
    "list_graph": t_list_graph,
    "list_available_nodes": t_list_available_nodes,
    "add_node": t_add_node,
    "connect_pins": t_connect,
    "disconnect_pins": t_disconnect,
    "set_pin_value": t_set_pin_value,
    "move_node": t_move_node,
    "remove_nodes": t_remove_nodes,
    "add_node_pin": t_add_pin,
    "refit_comment": t_refit_comment,
    "create_blueprint": t_create_blueprint,
    "add_reroute": t_add_reroute,
    "route_bus": t_route_bus,
    "tidy_graph": t_tidy_graph,
}


def call(tool, args_json):
    try:
        fn = TOOLS.get(tool)
        if fn is None:
            raise ToolError(f"unknown editor tool {tool!r}")
        args = _json.loads(args_json) if args_json else {}
        return _json.dumps({"ok": True, "result": fn(args)}, default=str, separators=(",", ":"))
    except ApiMismatch as e:
        return _json.dumps({"ok": False, "error": f"{e.name}: no signature matched in this DevKit build",
                            "attempts": e.errors[:6], "docs": e.docs,
                            "hint": "use api_help / run_python to call the function directly"}, default=str)
    except (ToolError, TypeSpecError, KeyError) as e:
        msg = f"missing argument {e}" if isinstance(e, KeyError) else str(e)
        return _json.dumps({"ok": False, "error": msg}, default=str)
    except Exception as e:  # noqa: BLE001
        return _json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}",
                            "traceback": _traceback.format_exc()[-3000:]}, default=str)
