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


@pytest.fixture(scope="module")
def openimcc_1993k_residuals(migrated):
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.battery.score import ScoreContext, score_store

    observations = {
        observation.observation_id: observation
        for observation in migrated.observations.values()
        if observation.source_id == SOURCE
        and _x_sio2(observation) in CLASS_S_X
        and _point_temperature(observation) == Decimal("1993.0")
    }
    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=observations,
        benches=migrated.benches,
        extract_review={SOURCE: "draft"},
    )
    residuals, _candidates = score_store(
        context, engines=(Engine.OPENIMCC,), include_diagnostics=True
    )
    return {residual.reference: residual for residual in residuals}


def _x_sio2(observation) -> Decimal | None:
    located = (observation.point_conditions or {}).get("composition")
    if located is None or not located.state.is_value:
        return None
    return dict(located.state.value.components).get("SiO2")


def _point_temperature(observation) -> Decimal | None:
    located = (observation.point_conditions or {}).get("temperature_K")
    if located is None or not located.state.is_value:
        return None
    return located.state.value


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


def _locators(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("locator") and isinstance(value, dict):
                yield key, value
            yield from _locators(value)
    elif isinstance(node, list):
        for item in node:
            yield from _locators(item)


def test_stolyarova_1991_phase_field_record_locators_have_no_null_keys() -> None:
    # A YAML flow mapping such as {table: Ib, IIa, IIb} silently parses as
    # table='Ib' plus null-valued keys 'IIa' and 'IIb'; every locator under
    # phase_field_records must carry only non-null values.
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "data" / "literature" / "extracts" / EXTRACT
    records = yaml.safe_load(path.read_text(encoding="utf-8"))["phase_field_records"]
    locators = list(_locators(records))
    # liquidus 1, plateaus 2, the four 15% sigma locators, caption note 1.
    assert len(locators) == 8
    for key, locator in locators:
        assert all(value is not None for value in locator.values()), (key, locator)
    tables = {plateau["id"]: plateau["locator"]["table"] for plateau in records["plateaus"]}
    assert tables == {
        "stolyarova-1991-table-ia-1993k-lime-plateau": "Ia",
        "stolyarova-1991-tables-ib-ii-1933k-lime-plateau": "Ib, IIa, IIb",
    }


# --- t-1123a reader rule (simulator/battery/phase_field.py) -----------------


def _notices(observation, kind):
    return [notice for notice in observation.notices if notice.kind is kind]


def _payload(notice, prefix):
    import json

    assert notice.reason.startswith(prefix + ":")
    return json.loads(notice.reason[len(prefix) + 1 :])


def test_stolyarova_1991_class_s_is_exactly_the_20_points_at_1933k(migrated) -> None:
    from simulator.battery.enums import NoticeKind
    from simulator.battery.phase_field import OUTSIDE_SINGLE_LIQUID_FIELD

    band = "two_phase_bulk_composition_not_liquid_composition"
    classified = {}
    for observation in _printed_rows(migrated):
        hits = [
            notice
            for notice in _notices(observation, NoticeKind.OUT_OF_CERTIFIED_BAND)
            if notice.band == band
        ]
        if hits:
            assert len(hits) == 1
            classified[observation.observation_id] = (_x_sio2(observation), hits[0])
    assert len(classified) == 20
    assert {x for x, _notice in classified.values()} == set(CLASS_S_X)
    assert Counter(
        _point_temperature(observation)
        for observation in _printed_rows(migrated)
        if observation.observation_id in classified
    ) == {Decimal("1933.0"): 20}
    for x, notice in classified.values():
        payload = _payload(notice, OUTSIDE_SINGLE_LIQUID_FIELD)
        assert payload["criterion"] == "stated_liquidus_side_and_printed_plateau_within_2_sigma"
        assert payload["locator"]["page"] == 3711
        values = payload["test_values"]
        assert Decimal(values["x"]) == x
        assert (values["liquidus"], values["liquidus_sigma"]) == ("0.37", "0.02")
        assert Decimal(values["plateau_max_z"]) <= 2
    assert not migrated.registry_issues


def test_stolyarova_1991_contested_rows_carry_stated_liquidus_and_sigma(migrated) -> None:
    from simulator.battery.enums import NoticeKind
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED

    rows = _printed_rows(migrated)
    contested = {
        observation.observation_id: observation
        for observation in rows
        if _notices(observation, NoticeKind.LIQUIDUS_POSITION_CONTESTED)
    }
    assert len(contested) == 52
    for observation in rows:
        flagged = observation.observation_id in contested
        at_unqualified_class_s_temperature = (
            _point_temperature(observation) == Decimal("1993.0")
            and _x_sio2(observation) in CLASS_S_X
        )
        assert flagged == (
            _x_sio2(observation) in CONTESTED_X or at_unqualified_class_s_temperature
        ), observation.observation_id
    for observation in contested.values():
        (notice,) = _notices(observation, NoticeKind.LIQUIDUS_POSITION_CONTESTED)
        payload = _payload(notice, LIQUIDUS_POSITION_CONTESTED)
        assert Decimal(payload["x"]) == _x_sio2(observation)
        assert payload["stated_liquidus"] == "0.37"
        assert payload["stated_liquidus_sigma"] == "0.02"
        assert payload["superseded_liquidus"] == ["0.41"]
        assert payload["locator"]["page"] == 3711
        assert not any(notice.band for notice in observation.notices)


def test_stolyarova_1991_1933k_class_s_refuses_single_liquid_at_bulk_composition(
    migrated, internal_analytical_residuals
) -> None:
    from simulator.battery.enums import RefusalReason

    class_s = [
        observation
        for observation in _printed_rows(migrated)
        if _x_sio2(observation) in CLASS_S_X
        and _point_temperature(observation) == Decimal("1933.0")
    ]
    assert len(class_s) == 20
    for observation in class_s:
        residual = internal_analytical_residuals[observation.observation_id]
        assert residual.numeric is None and not residual.score_eligible
        assert residual.refusal.reason is RefusalReason.BULK_NOT_LIQUID_COMPOSITION

    gas_rows = [
        observation
        for observation in class_s
        if quantity_token(observation.identity) is Quantity.P_PARTIAL
    ]
    assert Counter(observation.identity.species.formula for observation in gas_rows) == {
        "SiO": 4,
        "SiO2": 2,
        "O": 2,
    }


def test_stolyarova_1991_1993k_class_s_positions_are_predicted_and_flagged(
    migrated, openimcc_1993k_residuals
) -> None:
    from simulator.battery.enums import NoticeKind
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED
    from simulator.battery.score import (
        FLAGGED_STRATUM_LIQUIDUS_POSITION_CONTESTED,
        flagged_strata,
    )

    rows = [
        observation
        for observation in _printed_rows(migrated)
        if _point_temperature(observation) == Decimal("1993.0")
        and _x_sio2(observation) in CLASS_S_X
    ]
    assert len(rows) == 8
    for observation in rows:
        assert not _notices(observation, NoticeKind.OUT_OF_CERTIFIED_BAND)
        (notice,) = _notices(observation, NoticeKind.LIQUIDUS_POSITION_CONTESTED)
        assert notice.reason.startswith(LIQUIDUS_POSITION_CONTESTED + ":")
        residual = openimcc_1993k_residuals[observation.observation_id]
        assert residual.numeric is not None and residual.refusal is None
        assert FLAGGED_STRATUM_LIQUIDUS_POSITION_CONTESTED in flagged_strata(residual.notices)


def test_stolyarova_1991_contested_numeric_rows_are_flagged_out_of_certified_line(
    migrated, internal_analytical_residuals
) -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_LIQUIDUS_POSITION_CONTESTED,
        flagged_strata,
    )

    contested = [
        internal_analytical_residuals[observation.observation_id]
        for observation in _printed_rows(migrated)
        if _x_sio2(observation) in CONTESTED_X
    ]
    numeric = [residual for residual in contested if residual.numeric is not None]
    assert len(numeric) == 14
    assert all(
        FLAGGED_STRATUM_LIQUIDUS_POSITION_CONTESTED in flagged_strata(residual.notices)
        for residual in contested
    )


