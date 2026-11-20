"""Microsoft PowerPoint COM & Headless Fallback Renderer.

Provides faithful PDF export and real PowerPoint slide PNG preview rendering
via PowerPoint COM (matching backend/app/services/pdf_exporter.py for Word COM).
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from app.services.pdf_exporter import (
    convert_office_via_cloud_api,
    convert_office_via_soffice,
    resolve_render_engine,
)

logger = logging.getLogger(__name__)


def _natural_slide_sort_key(path: Path) -> int:
    """Extract numeric slide index from PowerPoint exported names (Diapositive1.PNG / Slide1.PNG)."""
    matches = re.findall(r"(\d+)", path.stem)
    return int(matches[-1]) if matches else 0


def _run_powerpoint_com(
    pptx_path: Path,
    pdf_path: Optional[Path] = None,
    png_dir: Optional[Path] = None,
) -> None:
    """Drive Microsoft PowerPoint COM silently to export PDF and/or 1280x720 PNG slide previews."""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$ppt = $null
$pres = $null
try {
    $ppt = New-Object -ComObject PowerPoint.Application
    $ppt.DisplayAlerts = 1
    # Open(FileName, ReadOnly=msoTrue, Untitled=msoFalse, WithWindow=msoFalse)
    $pres = $ppt.Presentations.Open($env:SMR_PPTX_PATH, -1, 0, 0)
    if ($env:SMR_PDF_PATH -and $env:SMR_PDF_PATH.Trim().Length -gt 0) {
        # ppSaveAsPDF = 32
        $pres.SaveAs($env:SMR_PDF_PATH, 32)
    }
    if ($env:SMR_PNG_DIR -and $env:SMR_PNG_DIR.Trim().Length -gt 0) {
        $pres.Export($env:SMR_PNG_DIR, "PNG", 1280, 720)
    }
} finally {
    if ($pres -ne $null) {
        try { $pres.Close() } catch {}
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pres) | Out-Null
    }
    if ($ppt -ne $null) {
        try { $ppt.Quit() } catch {}
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    env = os.environ.copy()
    env["SMR_PPTX_PATH"] = str(pptx_path.resolve())
    env["SMR_PDF_PATH"] = str(pdf_path.resolve()) if pdf_path is not None else ""
    env["SMR_PNG_DIR"] = str(png_dir.resolve()) if png_dir is not None else ""

    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "Erreur inconnue PowerPoint COM").strip()
        raise RuntimeError(f"Échec PowerPoint COM : {err_msg}")


def _convert_pptx_to_pdf_via_soffice(pptx_path: Path, pdf_path: Path) -> bool:
    """Headless LibreOffice Impress conversion with isolated profile."""
    return convert_office_via_soffice(pptx_path, pdf_path)


def render_pdf_pages_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    width: int = 1280,
    height: Optional[int] = 720,
    dpi: int = 150,
    prefix: str = "slide",
) -> List[Path]:
    """Rasterize every page of a PDF into PNG files using poppler pdftoppm."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("Binaire pdftoppm (poppler-utils) introuvable sur le système.")

    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".png":
            f.unlink()

    raw_prefix = out_dir / "_raw_page"
    cmd = [pdftoppm, "-png", "-r", str(dpi)]
    if width and height:
        cmd.extend(["-scale-to-x", str(width), "-scale-to-y", str(height)])
    elif width:
        cmd.extend(["-scale-to", str(width)])
    cmd.extend([str(pdf_path.resolve()), str(raw_prefix.resolve())])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "Erreur pdftoppm").strip()
        raise RuntimeError(f"Échec pdftoppm : {err}")

    raw_pngs = sorted(
        (p for p in out_dir.iterdir() if p.is_file() and p.name.startswith("_raw_page") and p.suffix.lower() == ".png"),
        key=_natural_slide_sort_key,
    )
    normalized: List[Path] = []
    for idx, src in enumerate(raw_pngs, start=1):
        target = out_dir / f"{prefix}_{idx:02d}.png"
        if target.exists():
            target.unlink()
        src.rename(target)
        normalized.append(target)
    return normalized


