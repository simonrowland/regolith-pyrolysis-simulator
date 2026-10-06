"""t-1123a pins for kems-053 Stolyarova 1991 (regolith-main ruling 2026-10-06).

These pins were committed before the phase_field_class reader change. They
hold on green and must keep holding after it: classifying the x(SiO2) 0.33 and
0.25 rows as outside the single-liquid field must never retype a gas species,
never promote the point composition into identity.composition, never hide an
identity advisory, and never turn the x(SiO2) 0.36-0.40 rows away from numeric.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from simulator.battery.enums import Engine, Phase, Quantity, ResidualStatus
from simulator.battery.identity import quantity_token

SOURCE = "kems-053-stolyarova-1991"
EXTRACT = "kems-053-stolyarova-1991.yaml"
CLASS_S_X = frozenset({Decimal("0.33"), Decimal("0.25")})
CONTESTED_X = frozenset({Decimal("0.36"), Decimal("0.38"), Decimal("0.39"), Decimal("0.40")})


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    from tests.battery.test_migrate import _migrate_real_extract

    return _migrate_real_extract(tmp_path_factory.mktemp("stolyarova1991"), EXTRACT)


@pytest.fixture(scope="module")
def internal_analytical_residuals(migrated):
    # The internal-analytical vapour path needs openimcc (engine_reason
    # openimcc_unavailable otherwise), so these pins run where it is importable.
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.battery.score import ScoreContext, score_store

    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
        extract_review={SOURCE: "draft"},
    )
    residuals, _candidates = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), include_diagnostics=True
    )
    return {residual.reference: residual for residual in residuals}


def _x_sio2(observation) -> Decimal | None:
    located = (observation.point_conditions or {}).get("composition")
    if located is None or not located.state.is_value:
        return None
    return dict(located.state.value.components).get("SiO2")


def _printed_rows(migrated):
    return [
        observation
        for observation in migrated.observations.values()
        if observation.source_id == SOURCE and _x_sio2(observation) is not None
    ]


def test_stolyarova_1991_gas_rows_keep_phase_g(migrated) -> None:
    gas = [
        observation
        for observation in _printed_rows(migrated)
        if quantity_token(observation.identity) is Quantity.P_PARTIAL
    ]
    # Table Ia 4 x 11 + Table Ib SiO 2 x 9, SiO2 5, O 9.
    assert len(gas) == 76
    assert sum(_x_sio2(observation) in CLASS_S_X for observation in gas) == 16
    for observation in gas:
        phase = observation.identity.species.phase
        assert phase.is_value and phase.value is Phase.G, (
            observation.observation_id,
            phase,
        )


def test_stolyarova_1991_point_composition_is_not_promoted(migrated) -> None:
    rows = _printed_rows(migrated)
    assert len(rows) == 130
    assert sum(_x_sio2(observation) in CLASS_S_X for observation in rows) == 28
    for observation in rows:
        composition = observation.identity.composition
        assert composition is None or not composition.is_value, observation.observation_id


def test_stolyarova_1991_identity_advisories_stay_434_138_138_84(migrated) -> None:
    detail = Counter(
        str(issue.detail)
        for issue in migrated.validation.issues
        if str(issue.reason) == "identity_incomplete"
    )
    assert sum(detail.values()) == 434
    assert detail["required axis composition is unknown"] == 138
    assert detail["required axis fO2_Pa is unknown"] == 138
    assert detail["required axis total_pressure_Pa is unknown"] == 84


def test_stolyarova_1991_x036_to_x040_rows_stay_numeric(
    migrated, internal_analytical_residuals
) -> None:
    contested = [
        observation
        for observation in _printed_rows(migrated)
        if _x_sio2(observation) in CONTESTED_X
    ]
    assert len(contested) == 44
    numeric = [
        internal_analytical_residuals[observation.observation_id]
        for observation in contested
        if internal_analytical_residuals[observation.observation_id].numeric is not None
    ]
    # Green: 14 internal-analytical numeric residuals on these 44 rows
    # (t-1123 proposal section 4: 40 single-liquid numeric = 26 openimcc + 14 here).
    assert len(numeric) == 14
    assert all(residual.status is not ResidualStatus.REFUSED for residual in numeric)


# --- t-1123a general two-test rule (simulator/battery/phase_field.py) ----

def _records(liquidus=None, plateau_cells=None, named_failure=None):
    from simulator.battery.phase_field import phase_field_records

    cells = plateau_cells or {"0.33": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.98 ± 0.05")}
    points_a = [{"x": x, "p": a} for x, (a, _b) in cells.items()]
    points_b = [{"x": x, "p": b} for x, (_a, b) in cells.items()]
    doc = {
        "species": {
            "A": {"observations": [{"observation_id": "series_a", "values": {"points": points_a}}]},
            "B": {"observations": [{"observation_id": "series_b", "values": {"points": points_b}}]},
        },
        "phase_field_records": {
            "liquidus": [
                liquidus
                or {
                    "id": "liq",
                    "component": "SiO2",
                    "position": "0.37",
                    "sigma": "0.02",
                    "outside_side": "below",
                    "superseded_positions": ["0.41"],
                    "locator": {"page": 1},
                }
            ],
            "plateaus": [
                {
                    "id": "plat",
                    "composition_field": "x",
                    "compositions": list(cells),
                    "series": [
                        {"observation_id": "series_a", "field": "p", "sigma_basis": "printed"},
                        {"observation_id": "series_b", "field": "p", "sigma_basis": "printed"},
                    ],
                    **({"named_failure": named_failure} if named_failure else {}),
                }
            ],
        },
    }
    return phase_field_records(doc)


def _declared(x, **test_values):
    values = {
        "x": x,
        "liquidus": "0.37",
        "liquidus_sigma": "0.02",
        "plateau_max_z": "0.35",
        "plateau_series_tested": 2,
        "plateau_series_failing": [],
        "plateau_series_untestable": 0,
    }
    values.update(test_values)
    return {
        "class": "outside_single_liquid_field",
        "basis": {
            "criterion": "stated_liquidus_side_and_printed_plateau_within_2_sigma",
            "liquidus": "liq",
            "plateau": "plat",
            "locator": {"page": 1},
            "test_values": values,
        },
    }


def test_two_test_rule_classifies_lime_side_plateau_points() -> None:
    from simulator.battery.phase_field import OUTSIDE_SINGLE_LIQUID_FIELD, classify_point

    records = _records()
    # z(series_a) = 0.5 / sqrt(2) = 0.354 -> 0.35; z(series_b) = 0.02 / sqrt(0.005) = 0.28.
    assert records.plateaus["plat"].z_by_series == {
        "series_a": Decimal("0.35"),
        "series_b": Decimal("0.28"),
    }
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.33"), "CaO": Decimal("0.67")},
        declared=_declared("0.33"),
    )
    assert outcome.kind == OUTSIDE_SINGLE_LIQUID_FIELD and outcome.problem is None


def test_two_test_rule_refuses_points_inside_the_liquidus_sigma_band() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    records = _records(plateau_cells={"0.36": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.98 ± 0.05")})
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.36")},
        declared=_declared("0.36"),
    )
    # 0.36 is not below 0.37 - 0.02: test (i) fails, the row stays liquid and contested.
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "test (i)" in (outcome.problem or "")
    assert outcome.payload["stated_liquidus"] == "0.37"
    assert outcome.payload["stated_liquidus_sigma"] == "0.02"


def test_two_test_rule_refuses_a_plateau_that_is_not_flat_within_2_sigma() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    # z(series_a) = 3.0 / sqrt(2) = 2.12 > 2, z(series_b) = 0.3 / sqrt(0.005) = 4.24 > 2.
    records = _records(plateau_cells={"0.33": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("13.0 ± 1.0", "0.70 ± 0.05")})
    assert records.plateaus["plat"].failing == ("series_a", "series_b")
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.33")},
        declared=_declared("0.33"),
    )
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "at most one named series may fail" in (outcome.problem or "")

    # Only series_b fails: z = 0.15 / sqrt(0.005) = 2.12 > 2; series_a stays 0.35.
    one = _records(
        plateau_cells={"0.33": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.85 ± 0.05")},
        named_failure={"observation_id": "series_b", "reason": "integral-derived"},
    )
    assert one.plateaus["plat"].failing == ("series_b",)
    assert one.plateaus["plat"].problem is None
    unnamed = _records(
        plateau_cells={"0.33": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.85 ± 0.05")},
    )
    assert unnamed.plateaus["plat"].problem is not None


def test_two_test_rule_refuses_carried_test_values_that_do_not_match() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    outcome = classify_point(
        _records(),
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.33")},
        declared=_declared("0.33", plateau_max_z="0.10"),
    )
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "do not match" in (outcome.problem or "")


def test_two_test_rule_leaves_single_liquid_rows_and_mirrors_the_outside_side() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    records = _records()
    for x in ("0.41", "0.50"):
        outcome = classify_point(
            records, observation_id="series_a", x_by_component={"SiO2": Decimal(x)}, declared=None
        )
        assert outcome.kind is None and outcome.problem is None
    outcome = classify_point(
        records, observation_id="series_a", x_by_component={"SiO2": Decimal("0.40")}, declared=None
    )
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    above = _records(
        liquidus={
            "id": "liq",
            "component": "SiO2",
            "position": "0.60",
            "sigma": "0.02",
            "outside_side": "above",
            "superseded_positions": ["0.55"],
            "locator": {"page": 1},
        }
    )
    assert classify_point(
        above, observation_id="series_a", x_by_component={"SiO2": Decimal("0.56")}, declared=None
    ).kind == LIQUIDUS_POSITION_CONTESTED
    assert classify_point(
        above, observation_id="series_a", x_by_component={"SiO2": Decimal("0.50")}, declared=None
    ).kind is None
