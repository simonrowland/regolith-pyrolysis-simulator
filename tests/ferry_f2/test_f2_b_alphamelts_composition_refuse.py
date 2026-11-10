"""F2 root B: AlphaMELTS composition refuses unresolvable positive-mol species."""

from __future__ import annotations

import pytest

from engines.alphamelts.provider import AlphaMELTSProvider
from simulator.accounting.exceptions import UnknownSpeciesError


@pytest.mark.parametrize('unknown_mol', [1e-12, 1e6])
def test_alphamelts_composition_refuses_unresolvable_species(unknown_mol) -> None:
    provider = AlphaMELTSProvider.__new__(AlphaMELTSProvider)
    with pytest.raises(UnknownSpeciesError, match='BogusXYZ'):
        provider._composition_wt_pct(
            {"SiO2": 1.0, "BogusXYZ": unknown_mol},
            {},
        )


def test_alphamelts_composition_healthy_sio2() -> None:
    provider = AlphaMELTSProvider.__new__(AlphaMELTSProvider)
    out = provider._composition_wt_pct({"SiO2": 1.0}, {})
    assert out == {"SiO2": pytest.approx(100.0)}