def export_pptx_to_pdf(pptx_path: Path, pdf_path: Path) -> Path:
    """Convert a compiled .pptx presentation into PDF via selected engine (COM, LibreOffice, or cloud_api)."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    engine = resolve_render_engine()
    if engine == "cloud_api":
        if convert_office_via_cloud_api(pptx_path, pdf_path) and pdf_path.exists():
            return pdf_path
        if _convert_pptx_to_pdf_via_soffice(pptx_path, pdf_path) and pdf_path.exists():
            return pdf_path
        raise RuntimeError("Impossible d'exporter le diaporama en PDF via cloud_api/LibreOffice.")

    if engine == "libreoffice":
        if _convert_pptx_to_pdf_via_soffice(pptx_path, pdf_path) and pdf_path.exists():
            return pdf_path
        if sys.platform == "win32":
            _run_powerpoint_com(pptx_path=pptx_path, pdf_path=pdf_path, png_dir=None)
            if pdf_path.exists() and pdf_path.stat().st_size > 1000:
                return pdf_path
        raise RuntimeError("LibreOffice (soffice) introuvable pour exporter le diaporama en PDF.")

    try:
        _run_powerpoint_com(pptx_path=pptx_path, pdf_path=pdf_path, png_dir=None)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return pdf_path
    except Exception as com_exc:
        logger.warning("PowerPoint COM PDF export failed (%s); checking LibreOffice/Cloud fallback...", com_exc)
        if _convert_pptx_to_pdf_via_soffice(pptx_path, pdf_path) and pdf_path.exists():
            return pdf_path
        if convert_office_via_cloud_api(pptx_path, pdf_path) and pdf_path.exists():
            return pdf_path
        raise RuntimeError(
            f"Impossible d'exporter le diaporama en PDF (PowerPoint COM : {com_exc})."
        ) from com_exc

    raise RuntimeError("La génération PDF du diaporama n'a produit aucun fichier valide.")


def render_pptx_previews(pptx_path: Path, previews_dir: Path) -> List[Path]:
    """Render every slide of a compiled .pptx as 1280x720 PNG files (PowerPoint COM or LibreOffice+pdftoppm)."""
    if previews_dir.exists():
        for f in previews_dir.iterdir():
            if f.is_file():
                f.unlink()
    previews_dir.mkdir(parents=True, exist_ok=True)

    engine = resolve_render_engine()
    if engine in ("libreoffice", "cloud_api"):
        with tempfile.TemporaryDirectory(prefix="sr_pptx_pdf_") as tmp_dir:
            tmp_pdf = Path(tmp_dir) / "slides.pdf"
            export_pptx_to_pdf(pptx_path, tmp_pdf)
            return render_pdf_pages_to_pngs(tmp_pdf, previews_dir, width=1280, height=720, prefix="slide")

    try:
        _run_powerpoint_com(pptx_path=pptx_path, pdf_path=None, png_dir=previews_dir)
    except Exception as com_exc:
        logger.warning("PowerPoint COM PNG render failed (%s); falling back to PDF + pdftoppm...", com_exc)
        with tempfile.TemporaryDirectory(prefix="sr_pptx_pdf_") as tmp_dir:
            tmp_pdf = Path(tmp_dir) / "slides.pdf"
            if _convert_pptx_to_pdf_via_soffice(pptx_path, tmp_pdf) or convert_office_via_cloud_api(pptx_path, tmp_pdf):
                return render_pdf_pages_to_pngs(tmp_pdf, previews_dir, width=1280, height=720, prefix="slide")
        raise RuntimeError(f"Échec du rendu PNG des diapositives : {com_exc}") from com_exc

    raw_pngs = [
        p
        for p in previews_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".png"
    ]
    raw_pngs.sort(key=_natural_slide_sort_key)

    # Normalize filenames cleanly to slide_01.png, slide_02.png, ...
    normalized: List[Path] = []
    for idx, src in enumerate(raw_pngs, start=1):
        target = previews_dir / f"slide_{idx:02d}.png"
        if src.resolve() != target.resolve():
            if target.exists():
                target.unlink()
            src.rename(target)
        normalized.append(target)
    return normalized
