"""Blueprint variable type specs.

Pure Python (no editor needed). This file is also pushed into the editor together with
editor_side.py, so it must not import anything from this package.

Grammar (case-insensitive keywords, whitespace ignored around tokens):

    type   := basic
            | ("object" | "class" | "struct") ":" path
            | ("array" | "set") "<" type ">"
            | "map" "<" type "," type ">"
    basic  := bool | int | byte | real | float | double | name | string | text

Examples: "int", "array<string>", "object:/Script/Engine.Actor",
"struct:/Script/CoreUObject.Vector", "map<name, int>",
"class:/Game/Mods/MyMod/BP_Thing.BP_Thing_C".

parse() returns a tuple tree:
    ("basic", "int") | ("object"|"class"|"struct", path) | ("array"|"set", inner) | ("map", key, value)
"""

BASIC_ALIASES = {
    "bool": "bool", "boolean": "bool",
    "int": "int", "integer": "int", "int32": "int",
    "byte": "byte", "uint8": "byte",
    "real": "real", "float": "real", "double": "real",
    "name": "name", "fname": "name",
    "string": "string", "str": "string", "fstring": "string",
    "text": "text", "ftext": "text",
}

REF_KINDS = ("object", "class", "struct")
CONTAINERS = ("array", "set", "map")


class TypeSpecError(ValueError):
    pass


def parse(spec):
    if not isinstance(spec, str) or not spec.strip():
        raise TypeSpecError("type spec must be a non-empty string")
    tree, pos = _parse_at(spec, 0)
    pos = _skip_ws(spec, pos)
    if pos != len(spec):
        raise TypeSpecError(f"unexpected text at position {pos}: {spec[pos:]!r}")
    return tree


def format_spec(tree):
    kind = tree[0]
    if kind == "basic":
        return tree[1]
    if kind in REF_KINDS:
        return f"{kind}:{tree[1]}"
    if kind in ("array", "set"):
        return f"{kind}<{format_spec(tree[1])}>"
    if kind == "map":
        return f"map<{format_spec(tree[1])},{format_spec(tree[2])}>"
    raise TypeSpecError(f"unknown node {tree!r}")


def _skip_ws(s, i):
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _read_word(s, i):
    start = i
    while i < len(s) and (s[i].isalnum() or s[i] == "_"):
        i += 1
    return s[start:i], i


def _expect(s, i, ch):
    i = _skip_ws(s, i)
    if i >= len(s) or s[i] != ch:
        found = s[i] if i < len(s) else "end of text"
        raise TypeSpecError(f"expected {ch!r} at position {i}, found {found!r}")
    return i + 1


def _parse_at(s, i):
    i = _skip_ws(s, i)
    word, j = _read_word(s, i)
    if not word:
        raise TypeSpecError(f"expected a type name at position {i}")
    key = word.lower()

    if key in REF_KINDS:
        j = _expect(s, j, ":")
        j = _skip_ws(s, j)
        start = j
        while j < len(s) and s[j] not in ",<>" and not s[j].isspace():
            j += 1
        path = s[start:j]
        if not path.startswith("/"):
            raise TypeSpecError(f"{key} path must start with '/', got {path!r}")
        return (key, path), j

    if key in ("array", "set"):
        j = _expect(s, j, "<")
        inner, j = _parse_at(s, j)
        if inner[0] in CONTAINERS:
            raise TypeSpecError("Blueprint containers cannot be nested directly; wrap them in a struct")
        j = _expect(s, j, ">")
        return (key, inner), j

    if key == "map":
        j = _expect(s, j, "<")
        k, j = _parse_at(s, j)
        j = _expect(s, j, ",")
        v, j = _parse_at(s, j)
        if k[0] in CONTAINERS or v[0] in CONTAINERS:
            raise TypeSpecError("Blueprint containers cannot be nested directly; wrap them in a struct")
        j = _expect(s, j, ">")
        return ("map", k, v), j

    if key in BASIC_ALIASES:
        return ("basic", BASIC_ALIASES[key]), j

    raise TypeSpecError(
        f"unknown type {word!r}; use one of {sorted(set(BASIC_ALIASES.values()))}, "
        "object:/path, class:/path, struct:/path, array<T>, set<T>, map<K,V>")


# ---- reading an EdGraphPinType back (from its export_text) into the same spec syntax ----

_PIN_CATEGORY = {
    "bool": "bool", "int": "int", "int64": "int64", "byte": "byte", "real": "real", "float": "real",
    "double": "real", "name": "name", "string": "string", "text": "text",
    "object": "object", "class": "class", "struct": "struct", "softobject": "softobject",
    "softclass": "softclass", "interface": "interface", "enum": "enum", "delegate": "delegate",
    "mcdelegate": "mcdelegate", "exec": "exec", "wildcard": "wildcard",
}


def _field(text, key):
    """Value of Key=... at the top level of an Unreal export_text struct."""
    import re as _re_ts
    m = _re_ts.search(r'(?:^|[(,])' + key + r'=("(?:[^"\\]|\\.)*"|[^,()]+)', text)
    if not m:
        return ""
    v = m.group(1)
    return v[1:-1] if v.startswith('"') else v


def _object_path(raw):
    """'/Script/CoreUObject.Class\\'/Script/Engine.Actor\\'' or None -> '/Script/Engine.Actor' or ''."""
    raw = raw.replace('\\"', '"').strip()
    if not raw or raw == "None":
        return ""
    if "'" in raw:
        parts = raw.split("'")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return raw


def _single(category, sub_object):
    cat = _PIN_CATEGORY.get(category.lower(), category.lower() or "?")
    if cat == "byte" and sub_object:
        cat = "enum"
    if sub_object and cat in ("object", "class", "struct", "softobject", "softclass", "interface", "enum"):
        return f"{cat}:{sub_object}"
    return cat


def describe_pin_type_text(text):
    """Turn EdGraphPinType.export_text() into a short spec like 'map<string,int>' or 'array<object:/Script/Engine.AnimMontage>'."""
    if not text or "PinCategory" not in text:
        return text
    inner = text[1:-1] if text.startswith("(") and text.endswith(")") else text
    # PinValueType=(...) holds the map value type; cut it out so its keys do not shadow the outer ones.
    value_part = ""
    start = inner.find("PinValueType=(")
    if start >= 0:
        depth, i = 0, start + len("PinValueType=")
        while i < len(inner):
            if inner[i] == "(":
                depth += 1
            elif inner[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        value_part = inner[start + len("PinValueType="):i + 1]
        inner = inner[:start] + inner[i + 1:]
    base = _single(_field(inner, "PinCategory"), _object_path(_field(inner, "PinSubCategoryObject")))
    container = _field(inner, "ContainerType").lower()
    if container == "array":
        return f"array<{base}>"
    if container == "set":
        return f"set<{base}>"
    if container == "map":
        vp = value_part[1:-1] if value_part.startswith("(") else value_part
        val = _single(_field(vp, "TerminalCategory"), _object_path(_field(vp, "TerminalSubCategoryObject")))
        return f"map<{base},{val}>"
    return base
