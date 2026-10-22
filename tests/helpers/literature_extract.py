from collections.abc import Mapping
from typing import Any


def find_context_row(
    document: Mapping[str, Any], observation_id: str
) -> Mapping[str, Any]:
    return next(
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row.get("observation_id") == observation_id
    )
