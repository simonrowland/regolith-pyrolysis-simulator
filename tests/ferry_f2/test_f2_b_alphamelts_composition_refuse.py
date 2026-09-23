"""F2 root B: AlphaMELTS composition refuses unresolvable positive-mol species."""

from __future__ import annotations

import pytest

from engines.alphamelts.provider import AlphaMELTSProvider


def test_alphamelts_composition_refuses_hole() -> None:
    provider = AlphaMELTSProvider.__new__(AlphaMELTSProvider)
    with pytest.raises(ValueError, match="refuses unresolvable species"):
        provider._composition_wt_pct(
            {"SiO2": 1.0, "BogusXYZ": 0.5},
            {},
        )


def test_alphamelts_composition_healthy_sio2() -> None:
    provider = AlphaMELTSProvider.__new__(AlphaMELTSProvider)
    out = provider._composition_wt_pct({"SiO2": 1.0}, {})
    assert out == {"SiO2": pytest.approx(100.0)}
