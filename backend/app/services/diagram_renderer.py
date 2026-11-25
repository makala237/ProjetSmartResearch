"""Publication-grade deterministic vector diagram engine (SVG for Web + HiDPI PNG for Word/PDF).

Supports 5 academic medical-journal layout templates inspired by PRISMA/CONSORT,
clinical diagnostic algorithms, decision flowcharts, and conceptual pipelines:
  1. horizontal_flow    — Left-to-right conceptual pipeline with progressive accents & inline arrow pills
  2. prisma_exclusion   — PRISMA/CONSORT vertical spine + orthogonal right-hand exclusion branches
  3. decision_flowchart — Process + decision diamond flowchart with Oui/Non orthogonal edges & return loops
  4. branching_tree     — 2D topological layered DAG/tree with orthogonal elbows & dashed links
  5. parallel_converge  — Top root splitting into parallel branches converging into a unified synthesis block

All edge labels render inline directly on arrows (no numbered-circle bottom legend box).
Legacy `layout="vertical"` / `layout="horizontal"` diagrams are automatically routed to the
best-fitting template based on graph topology so existing saved diagrams upgrade seamlessly.
"""

from collections import defaultdict, deque
from html import escape
import math
from pathlib import Path
import re
import textwrap
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.schemas.protocol import DiagramConnection, DiagramNode, FormattingRules, SectionDiagram

# Typography constants calibrated for >= 12 pt legibility on A4 and crisp web SVG.
DIAGRAM_MIN_BODY_FONT_PX = 16
DIAGRAM_TITLE_FONT_PX = 17
DIAGRAM_LABEL_FONT_PX = 14
DIAGRAM_LINE_H_BODY_PX = 21
DIAGRAM_LINE_H_TITLE_PX = 22
DIAGRAM_MAX_CANVAS_W_PX = 760
DIAGRAM_RENDER_SCALE = 2
PT_TO_CM = 2.54 / 72.0

VALID_DIAGRAM_LAYOUTS = (
    "vertical",
    "horizontal",
    "horizontal_flow",
    "prisma_exclusion",
    "decision_flowchart",
    "branching_tree",
    "parallel_converge",
)

VALID_DIAGRAM_SHAPES = (
    "rectangle",
    "rounded",
    "pill",
    "oval",
    "circle",
    "diamond",
    "cylinder",
)

# Semantic academic color palettes (soft pastel fill, crisp border, high-contrast dark text)
_ROLE_PALETTES: Dict[str, Dict[str, str]] = {
    "start_end": {
        "fill": "#FEF9E7",
        "stroke": "#D97706",
        "header_fill": "#FEF3C7",
        "title_fg": "#78350F",
        "body_fg": "#334155",
        "accent": "#D97706",
    },
    "process": {
        "fill": "#F0FDFA",
        "stroke": "#0F766E",
        "header_fill": "#CCFBF1",
        "title_fg": "#134E4A",
        "body_fg": "#1E293B",
        "accent": "#0F766E",
    },
    "decision": {
        "fill": "#FFF7ED",
        "stroke": "#C2410C",
        "header_fill": "#FFEDD5",
        "title_fg": "#7C2D12",
        "body_fg": "#334155",
        "accent": "#C2410C",
    },
    "exclusion": {
        "fill": "#FEF2F2",
        "stroke": "#B91C1C",
        "header_fill": "#FEE2E2",
        "title_fg": "#7F1D1D",
        "body_fg": "#334155",
        "accent": "#B91C1C",
    },
    "outcome": {
        "fill": "#FAF5F5",
        "stroke": "#6C2826",
        "header_fill": "#6C2826",
        "title_fg": "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": "#6C2826",
    },
    "prisma_main": {
        "fill": "#FFFFFF",
        "stroke": "#1E293B",
        "header_fill": "#F1F5F9",
        "title_fg": "#0F172A",
        "body_fg": "#1E293B",
        "accent": "#1E293B",
    },
    "prisma_excl": {
        "fill": "#F8FAFC",
        "stroke": "#64748B",
        "header_fill": "#F1F5F9",
        "title_fg": "#334155",
        "body_fg": "#475569",
        "accent": "#64748B",
    },
    "clinical": {
        "fill": "#FFF5EB",
        "stroke": "#B45309",
        "header_fill": "#FDE6D2",
        "title_fg": "#7C2D12",
        "body_fg": "#1E293B",
        "accent": "#B45309",
    },
    "blue_tech": {
        "fill": "#EFF6FF",
        "stroke": "#1D4ED8",
        "header_fill": "#DBEAFE",
        "title_fg": "#1E3A8A",
        "body_fg": "#1E293B",
        "accent": "#1D4ED8",
    },
    "stage_gap": {
        "fill": "#FEF2F2",
        "stroke": "#991B1B",
        "header_fill": "#991B1B",
        "title_fg": "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": "#991B1B",
    },
    "stage_study": {
        "fill": "#F0FDFA",
        "stroke": "#0F766E",
        "header_fill": "#0F766E",
        "title_fg": "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": "#0F766E",
    },
    "stage_goal": {
        "fill": "#EFF6FF",
        "stroke": "#1E3A8A",
        "header_fill": "#1E3A8A",
        "title_fg": "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": "#1E3A8A",
    },
    "stage_fmsb": {
        "fill": "#FAF5F5",
        "stroke": "#6C2826",
        "header_fill": "#6C2826",
        "title_fg": "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": "#6C2826",
    },
}

_EXCL_KEYWORDS = (
    "exclu",
    "non retenu",
    "non inclus",
    "perdu",
    "refus",
    "relance",
    "rejet",
    "élimin",
    "elimin",
    "abandoned",
    "dropout",
    "ineligible",
)


def _is_exclusion_node(node: DiagramNode, edge_label: str = "") -> bool:
    hay = f"{node.title} {' '.join(node.items or [])} {edge_label}".lower()
    return any(kw in hay for kw in _EXCL_KEYWORDS)


def _is_yes_no_label(label: str) -> bool:
    low = (label or "").strip().lower()
    return low in ("oui", "non", "yes", "no", "vrai", "faux", "true", "false")


def _sanitize_dark_color(hex_color: str) -> str:
    """Guarantee dark, high-contrast colors for accents/borders."""
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
    if luminance <= 135.0:
        return f"#{r:02X}{g:02X}{b:02X}"
    factor = 95.0 / max(1.0, luminance)
    dr = max(0, min(255, int(r * factor)))
    dg = max(0, min(255, int(g * factor)))
    db = max(0, min(255, int(b * factor)))
    return f"#{dr:02X}{dg:02X}{db:02X}"


def _tint_hex(hex_color: str, mix_white: float = 0.90) -> str:
    """Create a clean soft pastel tint from a hex color."""
    raw = _sanitize_dark_color(hex_color).lstrip("#")
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    tr = int(r + (255 - r) * mix_white)
    tg = int(g + (255 - g) * mix_white)
    tb = int(b + (255 - b) * mix_white)
    return f"#{tr:02X}{tg:02X}{tb:02X}"


