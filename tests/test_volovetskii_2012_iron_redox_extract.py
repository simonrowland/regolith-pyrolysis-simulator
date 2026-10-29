from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    IdentityEqualKind,
    Quantity,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import (
    fe3_fe2_ratio_from_ferric_fraction,
    wt_pct_to_mole_fraction,
)
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    resolve_fidelity_sample,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/volovetskii-2012-iron-redox.yaml"


def _migrate_extract():
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
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
    assert table1_i2.identity.composition.is_value
    assert table1_i2.identity.composition.value.as_map() == wt_pct_to_mole_fraction(
        {
            "SiO2": Decimal("70.46"),
            "TiO2": Decimal("0.06"),
            "Al2O3": Decimal("15.28"),
            "FeO": Decimal("3.41"),
            "MgO": Decimal("0.14"),
            "CaO": Decimal("2.06"),
            "Na2O": Decimal("4.07"),
            "K2O": Decimal("3.95"),
        }
    ).as_map()
    assert table1_i2.identity.total_pressure_Pa.value == Decimal("101325")
    assert table1_i2.point_conditions["fO2_log"].state.value == Decimal("-0.7")
    assert table1_i2.point_conditions["T_C"].state.value == Decimal("1350")
    assert table1_i2.point_conditions["T_C"].locator.table == "1"
    assert table1_i2.point_conditions["duration_h"].state.value == Decimal("12")

    run_observations = [
        o for o in observations
        if not o.observation_id.startswith(
            "volovetskii-2012-iron-redox::volo12_p9_initial_fe_redox"
        )
    ]
    assert len(run_observations) == 29
    assert all(
        o.identity.total_pressure_Pa.is_value
        and o.identity.total_pressure_Pa.value == Decimal("101325")
        for o in run_observations
    )
    assert all(
        o.identity.composition is not None and o.identity.composition.is_value
        for o in observations
    )
    numeric_fugacity_rows = [
        o for o in run_observations
        if isinstance(o.point_conditions["fO2_log"].state.value, Decimal)
    ]
    assert len(numeric_fugacity_rows) == 28
    assert all(
        o.point_conditions["fO2_log"].locator is not None
        for o in numeric_fugacity_rows
    )
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


def test_volovetskii_pantellerite_composition_sds_match_table_2():
    source = yaml.safe_load(EXTRACT.read_text())
    points = next(
        observation["values"]["points"]
        for observation in source["species"]["Fe"]["observations"]
        if observation["observation_id"] == "volo12_p9_pantellerite_table2"
    )
    actual = {
        point["sample_id_as_published"]: point["composition_uncertainty_sd_wt_pct"]
        for point in points
        if point["sample_id_as_published"] in {"P9-27*", "P9-34", "P9-36*"}
    }
    assert actual == {
        "P9-27*": {
            "SiO2": 0.77,
            "TiO2": 0.02,
            "Al2O3": 0.71,
            "FeO": 0.17,
            "MnO": 0.03,
            "MgO": 0.01,
            "CaO": 0.04,
            "Na2O": 0.83,
            "K2O": 0.17,
            "Cl": 0.03,
            "ZrO2": 0.06,
            "total": 0.76,
        },
        "P9-34": {
            "SiO2": 8.39,
            "TiO2": 0.09,
            "Al2O3": 8.54,
            "FeO": 2.00,
            "MnO": 0.05,
            "MgO": 0.02,
            "CaO": 0.11,
            "Na2O": 1.33,
            "K2O": 0.51,
            "Cl": 0.08,
            "ZrO2": 0.03,
            "total": 0.23,
        },
        "P9-36*": {
            "SiO2": 0.92,
            "TiO2": 0.08,
            "Al2O3": 2.96,
            "FeO": 2.35,
            "MnO": 0.09,
            "MgO": 0.03,
            "CaO": 0.01,
            "Na2O": 0.36,
            "K2O": 0.49,
            "Cl": 0.05,
            "ZrO2": 0.03,
            "total": 0.33,
        },
    }


