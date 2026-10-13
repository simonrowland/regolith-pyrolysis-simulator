from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import IdentityEqualKind, Quantity, UncertaintyKind
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import Migrator, fe3_fe2_ratio_from_ferric_fraction
from tools.validate_literature_extracts import resolve_fidelity_sample


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/volovetskii-2012-iron-redox.yaml"


def _migrate_extract():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_volovetskii_extract_migrates_measured_redox_and_run_conditions():
    source = yaml.safe_load(EXTRACT.read_text())
    result = _migrate_extract()

    assert len(result.observations) == 30
    observations = list(result.observations.values())
    assert all(o.identity.species.formula == "Fe" for o in observations)
    assert all(o.identity.species.phase.value.value == "glass" for o in observations)
    assert all(o.identity.quantity.value is Quantity.FE3_FE2_RATIO for o in observations)

    table1_i2 = next(
        o for o in observations
        if o.observation_id.startswith(
            "volovetskii-2012-iron-redox::volo12_i2_granite_table1::"
        )
        and o.identity.temperature_K.value == Decimal("1623.15")
    )
    expected = fe3_fe2_ratio_from_ferric_fraction(Decimal("0.72"))
    assert table1_i2.value.point == expected
    assert table1_i2.point_conditions["T_C"].state.value == Decimal("1350")
    assert table1_i2.point_conditions["T_C"].locator.table == "1"
    assert table1_i2.point_conditions["fO2_log"].state.value == Decimal("-0.7")
    assert table1_i2.point_conditions["duration_h"].state.value == Decimal("12")

    run_observations = [
        o for o in observations
        if not o.observation_id.startswith(
            "volovetskii-2012-iron-redox::volo12_p9_initial_fe_redox"
        )
    ]
    assert len(run_observations) == 29
    assert all(
        all(
            key in o.point_conditions and o.point_conditions[key].locator is not None
            for key in ("T_C", "fO2_log", "duration_h")
        )
        for o in run_observations
    )
    assert all(o.uncertainty.kind is UncertaintyKind.NONE for o in observations)

    p9_initial = next(
        o for o in observations
        if o.observation_id.startswith(
            "volovetskii-2012-iron-redox::volo12_p9_initial_fe_redox"
        )
    )
    assert p9_initial.value.point == fe3_fe2_ratio_from_ferric_fraction(
        Decimal("0.18")
    )
    assert identity_equal(
        p9_initial.identity, p9_initial.identity
    ).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert source["species"]["Fe"]["context"][0]["type"] == "recalculation_table"


def test_volovetskii_fidelity_pins_reject_mutated_source_values():
    source = yaml.safe_load(EXTRACT.read_text())
    mutable_cases = [
        ("volo12_i2_granite_table1", "points", ("Fe3_over_FeT", 0.71)),
        ("volo12_p9_pantellerite_table2", "points", ("Fe3_over_FeT", 0.92)),
        ("volo12_table3_regressions", "points", ("k", "MUTATED")),
        ("volo12_p9_initial_fe_redox", "Fe3_over_FeT", 0.19),
        (
            "volo12_i2_granite_table1",
            "experimental_conditions_as_printed",
            ("1 atm", "2 atm"),
        ),
        (
            "volo12_p9_pantellerite_table2",
            "experimental_conditions_as_printed",
            ("1 atm", "2 atm"),
        ),
        (
            "volo12_i2_granite_table1",
            "analytical_method_as_printed",
            ("relative intensities", "relative ratios"),
        ),
        (
            "volo12_p9_pantellerite_table2",
            "analytical_method_as_printed",
            ("relative intensities", "relative ratios"),
        ),
    ]

    for observation_id, field, mutation in mutable_cases:
        changed = deepcopy(source)
        observation = next(
            row
            for container in (
                changed["species"]["Fe"]["observations"],
                changed["species"]["Fe"]["context"],
            )
            for row in container
            if row["observation_id"] == observation_id
        )
        if field == "points":
            key, replacement = mutation
            observation["values"]["points"][0][key] = replacement
        elif field == "Fe3_over_FeT":
            observation["values"][field] = mutation
        else:
            old, new = mutation
            observation["values"][field] = observation["values"][field].replace(old, new, 1)

        sample = next(
            item
            for item in changed["fidelity_samples"]
            if item.get("observation_id") == observation_id
            and item.get("field") == field
        )
        assert resolve_fidelity_sample(changed, sample) != sample["value"]
