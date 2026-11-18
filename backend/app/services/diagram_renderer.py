"""Deterministic vector diagram builder (SVG for Web + high-DPI PNG for Word).

Converts structured SectionDiagram objects (nodes with shapes/sizes/items +
connections with labels) into compact, high-contrast academic diagrams.
"""

from html import escape
import math
from pathlib import Path
import re
import textwrap
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.schemas.protocol import DiagramNode, FormattingRules, SectionDiagram

# Typography constants calibrated so rendered diagram text reaches >= 12 pt on A4.
# At 96 DPI (1 pt = 4/3 px), 16 logical px == 12.0 pt exact physical height.
DIAGRAM_MIN_BODY_FONT_PX = 16
DIAGRAM_TITLE_FONT_PX = 18
DIAGRAM_LABEL_FONT_PX = 16
DIAGRAM_LINE_H_BODY_PX = 21
DIAGRAM_LINE_H_TITLE_PX = 23
DIAGRAM_MAX_CANVAS_W_PX = 596
DIAGRAM_RENDER_SCALE = 2
DIAGRAM_BADGE_RADIUS_PX = 12
DIAGRAM_BADGE_FONT_PX = 16
PT_TO_CM = 2.54 / 72.0


def _sanitize_dark_color(hex_color: str) -> str:
    """Guarantee dark, high-contrast colors for white header text and dark borders.

    Light/yellow/pastel colors are replaced or darkened so white text stays legible.
    """
    raw = (hex_color or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        return "#6C2826"
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return "#6C2826"

    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    if luminance <= 100.0:
        return f"#{r:02X}{g:02X}{b:02X}"

    # Avoid muddy dark yellow/olive when darkening yellow, gold, or light grey
    is_yellowish = r > 150 and g > 140 and b < 130
    is_neutral_light = max(r, g, b) - min(r, g, b) < 28
    if is_yellowish:
        return "#6C2826"
    if is_neutral_light:
        return "#1E3A5F"

    factor = 75.0 / max(1.0, luminance)
    dr = max(0, min(255, int(r * factor)))
    dg = max(0, min(255, int(g * factor)))
    db = max(0, min(255, int(b * factor)))
    return f"#{dr:02X}{dg:02X}{db:02X}"


def _node_max_chars(w: int, body_px: int = DIAGRAM_MIN_BODY_FONT_PX) -> int:
    # Average proportional advance width at 16px Arial is ~8.0px
    char_px = max(7.0, body_px * 0.50)
    return max(16, int((w - 22) / char_px))


def _wrap_node_lines(node: DiagramNode, w: int) -> Tuple[List[str], List[str]]:
    max_chars = _node_max_chars(w, DIAGRAM_MIN_BODY_FONT_PX)
    title_chars = max(14, int(max_chars * (DIAGRAM_MIN_BODY_FONT_PX / DIAGRAM_TITLE_FONT_PX)))
    title_lines = textwrap.wrap(node.title.strip(), width=title_chars) or [node.title]
    body_lines: List[str] = []
    for item in node.items:
        clean = item.strip()
        if not clean:
            continue
        wrapped = textwrap.wrap(clean, width=max_chars)
        for idx, w_line in enumerate(wrapped):
            prefix = "• " if idx == 0 and not w_line.startswith(("•", "-", "*")) else "  "
            body_lines.append(f"{prefix}{w_line}")
    return title_lines, body_lines


def _node_box_size(
    node: DiagramNode,
    grid_mode: bool = False,
) -> Tuple[int, int, int, List[str], List[str]]:
    if grid_mode:
        # 2-column grid within 596px logical canvas: 12*2 + 272*2 + 28 = 596px
        w = 272
    else:
        # Single-column stack: wider boxes (<= 520px) minimize tall text wrapping while fitting A4 width
        w = max(360, min(520, max(node.width_px or 460, 400)))
    if node.shape == "circle":
        w = max(160, min(250, w))

    t_lines, b_lines = _wrap_node_lines(node, w)
    header_h = max(28, len(t_lines) * (DIAGRAM_LINE_H_TITLE_PX - 2) + 8)
    body_h = len(b_lines) * (DIAGRAM_LINE_H_BODY_PX - 1) + (10 if b_lines else 4)
    h = max(52, header_h + body_h + 4)
    if node.shape == "circle":
        side = max(w, h)
        if grid_mode and side > 272:
            side = 272
        w, h = side, side
    return w, h, header_h, t_lines, b_lines


def _layout_two_columns(
    nodes: List[DiagramNode],
    pad_x: int,
    pad_y: int,
    gap_x: int,
    gap_y: int,
) -> Tuple[int, int, Dict[str, Tuple[int, int, int, int]], bool]:
    """Arrange nodes in a compact 2-column grid guaranteed <= 580px logical width."""
    boxes: Dict[str, Tuple[int, int, int, int]] = {}
    dims = [(n, *_node_box_size(n, grid_mode=True)) for n in nodes]
    col_widths = [0, 0]
    rows_count = math.ceil(len(dims) / 2)
    row_heights = [0] * rows_count
    for idx, (n, w, h, _, _, _) in enumerate(dims):
        r = idx // 2
        c = idx % 2
        col_widths[c] = max(col_widths[c], w)
        row_heights[r] = max(row_heights[r], h)
    total_w = pad_x * 2 + col_widths[0] + gap_x + col_widths[1]
    total_h = pad_y * 2 + sum(row_heights) + gap_y * max(0, rows_count - 1)
    row_tops: List[int] = []
    acc_y = pad_y
    for rh in row_heights:
        row_tops.append(acc_y)
        acc_y += rh + gap_y
    col_lefts = [pad_x, pad_x + col_widths[0] + gap_x]
    for idx, (n, w, h, _, _, _) in enumerate(dims):
        r = idx // 2
        c = idx % 2
        x = col_lefts[c] + (col_widths[c] - w) // 2
        y = row_tops[r] + (row_heights[r] - h) // 2
        boxes[n.id] = (x, y, w, h)
    return total_w, total_h, boxes, True


def _compute_layout(
    diagram: SectionDiagram,
) -> Tuple[int, int, Dict[str, Tuple[int, int, int, int]], bool]:
    """Return compact (canvas_w, canvas_h, {node_id: (x, y, w, h)}, grid_mode)."""
    nodes = diagram.nodes
    if not nodes:
        return 420, 160, {}, False

    is_horiz = diagram.layout == "horizontal"
    has_arrow_labels = any(bool((c.label or "").strip()) for c in (diagram.connections or []))
    if len(nodes) >= 5:
        pad_x = 12
        pad_y = 12
        gap_x = 28
        gap_y = 26 if has_arrow_labels else 22
    else:
        pad_x = 16
        pad_y = 16
        gap_x = 34 if has_arrow_labels else 32
        gap_y = 32 if has_arrow_labels else 28

    boxes: Dict[str, Tuple[int, int, int, int]] = {}
    if is_horiz and len(nodes) == 2:
        dims = [(n, *_node_box_size(n, grid_mode=True)) for n in nodes]
        total_w = pad_x * 2 + sum(d[1] for d in dims) + gap_x
        max_h = max(d[2] for d in dims)
        total_h = pad_y * 2 + max_h
        cur_x = pad_x
        for n, w, h, _, _, _ in dims:
            y = pad_y + (max_h - h) // 2
            boxes[n.id] = (cur_x, y, w, h)
            cur_x += w + gap_x
        return total_w, total_h, boxes, True

    if is_horiz and len(nodes) > 2:
        return _layout_two_columns(nodes, pad_x, pad_y, gap_x, gap_y)

    # Vertical stack: widen boxes first, and wrap to 2 columns if single column exceeds 640px height
    dims = [(n, *_node_box_size(n, grid_mode=False)) for n in nodes]
    total_h = pad_y * 2 + sum(d[2] for d in dims) + gap_y * max(0, len(dims) - 1)
    if len(nodes) >= 3 and total_h > 640:
        return _layout_two_columns(nodes, pad_x, pad_y, gap_x, gap_y)

    max_w = max(d[1] for d in dims)
    total_w = pad_x * 2 + max_w
    cur_y = pad_y
    for n, w, h, _, _, _ in dims:
        x = pad_x + (max_w - w) // 2
        boxes[n.id] = (x, cur_y, w, h)
        cur_y += h + gap_y
    return total_w, total_h, boxes, False


def _connector_endpoints(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Choose clean face-to-face anchor points between any two positioned boxes."""
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    cx1, cy1 = x1 + w1 / 2.0, y1 + h1 / 2.0
    cx2, cy2 = x2 + w2 / 2.0, y2 + h2 / 2.0
    # Strictly below (next row)
    if y2 >= y1 + h1 - 4:
        return cx1, y1 + h1, cx2, y2 - 2.0
    # Strictly above
    if y1 >= y2 + h2 - 4:
        return cx1, y1, cx2, y2 + h2 + 2.0
    # Same horizontal band: left to right
    if cx2 >= cx1:
        return x1 + w1, cy1, x2 - 2.0, cy2
    return x1, cy1, x2 + w2 + 2.0, cy2


def _point_hits_any_box(
    px: float,
    py: float,
    boxes: Dict[str, Tuple[float, float, float, float]],
    pad: float = 11.5,
) -> bool:
    """Return True if a badge centered at (px, py) would touch or overlap any node box."""
    for bx, by, bw, bh in boxes.values():
        if (bx - pad) <= px <= (bx + bw + pad) and (by - pad) <= py <= (by + bh + pad):
            return True
    return False


def _segment_crosses_intermediate_box(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    from_id: str,
    to_id: str,
    boxes: Dict[str, Tuple[float, float, float, float]],
) -> bool:
    """Detect whether a straight line (sx, sy)->(ex, ey) cuts through any third box."""
    min_x, max_x = min(sx, ex), max(sx, ex)
    min_y, max_y = min(sy, ey), max(sy, ey)
    for oid, (bx, by, bw, bh) in boxes.items():
        if oid in (from_id, to_id):
            continue
        if max_x < bx - 2 or min_x > bx + bw + 2 or max_y < by - 2 or min_y > by + bh + 2:
            continue
        # Sample points along interior of segment (avoid endpoints on source/target faces)
        for k in range(1, 10):
            t = k / 10.0
            px = sx + (ex - sx) * t
            py = sy + (ey - sy) * t
            if (bx + 2.0) <= px <= (bx + bw - 2.0) and (by + 2.0) <= py <= (by + bh - 2.0):
                return True
    return False


def _connector_path_points(
    conn_idx: int,
    conn: Any,
    boxes: Dict[str, Tuple[float, float, float, float]],
    canvas_w: int,
    grid_mode: bool,
) -> List[Tuple[float, float]]:
    """Build direct endpoints or an orthogonal gutter-routed polyline avoiding intermediate boxes."""
    b1 = boxes[conn.from_id]
    b2 = boxes[conn.to_id]
    sx, sy, ex, ey = _connector_endpoints(b1, b2)
    if not _segment_crosses_intermediate_box(sx, sy, ex, ey, conn.from_id, conn.to_id, boxes):
        return [(sx, sy), (ex, ey)]

    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    cy1 = y1 + h1 * 0.55
    cy2 = y2 + h2 * 0.45

    if grid_mode:
        # Route around the nearest open vertical gutter (center gap or side gutter)
        center_x = canvas_w / 2.0
        min_left = min(bx for bx, _, _, _ in boxes.values())
        max_right = max(bx + bw for bx, _, bw, _ in boxes.values())
        cx1 = x1 + w1 / 2.0
        cx2 = x2 + w2 / 2.0
        # Route vertical or cross-column skips cleanly down the central 28px gutter (never touching outer 12px border)
        if cx1 >= center_x and cx2 >= center_x:
            x_gut = max(14.0, center_x - 2.0 + ((conn_idx % 2) * 4.0))
            return [(x1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 - 2.0, cy2)]
        if cx1 <= center_x and cx2 <= center_x:
            x_gut = min(canvas_w - 14.0, center_x + 2.0 - ((conn_idx % 2) * 4.0))
            return [(x1 + w1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 + w2 + 2.0, cy2)]
        if cx1 >= center_x and cx2 < center_x:
            x_gut = center_x + (((conn_idx % 3) - 1) * 2.0)
            return [(x1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 + w2 + 2.0, cy2)]
        x_gut = center_x + (((conn_idx % 3) - 1) * 2.0)
        return [(x1 + w1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 - 2.0, cy2)]

    # Single-column stack: alternate clean right & left side gutters outside all boxes
    min_left = min(bx for bx, _, _, _ in boxes.values())
    max_right = max(bx + bw for bx, _, bw, _ in boxes.values())
    lane = conn_idx % 2
    right_room = canvas_w - max_right
    left_room = min_left
    use_right = (conn_idx % 2 == 1 and right_room >= 24.0) or (left_room < 24.0)
    if use_right:
        offset = 18.0 + (lane * 10.0 if right_room >= 40.0 else 0.0)
        x_gut = min(canvas_w - 14.0, max_right + offset)
        return [(x1 + w1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 + w2 + 2.0, cy2)]
    offset = 18.0 + ((conn_idx // 2) % 2 * 10.0 if left_room >= 40.0 else 0.0)
    x_gut = max(14.0, min_left - offset)
    return [(x1, cy1), (x_gut, cy1), (x_gut, cy2), (x2 - 2.0, cy2)]


def _extract_arrow_annotations(
    diagram: SectionDiagram,
) -> Tuple[Dict[int, int], List[Tuple[int, str]]]:
    """Map labeled diagram connections to sequential badge numbers 1..K and legend entries."""
    label_to_num: Dict[str, int] = {}
    conn_idx_to_num: Dict[int, int] = {}
    legend_items: List[Tuple[int, str]] = []
    for idx, conn in enumerate(diagram.connections):
        lbl = re.sub(r"\s+", " ", (conn.label or "").strip())
        if not lbl:
            continue
        if lbl not in label_to_num:
            num = len(legend_items) + 1
            label_to_num[lbl] = num
            legend_items.append((num, lbl))
        conn_idx_to_num[idx] = label_to_num[lbl]
    return conn_idx_to_num, legend_items


def _compute_legend_layout(
    legend_items: List[Tuple[int, str]],
    canvas_w: int,
    start_y: int,
) -> Optional[Dict[str, Any]]:
    """Compute a readable >=12pt annotation legend card below the flowchart nodes."""
    if not legend_items:
        return None
    lx = 12
    lw = max(320, canvas_w - 24)
    header_h = 26
    use_two_cols = len(legend_items) >= 3 and lw >= 440
    entries: List[Dict[str, Any]] = []
    line_h_compact = DIAGRAM_LINE_H_BODY_PX - 1

    if use_two_cols:
        col_w = (lw - 20) // 2
        wrap_chars = max(20, int((col_w - 36) / 8.0))
        cur_y = start_y + header_h + 7
        row_items: List[Tuple[int, str, List[str]]] = []
        for num, lbl in legend_items:
            lines = textwrap.wrap(lbl, width=wrap_chars) or [lbl]
            row_items.append((num, lbl, lines))
        rows = math.ceil(len(row_items) / 2)
        for r in range(rows):
            row_h = 0
            pair = row_items[r * 2 : r * 2 + 2]
            for c_idx, (num, _, lines) in enumerate(pair):
                ex = lx + 10 + c_idx * (col_w + 8)
                item_h = max(22, len(lines) * line_h_compact + 2)
                entries.append(
                    {
                        "num": num,
                        "x": ex,
                        "y": cur_y,
                        "lines": lines,
                    }
                )
                row_h = max(row_h, item_h)
            cur_y += row_h + 2
        lh = (cur_y - start_y) + 4
    else:
        wrap_chars = max(28, int((lw - 48) / 8.2))
        cur_y = start_y + header_h + 7
        for num, lbl in legend_items:
            lines = textwrap.wrap(lbl, width=wrap_chars) or [lbl]
            item_h = max(22, len(lines) * line_h_compact + 3)
            entries.append(
                {
                    "num": num,
                    "x": lx + 10,
                    "y": cur_y,
                    "lines": lines,
                }
            )
            cur_y += item_h
        lh = (cur_y - start_y) + 4

    return {
        "x": lx,
        "y": start_y,
        "w": lw,
        "h": lh,
        "header_h": header_h,
        "entries": entries,
    }


def _compute_full_layout_with_legend(
    diagram: SectionDiagram,
) -> Tuple[
    int,
    int,
    Dict[str, Tuple[int, int, int, int]],
    bool,
    Dict[int, int],
    List[Tuple[int, str]],
    Optional[Dict[str, Any]],
]:
    """Compute node boxes plus optional numbered-arrow legend below without exceeding 580px width."""
    base_w, base_h, raw_boxes, grid_mode = _compute_layout(diagram)
    conn_idx_to_num, legend_items = _extract_arrow_annotations(diagram)
    if not legend_items:
        return base_w, base_h, raw_boxes, grid_mode, conn_idx_to_num, legend_items, None

    target_w = min(DIAGRAM_MAX_CANVAS_W_PX, max(base_w, 500))
    shift_x = max(0, (target_w - base_w) // 2)
    # Wait: ensure single-column stacks with intermediate-skip arrows keep wide side gutters
    shifted_raw = {
        nid: (bx + shift_x, by, bw, bh)
        for nid, (bx, by, bw, bh) in raw_boxes.items()
    }
    if not grid_mode:
        has_skip = any(
            conn.from_id in shifted_raw
            and conn.to_id in shifted_raw
            and _segment_crosses_intermediate_box(
                *_connector_endpoints(shifted_raw[conn.from_id], shifted_raw[conn.to_id]),
                conn.from_id,
                conn.to_id,
                shifted_raw,
            )
            for conn in diagram.connections
        )
        if has_skip:
            # Cap single-column box width headroom by widening canvas to full 596px printable budget
            target_w = min(DIAGRAM_MAX_CANVAS_W_PX, max(base_w + 68, 560))
            shift_x = max(28, (target_w - base_w) // 2)
            # If boxes exceed 520px, trim x slightly? Wait: _node_box_size leaves max 520px -> min 38px per side!
            shifted_raw = {
                nid: (bx + max(0, (target_w - base_w) // 2), by, bw, bh)
                for nid, (bx, by, bw, bh) in raw_boxes.items()
            }
            boxes = shifted_raw
        else:
            boxes = shifted_raw
    else:
        boxes = shifted_raw
    max_node_bottom = max((by + bh for _, by, _, bh in boxes.values()), default=base_h - 12)
    legend_layout = _compute_legend_layout(legend_items, target_w, max_node_bottom + 10)
    total_h = (legend_layout["y"] + legend_layout["h"] + 12) if legend_layout else base_h
    return target_w, total_h, boxes, grid_mode, conn_idx_to_num, legend_items, legend_layout


def _compute_badge_positions(
    diagram: SectionDiagram,
    boxes: Dict[str, Tuple[float, float, float, float]],
    conn_idx_to_num: Dict[int, int],
    canvas_w: int,
    grid_mode: bool,
) -> List[Tuple[float, float, int]]:
    """Return non-overlapping (bx, by, badge_num) coordinates placed strictly outside all boxes."""
    placed: List[Tuple[float, float]] = []
    badges: List[Tuple[float, float, int]] = []
    t_candidates = (0.50, 0.38, 0.62, 0.26, 0.74, 0.16, 0.84, 0.10, 0.90)

    for idx, conn in enumerate(diagram.connections):
        if idx not in conn_idx_to_num:
            continue
        if conn.from_id not in boxes or conn.to_id not in boxes:
            continue
        pts = _connector_path_points(idx, conn, boxes, canvas_w, grid_mode)
        # Main segment: gutter vertical segment (pts[1]->pts[2]) when routed in gutter, else direct pts[0]->pts[1]
        if len(pts) >= 4:
            seg_order = [(pts[1], pts[2]), (pts[0], pts[1]), (pts[2], pts[3])]
        else:
            seg_order = [(pts[0], pts[1])]

        chosen: Optional[Tuple[float, float]] = None
        fallback_clear_box: Optional[Tuple[float, float]] = None

        for (p_start, p_end) in seg_order:
            sx, sy = p_start
            ex, ey = p_end
            dx, dy = ex - sx, ey - sy
            seg_len = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / seg_len, dx / seg_len
            for t in t_candidates:
                for perp in (0.0, 14.0, -14.0):
                    cx = sx + dx * t + nx * perp
                    cy = sy + dy * t + ny * perp
                    if cx < 13.0 or cx > canvas_w - 13.0 or cy < 13.0:
                        continue
                    if _point_hits_any_box(cx, cy, boxes, pad=11.5):
                        continue
                    if fallback_clear_box is None:
                        fallback_clear_box = (cx, cy)
                    if all(math.hypot(cx - px, cy - py) >= 26.0 for px, py in placed):
                        chosen = (cx, cy)
                        break
                if chosen is not None:
                    break
            if chosen is not None:
                break

        if chosen is None:
            if fallback_clear_box is not None:
                chosen = fallback_clear_box
            else:
                sx, sy = seg_order[0][0]
                ex, ey = seg_order[0][1]
                chosen = ((sx + ex) / 2.0, (sy + ey) / 2.0)

        placed.append(chosen)
        badges.append((chosen[0], chosen[1], conn_idx_to_num[idx]))
    return badges


def render_diagram_svg(diagram: SectionDiagram) -> str:
    """Render a SectionDiagram to high-legibility compact SVG (>= 12pt equivalent typography)."""
    (
        w_tot,
        h_tot,
        boxes,
        grid_mode,
        conn_idx_to_num,
        _,
        legend_layout,
    ) = _compute_full_layout_with_legend(diagram)
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_tot} {h_tot}" '
        f'style="width:100%;max-width:{w_tot}px;height:auto;font-family:Arial,sans-serif;background:#ffffff;">',
        "<defs>",
        '<marker id="arrowhead" markerWidth="9" markerHeight="6" refX="8" refY="3" orient="auto">',
        '<polygon points="0 0, 9 3, 0 6" fill="#6C2826" />',
        "</marker>",
        "</defs>",
    ]

    # Layer 1: Arrow lines only (routed cleanly in gutters if crossing intermediate boxes)
    for idx, conn in enumerate(diagram.connections):
        if conn.from_id not in boxes or conn.to_id not in boxes:
            continue
        pts = _connector_path_points(idx, conn, boxes, w_tot, grid_mode)
        dash = ' stroke-dasharray="5 3"' if conn.style == "dashed" else ""
        pts_attr = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(
            f'<polyline points="{pts_attr}" fill="none" stroke="#6C2826" '
            f'stroke-width="2"{dash} marker-end="url(#arrowhead)" />'
        )

    for n in diagram.nodes:
        if n.id not in boxes:
            continue
        x, y, w, h = boxes[n.id]
        color = _sanitize_dark_color(n.color)
        _, _, header_h, t_lines, b_lines = _node_box_size(n, grid_mode=grid_mode)

        if n.shape == "circle":
            cx, cy, r = x + w / 2.0, y + h / 2.0, min(w, h) / 2.0
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="#FFFDFD" stroke="{color}" stroke-width="2" />'
            )
        elif n.shape == "diamond":
            cx, cy = x + w / 2.0, y + h / 2.0
            pts = f"{cx:.1f},{y} {x + w},{cy:.1f} {cx:.1f},{y + h} {x},{cy:.1f}"
            parts.append(
                f'<polygon points="{pts}" fill="#FFFDFD" stroke="{color}" stroke-width="2" />'
            )
        else:
            rx = "8" if n.shape == "rounded" else "2"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
                f'fill="#FFFFFF" stroke="{color}" stroke-width="2" />'
            )
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{header_h}" rx="{rx}" ry="{rx}" '
                f'fill="{color}" />'
            )

        if n.shape in ("circle", "diamond"):
            ty = y + h / 2.0 - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX) / 2.0 + 5.0
            for idx_t, tl in enumerate(t_lines):
                parts.append(
                    f'<text x="{x + w / 2.0:.1f}" y="{ty + idx_t * DIAGRAM_LINE_H_TITLE_PX:.1f}" text-anchor="middle" '
                    f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{color}">{escape(tl)}</text>'
                )
        else:
            ty = y + 20.0
            for idx_t, tl in enumerate(t_lines):
                parts.append(
                    f'<text x="{x + w / 2.0:.1f}" y="{ty + idx_t * (DIAGRAM_LINE_H_TITLE_PX - 2):.1f}" text-anchor="middle" '
                    f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="#FFFFFF">{escape(tl)}</text>'
                )

            by = y + header_h + 18.0
            for idx_b, bl in enumerate(b_lines):
                parts.append(
                    f'<text x="{x + 12}" y="{by + idx_b * (DIAGRAM_LINE_H_BODY_PX - 1):.1f}" text-anchor="start" '
                    f'font-size="{DIAGRAM_MIN_BODY_FONT_PX}" fill="#0F172A">{escape(bl)}</text>'
                )

    # Layer 3: Numbered circular badges drawn ABOVE boxes and lines so they are never hidden
    for bx, by, badge_num in _compute_badge_positions(diagram, boxes, conn_idx_to_num, w_tot, grid_mode):
        parts.append(
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{DIAGRAM_BADGE_RADIUS_PX}" '
            f'fill="#FFFFFF" stroke="#6C2826" stroke-width="2.2" />'
        )
        parts.append(
            f'<text x="{bx:.1f}" y="{by + 5.5:.1f}" text-anchor="middle" '
            f'font-size="{DIAGRAM_BADGE_FONT_PX}" font-weight="700" fill="#6C2826">{badge_num}</text>'
        )

    # Layer 4: Dedicated annotation legend box below the diagram nodes
    if legend_layout:
        lx, ly, lw, lh = legend_layout["x"], legend_layout["y"], legend_layout["w"], legend_layout["h"]
        hh = legend_layout["header_h"]
        parts.append(
            f'<rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="8" ry="8" '
            f'fill="#FAF6F2" stroke="#6C2826" stroke-width="1.5" />'
        )
        parts.append(
            f'<rect x="{lx}" y="{ly}" width="{lw}" height="{hh}" rx="8" ry="8" '
            f'fill="#6C2826" />'
        )
        parts.append(
            f'<text x="{lx + 14}" y="{ly + 21}" text-anchor="start" '
            f'font-size="{DIAGRAM_TITLE_FONT_PX - 1}" font-weight="700" fill="#FFFFFF">'
            f'Légende des annotations (flèches)</text>'
        )
        for entry in legend_layout["entries"]:
            ex, ey = entry["x"], entry["y"]
            badge_cx, badge_cy = ex + 11, ey + 11
            parts.append(
                f'<circle cx="{badge_cx}" cy="{badge_cy}" r="10" '
                f'fill="#FFFFFF" stroke="#6C2826" stroke-width="1.8" />'
            )
            parts.append(
                f'<text x="{badge_cx}" y="{badge_cy + 5:.1f}" text-anchor="middle" '
                f'font-size="{DIAGRAM_BADGE_FONT_PX - 1}" font-weight="700" fill="#6C2826">{entry["num"]}</text>'
            )
            for l_idx, line_txt in enumerate(entry["lines"]):
                parts.append(
                    f'<text x="{ex + 28}" y="{ey + 16 + l_idx * DIAGRAM_LINE_H_BODY_PX:.1f}" text-anchor="start" '
                    f'font-size="{DIAGRAM_MIN_BODY_FONT_PX}" fill="#0F172A">{escape(line_txt)}</text>'
                )

    parts.append("</svg>")
    return "\n".join(parts)


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf", "calibrib.ttf"]
        if bold
        else ["arial.ttf", "DejaVuSans.ttf", "calibri.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_diagram_png(diagram: SectionDiagram, output_path: Path) -> Path:
    """Render a SectionDiagram to a crisp 2x high-DPI PNG sized for >= 12pt Word legibility."""
    scale = DIAGRAM_RENDER_SCALE
    (
        w_tot,
        h_tot,
        boxes,
        grid_mode,
        conn_idx_to_num,
        _,
        legend_layout,
    ) = _compute_full_layout_with_legend(diagram)
    img = Image.new("RGB", (w_tot * scale, h_tot * scale), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(DIAGRAM_TITLE_FONT_PX * scale, bold=True)
    font_body = _load_font(DIAGRAM_MIN_BODY_FONT_PX * scale, bold=False)
    font_badge = _load_font(DIAGRAM_BADGE_FONT_PX * scale, bold=True)
    font_legend_title = _load_font((DIAGRAM_TITLE_FONT_PX - 1) * scale, bold=True)

    # Layer 1: Arrow lines and arrowheads only (gutter-routed when skipping intermediate boxes)
    for idx, conn in enumerate(diagram.connections):
        if conn.from_id not in boxes or conn.to_id not in boxes:
            continue
        pts_l = _connector_path_points(idx, conn, boxes, w_tot, grid_mode)
        pts_s = [(px * scale, py * scale) for px, py in pts_l]
        for seg_i in range(len(pts_s) - 1):
            draw.line([pts_s[seg_i], pts_s[seg_i + 1]], fill="#6C2826", width=2 * scale)
        sx, sy = pts_s[-2]
        ex, ey = pts_s[-1]
        angle = math.atan2(ey - sy, ex - sx)
        arr_len = 8 * scale
        p1 = (ex - arr_len * math.cos(angle - math.pi / 6), ey - arr_len * math.sin(angle - math.pi / 6))
        p2 = (ex - arr_len * math.cos(angle + math.pi / 6), ey - arr_len * math.sin(angle + math.pi / 6))
        draw.polygon([(ex, ey), p1, p2], fill="#6C2826")

    for n in diagram.nodes:
        if n.id not in boxes:
            continue
        x, y, w, h = boxes[n.id]
        xs, ys, ws, hs = x * scale, y * scale, w * scale, h * scale
        color = _sanitize_dark_color(n.color)
        _, _, header_h_base, t_lines, b_lines = _node_box_size(n, grid_mode=grid_mode)
        header_h = header_h_base * scale

        if n.shape == "circle":
            draw.ellipse([xs, ys, xs + ws, ys + hs], fill="#FFFDFD", outline=color, width=2 * scale)
            ty = ys + hs / 2.0 - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
            for idx_t, tl in enumerate(t_lines):
                draw.text(
                    (xs + ws / 2.0, ty + idx_t * DIAGRAM_LINE_H_TITLE_PX * scale),
                    tl,
                    fill=color,
                    font=font_title,
                    anchor="mm",
                )
        elif n.shape == "diamond":
            cx, cy = xs + ws / 2.0, ys + hs / 2.0
            draw.polygon(
                [(cx, ys), (xs + ws, cy), (cx, ys + hs), (xs, cy)],
                fill="#FFFDFD",
                outline=color,
                width=2 * scale,
            )
            ty = cy - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
            for idx_t, tl in enumerate(t_lines):
                draw.text(
                    (cx, ty + idx_t * DIAGRAM_LINE_H_TITLE_PX * scale),
                    tl,
                    fill=color,
                    font=font_title,
                    anchor="mm",
                )
        else:
            radius = 8 * scale if n.shape == "rounded" else 2 * scale
            draw.rounded_rectangle(
                [xs, ys, xs + ws, ys + hs],
                radius=radius,
                fill="#FFFFFF",
                outline=color,
                width=2 * scale,
            )
            draw.rounded_rectangle([xs, ys, xs + ws, ys + header_h], radius=radius, fill=color)
            if radius > 4:
                draw.rectangle([xs, ys + header_h - radius, xs + ws, ys + header_h], fill=color)

            ty = ys + 15 * scale
            for idx_t, tl in enumerate(t_lines):
                draw.text(
                    (xs + ws / 2.0, ty + idx_t * (DIAGRAM_LINE_H_TITLE_PX - 2) * scale),
                    tl,
                    fill="#FFFFFF",
                    font=font_title,
                    anchor="mm",
                )

            by = ys + header_h + 5 * scale
            for idx_b, bl in enumerate(b_lines):
                draw.text(
                    (xs + 12 * scale, by + idx_b * (DIAGRAM_LINE_H_BODY_PX - 1) * scale),
                    bl,
                    fill="#0F172A",
                    font=font_body,
                )

    # Layer 3: Numbered circular badges drawn ABOVE boxes and lines
    r_s = DIAGRAM_BADGE_RADIUS_PX * scale
    for bx, by, badge_num in _compute_badge_positions(diagram, boxes, conn_idx_to_num, w_tot, grid_mode):
        bxs, bys = bx * scale, by * scale
        draw.ellipse(
            [bxs - r_s, bys - r_s, bxs + r_s, bys + r_s],
            fill="#FFFFFF",
            outline="#6C2826",
            width=max(2, int(round(2.2 * scale))),
        )
        draw.text(
            (bxs, bys),
            str(badge_num),
            fill="#6C2826",
            font=font_badge,
            anchor="mm",
        )

    # Layer 4: Dedicated annotation legend box below the flowchart nodes
    if legend_layout:
        lxs = legend_layout["x"] * scale
        lys = legend_layout["y"] * scale
        lws = legend_layout["w"] * scale
        lhs = legend_layout["h"] * scale
        hhs = legend_layout["header_h"] * scale
        rad = 8 * scale
        draw.rounded_rectangle(
            [lxs, lys, lxs + lws, lys + lhs],
            radius=rad,
            fill="#FAF6F2",
            outline="#6C2826",
            width=max(2, int(round(1.5 * scale))),
        )
        draw.rounded_rectangle([lxs, lys, lxs + lws, lys + hhs], radius=rad, fill="#6C2826")
        draw.rectangle([lxs, lys + hhs - rad, lxs + lws, lys + hhs], fill="#6C2826")
        draw.text(
            (lxs + 14 * scale, lys + hhs / 2.0),
            "Légende des annotations (flèches)",
            fill="#FFFFFF",
            font=font_legend_title,
            anchor="lm",
        )
        mr = 10 * scale
        for entry in legend_layout["entries"]:
            exs, eys = entry["x"] * scale, entry["y"] * scale
            bcx, bcy = exs + 11 * scale, eys + 11 * scale
            draw.ellipse(
                [bcx - mr, bcy - mr, bcx + mr, bcy + mr],
                fill="#FFFFFF",
                outline="#6C2826",
                width=max(2, int(round(1.8 * scale))),
            )
            draw.text(
                (bcx, bcy),
                str(entry["num"]),
                fill="#6C2826",
                font=font_badge,
                anchor="mm",
            )
            for l_idx, line_txt in enumerate(entry["lines"]):
                draw.text(
                    (exs + 28 * scale, eys + (2 + l_idx * DIAGRAM_LINE_H_BODY_PX) * scale),
                    line_txt,
                    fill="#0F172A",
                    font=font_body,
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="PNG")
    return output_path


def compute_minimum_legible_figure_size_cm(
    diagram: Optional[SectionDiagram] = None,
    image_path: Optional[Path] = None,
    rules: Optional[FormattingRules] = None,
    min_font_pt: float = 12.0,
    scale: int = DIAGRAM_RENDER_SCALE,
    base_font_px: int = DIAGRAM_MIN_BODY_FONT_PX,
) -> Dict[str, Any]:
    """Calculate minimum physical (width_cm, height_cm) on A4 so characters render >= min_font_pt (12pt).

    Ensures the figure AND its Word caption fit together inside the A4 printable margin box.
    """
    margin_cm = float(getattr(rules, "margin_cm", 2.5) or 2.5)
    usable_width_cm = max(8.0, 21.0 - 2.0 * margin_cm)
    # Reserve 2.2 cm for the centered Figure N caption + pre/post paragraph spacing on the same page
    usable_height_cm = max(8.0, 29.7 - 2.0 * margin_cm - 2.2)

    px_w = 0
    px_h = 0
    if image_path is not None and image_path.exists():
        try:
            with Image.open(image_path) as im:
                px_w, px_h = im.size
        except Exception:
            px_w, px_h = 0, 0

    if (px_w <= 0 or px_h <= 0) and diagram is not None:
        w_tot, h_tot, _, _, _, _, _ = _compute_full_layout_with_legend(diagram)
        px_w = max(1, w_tot * scale)
        px_h = max(1, h_tot * scale)

    if px_w <= 0 or px_h <= 0:
        px_w, px_h = 460 * scale, 260 * scale

    aspect = px_h / max(1, px_w)
    if diagram is not None:
        font_px_in_bitmap = float(base_font_px * scale)
    else:
        # External raster figures: treat baseline label height at 96 DPI (16px ~ 12pt) or 110 DPI equivalent
        font_px_in_bitmap = 16.0 * (2.0 if px_w >= 1000 else 1.0)

    # W_min_cm such that (font_px_in_bitmap / px_w) * (W_min_cm / PT_TO_CM) >= min_font_pt
    min_width_cm = (min_font_pt * PT_TO_CM * px_w) / max(1.0, font_px_in_bitmap)
    min_height_cm = min_width_cm * aspect

    # Aim slightly above threshold (+0.15pt safety margin) when space permits, without bloating vertically
    target_w_cm = max(min_width_cm * 1.01, min(usable_width_cm, min_width_cm * 1.02))
    target_w_cm = min(usable_width_cm, target_w_cm)
    target_h_cm = target_w_cm * aspect

    if target_h_cm > usable_height_cm:
        target_h_cm = usable_height_cm
        target_w_cm = target_h_cm / max(0.05, aspect)

    effective_font_pt = (font_px_in_bitmap / max(1, px_w)) * (target_w_cm / PT_TO_CM)
    fits_with_caption = (
        target_w_cm <= usable_width_cm + 0.01
        and target_h_cm <= usable_height_cm + 0.01
        and effective_font_pt >= (min_font_pt - 0.1)
    )

    return {
        "width_cm": round(target_w_cm, 2),
        "height_cm": round(target_h_cm, 2),
        "min_width_cm": round(min_width_cm, 2),
        "min_height_cm": round(min_height_cm, 2),
        "usable_width_cm": round(usable_width_cm, 2),
        "usable_height_cm": round(usable_height_cm, 2),
        "effective_font_pt": round(effective_font_pt, 2),
        "px_w": int(px_w),
        "px_h": int(px_h),
        "fits_with_caption_on_a4": bool(fits_with_caption),
    }
