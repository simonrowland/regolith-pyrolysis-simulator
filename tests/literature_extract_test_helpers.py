import os
from pathlib import Path


def corpus_root() -> Path:
    configured = os.environ.get("REGOLITH_CORPUS_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "Repos" / "regolith-corpus"


def iter_locators(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "locator" and isinstance(child, dict):
                yield child
            yield from iter_locators(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_locators(child)
