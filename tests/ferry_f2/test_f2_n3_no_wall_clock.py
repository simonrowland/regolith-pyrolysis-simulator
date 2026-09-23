"""F2 root N3: harvest/manifest writers omit wall-clock stamps."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "rel",
    [
        "tools/harvest_janaf_compilation.py",
        "tools/build_janaf_compilation_manifest.py",
        "tools/harvest_burcat_compilation.py",
        "tools/harvest_nasa_glenn_compilation.py",
        "tools/harvest_sgte_unary_compilation.py",
    ],
)
def test_harvest_tools_do_not_embed_datetime_now(rel: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "datetime.now(" not in text
    assert "date.today(" not in text


def test_sgte_build_manifest_omits_generated_at_when_none() -> None:
    from simulator.chemistry.sgte_unary import build_manifest

    sig = inspect.signature(build_manifest)
    assert sig.parameters["generated_at"].default is None