def _resolve_effective_layout(diagram: SectionDiagram) -> str:
    """Choose the best publication-grade layout template for a SectionDiagram."""
    raw = (diagram.layout or "").strip().lower()
    if raw in (
        "horizontal_flow",
        "prisma_exclusion",
        "decision_flowchart",
        "branching_tree",
        "parallel_converge",
    ):
        return raw

    nodes = diagram.nodes or []
    conns = diagram.connections or []
    if not nodes:
        return "horizontal_flow"

    # Build adjacency
    out_edges: Dict[str, List[DiagramConnection]] = defaultdict(list)
    in_deg: Dict[str, int] = {n.id: 0 for n in nodes}
    out_deg: Dict[str, int] = {n.id: 0 for n in nodes}
    node_map = {n.id: n for n in nodes}
    for c in conns:
        if c.from_id in node_map and c.to_id in node_map and c.from_id != c.to_id:
            out_edges[c.from_id].append(c)
            out_deg[c.from_id] += 1
            in_deg[c.to_id] += 1

    has_diamond = any(n.shape == "diamond" for n in nodes)
    has_yes_no = any(_is_yes_no_label(c.label) for c in conns)
    has_exclusion_leaf = any(
        out_deg.get(c.to_id, 0) == 0
        and out_deg.get(c.from_id, 0) >= 2
        and _is_exclusion_node(node_map[c.to_id], c.label)
        for c in conns
        if c.from_id in node_map and c.to_id in node_map
    )

    if has_exclusion_leaf and not has_diamond:
        return "prisma_exclusion"

    if has_diamond or has_yes_no:
        return "decision_flowchart"

    max_out = max(out_deg.values(), default=0)
    max_in = max(in_deg.values(), default=0)
    if max_out >= 2 and max_in >= 2:
        return "parallel_converge"
    if max_out >= 2:
        return "branching_tree"

    # Linear chain (no branching): 2..4 nodes -> crisp horizontal conceptual pipeline!
    if 2 <= len(nodes) <= 4:
        return "horizontal_flow"

    if raw == "horizontal" and len(nodes) <= 4:
        return "horizontal_flow"

    return "decision_flowchart"


VALID_DIAGRAM_THEMES: Dict[str, List[str]] = {
    "auto": [],
    "fmsb_burgundy": ["#6C2826", "#991B1B", "#B45309", "#7C2D12"],
    "medical_teal": ["#0F766E", "#0369A1", "#1E3A8A", "#0D9488"],
    "navy_slate": ["#1E3A8A", "#334155", "#0F766E", "#1D4ED8"],
    "emerald_forest": ["#047857", "#0F766E", "#1D4ED8", "#065F46"],
    "monochrome_prisma": ["#1E293B", "#334155", "#475569", "#0F172A"],
}


def _make_custom_palette(hex_color: str, flat_header: bool = False) -> Dict[str, str]:
    dark = _sanitize_dark_color(hex_color)
    return {
        "fill": _tint_hex(dark, 0.93),
        "stroke": dark,
        "header_fill": _tint_hex(dark, 0.84) if flat_header else dark,
        "title_fg": dark if flat_header else "#FFFFFF",
        "body_fg": "#1E293B",
        "accent": dark,
    }


def _safe_light_bg_fg(pal: Dict[str, str]) -> str:
    """Guarantee dark high-contrast text whenever drawing directly onto a light pastel fill."""
    fg = (pal.get("title_fg") or "").strip().upper()
    if not fg or fg in ("#FFFFFF", "#FFF", "#FAF5F5", "#F8FAFC"):
        return _sanitize_dark_color(pal.get("stroke") or "#1E293B")
    return fg


def _node_palette(
    node: DiagramNode,
    idx: int,
    total_nodes: int,
    effective_layout: str,
    is_excl: bool = False,
    is_root: bool = False,
    is_leaf: bool = False,
    color_theme: str = "auto",
) -> Dict[str, str]:
    """Select clean semantic academic colors for a node, honoring user color_theme and per-node color."""
    theme_key = (color_theme or "auto").strip().lower()
    theme_colors = VALID_DIAGRAM_THEMES.get(theme_key) or []

    # Respect non-default custom colors if explicitly chosen by user/AI
    custom = (node.color or "").strip().upper()
    has_custom_non_default = bool(custom) and custom not in ("#6C2826", "#000000", "")

    if has_custom_non_default and theme_key == "auto":
        pal = _make_custom_palette(custom, flat_header=(node.shape in ("circle", "oval", "pill", "diamond")))
        return pal

    if theme_colors:
        if theme_key == "monochrome_prisma":
            pal = dict(_ROLE_PALETTES["prisma_excl"] if is_excl else _ROLE_PALETTES["prisma_main"])
        else:
            base_hex = custom if has_custom_non_default else theme_colors[idx % len(theme_colors)]
            pal = _make_custom_palette(base_hex, flat_header=(node.shape in ("circle", "oval", "pill", "diamond")))
        return pal

    if effective_layout == "prisma_exclusion":
        return dict(_ROLE_PALETTES["prisma_excl"] if is_excl else _ROLE_PALETTES["prisma_main"])

    if node.shape == "diamond":
        return dict(_ROLE_PALETTES["decision"])

    if is_excl:
        return dict(_ROLE_PALETTES["exclusion"])

    if effective_layout == "horizontal_flow":
        stage_cycle = ["stage_gap", "stage_study", "stage_fmsb", "stage_goal"]
        if total_nodes == 3:
            stage_cycle = ["stage_gap", "stage_study", "stage_fmsb"]
        elif total_nodes == 2:
            stage_cycle = ["stage_study", "stage_fmsb"]
        pal = dict(_ROLE_PALETTES[stage_cycle[idx % len(stage_cycle)]])
        if node.shape in ("circle", "oval", "pill", "diamond"):
            pal["title_fg"] = pal["stroke"]
        return pal

    if effective_layout == "decision_flowchart":
        if node.shape in ("pill", "oval") or (is_root and not node.items) or (is_leaf and not node.items and total_nodes >= 3):
            return dict(_ROLE_PALETTES["start_end"])
        return dict(_ROLE_PALETTES["process"])

    if effective_layout == "parallel_converge":
        if is_root:
            return dict(_ROLE_PALETTES["stage_fmsb"])
        if is_leaf:
            return dict(_ROLE_PALETTES["outcome"])
        return dict(_ROLE_PALETTES["blue_tech"] if idx % 2 == 1 else _ROLE_PALETTES["process"])

    if effective_layout == "branching_tree":
        if node.shape in ("pill", "oval", "circle"):
            return dict(_ROLE_PALETTES["process"])
        if is_root:
            return dict(_ROLE_PALETTES["stage_fmsb"])
        return dict(_ROLE_PALETTES["clinical"])

    return dict(_ROLE_PALETTES["process"])


def _wrap_text(text: str, max_chars: int) -> List[str]:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []
    return textwrap.wrap(clean, width=max(10, max_chars), break_long_words=False) or [clean]


def _wrap_node_lines(node: DiagramNode, box_w: int, shape: str) -> Tuple[List[str], List[str]]:
    """Wrap title and bullet items cleanly inside box_w."""
    if shape == "diamond":
        usable_w = max(90, int(box_w * 0.56))
    elif shape in ("pill", "oval", "circle"):
        usable_w = max(100, int(box_w * 0.78))
    else:
        usable_w = max(110, box_w - 26)

    title_chars = max(12, int(usable_w / 8.0))
    body_chars = max(14, int(usable_w / 7.3))

    t_lines = _wrap_text(node.title, title_chars) or [node.title.strip() or node.id]
    b_lines: List[str] = []
    for item in node.items or []:
        clean = item.strip()
        if not clean:
            continue
        # Preserve bold/stat lines without redundant bullet if already prefixed or N = ...
        already_prefixed = clean.startswith(("•", "-", "*", "1.", "2.", "3.")) or bool(re.match(r"^[Nn]\s*=", clean))
        wrapped = _wrap_text(clean, body_chars - (0 if already_prefixed else 2))
        for i, wl in enumerate(wrapped):
            if i == 0 and not already_prefixed:
                b_lines.append(f"• {wl}")
            elif i > 0 and not already_prefixed:
                b_lines.append(f"  {wl}")
            else:
                b_lines.append(wl)
    return t_lines, b_lines


