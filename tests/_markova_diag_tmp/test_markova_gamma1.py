from __future__ import annotations

import pytest

import markova_diag_support as diag


@pytest.mark.parametrize("temperature_K", diag.TEMPERATURES_K)
@pytest.mark.parametrize("species", diag.SPECIES)
def test_markova_vi_gamma1(temperature_K, species):
    diag.run_cold_markova_vi_internal_analytical("gamma=1", temperature_K, species)
