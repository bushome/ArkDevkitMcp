"""Blueprint graph layout (pure Python, no editor needed; also sent into the editor).

Readability rules (from hand-arranged graphs):
  * wires only run forward: every node sits in a column to the right of everything feeding it
  * a pure data node sits in the column directly before the node that uses it, lined up with the input pin it
    feeds, so its wire is short and nearly flat
  * an exec chain stays on one straight row
  * nothing overlaps (real node sizes), positions snap to the editor grid
  * separate chains (each event and what hangs off it) get their own band, stacked top to bottom

layout_graph(nodes, edges) -> {node_id: (x, y)}
  nodes: {id: {"w": float, "h": float, "x": float, "y": float, "exec": bool}}   (exec = node has exec pins)
  edges: [(src_id, src_pin_offset_y, dst_id, dst_pin_offset_y, is_exec)]       (offset from node top)
"""

PIN_TOP = 40      # y offset of a node's first pin row
PIN_STEP = 24     # height of one pin row


def pin_offset(index):
    return PIN_TOP + PIN_STEP * index


CHAR_W = 6.5      # average width of one character of a label at 1:1 zoom
VALUE_CHAR_W = 5.0  # literal values in pin text boxes use a smaller font
ADVANCED_SHOWN_INPUTS = 2   # with advanced pins collapsed, typically only the first data inputs stay visible
ADVANCED_ARROW_H = 28       # the expand arrow row
DEV_ONLY_BANNER_H = 40      # the striped "Development Only" footer
COMPACT_OPERATORS = ("K2Node_PromotableOperator", "K2Node_CommutativeAssociativeBinaryOperator")
HIDDEN_LABELS = ("execute", "then", "self", "ReturnValue")


def display_label(name):
    """Pin labels are shown with spaces before capitals ('WorldContextObject' -> 'World Context Object')."""
    if name.startswith("b") and len(name) > 1 and name[1].isupper():
        name = name[1:]
    out = []
    for i, ch in enumerate(name):
        if i and ch.isupper() and not name[i - 1].isupper():
            out.append(" ")
        out.append(ch)
    return "".join(out)


def estimate_node_size(cls, title, ins, outs, advanced_hidden=False, dev_only=False):
    """Approximate drawn size of a K2 node. The editor's own get_node_size() is not reliable in the DevKit.
    ins / outs: lists of (pin_name, type_display, literal_value_or_None, connected).
    advanced_hidden: the node's advanced pins are collapsed (AdvancedPinDisplay == HIDDEN)."""
    extra_h = 0.0
    if advanced_hidden:
        execs = [p for p in ins if p[1].lower() == "exec"]
        data = [p for p in ins if p[1].lower() != "exec"]
        last_connected = max((i for i, p in enumerate(data) if p[3]), default=-1)
        ins = execs + data[:max(ADVANCED_SHOWN_INPUTS, last_connected + 1)]
        extra_h += ADVANCED_ARROW_H
    if dev_only:
        extra_h += DEV_ONLY_BANNER_H
    if cls == "K2Node_Knot":
        return 32.0, 16.0
    rows = max(len(ins), len(outs), 1)
    if cls in COMPACT_OPERATORS:
        return 142.0, 20.0 + PIN_STEP * rows
    if cls == "K2Node_VariableGet" and not ins:
        name = title[4:] if title.startswith("Get ") else title
        return len(display_label(name)) * CHAR_W + 60.0, 32.0

    def pin_w(pin, is_input):
        name, ptype, value, connected = pin
        w = 24.0
        if not (ptype.lower() == "exec" and name in ("execute", "then")) and name not in HIDDEN_LABELS:
            w += len(display_label(name)) * CHAR_W + 8
        if is_input and value and not connected and ptype.lower() != "exec":
            if ptype.lower() == "boolean":
                w += 24
            elif "structure" in ptype.lower():
                w += 48
            else:
                w += min(len(value), 40) * VALUE_CHAR_W + 16
        return w

    title_w = len(title) * CHAR_W + 56
    pins_w = max((pin_w(p, True) for p in ins), default=0) + max((pin_w(p, False) for p in outs), default=0) + 32
    rows = max(len(ins), len(outs), 1)
    return max(title_w, pins_w, 96.0), 32.0 + PIN_STEP * rows + extra_h


