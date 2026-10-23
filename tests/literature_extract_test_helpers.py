import os
import subprocess
from pathlib import Path

import pytest


def iter_locators(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "locator" and isinstance(child, dict):
                yield child
            yield from iter_locators(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_locators(child)


def source_page_text(source_id: str, page: int) -> str:
    corpus_root = Path(
        os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
    )
    pdf = corpus_root / "raw" / source_id / f"{source_id}.pdf"
    if not pdf.is_file():
        pytest.skip(f"source PDF is absent: {pdf}")
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
