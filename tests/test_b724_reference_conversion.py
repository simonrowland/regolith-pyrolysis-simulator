"""Round-2 reference-state regressions for the intrinsic redox publisher."""

import pytest

from simulator.fe_redox import _published_intrinsic_melt_fO2


ACCOUNT = {"FeO": 1.0, "Cr2O3": 1.0, "Al2O3": 1.0, "SiO2": 4.0}


@pytest.mark.parametrize("temperature, expected", [
    (1873.15, {"FeO": -10.12176596396069, "SiO2": -16.399171408348767,
               "Al2O3": -21.515944568874563, "Cr2O3": -10.922031113482255}),
    (2473.15, {"FeO": -7.064756023553955, "SiO2": -10.082176716696138,
               "Al2O3": -14.05165852590972, "Cr2O3": -5.734391936799886}),
])
def test_pre_conversion_candidate_pins(temperature, expected):
    # Captured from production a5ad8a8c5 before moving any JANAF arithmetic.
    publication = _published_intrinsic_melt_fO2(-40.0, ACCOUNT, temperature)
    assert {oxide: row["fO2_log_lower_bound"]
            for oxide, row in publication["candidates"].items()} == expected
