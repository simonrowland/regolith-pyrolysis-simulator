"""One owner for the regolith-corpus checkout tests read.

``corpus_root`` is the stable name. Offer 21 switches
``tests/tools/test_mineru_table_emit.py`` onto this function.
"""

from __future__ import annotations

import os
from pathlib import Path


def corpus_root() -> Path | None:
    """Return the corpus checkout, or None when no checkout is available.

    ``REGOLITH_CORPUS_ROOT`` wins when it is set. Otherwise the sibling
    directory ``<repo parent>/regolith-corpus`` is returned when it is a
    directory. Callers skip when this returns None.
    """
    override = os.environ.get("REGOLITH_CORPUS_ROOT")
    if override:
        return Path(override).expanduser()
    sibling = Path(__file__).resolve().parents[2].parent / "regolith-corpus"
    if sibling.is_dir():
        return sibling
    return None