def _measure_node(
    node: DiagramNode,
    target_w: int,
    effective_layout: str,
    is_root: bool = False,
    is_leaf: bool = False,
) -> Dict[str, Any]:
    """Compute exact (w, h, header_h, shape, t_lines, b_lines) for a node."""
    shape = (node.shape or "rounded").strip().lower()
    if shape not in VALID_DIAGRAM_SHAPES:
        shape = "rounded"

    # Upgrade start/end empty-item nodes in decision flowcharts to crisp pills
    if effective_layout == "decision_flowchart" and shape == "rounded" and not node.items and (is_root or is_leaf):
        shape = "pill"
    elif effective_layout == "prisma_exclusion" and shape == "rounded":
        shape = "rectangle"

    if shape == "diamond":
        w = max(170, min(240, target_w))
        t_lines, b_lines = _wrap_node_lines(node, w, shape)
        text_h = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + len(b_lines) * DIAGRAM_LINE_H_BODY_PX
        h = max(84, int(text_h * 1.85) + 28)
        return {"w": w, "h": h, "header_h": 0, "shape": shape, "t_lines": t_lines, "b_lines": b_lines}

    if shape in ("pill", "oval"):
        w = max(170, min(280, target_w))
        t_lines, b_lines = _wrap_node_lines(node, w, shape)
        text_h = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + len(b_lines) * DIAGRAM_LINE_H_BODY_PX
        h = max(48, text_h + 24)
        return {"w": w, "h": h, "header_h": 0, "shape": shape, "t_lines": t_lines, "b_lines": b_lines}

    if shape == "circle":
        w = max(120, min(190, target_w))
        t_lines, b_lines = _wrap_node_lines(node, w, shape)
        text_h = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + len(b_lines) * DIAGRAM_LINE_H_BODY_PX
        side = max(w, text_h + 34)
        return {"w": side, "h": side, "header_h": 0, "shape": shape, "t_lines": t_lines, "b_lines": b_lines}

    w = max(165, target_w)
    t_lines, b_lines = _wrap_node_lines(node, w, shape)

    if effective_layout == "prisma_exclusion":
        # Clean academic PRISMA box: centered bold title + centered body lines, no dark header bar
        body_gap = 6 if b_lines else 0
        h = max(56, 16 + len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + body_gap + len(b_lines) * DIAGRAM_LINE_H_BODY_PX + 14)
        return {"w": w, "h": h, "header_h": 0, "shape": "rectangle", "t_lines": t_lines, "b_lines": b_lines}

    if not b_lines and effective_layout in ("decision_flowchart", "branching_tree"):
        # Compact single-tone card when there are no bullet items
        h = max(50, len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + 24)
        return {"w": w, "h": h, "header_h": 0, "shape": shape, "t_lines": t_lines, "b_lines": b_lines}

    header_h = max(32, len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + 12)
    body_h = (len(b_lines) * DIAGRAM_LINE_H_BODY_PX + 16) if b_lines else 6
    h = max(56, header_h + body_h)
    return {"w": w, "h": h, "header_h": header_h, "shape": shape, "t_lines": t_lines, "b_lines": b_lines}


def _wrap_pill_lines(label: str) -> List[str]:
    txt = re.sub(r"\s+", " ", (label or "").strip())
    if not txt:
        return []
    if len(txt) <= 18:
        return [txt]
    lines = textwrap.wrap(txt, width=16, break_long_words=False)
    if len(lines) <= 2:
        return lines
    return [lines[0], " ".join(lines[1:])]


def _estimate_pill_size(label: str) -> Tuple[int, int]:
    lines = _wrap_pill_lines(label)
    if not lines:
        return 0, 0
    max_len = max(len(ln) for ln in lines)
    w = max(52, int(max_len * 8.4) + 24)
    h = 26 if len(lines) == 1 else 42
    return w, h


def _topological_layers(
    nodes: List[DiagramNode],
    conns: List[DiagramConnection],
) -> Tuple[List[List[str]], Set[Tuple[str, str]]]:
    """Assign nodes to DAG layers (0..L-1) and identify back-edges (feedback loops)."""
    node_ids = [n.id for n in nodes]
    id_set = set(node_ids)
    adj: Dict[str, List[str]] = defaultdict(list)
    in_deg: Dict[str, int] = {nid: 0 for nid in node_ids}
    order_idx = {nid: i for i, nid in enumerate(node_ids)}

    back_edges: Set[Tuple[str, str]] = set()
    # Simple DFS to detect cycle back-edges
    state: Dict[str, int] = {nid: 0 for nid in node_ids}  # 0=unvisited, 1=visiting, 2=done
    raw_adj: Dict[str, List[str]] = defaultdict(list)
    for c in conns:
        if c.from_id in id_set and c.to_id in id_set and c.from_id != c.to_id:
            raw_adj[c.from_id].append(c.to_id)

    def dfs(u: str) -> None:
        state[u] = 1
        for v in raw_adj.get(u, []):
            if state[v] == 1:
                back_edges.add((u, v))
            elif state[v] == 0:
                dfs(v)
        state[u] = 2

    for nid in node_ids:
        if state[nid] == 0:
            dfs(nid)

    for c in conns:
        if c.from_id in id_set and c.to_id in id_set and c.from_id != c.to_id:
            if (c.from_id, c.to_id) not in back_edges:
                adj[c.from_id].append(c.to_id)
                in_deg[c.to_id] += 1

    # Longest-path layering from roots
    layer_of: Dict[str, int] = {}
    q = deque([nid for nid in node_ids if in_deg[nid] == 0])
    for nid in q:
        layer_of[nid] = 0

    temp_in = dict(in_deg)
    while q:
        u = q.popleft()
        lu = layer_of.get(u, 0)
        for v in adj.get(u, []):
            layer_of[v] = max(layer_of.get(v, 0), lu + 1)
            temp_in[v] -= 1
            if temp_in[v] == 0:
                q.append(v)

    for nid in node_ids:
        if nid not in layer_of:
            layer_of[nid] = order_idx[nid]

    max_l = max(layer_of.values(), default=0)
    layers: List[List[str]] = [[] for _ in range(max_l + 1)]
    for nid in node_ids:
        layers[layer_of[nid]].append(nid)
    return [ly for ly in layers if ly], back_edges


