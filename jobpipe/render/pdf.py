"""DOCX to PDF conversion and page counting."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from pypdf import PdfReader
from jobpipe.config import settings

log = logging.getLogger(__name__)


class ConversionError(Exception):
    pass


def to_pdf(docx_path: Path, pdf_path: Path) -> str:
    """Convert with Word (docx2pdf); LibreOffice headless only if Word fails.
    Returns the converter used."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    if settings()["render"].get("converter", "word") == "word":
        try:
            from docx2pdf import convert

            if sys.platform == "win32":
                import pythoncom

                pythoncom.CoInitialize()  # Word COM from a worker thread (`run` per-job timeout)

            convert(str(docx_path), str(pdf_path))
            if pdf_path.exists():
                return "word"
            errors.append("word: no output")
        except Exception as e:  # noqa: BLE001  (COM raises assorted types)
            errors.append(f"word: {e}")
            log.warning("Word conversion failed, trying LibreOffice: %s", e)
    soffice = _soffice()
    if soffice:
        with tempfile.TemporaryDirectory(prefix="jobpipe-lo-") as tmp:
            r = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(docx_path)],
                capture_output=True, text=True, timeout=180,
            )
            produced = Path(tmp) / (docx_path.stem + ".pdf")
            if r.returncode == 0 and produced.exists():
                shutil.move(str(produced), pdf_path)
                return "libreoffice"
            errors.append(f"libreoffice: {r.stderr.strip()[-300:]}")
    raise ConversionError("; ".join(errors) or "no converter available")


def _soffice() -> str | None:
    for c in (shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if c and Path(c).exists():
            return c
    return None


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)