def _components(ids, adj_undirected):
    seen, comps = set(), []
    for start in ids:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in adj_undirected[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        comps.append(comp)
    return comps


def _acyclic_edges(comp, out_edges, order_key):
    """Drop back edges (found by DFS from the top-left-most roots) so layering terminates."""
    comp_set = set(comp)
    indeg = {n: 0 for n in comp}
    for n in comp:
        for e in out_edges[n]:
            if e[2] in comp_set:
                indeg[e[2]] += 1
    roots = sorted([n for n in comp if indeg[n] == 0], key=order_key) or [min(comp, key=order_key)]
    state, keep = {}, []
    for r in roots + sorted(comp, key=order_key):
        if r in state:
            continue
        stack = [(r, iter(out_edges[r]))]
        state[r] = 1
        while stack:
            n, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[n] = 2
                stack.pop()
                continue
            m = nxt[2]
            if m not in comp_set:
                continue
            if state.get(m) == 1:
                continue  # back edge: ignore for layering
            keep.append(nxt)
            if m not in state:
                state[m] = 1
                stack.append((m, iter(out_edges[m])))
    return keep


def _topo(comp, edges):
    indeg = {n: 0 for n in comp}
    succ = {n: [] for n in comp}
    for e in edges:
        indeg[e[2]] += 1
        succ[e[0]].append(e[2])
    order, ready = [], [n for n in comp if indeg[n] == 0]
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return order


def layout_graph(nodes, edges, gap_x=48, gap_y=16, band_gap=96, grid=16, origin=None):
    ids = list(nodes)
    if not ids:
        return {}
    out_edges = {n: [] for n in ids}
    adj = {n: set() for n in ids}
    for e in edges:
        if e[0] in nodes and e[2] in nodes and e[0] != e[2]:
            out_edges[e[0]].append(e)
            adj[e[0]].add(e[2])
            adj[e[2]].add(e[0])

    def order_key(n):
        return (nodes[n]["y"], nodes[n]["x"], n)

    comps = _components(sorted(ids, key=order_key), adj)
    comps.sort(key=lambda c: min(order_key(n) for n in c))
    ox = min(nodes[n]["x"] for n in ids) if origin is None else origin[0]
    band_y = min(nodes[n]["y"] for n in ids) if origin is None else origin[1]
    result = {}

    for comp in comps:
        dag = _acyclic_edges(comp, out_edges, order_key)
        preds = {n: [] for n in comp}
        succs = {n: [] for n in comp}
        for e in dag:
            preds[e[2]].append(e)
            succs[e[0]].append(e)
        order = _topo(comp, dag)

        # columns: longest path from the roots ...
        col = {}
        for n in order:
            col[n] = max((col[e[0]] + 1 for e in preds[n]), default=0)
        # ... then pull pure data nodes right, next to their first consumer
        for n in reversed(order):
            if nodes[n]["exec"] or not succs[n]:
                continue
            latest = min(col[e[2]] for e in succs[n]) - 1
            earliest = max((col[e[0]] + 1 for e in preds[n]), default=0)
            col[n] = max(earliest, latest)

        ncols = max(col.values()) + 1
        widths = [0.0] * ncols
        for n in comp:
            widths[col[n]] = max(widths[col[n]], nodes[n]["w"])
        col_x = [ox]
        for c in range(1, ncols):
            col_x.append(col_x[-1] + widths[c - 1] + gap_x)

        y = {}
        # exec spine: follow the first exec link so a chain stays on one row
        for n in order:
            if not nodes[n]["exec"]:
                continue
            ex = [e for e in preds[n] if e[4] and e[0] in y]
            y[n] = (y[ex[0][0]] + ex[0][1] - ex[0][3]) if ex else band_y
        # Data nodes hang below the exec row so exec wires never run through them:
        # at least below the exec lane (first pin row) and below any exec node in the same column.
        exec_cols = {}
        for n in comp:
            if nodes[n]["exec"] and n in y:
                exec_cols[col[n]] = max(exec_cols.get(col[n], band_y), y[n] + nodes[n]["h"])
        lane = band_y + PIN_TOP + PIN_STEP if exec_cols else None

        def floor_for(c):
            if lane is None:
                return band_y
            return max(lane, exec_cols.get(c, lane)) + gap_y

        # right to left: line the output pin up with the input pin it feeds, but never above the floor
        for n in sorted(order, key=lambda m: -col[m]):
            if n in y:
                continue
            feeds = [e for e in succs[n] if e[2] in y]
            want = (y[feeds[0][2]] + feeds[0][3] - feeds[0][1]) if feeds else band_y
            y[n] = max(want, floor_for(col[n]))

        # resolve overlaps column by column (exec spine nodes keep priority)
        for c in range(ncols):
            members = sorted([n for n in comp if col[n] == c], key=lambda m: (y[m], not nodes[m]["exec"]))
            bottom = None
            for n in members:
                if bottom is not None and y[n] < bottom + gap_y:
                    y[n] = bottom + gap_y
                bottom = y[n] + nodes[n]["h"]

        top = min(y.values())
        shift = band_y - top if top < band_y else 0
        for n in comp:
            result[n] = (_snap(col_x[col[n]], grid), _snap(y[n] + shift, grid))
        band_y = max(result[n][1] + nodes[n]["h"] for n in comp) + band_gap
    return result


def _snap(v, grid):
    return int(round(v / grid) * grid) if grid else int(round(v))


# ---- placing new nodes into an existing, hand-arranged graph ----
#
# Everything not in `movable` stays exactly where the user put it and is treated as an obstacle.
# Conventions (the user's ladder-style graphs): exec is the rail, running left to right; a new exec node goes on
# the rail right after its predecessor (or before its successor); a data node goes to the left of and below the
# pin it feeds (it taps up into the pin, like a tap off a bus); a node that only consumes goes to the right of
# its producer. If the spot is taken, search down, then further right, for the first free spot.

def _overlaps(x, y, w, h, rects, pad):
    for rx, ry, rw, rh in rects:
        if x < rx + rw + pad and rx < x + w + pad and y < ry + rh + pad and ry < y + h + pad:
            return True
    return False


def place_nodes(nodes, edges, movable, gap_x=96, gap_y=16, grid=16, pad=16, max_steps=60, feeder_gap_x=128):
    """nodes: {id: {"w","h","x","y","exec"}} for ALL nodes; movable: ids to place. Returns {id: (x, y)}."""
    movable = [m for m in movable if m in nodes]
    mset = set(movable)
    pos = {k: (v["x"], v["y"]) for k, v in nodes.items() if k not in mset}
    rects = [(x, y, nodes[k]["w"], nodes[k]["h"]) for k, (x, y) in pos.items()]
    preds = {k: [] for k in nodes}
    succs = {k: [] for k in nodes}
    for e in edges:
        if e[0] in nodes and e[2] in nodes and e[0] != e[2]:
            succs[e[0]].append(e)
            preds[e[2]].append(e)

    # place exec nodes first (they define rows), then data nodes; within each, follow dependency order
    remaining = sorted(movable, key=lambda k: (not nodes[k]["exec"], nodes[k]["y"], nodes[k]["x"]))
    placed = {}
    for _ in range(len(remaining) + 1):
        progressed = False
        for k in list(remaining):
            spot = _anchor(k, nodes, preds, succs, pos, gap_x, gap_y, feeder_gap_x)
            if spot is None:
                linked_unplaced = [e for e in preds[k] + succs[k]
                                   if (e[0] in mset and e[0] not in pos) or (e[2] in mset and e[2] not in pos)]
                if linked_unplaced:
                    continue  # wait until a neighbour has a position to anchor to
                spot = (nodes[k]["x"], nodes[k]["y"])
            x, y = _free_spot(spot, nodes[k], rects, nodes[k]["exec"], gap_x, gap_y, grid, pad, max_steps)
            pos[k] = placed[k] = (x, y)
            rects.append((x, y, nodes[k]["w"], nodes[k]["h"]))
            remaining.remove(k)
            progressed = True
        if not remaining or not progressed:
            break
    for k in remaining:  # disconnected leftovers: keep their position if free, else nearest free spot
        x, y = _free_spot((nodes[k]["x"], nodes[k]["y"]), nodes[k], rects, False, gap_x, gap_y, grid, pad, max_steps)
        placed[k] = (x, y)
        rects.append((x, y, nodes[k]["w"], nodes[k]["h"]))
    return placed


def _anchor(k, nodes, preds, succs, pos, gap_x, gap_y, feeder_gap_x):
    n = nodes[k]
    if n["exec"]:
        for e in preds[k]:                      # right after the exec predecessor, pin rows aligned
            if e[4] and e[0] in pos:
                px, py = pos[e[0]]
                return px + nodes[e[0]]["w"] + gap_x, py + e[1] - e[3]
        for e in succs[k]:                      # or right before the exec successor
            if e[4] and e[2] in pos:
                sx, sy = pos[e[2]]
                return sx - n["w"] - gap_x, sy + e[3] - e[1]
    for e in succs[k]:                          # data feeder: left of and below the pin it feeds
        if e[2] in pos:
            cx, cy = pos[e[2]]
            return cx - n["w"] - feeder_gap_x, max(cy + e[3] - e[1], cy + PIN_TOP + PIN_STEP + gap_y)
    for e in preds[k]:                          # consumer: right of its producer, level with the wire
        if e[0] in pos:
            px, py = pos[e[0]]
            return px + nodes[e[0]]["w"] + gap_x, py + e[1] - e[3]
    return None


def _free_spot(spot, n, rects, is_exec, gap_x, gap_y, grid, pad, max_steps):
    x0, y0 = _snap(spot[0], grid), _snap(spot[1], grid)
    if not _overlaps(x0, y0, n["w"], n["h"], rects, pad):
        return x0, y0
    step = max(grid, 16)
    # exec nodes prefer staying on their row (move right); data nodes prefer moving down
    for i in range(1, max_steps + 1):
        tries = [(x0 + i * step, y0), (x0, y0 + i * step)] if is_exec else [(x0, y0 + i * step), (x0 - i * step, y0 + i * step)]
        for x, y in tries:
            if not _overlaps(x, y, n["w"], n["h"], rects, pad):
                return _snap(x, grid), _snap(y, grid)
    return x0, _snap(max((r[1] + r[3] for r in rects), default=y0) + gap_y, grid)