def _build_scene(diagram: SectionDiagram) -> Dict[str, Any]:
    """Compute complete positioned scene: nodes, orthogonal routed edges, and inline label pills."""
    nodes = diagram.nodes or []
    conns = diagram.connections or []
    if not nodes:
        return {"w": 420, "h": 140, "nodes": [], "edges": [], "layout": "horizontal_flow"}

    effective_layout = _resolve_effective_layout(diagram)
    color_theme = str(getattr(diagram, "color_theme", "auto") or "auto").strip().lower()
    node_map = {n.id: n for n in nodes}
    in_deg: Dict[str, int] = {n.id: 0 for n in nodes}
    out_edges: Dict[str, List[DiagramConnection]] = defaultdict(list)
    for c in conns:
        if c.from_id in node_map and c.to_id in node_map and c.from_id != c.to_id:
            out_edges[c.from_id].append(c)
            in_deg[c.to_id] += 1

    layers, back_edges = _topological_layers(nodes, conns)
    boxes: Dict[str, Dict[str, Any]] = {}
    excl_ids: Set[str] = set()

    # Identify exclusion side-nodes
    for c in conns:
        if c.from_id in node_map and c.to_id in node_map:
            if len(out_edges[c.to_id]) == 0 and len(out_edges[c.from_id]) >= 2:
                if _is_exclusion_node(node_map[c.to_id], c.label) or (
                    effective_layout == "decision_flowchart" and (c.label or "").strip().lower() in ("non", "no")
                ):
                    excl_ids.add(c.to_id)

    # ── LAYOUT 1: HORIZONTAL CONCEPTUAL PIPELINE ─────────────────────────────
    if effective_layout == "horizontal_flow":
        pad_x, pad_y = 24, 24
        n_count = len(nodes)
        # Choose arrow gap wide enough to hold inline edge labels cleanly!
        max_lbl_w = max((_estimate_pill_size(c.label)[0] for c in conns if (c.label or "").strip()), default=0)
        gap_x = max(56, max_lbl_w + 26)
        usable_for_nodes = max(460, DIAGRAM_MAX_CANVAS_W_PX - 2 * pad_x - gap_x * max(0, n_count - 1))
        card_w = max(175, min(245, usable_for_nodes // max(1, n_count)))

        measured = []
        for idx, n in enumerate(nodes):
            m = _measure_node(n, card_w, effective_layout, is_root=(idx == 0), is_leaf=(idx == n_count - 1))
            measured.append((idx, n, m))

        max_h = max(m["h"] for _, _, m in measured)
        cur_x = pad_x
        for idx, n, m in measured:
            # Equalize card heights in horizontal conceptual pipeline for magazine-grade symmetry
            h_eq = max_h if (m["shape"] in ("rounded", "rectangle") and n_count <= 4) else m["h"]
            y = pad_y + (max_h - h_eq) // 2
            pal = _node_palette(n, idx, n_count, effective_layout, is_root=(idx == 0), is_leaf=(idx == n_count - 1), color_theme=color_theme)
            boxes[n.id] = {
                "node": n,
                "x": cur_x,
                "y": y,
                "w": m["w"],
                "h": h_eq,
                "header_h": m["header_h"],
                "shape": m["shape"],
                "t_lines": m["t_lines"],
                "b_lines": m["b_lines"],
                "palette": pal,
            }
            cur_x += m["w"] + gap_x

        canvas_w = cur_x - gap_x + pad_x
        canvas_h = pad_y * 2 + max_h

    # ── LAYOUT 2: PRISMA / CONSORT SCREENING & EXCLUSION FLOW ────────────────
    elif effective_layout == "prisma_exclusion":
        pad_x, pad_y = 24, 24
        main_w = 276
        excl_w = 248
        gap_x = 68
        gap_y = 48

        # Partition into main spine nodes vs right-hand exclusion nodes
        main_ids = [n.id for n in nodes if n.id not in excl_ids]
        excl_by_parent: Dict[str, List[str]] = defaultdict(list)
        for c in conns:
            if c.to_id in excl_ids and c.from_id in node_map:
                excl_by_parent[c.from_id].append(c.to_id)

        # Measure all main spine nodes
        cur_y = pad_y
        for idx, nid in enumerate(main_ids):
            n = node_map[nid]
            m = _measure_node(n, main_w, effective_layout, is_root=(idx == 0), is_leaf=(idx == len(main_ids) - 1))
            pal = _node_palette(n, idx, len(nodes), effective_layout, is_excl=False, color_theme=color_theme)
            boxes[nid] = {
                "node": n,
                "x": pad_x,
                "y": cur_y,
                "w": m["w"],
                "h": m["h"],
                "header_h": m["header_h"],
                "shape": m["shape"],
                "t_lines": m["t_lines"],
                "b_lines": m["b_lines"],
                "palette": pal,
            }
            # Place right-hand exclusion branch offset halfway between this node and the next spine node (classic PRISMA!)
            children_excl = excl_by_parent.get(nid, [])
            extra_drop = 0
            if children_excl:
                ex_y = cur_y + m["h"] + 10
                for e_idx, eid in enumerate(children_excl):
                    en = node_map[eid]
                    em = _measure_node(en, excl_w, effective_layout, is_excl=True) if False else _measure_node(en, excl_w, effective_layout)
                    epal = _node_palette(en, e_idx, len(nodes), effective_layout, is_excl=True, color_theme=color_theme)
                    boxes[eid] = {
                        "node": en,
                        "x": pad_x + main_w + gap_x,
                        "y": ex_y,
                        "w": em["w"],
                        "h": em["h"],
                        "header_h": em["header_h"],
                        "shape": em["shape"],
                        "t_lines": em["t_lines"],
                        "b_lines": em["b_lines"],
                        "palette": epal,
                    }
                    ex_y += em["h"] + 14
                    extra_drop = max(extra_drop, em["h"] + 22)
            cur_y += m["h"] + max(gap_y, extra_drop)

        canvas_w = pad_x * 2 + main_w + (gap_x + excl_w if excl_ids else 0)
        canvas_h = max((b["y"] + b["h"] for b in boxes.values()), default=160) + pad_y

    # ── LAYOUT 3, 4, 5: LAYERED 2D GRAPH (DECISION / BRANCHING / PARALLEL) ──
    else:
        pad_x = 32
        pad_y = 24
        has_back_edge = len(back_edges) > 0
        left_loop_gutter = 64 if has_back_edge else 0
        gap_x = 42
        gap_y = 66 if effective_layout == "decision_flowchart" else 56

        # In decision_flowchart, pull side exclusion/exception nodes onto the SAME row as their parent diamond!
        side_nodes_by_parent: Dict[str, str] = {}
        if effective_layout == "decision_flowchart" and excl_ids:
            for c in conns:
                if c.to_id in excl_ids and c.from_id in node_map and c.from_id not in side_nodes_by_parent:
                    side_nodes_by_parent[c.from_id] = c.to_id

        side_set = set(side_nodes_by_parent.values())
        filtered_layers: List[List[str]] = []
        for ly in layers:
            kept = [nid for nid in ly if nid not in side_set]
            if kept:
                filtered_layers.append(kept)

        max_cols = max((len(ly) for ly in filtered_layers), default=1)
        if effective_layout == "decision_flowchart" and max_cols == 1:
            base_w = 340
        elif max_cols == 1:
            base_w = 340
        elif max_cols == 2:
            base_w = 260
        else:
            base_w = max(175, min(220, (DIAGRAM_MAX_CANVAS_W_PX - 2 * pad_x - gap_x * (max_cols - 1)) // max_cols))

        # Measure each row
        row_items: List[List[Tuple[str, Dict[str, Any]]]] = []
        row_widths: List[int] = []
        row_heights: List[int] = []
        for r_idx, ly in enumerate(filtered_layers):
            items_in_row: List[Tuple[str, Dict[str, Any]]] = []
            for nid in ly:
                n = node_map[nid]
                is_root = r_idx == 0 and in_deg.get(nid, 0) == 0
                is_leaf = r_idx == len(filtered_layers) - 1 and len(out_edges.get(nid, [])) == 0
                w_hint = min(380, base_w + 80) if (effective_layout == "parallel_converge" and len(ly) == 1 and max_cols >= 2) else base_w
                m = _measure_node(n, w_hint, effective_layout, is_root=is_root, is_leaf=is_leaf)
                items_in_row.append((nid, m))
            rw = sum(m["w"] for _, m in items_in_row) + gap_x * max(0, len(items_in_row) - 1)
            rh = max((m["h"] for _, m in items_in_row), default=56)
            row_items.append(items_in_row)
            row_widths.append(rw)
            row_heights.append(rh)

        side_extra_left = 0
        side_measures: Dict[str, Dict[str, Any]] = {}
        for parent_id, sid in side_nodes_by_parent.items():
            sn = node_map[sid]
            sm = _measure_node(sn, 200, effective_layout, is_leaf=False)
            side_measures[sid] = sm
            side_extra_left = max(side_extra_left, sm["w"] + 78)

        content_w = max(row_widths, default=base_w)
        center_x = pad_x + max(left_loop_gutter, side_extra_left) + content_w / 2.0

        cur_y = pad_y
        node_order = {n.id: i for i, n in enumerate(nodes)}
        for r_idx, items_in_row in enumerate(row_items):
            rw = row_widths[r_idx]
            rh = row_heights[r_idx]
            cur_x = int(round(center_x - rw / 2.0))
            for nid, m in items_in_row:
                n = node_map[nid]
                is_root = r_idx == 0 and in_deg.get(nid, 0) == 0
                is_leaf = r_idx == len(row_items) - 1 and len(out_edges.get(nid, [])) == 0
                y = cur_y + (rh - m["h"]) // 2
                pal = _node_palette(
                    n,
                    node_order.get(nid, 0),
                    len(nodes),
                    effective_layout,
                    is_excl=(nid in excl_ids),
                    is_root=is_root,
                    is_leaf=is_leaf,
                    color_theme=color_theme,
                )
                boxes[nid] = {
                    "node": n,
                    "x": cur_x,
                    "y": y,
                    "w": m["w"],
                    "h": m["h"],
                    "header_h": m["header_h"],
                    "shape": m["shape"],
                    "t_lines": m["t_lines"],
                    "b_lines": m["b_lines"],
                    "palette": pal,
                }
                # Position attached side exception node to the left (like images (3).png!)
                if nid in side_nodes_by_parent:
                    sid = side_nodes_by_parent[nid]
                    sm = side_measures[sid]
                    sn = node_map[sid]
                    sx = max(pad_x, cur_x - 76 - sm["w"])
                    sy = y + (m["h"] - sm["h"]) // 2
                    spal = _node_palette(sn, node_order.get(sid, 0), len(nodes), effective_layout, is_excl=True, color_theme=color_theme)
                    boxes[sid] = {
                        "node": sn,
                        "x": sx,
                        "y": sy,
                        "w": sm["w"],
                        "h": sm["h"],
                        "header_h": sm["header_h"],
                        "shape": sm["shape"],
                        "t_lines": sm["t_lines"],
                        "b_lines": sm["b_lines"],
                        "palette": spal,
                    }
                cur_x += m["w"] + gap_x
            cur_y += rh + gap_y

        max_x = max((b["x"] + b["w"] for b in boxes.values()), default=420)
        max_y = max((b["y"] + b["h"] for b in boxes.values()), default=160)
        canvas_w = max_x + pad_x
        canvas_h = max_y + pad_y

    # ── ROUTE ORTHOGONAL EDGES + INLINE LABEL PILLS ──────────────────────────
    edges_out: List[Dict[str, Any]] = []
    skip_bridge_slot = 0
    max_box_bottom = max((b["y"] + b["h"] for b in boxes.values()), default=160)

    for c_idx, conn in enumerate(conns):
        if conn.from_id not in boxes or conn.to_id not in boxes:
            continue
        b1 = boxes[conn.from_id]
        b2 = boxes[conn.to_id]
        cx1, cy1 = b1["x"] + b1["w"] / 2.0, b1["y"] + b1["h"] / 2.0
        cx2, cy2 = b2["x"] + b2["w"] / 2.0, b2["y"] + b2["h"] / 2.0
        is_back = (conn.from_id, conn.to_id) in back_edges
        label = (conn.label or "").strip()

        # 1. Back-edge (feedback loop going upward on the left gutter, like images (3).png!)
        if is_back or cy2 < cy1 - 10:
            loop_x = max(18.0, min(b1["x"], b2["x"]) - 42.0 - (c_idx % 2) * 14.0)
            pts = [
                (float(b1["x"]), cy1),
                (loop_x, cy1),
                (loop_x, cy2),
                (float(b2["x"]) - 2.0, cy2),
            ]
            lbl_pos = (loop_x, (cy1 + cy2) / 2.0)

        # 2. PRISMA right-hand exclusion orthogonal elbow from main spine stem
        elif effective_layout == "prisma_exclusion" and conn.to_id in excl_ids:
            stem_y = cy2
            pts = [
                (cx1, float(b1["y"] + b1["h"])),
                (cx1, stem_y),
                (float(b2["x"]) - 2.0, stem_y),
            ]
            lbl_pos = ((cx1 + b2["x"]) / 2.0, stem_y)

        # 3. Same horizontal band (left-to-right or right-to-left)
        elif abs(cy1 - cy2) <= max(b1["h"], b2["h"]) * 0.45:
            lo_x = min(b1["x"] + b1["w"], b2["x"] + b2["w"])
            hi_x = max(b1["x"], b2["x"])
            # Detect if any intermediate box sits between b1 and b2 on this horizontal band
            blocks_between = any(
                oid not in (conn.from_id, conn.to_id)
                and ob["x"] < hi_x
                and (ob["x"] + ob["w"]) > lo_x
                and ob["y"] <= max(cy1, cy2)
                and (ob["y"] + ob["h"]) >= min(cy1, cy2)
                for oid, ob in boxes.items()
            )
            if blocks_between:
                # Route cleanly UNDER the boxes as an orthogonal U-bridge so nothing ever overlaps!
                _, pill_h = _estimate_pill_size(label)
                bridge_y = float(max_box_bottom) + 34.0 + skip_bridge_slot * (max(36, pill_h + 14))
                skip_bridge_slot += 1
                pts = [
                    (cx1, float(b1["y"] + b1["h"])),
                    (cx1, bridge_y),
                    (cx2, bridge_y),
                    (cx2, float(b2["y"] + b2["h"]) + 2.0),
                ]
                lbl_pos = ((cx1 + cx2) / 2.0, bridge_y)
                canvas_h = max(canvas_h, int(math.ceil(bridge_y + max(18, pill_h / 2.0) + 18)))
            elif cx2 >= cx1:
                pts = [(float(b1["x"] + b1["w"]), cy1), (float(b2["x"]) - 2.0, cy2)]
                lbl_pos = ((pts[0][0] + pts[-1][0]) / 2.0, (pts[0][1] + pts[-1][1]) / 2.0)
            else:
                pts = [(float(b1["x"]), cy1), (float(b2["x"] + b2["w"]) + 2.0, cy2)]
                lbl_pos = ((pts[0][0] + pts[-1][0]) / 2.0, (pts[0][1] + pts[-1][1]) / 2.0)

        # 4. Top-to-bottom vertical or branching orthogonal elbow
        else:
            start_y = float(b1["y"] + b1["h"])
            end_y = float(b2["y"]) - 2.0
            if abs(cx1 - cx2) <= 4.0:
                pts = [(cx1, start_y), (cx2, end_y)]
                lbl_pos = (cx1, (start_y + end_y) / 2.0)
            else:
                # If source is a diamond and destination is left/right below, exit from diamond side!
                if b1["shape"] == "diamond" and abs(cx2 - cx1) > b1["w"] * 0.4:
                    sx = float(b1["x"] + b1["w"]) if cx2 > cx1 else float(b1["x"])
                    pts = [(sx, cy1), (cx2, cy1), (cx2, end_y)]
                    lbl_pos = ((sx + cx2) / 2.0, cy1)
                else:
                    mid_y = start_y + (end_y - start_y) * 0.48
                    pts = [(cx1, start_y), (cx1, mid_y), (cx2, mid_y), (cx2, end_y)]
                    lbl_pos = (cx2, mid_y + (end_y - mid_y) * 0.42) if abs(cx2 - cx1) > 40 else ((cx1 + cx2) / 2.0, mid_y)

        edge_color = "#B91C1C" if conn.to_id in excl_ids else ("#334155" if effective_layout == "prisma_exclusion" else "#475569")
        edges_out.append(
            {
                "from_id": conn.from_id,
                "to_id": conn.to_id,
                "label": label,
                "style": conn.style or "solid",
                "points": pts,
                "label_pos": lbl_pos,
                "color": edge_color,
            }
        )

    # Shift everything right if any feedback loop extended near x < 16
    min_x_all = min(
        [b["x"] for b in boxes.values()]
        + [p[0] - (_estimate_pill_size(e["label"])[0] / 2.0 if e["label"] else 0) for e in edges_out for p in e["points"]],
        default=16,
    )
    if min_x_all < 16:
        dx = int(math.ceil(16 - min_x_all))
        for b in boxes.values():
            b["x"] += dx
        for e in edges_out:
            e["points"] = [(px + dx, py) for px, py in e["points"]]
            e["label_pos"] = (e["label_pos"][0] + dx, e["label_pos"][1])
        canvas_w += dx

    return {
        "w": int(math.ceil(canvas_w)),
        "h": int(math.ceil(canvas_h)),
        "nodes": [boxes[n.id] for n in nodes if n.id in boxes],
        "edges": edges_out,
        "layout": effective_layout,
    }


def _rounded_orthogonal_svg_path(pts: List[Tuple[float, float]], radius: float = 8.0) -> str:
    """Build an SVG path `d` string with crisp rounded 90-degree corners."""
    if len(pts) < 2:
        return ""
    if len(pts) == 2:
        return f"M {pts[0][0]:.1f} {pts[0][1]:.1f} L {pts[1][0]:.1f} {pts[1][1]:.1f}"
    cmds = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        v1x, v1y = p1[0] - p0[0], p1[1] - p0[1]
        v2x, v2y = p2[0] - p1[0], p2[1] - p1[1]
        d1 = math.hypot(v1x, v1y)
        d2 = math.hypot(v2x, v2y)
        r = min(radius, d1 / 2.0, d2 / 2.0)
        if r < 1.0:
            cmds.append(f"L {p1[0]:.1f} {p1[1]:.1f}")
            continue
        ax = p1[0] - (v1x / d1) * r
        ay = p1[1] - (v1y / d1) * r
        bx = p1[0] + (v2x / d2) * r
        by = p1[1] + (v2y / d2) * r
        cmds.append(f"L {ax:.1f} {ay:.1f} Q {p1[0]:.1f} {p1[1]:.1f} {bx:.1f} {by:.1f}")
    cmds.append(f"L {pts[-1][0]:.1f} {pts[-1][1]:.1f}")
    return " ".join(cmds)


def render_diagram_svg(diagram: SectionDiagram) -> str:
    """Render a SectionDiagram to a publication-grade vector SVG."""
    scene = _build_scene(diagram)
    w_tot, h_tot = scene["w"], scene["h"]
    eff_layout = scene["layout"]

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_tot} {h_tot}" '
        f'style="width:100%;max-width:{w_tot}px;height:auto;font-family:\'Segoe UI\',Arial,sans-serif;background:#ffffff;">',
        "<defs>",
        '<filter id="cardShadow" x="-6%" y="-6%" width="112%" height="118%">',
        '<feDropShadow dx="0" dy="2" stdDeviation="2.5" flood-color="#0F172A" flood-opacity="0.08" />',
        "</filter>",
        '<marker id="arrow-slate" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">',
        '<polygon points="0 0.5, 9 3.5, 0 6.5" fill="#475569" />',
        "</marker>",
        '<marker id="arrow-dark" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">',
        '<polygon points="0 0.5, 9 3.5, 0 6.5" fill="#334155" />',
        "</marker>",
        '<marker id="arrow-red" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">',
        '<polygon points="0 0.5, 9 3.5, 0 6.5" fill="#B91C1C" />',
        "</marker>",
        "</defs>",
    ]

    # Layer 1: Orthogonal arrows
    for edge in scene["edges"]:
        pts = edge["points"]
        d_path = _rounded_orthogonal_svg_path(pts, radius=8.0)
        dash = ' stroke-dasharray="6 4"' if edge["style"] == "dashed" else ""
        ec = edge["color"]
        marker = "arrow-red" if ec == "#B91C1C" else ("arrow-dark" if ec == "#334155" else "arrow-slate")
        parts.append(
            f'<path d="{d_path}" fill="none" stroke="{ec}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"{dash} marker-end="url(#{marker})" />'
        )

    # Layer 2: Nodes
    use_shadow = eff_layout != "prisma_exclusion"
    shadow_attr = ' filter="url(#cardShadow)"' if use_shadow else ""

    for b in scene["nodes"]:
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        shape = b["shape"]
        pal = b["palette"]
        header_h = b["header_h"]
        t_lines = b["t_lines"]
        b_lines = b["b_lines"]

        if shape == "diamond":
            cx, cy = x + w / 2.0, y + h / 2.0
            pts_str = f"{cx:.1f},{y} {x + w},{cy:.1f} {cx:.1f},{y + h} {x},{cy:.1f}"
            parts.append(
                f'<polygon points="{pts_str}" fill="{pal["fill"]}" stroke="{pal["stroke"]}" '
                f'stroke-width="2" stroke-linejoin="round"{shadow_attr} />'
            )
            ty = cy - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX) / 2.0 + 5.0
            for idx_t, tl in enumerate(t_lines):
                parts.append(
                    f'<text x="{cx:.1f}" y="{ty + idx_t * DIAGRAM_LINE_H_TITLE_PX:.1f}" text-anchor="middle" '
                    f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{pal["title_fg"]}">{escape(tl)}</text>'
                )

        elif shape in ("pill", "oval"):
            rx = h / 2.0 if shape == "pill" else w / 2.0
            ry = h / 2.0
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx:.1f}" ry="{ry:.1f}" '
                f'fill="{pal["fill"]}" stroke="{pal["stroke"]}" stroke-width="2"{shadow_attr} />'
            )
            all_lines = [(tl, True) for tl in t_lines] + [(bl, False) for bl in b_lines]
            total_th = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + len(b_lines) * DIAGRAM_LINE_H_BODY_PX
            cur_ty = y + (h - total_th) / 2.0 + 14.0
            for txt, is_title in all_lines:
                fs = DIAGRAM_TITLE_FONT_PX if is_title else DIAGRAM_MIN_BODY_FONT_PX
                fw = "700" if is_title else "400"
                fg = pal["title_fg"] if is_title else pal["body_fg"]
                parts.append(
                    f'<text x="{x + w / 2.0:.1f}" y="{cur_ty:.1f}" text-anchor="middle" '
                    f'font-size="{fs}" font-weight="{fw}" fill="{fg}">{escape(txt)}</text>'
                )
                cur_ty += DIAGRAM_LINE_H_TITLE_PX if is_title else DIAGRAM_LINE_H_BODY_PX

        elif shape == "circle":
            cx, cy, r = x + w / 2.0, y + h / 2.0, min(w, h) / 2.0
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{pal["fill"]}" '
                f'stroke="{pal["stroke"]}" stroke-width="2"{shadow_attr} />'
            )
            ty = cy - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX) / 2.0 + 5.0
            for idx_t, tl in enumerate(t_lines):
                parts.append(
                    f'<text x="{cx:.1f}" y="{ty + idx_t * DIAGRAM_LINE_H_TITLE_PX:.1f}" text-anchor="middle" '
                    f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{pal["title_fg"]}">{escape(tl)}</text>'
                )

        elif shape == "cylinder":
            ry = 11.0
            cx = x + w / 2.0
            parts.append(
                f'<path d="M {x} {y + ry:.1f} L {x} {y + h - ry:.1f} '
                f'A {w/2:.1f} {ry:.1f} 0 0 0 {x + w} {y + h - ry:.1f} '
                f'L {x + w} {y + ry:.1f} Z" fill="{pal["fill"]}" stroke="{pal["stroke"]}" stroke-width="2"{shadow_attr} />'
            )
            parts.append(
                f'<ellipse cx="{cx:.1f}" cy="{y + ry:.1f}" rx="{w/2:.1f}" ry="{ry:.1f}" '
                f'fill="{pal["header_fill"]}" stroke="{pal["stroke"]}" stroke-width="2" />'
            )
            ty = y + ry * 2 + 16.0
            for idx_t, tl in enumerate(t_lines):
                parts.append(
                    f'<text x="{cx:.1f}" y="{ty + idx_t * DIAGRAM_LINE_H_TITLE_PX:.1f}" text-anchor="middle" '
                    f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{pal["title_fg"]}">{escape(tl)}</text>'
                )

        else:
            rx = "3" if shape == "rectangle" else "10"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
                f'fill="{pal["fill"]}" stroke="{pal["stroke"]}" stroke-width="1.8"{shadow_attr} />'
            )
            if header_h > 0:
                # Header band with rounded top corners only
                r_num = int(rx)
                parts.append(
                    f'<path d="M {x + r_num} {y} H {x + w - r_num} Q {x + w} {y} {x + w} {y + r_num} '
                    f'V {y + header_h} H {x} V {y + r_num} Q {x} {y} {x + r_num} {y} Z" '
                    f'fill="{pal["header_fill"]}" />'
                )
                ty = y + (header_h - len(t_lines) * DIAGRAM_LINE_H_TITLE_PX) / 2.0 + 15.0
                for idx_t, tl in enumerate(t_lines):
                    parts.append(
                        f'<text x="{x + w / 2.0:.1f}" y="{ty + idx_t * DIAGRAM_LINE_H_TITLE_PX:.1f}" text-anchor="middle" '
                        f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{pal["title_fg"]}">{escape(tl)}</text>'
                    )
                by = y + header_h + 20.0
                for idx_b, bl in enumerate(b_lines):
                    parts.append(
                        f'<text x="{x + 13}" y="{by + idx_b * DIAGRAM_LINE_H_BODY_PX:.1f}" text-anchor="start" '
                        f'font-size="{DIAGRAM_MIN_BODY_FONT_PX}" fill="{pal["body_fg"]}">{escape(bl)}</text>'
                    )
            else:
                # PRISMA / flat academic card: centered title + centered or clean body lines
                body_gap = 6 if b_lines else 0
                total_th = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + body_gap + len(b_lines) * DIAGRAM_LINE_H_BODY_PX
                cur_ty = y + (h - total_th) / 2.0 + 15.0
                flat_title_fg = _safe_light_bg_fg(pal)
                for tl in t_lines:
                    parts.append(
                        f'<text x="{x + w / 2.0:.1f}" y="{cur_ty:.1f}" text-anchor="middle" '
                        f'font-size="{DIAGRAM_TITLE_FONT_PX}" font-weight="700" fill="{flat_title_fg}">{escape(tl)}</text>'
                    )
                    cur_ty += DIAGRAM_LINE_H_TITLE_PX
                cur_ty += body_gap
                anchor = "middle" if eff_layout == "prisma_exclusion" else "start"
                bx = x + w / 2.0 if anchor == "middle" else x + 13
                for bl in b_lines:
                    is_bold_count = bool(re.match(r"^[Nn]\s*=", bl.strip()))
                    fw = "700" if is_bold_count else "400"
                    parts.append(
                        f'<text x="{bx:.1f}" y="{cur_ty:.1f}" text-anchor="{anchor}" '
                        f'font-size="{DIAGRAM_MIN_BODY_FONT_PX}" font-weight="{fw}" fill="{pal["body_fg"]}">{escape(bl)}</text>'
                    )
                    cur_ty += DIAGRAM_LINE_H_BODY_PX

    # Layer 3: Inline edge label pills drawn cleanly ON the arrows (no bottom legend!)
    for edge in scene["edges"]:
        lbl = edge["label"]
        if not lbl:
            continue
        pill_lines = _wrap_pill_lines(lbl)
        lx, ly = edge["label_pos"]
        pw, ph = _estimate_pill_size(lbl)
        ec = edge["color"]
        parts.append(
            f'<rect x="{lx - pw / 2.0:.1f}" y="{ly - ph / 2.0:.1f}" width="{pw}" height="{ph}" '
            f'rx="12" ry="12" fill="#FFFFFF" stroke="{ec}" stroke-width="1.5" />'
        )
        line_h = 16.0
        start_y = ly - ((len(pill_lines) - 1) * line_h) / 2.0 + 4.5
        for pl_idx, pl_txt in enumerate(pill_lines):
            parts.append(
                f'<text x="{lx:.1f}" y="{start_y + pl_idx * line_h:.1f}" text-anchor="middle" '
                f'font-size="{DIAGRAM_LABEL_FONT_PX}" font-weight="700" fill="{ec}">{escape(pl_txt)}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "calibrib.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "calibri.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    fill: str,
    width: int,
    dash_len: float = 12.0,
    gap_len: float = 8.0,
) -> None:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return
    ux, uy = dx / dist, dy / dist
    cur = 0.0
    while cur < dist:
        nxt = min(dist, cur + dash_len)
        draw.line(
            [(p1[0] + ux * cur, p1[1] + uy * cur), (p1[0] + ux * nxt, p1[1] + uy * nxt)],
            fill=fill,
            width=width,
        )
        cur += dash_len + gap_len