def test_volovetskii_migrated_composition_matches_every_sample_pin():
    source = yaml.safe_load(EXTRACT.read_text())
    result = _migrate_extract()
    observations = list(result.observations.values())
    point_pins = {
        sample["observation_id"]: sample["value"]
        for sample in source["fidelity_samples"]
        if sample.get("field") == "points"
    }
    checked_compositions = 0

    for series in source["species"]["Fe"]["observations"]:
        points = series["values"].get("points", [])
        if not points:
            continue
        pinned_by_sample = {
            point["sample_id_as_published"]: point
            for point in point_pins[series["observation_id"]]
        }
        series_observations = [
            observation
            for observation in observations
            if f"::{series['observation_id']}::" in observation.observation_id
        ]
        by_conditions = {}
        for observation in series_observations:
            conditions = observation.point_conditions
            oxygen = conditions["fO2_log"].state.value
            oxygen_key = (
                ("point", oxygen)
                if isinstance(oxygen, Decimal)
                else (
                    oxygen.kind.value,
                    oxygen.bound_operator,
                    oxygen.bound_value,
                )
            )
            key = (
                conditions["T_C"].state.value,
                conditions["duration_h"].state.value,
                oxygen_key,
            )
            assert key not in by_conditions
            by_conditions[key] = observation
        assert len(by_conditions) == len(points) == len(series_observations)

        for point in points:
            source_oxygen = point["point_conditions"]["fO2_log"]["state"]["value"]
            oxygen_key = (
                (
                    source_oxygen["kind"],
                    source_oxygen["bound_operator"],
                    Decimal(str(source_oxygen["bound_value"])),
                )
                if isinstance(source_oxygen, dict)
                else ("point", Decimal(str(source_oxygen)))
            )
            key = (
                Decimal(str(point["T_C"])),
                Decimal(str(point["duration_h_as_published"])),
                oxygen_key,
            )
            observation = by_conditions[key]
            composition = observation.identity.composition
            assert composition is not None and composition.is_value
            expected = pinned_by_sample[point["sample_id_as_published"]][
                "composition_mol"
            ]
            assert composition.value.as_map() == {
                species: Decimal(value) for species, value in expected.items()
            }
            checked_compositions += 1

            log_fugacity = observation.point_conditions["fO2_log"].state.value
            if point["sample_id_as_published"] == "I2-16":
                assert point["sample_id_as_published"] == "I2-16"
                assert log_fugacity.kind is ValueKind.BOUND
                assert not observation.identity.fO2_Pa.is_value
            else:
                assert isinstance(log_fugacity, Decimal)

            if point.get("oxygen_buffer_as_published") == "IW":
                assert point["log_fO2_as_published"] == "-10.5 (IW)"

    initial = next(
        observation
        for observation in observations
        if observation.observation_id.endswith("::volo12_p9_initial_fe_redox")
    )
    initial_pin = next(
        sample["value"]
        for sample in source["fidelity_samples"]
        if sample.get("observation_id") == "volo12_p9_initial_fe_redox"
        and sample.get("field") == "composition_mol"
    )
    assert initial.identity.composition is not None
    assert initial.identity.composition.is_value
    assert initial.identity.composition.value.as_map() == {
        species: Decimal(value) for species, value in initial_pin.items()
    }
    checked_compositions += 1

    assert checked_compositions == 30


def test_volovetskii_fidelity_pins_match_and_reject_mutated_composition_sd():
    source = yaml.safe_load(EXTRACT.read_text())
    assert check_all_fidelity_samples_match(source, label=EXTRACT.stem) == []

    mutated = deepcopy(source)
    p9_27 = next(
        point
        for series in mutated["species"]["Fe"]["observations"]
        for point in series["values"].get("points", [])
        if point.get("sample_id_as_published") == "P9-27*"
    )
    p9_27["composition_uncertainty_sd_wt_pct"]["Na2O"] = Decimal("0.84")
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


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
