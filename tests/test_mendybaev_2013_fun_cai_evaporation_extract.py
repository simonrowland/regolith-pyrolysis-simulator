"""Regression pins for Mendybaev et al. (2013) Table 1 migration."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2013-fun-cai-evaporation.yaml"
OXIDES = ("MgO", "Al2O3", "SiO2", "CaO")

# Table 1 was transcribed from its 600 dpi page render. Each row stores the
# printed duration, initial mass converted once to kg, then value and sigma
# pairs in OXIDES order.
EXPECTED = {
    "FUN1-3": ("0", "0.0000161", (("50.01", ".31"), ("4.04", ".21"), ("42.99", ".11"), ("2.96", ".15"))),
    "FUN1-12": ("0", "0.0000214", (("50.73", ".45"), ("4.19", ".27"), ("42.05", ".09"), ("3.03", ".22"))),
    "FUN1-15": ("5", "0.0000258", (("49.25", ".15"), ("4.42", ".08"), ("43.04", ".06"), ("3.29", ".06"))),
    "FUN1-13": ("10", "0.0000286", (("49.03", ".32"), ("4.39", ".15"), ("43.16", ".08"), ("3.43", ".14"))),
    "FUN1-1": ("15", "0.0000103", (("45.80", ".22"), ("6.74", ".18"), ("42.20", ".17"), ("5.27", ".11"))),
    "FUN1-10": ("15", "0.0000214", (("46.01", ".32"), ("6.77", ".17"), ("42.29", ".11"), ("4.93", ".16"))),
    "FUN1-5": ("20", "0.0000180", (("41.39", ".19"), ("10.17", ".09"), ("40.21", ".09"), ("8.19", ".13"))),
    "FUN1-11": ("25", "0.0000300", (("43.37", ".26"), ("8.98", ".19"), ("41.02", ".08"), ("6.63", ".12"))),
    "FUN1-16": ("25", "0.0000238", (("43.25", ".65"), ("8.82", ".39"), ("41.39", ".07"), ("6.54", ".31"))),
    "FUN1-4": ("30", "0.0000278", (("26.93", ".37"), ("20.88", ".20"), ("33.91", ".15"), ("18.29", ".30"))),
    "FUN1-14": ("30", "0.0000290", (("41.40", ".19"), ("10.72", ".12"), ("39.92", ".07"), ("7.95", ".11"))),
    "FUN1-9": ("35", "0.0000377", (("42.79", "1.00"), ("9.44", ".68"), ("40.74", ".19"), ("7.03", ".50"))),
    "FUN1-17": ("35", "0.0000278", (("34.53", ".16"), ("15.38", ".11"), ("37.39", ".11"), ("12.70", ".12"))),
    "FUN1-18": ("40", "0.0000312", (("33.22", ".23"), ("16.30", ".17"), ("36.83", ".11"), ("13.65", ".16"))),
    "FUN2-4": ("0", "0.0000334", (("30.97", ".24"), ("17.50", ".15"), ("37.00", ".08"), ("14.52", ".15"))),
    "FUN2-5": ("10", "0.0000315", (("27.00", ".29"), ("20.42", ".25"), ("34.67", ".19"), ("17.91", ".22"))),
    "FUN2-1": ("20", "0.0000619", (("25.68", ".14"), ("21.78", ".11"), ("33.51", ".10"), ("19.03", ".08"))),
    "FUN2-2": ("20", "0.0000455", (("24.21", ".10"), ("22.79", ".11"), ("32.91", ".11"), ("20.09", ".09"))),
    "FUN2-3": ("25", "0.0000372", (("19.25", ".14"), ("26.76", ".17"), ("30.24", ".13"), ("23.74", ".22"))),
    "FUN2-8": ("30", "0.0000309", (("11.21", ".03"), ("33.77", ".04"), ("25.53", ".04"), ("29.49", ".06"))),
    "FUN2-7": ("35", "0.0000418", (("12.71", ".06"), ("32.34", ".06"), ("26.60", ".05"), ("28.35", ".08"))),
    "FUN2-6": ("40", "0.0000419", (("4.86", ".12"), ("39.51", ".20"), ("20.96", ".19"), ("34.67", ".16"))),
    "FUN2-9": ("50", "0.0000559", (("5.71", ".02"), ("38.72", ".05"), ("21.79", ".05"), ("33.78", ".07"))),
    "FUN2-10": ("55", "0.0000514", (("1.55", ".01"), ("43.60", ".07"), ("17.11", ".04"), ("37.74", ".10"))),
}

STARTING = {
    "FUN1": {"MgO": "53.14", "Al2O3": "2.97", "SiO2": "41.27", "CaO": "2.36"},
    "FUN2": {"MgO": "33.03", "Al2O3": "15.18", "SiO2": "38.39", "CaO": "13.40"},
}


def _migrate(path: Path = EXTRACT):
    migrator = migrate_one_extract(ROOT, path, index={}, aliases={})
    migrator.finalize()
    return migrator.result


def _assert_migrated_residue_points(result, expected=EXPECTED) -> None:
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == len(expected) * len(OXIDES)

    actual = {}
    for observation in result.observations.values():
        sample = observation.experiment_id.rsplit("run-", 1)[1].upper()
        duration, mass_kg, cells = expected[sample]
        formula = observation.identity.species.formula
        assert formula in OXIDES
        assert observation.identity.species.phase.value.value == "l"
        assert observation.value.kind.value == "point"
        assert observation.admission.status.value == "admitted"
        assert observation.locator.page == 5
        assert observation.locator.table == "1"

        cell = cells[OXIDES.index(formula)]
        actual[(sample, formula)] = (
            observation.value.point,
            Decimal(str(observation.uncertainty.verbatim["sigma"])),
        )
        assert actual[(sample, formula)] == (Decimal(cell[0]), Decimal(cell[1]))

        assert observation.identity.temperature_K.value == Decimal("2173.15")
        assert observation.identity.sample_mass_kg.value == Decimal(mass_kg)
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.tag.value == "not_applicable"
        assert observation.identity.exposure.value.duration_s.value == Decimal(duration) * 60

        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        assert temperature.state.value == Decimal("2173.15")
        assert temperature.locator.page == 4
        time = conditions["time_min"]
        assert time.state.value == Decimal(duration)
        assert time.locator.table == "1"
        pressure = conditions["total_pressure_Pa"]
        assert pressure.state.value.kind.value == "bound"
        assert pressure.state.value.bound_operator == "<"
        assert pressure.state.value.bound_value == Decimal("0.0001")
        assert pressure.locator.page == 4

        composition_id = sample.split("-", 1)[0]
        starting_composition = conditions["starting_composition"]
        assert {
            oxide: Decimal(str(value))
            for oxide, value in starting_composition.state.value.items()
        } == {oxide: Decimal(value) for oxide, value in STARTING[composition_id].items()}
        assert starting_composition.locator.table == "1"

        experiment = result.experiments[observation.experiment_id]
        assert experiment.sample.mass_kg.state.value.point == Decimal(mass_kg)
        assert experiment.sample.initial_composition.state.is_value
        initial_composition = experiment.sample.initial_composition.state.value
        assert observation.identity.composition.value == initial_composition
        assert conditions["composition"].state.value == initial_composition
        assert experiment.pressure_environment.total_pressure_Pa.state.value.kind.value == "bound"

    expected_cells = {
        (sample, oxide): (Decimal(value), Decimal(sigma))
        for sample, (_duration, _mass, cells) in expected.items()
        for oxide, (value, sigma) in zip(OXIDES, cells, strict=True)
    }
    assert actual == expected_cells


def test_mendybaev_table_1_residue_points_conditions_and_initial_charge_migrate() -> None:
    result = _migrate()
    _assert_migrated_residue_points(result)

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    marker_count = 0
    for oxide in OXIDES:
        observations = document["species"][oxide]["observations"]
        assert len(observations) == 2
        for observation in observations:
            values = observation["values"]
            initial = values["series"][0]
            assert initial["T_C_is_initial_composition"] is True
            assert "T_C" not in initial
            assert "mass_loss_pct" not in initial
            marker_count += 1
    assert marker_count == 8


def test_mendybaev_migrated_point_pins_fail_for_expected_or_extract_mutations(tmp_path: Path) -> None:
    result = _migrate()
    wrong_expected = deepcopy(EXPECTED)
    duration, mass_kg, cells = wrong_expected["FUN1-3"]
    wrong_cells = list(cells)
    wrong_cells[0] = ("50.02", wrong_cells[0][1])
    wrong_expected["FUN1-3"] = (duration, mass_kg, tuple(wrong_cells))
    with pytest.raises(AssertionError):
        _assert_migrated_residue_points(result, wrong_expected)

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    series = document["species"]["MgO"]["observations"][0]["values"]["series"]
    row = next(item for item in series if item.get("sample") == "FUN1-3")
    row["MgO_wt_pct"] = 50.02
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    mutated_result = _migrate(mutated_extract)
    with pytest.raises(AssertionError):
        _assert_migrated_residue_points(mutated_result)
