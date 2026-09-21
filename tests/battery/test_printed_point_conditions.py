"""Printed per-point conditions route into typed ``point_conditions`` (t-945).

Each case migrates one real extract in a minimal tree and asserts the printed
per-observation temperature lands in ``observation.point_conditions["temperature_K"]``
with its locator. The quoted print is the ground truth; the routed value must
equal it. Series-level refusals (kems-095-ueda-1986) must remain refusals: a
printed range, or two printed alternative temperatures, never collapse into a
single point condition.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import REPO_ROOT, migrate
from tests.battery.test_migrate import _write_min_tree

_EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

# (extract stem, observation local id, printed temperature in K, locator key=value)
_TEMPERATURE_ROUTES = (
    # Table 1 column header: "Vapor pressure at 1,873 K (Pa)" (p. 302).
    ("kems-049-kato-1993-ms-review", "kato_1993_table1_fe_psat_1873k", "1873.0", "table", "1"),
    ("kems-049-kato-1993-ms-review", "kato_1993_table1_si_psat_1873k", "1873.0", "table", "1"),
    ("kems-049-kato-1993-ms-review", "kato_1993_table1_w_psat_1873k", "1873.0", "table", "1"),
    # Table 3 "at 1300 C"; the extract documents the authors' T_C + 273 convention.
    ("kems-057-kambayashi-1985", "kambayashi_1985_pbo_table3_ion_current_ratios_1300c", "1573.0", "table", "3"),
    ("kems-057-kambayashi-1985", "kambayashi_1985_fig2_pbo_p2o5_ion_ratios_figure_only", "1573.0", "figure", "2"),
    # Fig. 8 slope: "Clausius-Clapeyron of I_Fe+ T vs 1/T at 1370 C".
    ("kems-057-kambayashi-1985", "kambayashi_1985_fe_sublimation_enthalpy_1370c", "1643.0", "figure", "8"),
    # eq. (5) prints "at 1600 C"; the extract documents T_K = 1600 + 273.15.
    ("kems-048-turkdogan-2001-sio2-gamma", "turkdogan_2001_eq5_log_gamma_sio2_cao_saturated_model_derived", "1873.15", "equation", "5"),
    ("kems-048-turkdogan-2001-sio2-gamma", "turkdogan_2001_eq5_log_gamma_p2o5_model_derived", "1873.15", "equation", "5"),
    # Quoted Table 4: "Izotermicheskaya degazatsiya ... (800 C, posle 15 min)".
    ("murchison-degassing-2023-springer", "voropaev_2023_table4_chelyabinsk_quoted_h2", "1073.15", "table", "4"),
    ("murchison-degassing-2023-springer", "voropaev_2023_table4_chelyabinsk_quoted_h2o", "1073.15", "table", "4"),
    # Figure captions print the single temperature of each plotted dataset.
    ("slag-003-hino-kitagawa-banya-1993", "hino_kitagawa_banya_1993_fig4_figure_only", "1823.0", "figure", "4"),
    ("slag-003-hino-kitagawa-banya-1993", "hino_kitagawa_banya_1993_fig6_figure_only", "1923.0", "figure", "6"),
    ("slag-003-hino-kitagawa-banya-1993", "hino_kitagawa_banya_1993_fig10_figure_only", "1873.0", "figure", "10"),
    # Table 1 FactSage prediction "at 2500 K"; second-law prose "at 1976 K".
    ("kems-139-jacobson-2024", "jacobson_2024_t1_factsage_2500k_predicted_psat", "2500.0", "table", "1"),
    ("kems-139-jacobson-2024", "jacobson_2024_t2_hfo_second_law_narrative", "1976.0", None, None),
    # "each experiment run at a temperature of 2650 K" (Experiments section).
    ("steurer-1985-vapor-phase-pyrolysis", "steurer_1985_sio2_induction_experiment", "2650.0", None, None),
    # Prose prints 1300 C / 1400 C run temperatures.
    ("boulliung-2025-mercury-volatile-metals-magmatic", "boulliung_2025_hg_retention_1300c", "1573.15", "figure", "2A"),
    ("boulliung-2025-mercury-volatile-metals-magmatic", "boulliung_2025_hg_equilibrium_partial_pressure", "1573.15", None, None),
    ("boulliung-2025-mercury-volatile-metals-magmatic", "boulliung_2025_hg_equilibrium_partial_pressure_1400c", "1673.15", None, None),
    # Figure captions print the temperature of the plotted Ti-Co data.
    ("kems-095-ueda-1986", "ueda_1986_fig4_time_dependence_figure_only", "2020.0", "figure", "4"),
    ("kems-095-ueda-1986", "ueda_1986_fig8_binary_integration_1973K_figure_only", "1973.0", "figure", "8"),
    ("kems-095-ueda-1986", "ueda_1986_fig9_ternary_integration_1973K_figure_only", "1973.0", "figure", "9"),
    ("kems-095-ueda-1986", "ueda_1986_fig13_mixing_1873K_figure_only", "1873.0", "figure", "13"),
)


def _migrate_real_extract(tmp_path: Path, stem: str):
    doc = yaml.safe_load((_EXTRACTS / f"{stem}.yaml").read_text(encoding="utf-8"))
    root = _write_min_tree(tmp_path, doc)
    return migrate(root, write=False)


@pytest.mark.parametrize(
    "stem,observation_id,printed_K,locator_key,locator_value",
    _TEMPERATURE_ROUTES,
    ids=[f"{stem}::{obs}" for stem, obs, *_ in _TEMPERATURE_ROUTES],
)
def test_printed_point_temperature_routes(
    tmp_path: Path, stem: str, observation_id: str, printed_K: str,
    locator_key: str | None, locator_value: str | None,
) -> None:
    result = _migrate_real_extract(tmp_path, stem)
    obs = result.observations[f"{stem}::{observation_id}"]
    located = (obs.point_conditions or {}).get("temperature_K")
    assert located is not None, f"{observation_id}: printed temperature not routed"
    assert located.state.is_value
    assert located.state.value == Decimal(printed_K)
    locator = located.locator
    assert locator is not None, "routed point condition must carry its locator"
    if locator_key is not None:
        assert getattr(locator, locator_key) == locator_value


def test_kato_table1_routes_every_printed_1873k_row(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-049-kato-1993-ms-review")
    routed = [
        oid for oid, obs in result.observations.items()
        if "::kato_1993_table1_" in oid
        and (obs.point_conditions or {}).get("temperature_K") is not None
        and obs.point_conditions["temperature_K"].state.is_value
    ]
    assert len(routed) == 20
    for oid in routed:
        assert result.observations[oid].point_conditions["temperature_K"].state.value == Decimal("1873.0")


def test_ueda_series_refusals_are_not_collapsed(tmp_path: Path) -> None:
    """The measurement series keeps its typed absences; only per-point prints route."""
    result = _migrate_real_extract(tmp_path, "kems-095-ueda-1986")
    experiment = result.experiments[
        "10.2320/jinstmet1952.50.12_1081::experiment::ti-co-kems-series"
    ]
    # Printed 1840-2020 K spans the series: no single experiment setpoint.
    assert experiment.conditions["temperature_K"].state.is_unknown
    # The routed figure observations carry only the printed temperature: no
    # pressure was printed for those figures, so none is routed.
    for obs_id in (
        "ueda_1986_fig4_time_dependence_figure_only",
        "ueda_1986_fig8_binary_integration_1973K_figure_only",
        "ueda_1986_fig9_ternary_integration_1973K_figure_only",
        "ueda_1986_fig13_mixing_1873K_figure_only",
    ):
        obs = result.observations[f"kems-095-ueda-1986::{obs_id}"]
        assert set((obs.point_conditions or {}).keys()) == {"temperature_K"}
    # Table 2 prints each gamma at BOTH 1873 K and 1973 K: no single point route.
    table2 = result.observations["kems-095-ueda-1986::ueda_1986_gamma_ti_table2"]
    assert not (table2.point_conditions or {}).get("temperature_K")
