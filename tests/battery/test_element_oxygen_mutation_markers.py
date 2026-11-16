from __future__ import annotations

import runpy
from pathlib import Path


def test_element_oxygen_mutation_markers_and_checks_are_live() -> None:
    script_path = Path(__file__).with_name("mutate_element_oxygen_excess.py")
    script = runpy.run_path(str(script_path))
    production_source = script["SOURCE"]
    original_bytes = production_source.read_bytes()

    try:
        markers = {
            name: value
            for name, value in script.items()
            if name.endswith("_MARKER") and isinstance(value, str)
        }
        source = original_bytes.decode("utf-8")

        assert markers, "mutation script defines no production markers"
        for name, marker in markers.items():
            assert source.count(marker) == 1, (
                f"{name} must occur exactly once in {production_source}"
            )
    finally:
        assert production_source.read_bytes() == original_bytes, (
            f"collected marker check changed {production_source}"
        )
