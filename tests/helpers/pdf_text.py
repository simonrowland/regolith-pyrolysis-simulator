"""Small helpers for source PDF text used by literature extract pins."""

from __future__ import annotations

from pathlib import Path
import subprocess


def pdftotext_page_text(pdf: Path, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
