"""Compact TOON-style text for tool replies (fewer tokens than JSON; measured ~55% of pretty JSON on list_graph).

Shapes:
    key: value
    key:                         nested object, indented one level
    key[N]: a,b,c                list of scalars
    key[N]{f1,f2,...}:           list of same-shaped objects, one row per item
      v1,v2,...
    key[N]:                      list of mixed/nested items, each introduced by "-"
      - ...
Cells: lists of scalars are joined with "|"; missing values are empty; strings containing , | " or newlines,
leading/trailing spaces, or that are empty are JSON-quoted.
"""

import json


def _scalar(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    if s == "" or s != s.strip() or any(c in s for c in ',|"\n\r'):
        return json.dumps(s, ensure_ascii=False)
    return s


def _is_scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


def _is_cell(v):
    return _is_scalar(v) or (isinstance(v, list) and all(_is_scalar(x) for x in v))


def _cell(v):
    return "|".join(_scalar(x) for x in v) if isinstance(v, list) else _scalar(v)


def _keys(rows):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    return keys


def _encode(obj, pad, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _field(str(k), v, pad, out)
    elif isinstance(obj, list):
        _field("items", obj, pad, out)
    else:
        out.append(pad + _scalar(obj))


def _field(k, v, pad, out):
    if _is_scalar(v):
        out.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(v, dict):
        if not v:
            out.append(f"{pad}{k}: {{}}")
        else:
            out.append(f"{pad}{k}:")
            _encode(v, pad + "  ", out)
    elif isinstance(v, list):
        if not v:
            out.append(f"{pad}{k}[0]:")
        elif all(_is_scalar(x) for x in v):
            out.append(f"{pad}{k}[{len(v)}]: " + ",".join(_scalar(x) for x in v))
        elif all(isinstance(x, dict) and all(_is_cell(y) for y in x.values()) for x in v):
            keys = _keys(v)
            out.append(f"{pad}{k}[{len(v)}]{{{','.join(keys)}}}:")
            for row in v:
                out.append(pad + "  " + ",".join(_cell(row.get(c)) for c in keys))
        else:
            out.append(f"{pad}{k}[{len(v)}]:")
            for item in v:
                if isinstance(item, dict):
                    sub = []
                    _encode(item, pad + "    ", sub)
                    if sub:
                        sub[0] = pad + "  - " + sub[0].lstrip()
                    out.extend(sub)
                else:
                    sub = []
                    _encode(item, pad + "    ", sub)
                    out.append(pad + "  - " + (sub[0].lstrip() if sub else ""))
                    out.extend(sub[1:])
    else:
        out.append(f"{pad}{k}: {_scalar(str(v))}")


def dumps(obj):
    if _is_scalar(obj):
        return _scalar(obj)
    out = []
    _encode(obj, "", out)
    return "\n".join(out)
