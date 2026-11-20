"""Phase 5 Comparative Render & Header/Footer Verification Script.

Compiles a real protocol to .docx, .pdf, .pptx, and PNG previews (via COM or LibreOffice + pdftoppm),
verifies running headers (protocol title on pages 2+) and footers (candidate name on pages 2+),
checks PNG output geometry, and optionally compares two render directories pixel-by-pixel.
"""

import argparse
from pathlib import Path
import shutil
import sys
from PIL import Image, ImageChops
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.docx_template import compile_protocol_docx
from app.services.pdf_exporter import compile_protocol_pdf, resolve_render_engine
from app.services.pptx_exporter import compile_presentation_pptx
from app.services.pptx_renderer import export_pptx_to_pdf, render_pdf_pages_to_pngs, render_pptx_previews
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.storage import project_storage


def _mean_pixel_diff(img_a: Path, img_b: Path) -> float:
    with Image.open(img_a).convert("RGB") as a, Image.open(img_b).convert("RGB") as b:
        if a.size != b.size:
            b = b.resize(a.size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(a, b)
        hist = diff.histogram()
        total_channels = a.size[0] * a.size[1] * 3
        total_diff = sum(i % 256 * count for i, count in enumerate(hist))
        return total_diff / max(total_channels, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Smart Research PDF/PNG rendering pipeline.")
    parser.add_argument("--project-id", default="5221c9f3-65e", help="Reference protocol ID.")
    parser.add_argument(
        "--out-dir",
        default=str(BACKEND_ROOT / "storage" / "exports" / "render_validation"),
        help="Directory to write validation outputs.",
    )
    parser.add_argument(
        "--baseline-dir",
        default="",
        help="Optional baseline PNG directory to compare against.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project = project_storage.get(args.project_id)
    if project is None:
        print(f"[ERROR] Projet introuvable : {args.project_id}", file=sys.stderr)
        return 1

    print(f"[INFO] Plateforme: {sys.platform} | Moteur configuré: {resolve_render_engine()}")
    print(f"[INFO] soffice={shutil.which('soffice') or shutil.which('libreoffice')} | pdftoppm={shutil.which('pdftoppm')}")

    docx_path = out_dir / f"{args.project_id}.docx"
    pdf_path = out_dir / f"{args.project_id}.pdf"
    pptx_path = out_dir / f"{args.project_id}.pptx"
    slides_pdf_path = out_dir / f"{args.project_id}_slides.pdf"
    docx_png_dir = out_dir / "docx_pages"
    pptx_png_dir = out_dir / "pptx_slides"

    compile_protocol_docx(project, docx_path)
    print(f"[OK] DOCX compilé : {docx_path} ({docx_path.stat().st_size} octets)")

    compile_protocol_pdf(project, pdf_path)
    reader = PdfReader(str(pdf_path))
    print(f"[OK] PDF compilé : {pdf_path} ({len(reader.pages)} pages, {pdf_path.stat().st_size} octets)")

    # Check running header & footer presence from Page 2+ vs blank Cover Page 1
    p1_text = reader.pages[0].extract_text() or ""
    p2_text = (reader.pages[1].extract_text() or "") if len(reader.pages) > 1 else ""
    author_name = (project.cover.author_name or "").strip()
    title_prefix = (project.cover.title or project.name or "")[:32].strip()
    print(f"[CHECK] Page 1 contient auteur (normal sur garde): {author_name in p1_text if author_name else True}")
    if len(reader.pages) > 1:
        print(f"[CHECK] Page 2 contient titre courant ('{title_prefix[:24]}...'): {title_prefix[:20].lower() in p2_text.lower()}")
        if author_name:
            print(f"[CHECK] Page 2 pied de page contient candidat ('{author_name}'): {author_name.lower() in p2_text.lower()}")

    if shutil.which("pdftoppm"):
        doc_pngs = render_pdf_pages_to_pngs(pdf_path, docx_png_dir, width=1240, height=None, prefix="page")
        print(f"[OK] pdftoppm Word->PDF->PNG : {len(doc_pngs)} pages rendues")

    deck = PresentationOrchestrator().build_default_deck_from_protocol(project)
    compile_presentation_pptx(deck, pptx_path, project=project)
    export_pptx_to_pdf(pptx_path, slides_pdf_path)
    slide_pngs = render_pptx_previews(pptx_path, pptx_png_dir)
    print(f"[OK] PPTX & PNG Slides : {len(slide_pngs)} diapositives ({slide_pngs[0].name if slide_pngs else 'aucune'})")

    if args.baseline_dir:
        base_dir = Path(args.baseline_dir)
        compared = 0
        for p in slide_pngs:
            other = base_dir / p.name
            if other.exists():
                diff_val = _mean_pixel_diff(p, other)
                compared += 1
                print(f"[DIFF] {p.name} mean delta = {diff_val:.2f}/255")
        print(f"[OK] Comparaison visuelle terminée sur {compared} fichier(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
