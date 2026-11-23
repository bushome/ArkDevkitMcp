"""Blueprint graph layout (pure Python, no editor needed; also sent into the editor).

Readability rules (from hand-arranged graphs):
  * wires only run forward: every node sits in a column to the right of everything feeding it
  * a pure data node sits in the column directly before the node that uses it, lined up with the input pin it
    feeds, so its wire is short and nearly flat
  * an exec chain stays on one straight row
  * nothing overlaps (estimated node sizes), positions snap to an 8-unit grid (what hand layouts use)
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
    # auto-inserted conversions ("To String (Integer)" etc.) draw as compact one-in/one-out nodes
    if (cls == "K2Node_CallFunction" and title.startswith("To ") and len(ins) == 1 and len(outs) == 1
            and ins[0][1].lower() != "exec" and outs[0][1].lower() != "exec"):
        return 140.0, 32.0
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


def layout_graph(nodes, edges, gap_x=48, gap_y=16, band_gap=96, grid=8, origin=None):
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
        lane = band_y + PIN_TOP if exec_cols else None   # just under the exec pin row (calibrated by hand layouts)

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
    # round half up: Python's round() rounds halves to even, which made 104 snap to 96 but 56 snap to 64
    import math as _math_snap
    return int(_math_snap.floor(v / grid + 0.5) * grid) if grid else int(_math_snap.floor(v + 0.5))


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


def place_nodes(nodes, edges, movable, gap_x=96, gap_y=16, grid=8, pad=16, max_steps=60, feeder_gap_x=128):
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
            return cx - n["w"] - feeder_gap_x, max(cy + e[3] - e[1], cy + PIN_TOP + gap_y)
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


# ---- ladder placement: make room on the rail, compute data near its source, carry results on lanes ----
#
# Calibrated from Sean's arrangement of the ADD-path dedup insertion in CloudBshelf (tests/captures/
# cloudbshelf_training_add_dedup_*.json):
#  * An exec node inserted between two rail nodes goes on the rail. If it does not fit, the downstream rail
#    (the successor and everything after it on exec wires) shifts right as one block, together with the
#    successor block's own feeders, keeping the rail's local spacing: the gap that was between predecessor and
#    successor is used on both sides of the new node. The block also moves vertically so its exec input lines up
#    with the new node's output pin (Sean re-levelled it by -24 in both of his passes).
#  * A data computation whose inputs come from far away (a lane source) is built beside the source, on a row
#    just below the lowest lane running past it, left to right in dependency order, each input lined up with the
#    pin it feeds.
#  * Its result reaches a distant consumer through a reroute knot on its own lane (knot ~560 before the
#    consumer, level with the result pin), then rises into the consumer's pin.

FEEDER_REACH = 600       # feeders this far left of the successor move with the shifted block
FAR_SOURCE = 1500        # a data chain whose sources are this far from its consumer is built at the source
SOURCE_OFFSET = 640      # chain starts this far right of its rightmost source node
CHAIN_GAP = 24           # gap between nodes of a source-anchored chain
LANE_MIN = 800           # results travelling further than this get a knot lane
LANE_KNOT_BEFORE = 560   # knot sits this far left of the consumer
FAR_LANE = 2000          # a wire longer than this needs its own lane (counts as a "long wire" when choosing placement)
SINK_GAP = 48            # gap between a feeder column and the consumer it feeds
COL_GAP = 32             # gap between feeder columns
STACK_GAP = 16           # vertical gap between stacked feeders

# Placement choice (Sean, 2026-11-22): put a data computation wherever it needs the fewest NEW long wires.
#   beside the consumer: each input whose value only exists far away (no lane/knot carrying it near the consumer)
#                        costs one new lane;
#   at the source:       each result that then has to travel far to its consumer costs one new lane.
# Ties go beside the consumer (keeps the progression easiest to follow). Beside the consumer, feeders stack in
# tight columns left of the pin they feed, top to bottom in pin order, below the exec lane.


def _value_reach(src, succs, nodes, pos, limit_x):
    """Rightmost x (<= limit_x) at which the value leaving `src` is available: follow knot-to-knot lanes forward."""
    best, seen, stack = pos[src][0], set(), [src]
    while stack:
        k = stack.pop()
        if k in seen:
            continue
        seen.add(k)
        if pos[k][0] <= limit_x:
            best = max(best, pos[k][0])
        for e in succs[k]:
            if nodes.get(e[2], {}).get("knot") and e[2] in pos:
                stack.append(e[2])
    return best


def _place_beside_consumer(grp, gset, nodes, preds, succs, pos, gap_y, grid):
    exits = [e for k in grp for e in succs[k] if e[2] not in gset and e[2] in pos]
    out, columns = {}, []
    level = sorted({e[0] for e in exits}, key=lambda k: min((pos[e[2]][0], e[3]) for e in exits if e[0] == k))
    placed_ids = set()
    while level:
        columns.append(level)
        placed_ids.update(level)
        nxt = []
        for k in level:
            for e in sorted(preds[k], key=lambda e: e[3]):
                if e[0] in gset and e[0] not in placed_ids and e[0] not in nxt:
                    nxt.append(e[0])
        level = nxt
    right_edge = None
    for ci, col in enumerate(columns):
        w = max(nodes[k]["w"] for k in col)
        if ci == 0:
            c = min((pos[e[2]][0] for e in exits))
            x = c - w - SINK_GAP
        else:
            x = right_edge - w - COL_GAP
        bottom = None
        for k in col:
            # the pin this node feeds: in the consumer (column 0) or in an already placed group node
            feeds = [e for e in succs[k] if e[2] in out or (ci == 0 and e[2] not in gset and e[2] in pos)]
            feeds.sort(key=lambda e: e[3])
            if feeds:
                e = feeds[0]
                ty = out[e[2]][1] if e[2] in out else pos[e[2]][1]
                want = ty + e[3] - e[1]
                consumer = e[2]
                floor = ty + PIN_TOP + gap_y if nodes[consumer]["exec"] else ty
            else:
                want = floor = (bottom if bottom is not None else 0)
            y = max(want, floor)
            if bottom is not None:
                y = max(y, bottom + STACK_GAP)
            out[k] = (x, y)
            bottom = y + nodes[k]["h"]
        right_edge = x
    # never on top of an existing node: move the whole group down until it is clear
    rects = [(pos[f][0], pos[f][1], nodes[f]["w"], nodes[f]["h"]) for f in pos if f not in gset]
    for _ in range(80):
        if not any(_overlaps(x, y, nodes[k]["w"], nodes[k]["h"], rects, 8) for k, (x, y) in out.items()):
            break
        out = {k: (x, y + 8) for k, (x, y) in out.items()}
    return {k: (_snap(x, grid), _snap(y, grid)) for k, (x, y) in out.items()}


def _exec_downstream(start, succs, fixed):
    seen, stack = set(), [start]
    while stack:
        k = stack.pop()
        if k in seen or k not in fixed:
            continue
        seen.add(k)
        stack.extend(e[2] for e in succs[k] if e[4])
    return seen


def place_ladder(nodes, edges, movable, make_room=True, result_lanes=True, gap_x=96, gap_y=16, grid=8):
    """Returns {"positions": {id: (x, y)} for movable AND shifted nodes,
                "shifted": {id: (dx, dy)}, "lanes": [{"x", "y", "src", "src_pin", "dst", "dst_pin"}],
                "fallback": [ids placed by place_nodes]}."""
    movable = [m for m in movable if m in nodes]
    mset = set(movable)
    fixed = {k for k in nodes if k not in mset}
    pos = {k: [nodes[k]["x"], nodes[k]["y"]] for k in fixed}
    preds = {k: [] for k in nodes}
    succs = {k: [] for k in nodes}
    for e in edges:
        if e[0] in nodes and e[2] in nodes and e[0] != e[2]:
            succs[e[0]].append(e)
            preds[e[2]].append(e)
    shifted, lanes, placed = {}, [], {}

    def right(k):
        return pos[k][0] + nodes[k]["w"]

    # 1) exec nodes inserted on the rail
    for k in [m for m in movable if nodes[m]["exec"]]:
        pe = next((e for e in preds[k] if e[4] and e[0] in pos), None)
        se = next((e for e in succs[k] if e[4] and e[2] in fixed), None)
        if pe is None:
            continue
        p = pe[0]
        y = pos[p][1] + pe[1] - pe[3]
        if se is not None and make_room:
            s = se[2]
            gap = max(pos[s][0] - right(p), gap_x)
            x = right(p) + gap
            need = _snap(x + nodes[k]["w"] + gap - pos[s][0], grid)
            if need > 0:
                block = _exec_downstream(s, succs, fixed)
                grew = True
                while grew:                     # the block's feeders (and their feeders) travel with it
                    grew = False
                    for b in list(block):
                        for e in preds[b]:
                            f = e[0]
                            if (f not in block and f in fixed and not nodes[f]["exec"] and not nodes[f].get("knot")
                                    and pos[f][0] >= pos[s][0] - FEEDER_REACH
                                    and all(c[2] in block for c in succs[f])):
                                block.add(f)
                                grew = True
                # re-level: the block's exec input now hangs off the new node's pin (e.g. Branch False sits one
                # pin row lower than the old predecessor's pin), so move the block vertically to keep the rail flat
                ky = _snap(y, grid)
                dy = _snap(ky + se[1] - se[3] - pos[s][1], grid)
                for b in block:
                    pos[b][0] += need
                    pos[b][1] += dy
                    shifted[b] = (need, dy)
        else:
            x = right(p) + gap_x
        pos[k] = [_snap(x, grid), _snap(y, grid)]
        placed[k] = tuple(pos[k])

    # 2) data chains: connected groups of movable data nodes
    data = [m for m in movable if not nodes[m]["exec"]]
    groups, seen = [], set()
    for d in data:
        if d in seen:
            continue
        grp, stack = [], [d]
        while stack:
            k = stack.pop()
            if k in seen or k not in data:
                continue
            seen.add(k)
            grp.append(k)
            stack.extend(e[0] for e in preds[k])
            stack.extend(e[2] for e in succs[k])
        groups.append(grp)
    fallback, decisions = [], []
    for grp in groups:
        gset = set(grp)
        sources = {e[0] for k in grp for e in preds[k] if e[0] not in gset and e[0] in pos}
        exits = [e for k in grp for e in succs[k] if e[2] not in gset and e[2] in pos]
        consumer_x = min((pos[e[2]][0] for e in exits), default=None)
        if consumer_x is None:
            fallback.extend(grp)          # feeds nothing placed yet
            continue
        in_edges = [e for k in grp for e in preds[k] if e[0] not in gset and e[0] in pos]
        cost_consumer = sum(1 for e in in_edges
                            if consumer_x - _value_reach(e[0], succs, nodes, pos, consumer_x) >= FAR_LANE)
        src_x = max((pos[s][0] for s in sources), default=consumer_x) + SOURCE_OFFSET
        chain_w = sum(nodes[k]["w"] + CHAIN_GAP for k in grp)
        cost_source = sum(1 for e in exits if pos[e[2]][0] - (src_x + chain_w) >= FAR_LANE) if sources else 99
        if cost_consumer <= cost_source:
            got = _place_beside_consumer(grp, gset, nodes, preds, succs, pos, gap_y, grid)
            for k, v in got.items():
                pos[k] = list(v)
                placed[k] = v
            decisions.append({"group": grp, "placement": "beside consumer",
                              "new_long_wires": {"beside_consumer": cost_consumer, "at_source": cost_source}})
            continue
        decisions.append({"group": grp, "placement": "at source",
                          "new_long_wires": {"beside_consumer": cost_consumer, "at_source": cost_source}})
        # order: dependency order; among ready nodes, those fed from outside first
        order, done = [], set()
        while len(order) < len(grp):
            ready = [k for k in grp if k not in done and all(e[0] not in gset or e[0] in done for e in preds[k])]
            ready.sort(key=lambda k: (not any(e[0] in sources for e in preds[k]), grp.index(k)))
            order.append(ready[0])
            done.add(ready[0])
        x0 = max(pos[s][0] for s in sources) + SOURCE_OFFSET
        xs, x = {}, x0
        for k in order:
            xs[k] = x
            x += nodes[k]["w"] + CHAIN_GAP
        x_end = x
        # row: below the lowest lane (knot output wire) or node running through the chain's span
        floor = max(pos[s][1] + nodes[s]["h"] for s in sources)
        for f in fixed:
            fx, fy = pos[f]
            if nodes[f].get("knot"):
                reach = max((pos[e[2]][0] for e in succs[f] if e[2] in pos), default=fx)
                if fx <= x_end and reach >= x0:
                    floor = max(floor, fy + nodes[f]["h"])
            elif fx < x_end and fx + nodes[f]["w"] > x0:
                floor = max(floor, fy + nodes[f]["h"] + gap_y)
        ys = {}
        for k in reversed(order):               # line inputs up with the pin they feed
            feed = next((e for e in succs[k] if e[2] in ys), None)
            ys[k] = max(floor, ys[feed[2]] + feed[3] - feed[1]) if feed else floor
        for k in order:
            pos[k] = [_snap(xs[k], grid), _snap(ys[k], grid)]
            placed[k] = tuple(pos[k])
        # 3) result lanes
        if result_lanes:
            for e in exits:
                if pos[e[2]][0] - (pos[e[0]][0] + nodes[e[0]]["w"]) >= LANE_MIN:
                    lanes.append({"x": _snap(pos[e[2]][0] - LANE_KNOT_BEFORE, grid),
                                  "y": _snap(pos[e[0]][1] + e[1] - 8, grid),
                                  "src": e[0], "src_pin": e[5] if len(e) > 5 else None,
                                  "dst": e[2], "dst_pin": e[6] if len(e) > 6 else None})
    if fallback:
        world = {k: dict(nodes[k], x=pos[k][0], y=pos[k][1]) for k in pos}
        for k in fallback:
            world[k] = dict(nodes[k])
        placed.update(place_nodes(world, edges, fallback, gap_x=gap_x, gap_y=gap_y, grid=grid))
    positions = dict(placed)
    for b in shifted:
        positions[b] = tuple(pos[b])
    return {"positions": positions, "shifted": shifted, "lanes": lanes, "fallback": fallback, "decisions": decisions}
