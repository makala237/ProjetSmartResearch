"""Generate clean Smart Research web, navbar, and PWA icon assets from the master logo PNG."""

from collections import deque
from pathlib import Path
from PIL import Image


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOGO = WORKSPACE_ROOT / "Logo Smart Research – Innovation et savoir-2.png"
PUBLIC_DIR = WORKSPACE_ROOT / "frontend" / "public"


def _clean_white_background(img: Image.Image, white_thresh: int = 244, feather: int = 22) -> Image.Image:
    """Convert near-white studio background pixels connected to edges into clean alpha transparency."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def is_bg(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        return r >= white_thresh and g >= white_thresh and b >= white_thresh and max(r, g, b) - min(r, g, b) <= 14

    for x in range(w):
        for y in (0, h - 1):
            if is_bg(x, y):
                visited[y * w + x] = 1
                q.append((x, y))
    for y in range(1, h - 1):
        for x in (0, w - 1):
            if is_bg(x, y):
                visited[y * w + x] = 1
                q.append((x, y))

    while q:
        cx, cy = q.popleft()
        px[cx, cy] = (255, 255, 255, 0)
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if not visited[idx] and is_bg(nx, ny):
                    visited[idx] = 1
                    q.append((nx, ny))

    # Soft anti-alias border adjacent to transparent background
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = (r + g + b) / 3.0
            if lum > 255 - feather and max(r, g, b) - min(r, g, b) <= 16:
                if (
                    px[x - 1, y][3] == 0
                    or px[x + 1, y][3] == 0
                    or px[x, y - 1][3] == 0
                    or px[x, y + 1][3] == 0
                ):
                    alpha = max(0, min(255, int(255 * (255 - lum) / feather)))
                    px[x, y] = (r, g, b, alpha)
    return rgba


def _extract_emblem_only(full_rgba: Image.Image) -> Image.Image:
    """Keep only connected components belonging to the upper S-ribbon/book emblem (exclude text)."""
    w, h = full_rgba.size
    px = full_rgba.load()
    visited = bytearray(w * h)
    kept_mask = bytearray(w * h)

    for sy in range(h):
        for sx in range(w):
            s_idx = sy * w + sx
            if visited[s_idx] or px[sx, sy][3] <= 18:
                continue
            visited[s_idx] = 1
            comp: list[tuple[int, int]] = []
            q: deque[tuple[int, int]] = deque([(sx, sy)])
            min_y, max_y = sy, sy
            while q:
                cx, cy = q.popleft()
                comp.append((cx, cy))
                if cy < min_y:
                    min_y = cy
                if cy > max_y:
                    max_y = cy
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        n_idx = ny * w + nx
                        if not visited[n_idx] and px[nx, ny][3] > 18:
                            visited[n_idx] = 1
                            q.append((nx, ny))

            # Emblem pieces all start above y = 0.50*h; wordmark ("Smart Research") & tagline start at y >= 0.525*h
            if min_y < int(0.515 * h) and max_y < int(0.60 * h):
                for cx, cy in comp:
                    kept_mask[cy * w + cx] = 1

    emblem = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    e_px = emblem.load()
    for y in range(h):
        for x in range(w):
            if kept_mask[y * w + x]:
                e_px[x, y] = px[x, y]

    bbox = emblem.getbbox()
    if bbox:
        emblem = emblem.crop(bbox)
    return _pad_square_rgba(emblem, size=256, pad_ratio=0.08)


def _pad_square_rgba(img: Image.Image, size: int, pad_ratio: float = 0.06, bg_rgba=None) -> Image.Image:
    inner = int(round(size * (1.0 - 2 * pad_ratio)))
    iw, ih = img.size
    scale = min(inner / max(iw, 1), inner / max(ih, 1))
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), bg_rgba or (0, 0, 0, 0))
    ox = (size - nw) // 2
    oy = (size - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def main() -> None:
    if not SOURCE_LOGO.exists():
        raise FileNotFoundError(f"Logo source introuvable : {SOURCE_LOGO}")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    with Image.open(SOURCE_LOGO) as raw:
        full_rgba = _clean_white_background(raw)

    full_bbox = full_rgba.getbbox()
    cropped_full = full_rgba.crop(full_bbox) if full_bbox else full_rgba
    logo_full_out = _pad_square_rgba(cropped_full, size=600, pad_ratio=0.04)
    logo_full_out.save(PUBLIC_DIR / "logo-smart-research.png", format="PNG")

    mark_out = _extract_emblem_only(full_rgba)
    mark_out.save(PUBLIC_DIR / "logo-mark.png", format="PNG")

    # PWA icons on solid white card background for crisp maskable launchers
    for icon_size in (192, 512):
        icon_img = _pad_square_rgba(
            mark_out,
            size=icon_size,
            pad_ratio=0.14,
            bg_rgba=(255, 255, 255, 255),
        )
        icon_img.save(PUBLIC_DIR / f"icon-{icon_size}.png", format="PNG")

    print("Brand assets generated cleanly in:", PUBLIC_DIR)


if __name__ == "__main__":
    main()