def _records(liquidus=None, plateau_cells=None, named_failure=None):
    from simulator.battery.phase_field import phase_field_records

    cells = plateau_cells or {"0.33": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.98 ± 0.05")}
    liquidus_record = dict(
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
    )
    liquidus_record.setdefault("temperature_K_as_captioned", "1933")
    points_a = [{"x": x, "p": a} for x, (a, _b) in cells.items()]
    points_b = [{"x": x, "p": b} for x, (_a, b) in cells.items()]
    doc = {
        "species": {
            "A": {"observations": [{"observation_id": "series_a", "values": {"points": points_a}}]},
            "B": {"observations": [{"observation_id": "series_b", "values": {"points": points_b}}]},
        },
        "phase_field_records": {
            "liquidus": [liquidus_record],
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
    # z(series_a) = 0.5 / sqrt(2) = 0.354; z(series_b) = 0.02 / sqrt(0.005) = 0.28.
    assert records.plateaus["plat"].z_by_series == {
        "series_a": Decimal("0.5") / Decimal(2).sqrt(),
        "series_b": Decimal("0.02") / Decimal("0.005").sqrt(),
    }
    assert records.plateaus["plat"].max_passing_z == Decimal("0.35")
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.33"), "CaO": Decimal("0.67")},
        temperature_K=Decimal("1933"),
        declared=_declared("0.33"),
    )
    assert outcome.kind == OUTSIDE_SINGLE_LIQUID_FIELD and outcome.problem is None


def test_liquidus_classification_requires_the_captioned_temperature() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    outcome = classify_point(
        _records(),
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.33")},
        temperature_K=Decimal("1993"),
        declared=_declared("0.33"),
    )
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert outcome.problem is None


def test_plateau_z_just_above_two_sigma_is_a_failing_series() -> None:
    from simulator.battery.phase_field import phase_field_records

    records = phase_field_records(
        {
            "species": {
                "A": {
                    "observations": [
                        {
                            "observation_id": "series_a",
                            "values": {
                                "points": [
                                    {"x": "0.1", "p": "1.000 ± 0.5"},
                                    {"x": "0.2", "p": "2.417 ± 0.5"},
                                ]
                            },
                        }
                    ]
                }
            },
            "phase_field_records": {
                "liquidus": [
                    {
                        "id": "liq",
                        "component": "CaO",
                        "position": "0.5",
                        "sigma": "0.01",
                        "temperature_K_as_captioned": "1933",
                        "outside_side": "below",
                        "locator": {"table": "fixture"},
                    }
                ],
                "plateaus": [
                    {
                        "id": "plat",
                        "composition_field": "x",
                        "compositions": ["0.1", "0.2"],
                        "series": [
                            {
                                "observation_id": "series_a",
                                "field": "p",
                                "sigma_basis": "printed",
                            }
                        ],
                    }
                ],
            },
        }
    )
    assert records is not None
    plateau = records.plateaus["plat"]
    # The printed cells differ by 2.417 - 1.000 = 1.417; independent
    # propagation gives 1.417 / sqrt(0.5^2 + 0.5^2) = 2.0039 > 2.
    expected_z = Decimal("1.417") / (Decimal("0.5") ** 2 + Decimal("0.5") ** 2).sqrt()
    assert plateau.z_by_series["series_a"] == expected_z
    assert expected_z > Decimal(2)
    assert plateau.failing == ("series_a",)
    assert plateau.problem is not None


def test_two_test_rule_refuses_points_inside_the_liquidus_sigma_band() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    records = _records(plateau_cells={"0.36": ("10.0 ± 1.0", "1.00 ± 0.05"), "0.25": ("10.5 ± 1.0", "0.98 ± 0.05")})
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.36")},
        temperature_K=Decimal("1933"),
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
        temperature_K=Decimal("1933"),
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
        temperature_K=Decimal("1933"),
        declared=_declared("0.33", plateau_max_z="0.10"),
    )
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "do not match" in (outcome.problem or "")


def test_two_test_rule_leaves_single_liquid_rows_and_mirrors_the_outside_side() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED, classify_point

    records = _records()
    for x in ("0.41", "0.50"):
        outcome = classify_point(
            records,
            observation_id="series_a",
            x_by_component={"SiO2": Decimal(x)},
            temperature_K=Decimal("1933"),
            declared=None,
        )
        assert outcome.kind is None and outcome.problem is None
    outcome = classify_point(
        records,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.40")},
        temperature_K=Decimal("1933"),
        declared=None,
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
        above,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.56")},
        temperature_K=Decimal("1933"),
        declared=None,
    ).kind == LIQUIDUS_POSITION_CONTESTED
    assert classify_point(
        above,
        observation_id="series_a",
        x_by_component={"SiO2": Decimal("0.50")},
        temperature_K=Decimal("1933"),
        declared=None,
    ).kind is None


# --- t-1123a r2: remaining reader guards (each has a mutation proof) ---------


def _classify(
    records,
    x="0.33",
    declared=None,
    observation_id="series_a",
    temperature_K=Decimal("1933"),
):
    from simulator.battery.phase_field import classify_point

    return classify_point(
        records,
        observation_id=observation_id,
        x_by_component={"SiO2": Decimal(x)},
        temperature_K=temperature_K,
        declared=declared if declared is not None else _declared(x),
    )


def test_two_test_rule_refuses_a_liquidus_record_without_a_locator() -> None:
    records = _records(
        liquidus={
            "id": "liq",
            "component": "SiO2",
            "position": "0.37",
            "sigma": "0.02",
            "outside_side": "below",
        }
    )
    assert records.liquidus == {}
    assert len(records.problems) == 1 and "locator" in records.problems[0]
    outcome = _classify(records)
    assert outcome.kind is None
    assert outcome.problem == "phase_field_class without an applicable liquidus record"


def test_two_test_rule_refuses_a_basis_that_does_not_name_the_records() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED

    records = _records()
    cases = {
        "criterion": ("plateau_only", "basis.criterion must be"),
        "liquidus": ("another-liquidus", "names another liquidus record"),
        "plateau": ("another-plateau", "names no plateau record"),
        "locator": (None, "basis needs a locator"),
    }
    for key, (value, message) in cases.items():
        declared = _declared("0.33")
        declared["basis"][key] = value
        outcome = _classify(records, declared=declared)
        # The row is not class S; it falls back to the contested flag.
        assert outcome.kind == LIQUIDUS_POSITION_CONTESTED, key
        assert message in (outcome.problem or ""), (key, outcome.problem)
    wrong_class = _declared("0.33")
    wrong_class["class"] = "two_phase"
    assert "phase_field_class must be" in (_classify(records, declared=wrong_class).problem or "")


def test_two_test_rule_test_ii_needs_the_point_in_the_plateau_record() -> None:
    from simulator.battery.phase_field import LIQUIDUS_POSITION_CONTESTED

    records = _records()
    # x(SiO2) 0.30 passes test (i) (0.30 < 0.35) but is not a plateau composition.
    outcome = _classify(records, x="0.30")
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "test (ii) fails" in (outcome.problem or "")
    # Right composition, but a series the plateau record does not list.
    outcome = _classify(records, observation_id="series_c")
    assert outcome.kind == LIQUIDUS_POSITION_CONTESTED
    assert "test (ii) fails" in (outcome.problem or "")


def test_two_test_rule_never_counts_untestable_or_parenthesised_cells() -> None:
    from simulator.battery.phase_field import phase_field_records

    def doc(cell_033, cell_025, basis):
        return {
            "species": {
                "A": {
                    "observations": [
                        {
                            "observation_id": "series_a",
                            "values": {"points": [{"x": "0.33", "p": cell_033}, {"x": "0.25", "p": cell_025}]},
                        }
                    ]
                }
            },
            "phase_field_records": {
                "liquidus": [],
                "plateaus": [
                    {
                        "id": "plat",
                        "composition_field": "x",
                        "compositions": ["0.33", "0.25"],
                        "series": [{"observation_id": "series_a", "field": "p", "sigma_basis": basis}],
                    }
                ],
            },
        }

    untestable = phase_field_records(doc("6.7", "6.8", "none_printed")).plateaus["plat"]
    assert untestable.untestable == ("series_a",) and untestable.z_by_series == {}
    assert untestable.problem == "plateau has no testable series"
    # "(5.3)" is a parenthesised (not measured) cell: it is never a plateau value.
    parenthesised = phase_field_records(doc("(5.3) ± 1.0", "2.0 ± 1.0", "printed")).plateaus["plat"]
    assert parenthesised.z_by_series == {}
    assert "lacks a usable printed cell" in (parenthesised.problem or "")


def test_migrator_refuses_misplaced_phase_field_class() -> None:
    from types import SimpleNamespace

    from simulator.battery.enums import RefusalReason
    from simulator.battery.migrate import Migrator

    declared = {"phase_field_class": _declared("0.33")}
    cases = (
        ("no records", None, None, "phase_field_class without phase_field_records"),
        ("printed phase", _records(), "liquid", "phase_field_class on a point with a printed phase"),
        ("no composition", _records(), None, "phase_field_class needs a printed mole-fraction composition"),
    )
    for name, records, printed_phase_kind, message in cases:
        stub = SimpleNamespace(
            _phase_field_records={"src": records},
            result=SimpleNamespace(registry_issues=[]),
        )
        outcome = Migrator._point_phase_field(
            stub,
            source_id="src",
            parent_id="src::series_a",
            point_id="src::series_a::p0",
            raw_item=declared,
            point_conditions=None,
            printed_phase_kind=printed_phase_kind,
        )
        assert outcome.kind is None, name
        assert outcome.problem == message, name
        (issue,) = stub.result.registry_issues
        assert issue.reason is RefusalReason.CONDITIONAL_FIELD, name
        assert issue.detail == message, name
