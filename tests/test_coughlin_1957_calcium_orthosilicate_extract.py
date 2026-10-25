from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/coughlin-obrien-1957-calcium-orthosilicate-heat-content.yaml"
sys.path.insert(0, str(ROOT / "tools"))

import validate_literature_extracts as vle  # noqa: E402
from tests.battery.factories import migrate_one_extract  # noqa: E402


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["Ca2SiO4"]["observations"]
    }


def test_render_pins_and_printed_table_counts() -> None:
    document = _document()
    rows = _observations(document)
    table_i = [row for row in rows.values() if row["locator"].get("table") == "I"]
    table_ii = [row for row in rows.values() if row["locator"].get("table") == "II"]
    assert len(table_i) == 47
    assert len(table_ii) == 29
    assert len({row["observation_id"].split("_r", 1)[1].split("_", 1)[0] for row in table_ii}) == 20
    pretransition = rows[
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p002_t05_r12_c06"
    ]
    assert pretransition["values"]["value_as_printed"] == "62,130"
    assert pretransition["values"]["footnote_marker_as_printed"] == "a"
    assert pretransition["values"]["footnote_text_as_printed"] == (
        "Shows pretransition effect."
    )

    pins = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}
    expected = {
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p002_t05_r03_c06": (30490, 768),
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p002_t05_r10_c02": (3540, 768),
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p003_t06_r13_c02": (32220, 769),
        "coughlin_obrien_1957_beta_alpha_prime_transition_temperature": (970, 769),
    }
    for observation_id, (value, published_page) in expected.items():
        sample = pins[observation_id]
        assert sample["value"] == value
        assert sample["locator"]["published_page"] == published_page
        assert vle.check_fidelity_sample_matches(document, sample) == []

    mutated = deepcopy(document)
    mutated_rows = _observations(mutated)
    mutated_rows[next(iter(expected))]["values"]["value"] += 1
    assert vle.check_fidelity_sample_matches(mutated, pins[next(iter(expected))])


def test_isolated_migration_maps_each_heat_point_temperature_and_keeps_pressure_unknown() -> None:
    document = _document()
    source_rows = _observations(document)
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 82
    assert len(result.validation.hard_issues) == 0
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 28

    migrated = {
        row.observation_id.split("::", 1)[1]: row
        for row in result.observations.values()
    }
    for source_id, source in source_rows.items():
        if source["locator"].get("table") not in {"I", "II"}:
            continue
        target = migrated[source_id]
        temperature = target.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert temperature.value == Decimal(str(source["values"]["T_K"]))
        source_condition = source["point_conditions"]["temperature_K"]
        assert source_condition["state"]["tag"] == "value"
        condition = target.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == temperature.value
        assert condition.locator.page == source["locator"]["page"]
        reference_temperature = target.point_conditions["reference_temperature_K"]
        assert reference_temperature.state.is_value
        assert reference_temperature.state.value == Decimal("298.15")
        assert reference_temperature.locator.page == source["locator"]["page"]
        reference_phase = target.point_conditions["reference_polymorph_as_printed"]
        assert reference_phase.state.is_value
        assert reference_phase.state.value == source["values"]["reference_polymorph_as_printed"]
        assert reference_phase.locator.page == source["locator"]["page"]
        assert "total_pressure_Pa" not in source["point_conditions"]
        assert "pressure_basis_Pa" not in source["values"]

    for row in source_rows.values():
        if row["values"].get("quantity") == "transition_temperature":
            pressure = row["point_conditions"]["total_pressure_Pa"]["state"]
            assert pressure["tag"] == "unknown"
            assert "value" not in pressure
        assert "pressure_basis_Pa" not in row["values"]

    for observation_id in (
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p002_t05_r03_c06",
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p002_t05_r10_c02",
        "coughlin_obrien_1957_calcium_orthosilicate_heat_content_p003_t06_r13_c02",
    ):
        identity = migrated[observation_id].identity
        assert identity.quantity.is_value
        assert identity.quantity.value.value == "H_minus_H298"
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        assert identity.composition is not None and identity.composition.is_not_applicable
        assert identity.total_pressure_Pa is not None and identity.total_pressure_Pa.is_not_applicable
        assert identity.fO2_Pa is not None and identity.fO2_Pa.is_not_applicable
        assert identity.standard_pressure_Pa is not None and identity.standard_pressure_Pa.is_unknown


def test_table_ii_lineage_uses_only_measured_same_phase_table_i_rows() -> None:
    document = _document()
    source_rows = _observations(document)
    table_i_by_phase: dict[str, set[str]] = {}
    for row in source_rows.values():
        if row["locator"].get("table") == "I":
            table_i_by_phase.setdefault(row["phase_as_printed"], set()).add(
                row["observation_id"]
            )
    table_ii_rows = [
        row
        for row in source_rows.values()
        if row["locator"].get("table") == "II"
        and row.get("values", {}).get("fit_equation_context_id")
    ]
    assert len(table_ii_rows) == 29

    migrator = migrate_one_extract(root=ROOT, path=EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = {
        row.observation_id.split("::", 1)[1]: row
        for row in migrator.result.observations.values()
    }
    for row in table_ii_rows:
        expected_parents = table_i_by_phase[row["phase_as_printed"]]
        assert row["derived_from"] == sorted(expected_parents)
        assert row["derivation"]["inputs"] == sorted(expected_parents)
        assert all(source_rows[parent]["locator"].get("table") == "I" for parent in row["derived_from"])
        equation_context = next(
            context
            for context in document["species"]["Ca2SiO4"]["context"]
            if context["observation_id"] == row["values"]["fit_equation_context_id"]
        )
        assert equation_context["values"]["equation_as_printed"] in row["derivation"]["relation"]

        migrated_inputs = set(migrated[row["observation_id"]].derivation.inputs)
        assert migrated_inputs == {
            f"coughlin-obrien-1957-calcium-orthosilicate-heat-content::{parent}"
            for parent in expected_parents
        }

    reference_phases = {"gamma": "gamma", "beta": "beta", "alpha-prime": "beta", "alpha": "beta"}
    for row in source_rows.values():
        if row["locator"].get("table") not in {"I", "II"}:
            continue
        if row.get("values", {}).get("reference_temperature_K") != 298.15:
            continue
        assert row["values"]["reference_polymorph_as_printed"] == reference_phases[row["phase_as_printed"]]
