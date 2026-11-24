"""Native read-only SQLite connection shared by optimizer and web readers."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from urllib.parse import quote


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn
