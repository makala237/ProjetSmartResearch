import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from app.schemas.protocol import ProtocolProject
from app.services.docx_template import compile_protocol_docx
from app.services.humanizer import apply_human_author_metadata_pdf


def _run_word_com_update(docx_path: Path, pdf_path: Optional[Path] = None, toc_depth: int = 2) -> None:
    """Update Word dynamic fields/TOC/TOF (forcing left alignment and CAMES/Analytical TOC depth) and optionally export PDF."""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {
    $depth = 2
    if ($env:SMR_TOC_DEPTH -and [int]::TryParse($env:SMR_TOC_DEPTH, [ref]$depth)) {
        if ($depth -lt 1 -or $depth -gt 3) { $depth = 2 }
    }
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($env:SMR_DOCX_PATH, $false, $false)
    foreach ($toc in $doc.TablesOfContents) {
        try {
            $toc.UpperHeadingLevel = 1
            $toc.LowerHeadingLevel = $depth
            $toc.IncludePageNumbers = $true
            $toc.RightAlignPageNumbers = $true
        } catch {}
        $toc.Update()
        foreach ($p in $toc.Range.Paragraphs) {
            $p.Alignment = 0
        }
    }
    $doc.Fields.Update() | Out-Null
    foreach ($toc in $doc.TablesOfContents) {
        try {
            $toc.UpperHeadingLevel = 1
            $toc.LowerHeadingLevel = $depth
            $toc.IncludePageNumbers = $true
            $toc.RightAlignPageNumbers = $true
        } catch {}
        $toc.Update()
        foreach ($p in $toc.Range.Paragraphs) {
            $p.Alignment = 0
        }
    }
    foreach ($tof in $doc.TablesOfFigures) {
        $tof.Update()
        foreach ($p in $tof.Range.Paragraphs) {
            $p.Alignment = 0
        }
    }
    $doc.Repaginate()
    $doc.Save()
    if ($env:SMR_PDF_PATH -and $env:SMR_PDF_PATH.Trim().Length -gt 0) {
        $doc.ExportAsFixedFormat($env:SMR_PDF_PATH, 17)
    }
} finally {
    if ($doc -ne $null) {
        try { $doc.Close($false) } catch {}
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
    }
    if ($word -ne $null) {
        try { $word.Quit() } catch {}
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    env = os.environ.copy()
    env["SMR_DOCX_PATH"] = str(docx_path.resolve())
    env["SMR_PDF_PATH"] = str(pdf_path.resolve()) if pdf_path is not None else ""
    env["SMR_TOC_DEPTH"] = str(max(1, min(3, int(toc_depth or 2))))

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
        err_msg = (proc.stderr or proc.stdout or "Erreur inconnue Word COM").strip()
        raise RuntimeError(f"Échec Word COM : {err_msg}")


def _convert_docx_to_pdf_via_word_com(docx_path: Path, pdf_path: Path, toc_depth: int = 2) -> None:
    """Convert a compiled .docx to PDF via Microsoft Word COM (updates fields & TOC)."""
    _run_word_com_update(docx_path, pdf_path=pdf_path, toc_depth=toc_depth)


def update_docx_fields_via_word_com(docx_path: Path, toc_depth: int = 2) -> bool:
    """Best-effort in-place update of TOC, TOF and SEQ fields inside a saved .docx."""
    try:
        _run_word_com_update(docx_path, pdf_path=None, toc_depth=toc_depth)
        return True
    except Exception:
        return False
        return True
    except Exception:
        return False


def _convert_docx_to_pdf_via_soffice(docx_path: Path, pdf_path: Path) -> bool:
    """Optional LibreOffice headless fallback if Microsoft Word COM is unavailable."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    proc = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_path.parent.resolve()),
            str(docx_path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0


def compile_protocol_pdf(project: ProtocolProject, output_pdf_path: Path) -> Path:
    """Compile the complete FMSB protocol into .docx, update dynamic fields, and export PDF."""
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    docx_path = output_pdf_path.with_suffix(".docx")
    compile_protocol_docx(project, docx_path)

    if output_pdf_path.exists():
        output_pdf_path.unlink()

    toc_depth = int(getattr(project.formatting, "toc_depth", 2) or 2)
    try:
        _convert_docx_to_pdf_via_word_com(docx_path, output_pdf_path, toc_depth=toc_depth)
    except Exception as word_err:
        if not _convert_docx_to_pdf_via_soffice(docx_path, output_pdf_path):
            raise RuntimeError(str(word_err)) from word_err

    if not output_pdf_path.exists() or output_pdf_path.stat().st_size == 0:
        raise RuntimeError("Le fichier PDF généré est vide ou introuvable.")

    try:
        apply_human_author_metadata_pdf(output_pdf_path, project)
    except Exception:
        pass

    return output_pdf_path