def render_diagram_png(diagram: SectionDiagram, output_path: Path) -> Path:
    """Render a SectionDiagram to a crisp 2x HiDPI PNG matching the SVG layout."""
    scale = DIAGRAM_RENDER_SCALE
    scene = _build_scene(diagram)
    w_tot, h_tot = scene["w"], scene["h"]
    eff_layout = scene["layout"]

    img = Image.new("RGB", (w_tot * scale, h_tot * scale), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(DIAGRAM_TITLE_FONT_PX * scale, bold=True)
    font_body = _load_font(DIAGRAM_MIN_BODY_FONT_PX * scale, bold=False)
    font_body_bold = _load_font(DIAGRAM_MIN_BODY_FONT_PX * scale, bold=True)
    font_label = _load_font(DIAGRAM_LABEL_FONT_PX * scale, bold=True)

    # Layer 1: Orthogonal edges + arrowheads
    for edge in scene["edges"]:
        pts_s = [(px * scale, py * scale) for px, py in edge["points"]]
        ec = edge["color"]
        for i in range(len(pts_s) - 1):
            if edge["style"] == "dashed":
                _draw_dashed_line(draw, pts_s[i], pts_s[i + 1], fill=ec, width=2 * scale)
            else:
                draw.line([pts_s[i], pts_s[i + 1]], fill=ec, width=2 * scale)
        sx, sy = pts_s[-2]
        ex, ey = pts_s[-1]
        angle = math.atan2(ey - sy, ex - sx)
        arr_len = 8.5 * scale
        p1 = (ex - arr_len * math.cos(angle - math.pi / 6), ey - arr_len * math.sin(angle - math.pi / 6))
        p2 = (ex - arr_len * math.cos(angle + math.pi / 6), ey - arr_len * math.sin(angle + math.pi / 6))
        draw.polygon([(ex, ey), p1, p2], fill=ec)

    # Layer 2: Nodes
    for b in scene["nodes"]:
        xs, ys, ws, hs = b["x"] * scale, b["y"] * scale, b["w"] * scale, b["h"] * scale
        shape = b["shape"]
        pal = b["palette"]
        header_h = b["header_h"] * scale
        t_lines = b["t_lines"]
        b_lines = b["b_lines"]

        if shape == "diamond":
            cx, cy = xs + ws / 2.0, ys + hs / 2.0
            draw.polygon([(cx, ys), (xs + ws, cy), (cx, ys + hs), (xs, cy)], fill=pal["fill"], outline=pal["stroke"], width=2 * scale)
            ty = cy - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
            for idx_t, tl in enumerate(t_lines):
                draw.text((cx, ty + idx_t * DIAGRAM_LINE_H_TITLE_PX * scale), tl, fill=pal["title_fg"], font=font_title, anchor="mm")

        elif shape in ("pill", "oval"):
            rad = int(hs / 2) if shape == "pill" else int(min(ws, hs) / 2)
            draw.rounded_rectangle([xs, ys, xs + ws, ys + hs], radius=rad, fill=pal["fill"], outline=pal["stroke"], width=2 * scale)
            all_lines = [(tl, True) for tl in t_lines] + [(bl, False) for bl in b_lines]
            total_th = (len(t_lines) * DIAGRAM_LINE_H_TITLE_PX + len(b_lines) * DIAGRAM_LINE_H_BODY_PX) * scale
            cur_ty = ys + (hs - total_th) / 2.0 + (DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
            for txt, is_title in all_lines:
                draw.text(
                    (xs + ws / 2.0, cur_ty),
                    txt,
                    fill=pal["title_fg"] if is_title else pal["body_fg"],
                    font=font_title if is_title else font_body,
                    anchor="mm",
                )
                cur_ty += (DIAGRAM_LINE_H_TITLE_PX if is_title else DIAGRAM_LINE_H_BODY_PX) * scale

        elif shape == "circle":
            draw.ellipse([xs, ys, xs + ws, ys + hs], fill=pal["fill"], outline=pal["stroke"], width=2 * scale)
            ty = ys + hs / 2.0 - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
            for idx_t, tl in enumerate(t_lines):
                draw.text((xs + ws / 2.0, ty + idx_t * DIAGRAM_LINE_H_TITLE_PX * scale), tl, fill=pal["title_fg"], font=font_title, anchor="mm")

        else:
            rad = 3 * scale if shape == "rectangle" else 10 * scale
            draw.rounded_rectangle([xs, ys, xs + ws, ys + hs], radius=rad, fill=pal["fill"], outline=pal["stroke"], width=2 * scale)
            if header_h > 0:
                draw.rounded_rectangle([xs, ys, xs + ws, ys + header_h], radius=rad, fill=pal["header_fill"])
                if rad > 4:
                    draw.rectangle([xs, ys + header_h - rad, xs + ws, ys + header_h], fill=pal["header_fill"])
                # Redraw crisp outer border on top
                draw.rounded_rectangle([xs, ys, xs + ws, ys + hs], radius=rad, outline=pal["stroke"], width=2 * scale)
                ty = ys + header_h / 2.0 - ((len(t_lines) - 1) * DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
                for idx_t, tl in enumerate(t_lines):
                    draw.text((xs + ws / 2.0, ty + idx_t * DIAGRAM_LINE_H_TITLE_PX * scale), tl, fill=pal["title_fg"], font=font_title, anchor="mm")
                by = ys + header_h + 8 * scale
                for idx_b, bl in enumerate(b_lines):
                    draw.text((xs + 13 * scale, by + idx_b * DIAGRAM_LINE_H_BODY_PX * scale), bl, fill=pal["body_fg"], font=font_body)
            else:
                body_gap = 6 * scale if b_lines else 0
                total_th = len(t_lines) * DIAGRAM_LINE_H_TITLE_PX * scale + body_gap + len(b_lines) * DIAGRAM_LINE_H_BODY_PX * scale
                cur_ty = ys + (hs - total_th) / 2.0 + (DIAGRAM_LINE_H_TITLE_PX * scale) / 2.0
                flat_title_fg = _safe_light_bg_fg(pal)
                for tl in t_lines:
                    draw.text((xs + ws / 2.0, cur_ty), tl, fill=flat_title_fg, font=font_title, anchor="mm")
                    cur_ty += DIAGRAM_LINE_H_TITLE_PX * scale
                cur_ty += body_gap
                is_centered = eff_layout == "prisma_exclusion"
                for bl in b_lines:
                    is_bold_count = bool(re.match(r"^[Nn]\s*=", bl.strip()))
                    fnt = font_body_bold if is_bold_count else font_body
                    if is_centered:
                        draw.text((xs + ws / 2.0, cur_ty), bl, fill=pal["body_fg"], font=fnt, anchor="mm")
                    else:
                        draw.text((xs + 13 * scale, cur_ty - 7 * scale), bl, fill=pal["body_fg"], font=fnt)
                    cur_ty += DIAGRAM_LINE_H_BODY_PX * scale

    # Layer 3: Inline edge label pills
    for edge in scene["edges"]:
        lbl = edge["label"]
        if not lbl:
            continue
        pill_lines = _wrap_pill_lines(lbl)
        lx, ly = edge["label_pos"][0] * scale, edge["label_pos"][1] * scale
        pw, ph = _estimate_pill_size(lbl)
        pws, phs = pw * scale, ph * scale
        ec = edge["color"]
        draw.rounded_rectangle(
            [lx - pws / 2.0, ly - phs / 2.0, lx + pws / 2.0, ly + phs / 2.0],
            radius=12 * scale,
            fill="#FFFFFF",
            outline=ec,
            width=max(2, int(round(1.5 * scale))),
        )
        line_h = 16.0 * scale
        start_y = ly - ((len(pill_lines) - 1) * line_h) / 2.0
        for pl_idx, pl_txt in enumerate(pill_lines):
            draw.text((lx, start_y + pl_idx * line_h), pl_txt, fill=ec, font=font_label, anchor="mm")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="PNG")
    return output_path


def compute_minimum_legible_figure_size_cm(
    diagram: Optional[SectionDiagram] = None,
    image_path: Optional[Path] = None,
    rules: Optional[FormattingRules] = None,
    min_font_pt: float = 11.0,
    scale: int = DIAGRAM_RENDER_SCALE,
    base_font_px: int = DIAGRAM_MIN_BODY_FONT_PX,
) -> Dict[str, Any]:
    """Calculate minimum physical (width_cm, height_cm) on A4 so characters stay crisp and legible."""
    margin_cm = float(getattr(rules, "margin_cm", 2.5) or 2.5)
    usable_width_cm = max(8.0, 21.0 - 2.0 * margin_cm)
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
        scene = _build_scene(diagram)
        px_w = max(1, scene["w"] * scale)
        px_h = max(1, scene["h"] * scale)

    if px_w <= 0 or px_h <= 0:
        px_w, px_h = 560 * scale, 260 * scale

    aspect = px_h / max(1, px_w)
    if diagram is not None:
        font_px_in_bitmap = float(base_font_px * scale)
    else:
        font_px_in_bitmap = 16.0 * (2.0 if px_w >= 1000 else 1.0)

    min_width_cm = (min_font_pt * PT_TO_CM * px_w) / max(1.0, font_px_in_bitmap)
    min_height_cm = min_width_cm * aspect

    target_w_cm = min(usable_width_cm, max(min_width_cm * 1.01, usable_width_cm * 0.92))
    target_h_cm = target_w_cm * aspect

    if target_h_cm > usable_height_cm:
        target_h_cm = usable_height_cm
        target_w_cm = target_h_cm / max(0.05, aspect)

    effective_font_pt = (font_px_in_bitmap / max(1, px_w)) * (target_w_cm / PT_TO_CM)
    fits_with_caption = (
        target_w_cm <= usable_width_cm + 0.01
        and target_h_cm <= usable_height_cm + 0.01
        and effective_font_pt >= (min_font_pt - 0.5)
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
