"""Lab-parameter vocabulary lift: printed extract names → Experiment schema."""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import (
    REPO_ROOT,
    apparatus_from_equipment,
    migrate,
    pressure_from_equipment,
    sample_from_equipment,
)
from simulator.battery.records import as_decimal
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _richter_weight_extract() -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["locator"] = {
        "table": "1",
        "paragraph": "R3-12",
        "published_page": 5549,
        "source_path": "docs/references/pdfs/99-kems-langmuir/kems-010-richter-2007.pdf",
    }
    # Richter 2007 Table 1 prints initial_weight_mg per sample row (R3-12 = 25.4 mg).
    row["equipment"] = {
        "initial_weight_mg": 25.4,
    }
    return extract


def test_labparam_richter_initial_weight_mg_reaches_sample_mass_kg(tmp_path: Path) -> None:
    """Printed Richter initial_weight_mg must land on Experiment.sample.mass_kg.

    Fail-proof: the current migrator only reads mass / mass_kg / sample_mass_kg,
    so this goes red until the vocabulary table includes initial_weight_mg.
    """

    root = _write_min_tree(tmp_path, _richter_weight_extract())
    result = migrate(root, write=False)
    assert result.experiments, "expected one experiment from the fixture"
    sample = next(iter(result.experiments.values())).sample
    located = sample.mass_kg
    assert located is not None, (
        "sample.mass_kg is missing; printed initial_weight_mg 25.4 was dropped"
    )
    assert located.state.is_value, located.state.reason
    assert located.state.value == as_decimal("25.4") / as_decimal("1000000")
    assert located.locator is not None, "no value without a locator"
    assert located.inference is not None
    assert located.inference.relation == "mg_to_kg"
    params = dict(located.inference.parameters)
    assert params["original"].state.value == as_decimal("25.4")


def test_labparam_vocabulary_is_data(tmp_path: Path) -> None:
    """A new printed name in the YAML table lifts with no migrator code change."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["locator"] = {"table": "I", "page": 2}
    row["equipment"] = {"crucible_charge_grams": 3.5}
    root = _write_min_tree(tmp_path, extract)
    vocab_src = REPO_ROOT / "data" / "literature" / "lab_parameter_vocabulary.yaml"
    dest = root / "data" / "literature" / "lab_parameter_vocabulary.yaml"
    doc = yaml.safe_load(vocab_src.read_text(encoding="utf-8"))
    printed = [str(e["printed"]) for e in doc["entries"]]
    assert "crucible_charge_grams" not in printed
    doc["entries"].append(
        {
            "printed": "crucible_charge_grams",
            "field": "sample.mass_kg",
            "unit": "g",
        }
    )
    dest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    source = inspect.getsource(sample_from_equipment)
    assert "crucible_charge_grams" not in source
    result = migrate(root, write=False)
    located = next(iter(result.experiments.values())).sample.mass_kg
    assert located is not None and located.state.is_value
    assert located.state.value == as_decimal("3.5") / as_decimal("1000")


def test_labparam_absence_reason_names_keys_looked_for(tmp_path: Path) -> None:
    """A missed lookup must not claim the source is silent."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["locator"] = {"table": "I", "page": 2}
    # Sauerborn-shaped: a pressure is printed, but not under chamber_pressure.
    row["equipment"] = {
        "vacuum_mbar": {
            "value": 0.01,
            "locator": {"table": "5.1", "page": 68},
        }
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    env = next(iter(result.experiments.values())).pressure_environment
    reason = env.total_pressure_Pa.state.reason or ""
    if env.total_pressure_Pa.state.is_unknown:
        assert "source does not state" not in reason
        assert "vacuum_mbar" in reason or "chamber_pressure" in reason
    else:
        assert env.total_pressure_Pa.state.value == as_decimal("1")  # 0.01 mbar = 1 Pa

    silent = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    silent["species"]["Na"]["observations"][0]["equipment"] = {
        "cell_material": {
            "value": "Mo",
            "locator": {"table": "I", "page": 2},
        }
    }
    root2 = _write_min_tree(tmp_path / "silent", silent)
    result2 = migrate(root2, write=False)
    reason2 = (
        next(iter(result2.experiments.values()))
        .pressure_environment.total_pressure_Pa.state.reason
        or ""
    )
    assert "source does not state" not in reason2
    assert "chamber_pressure" in reason2


def _append_vocab_row(root: Path, printed: str, field: str, unit: str | None) -> None:
    vocab_src = REPO_ROOT / "data" / "literature" / "lab_parameter_vocabulary.yaml"
    dest = root / "data" / "literature" / "lab_parameter_vocabulary.yaml"
    source = dest if dest.is_file() else vocab_src
    doc = yaml.safe_load(source.read_text(encoding="utf-8"))
    printed_names = [str(e["printed"]) for e in doc["entries"]]
    if printed not in printed_names:
        doc["entries"].append({"printed": printed, "field": field, "unit": unit})
    dest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _fixture_with_equipment(equipment: dict) -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["locator"] = {"table": "I", "page": 2}
    row["equipment"] = equipment
    return extract


def test_labparam_mapping_leaf_builds_pumping_container(tmp_path: Path) -> None:
    """A dotted Mapping destination must create the container and set the leaf.

    Fail-proof: today's lifter only materialises scalar Located[Decimal] fields,
    so pressure_environment.pumping stays None even when the vocabulary names it.
    """

    extract = _fixture_with_equipment({"pumping_speed_L_s": 10})
    root = _write_min_tree(tmp_path, extract)
    _append_vocab_row(
        root,
        "pumping_speed_L_s",
        "pressure_environment.pumping.pumping_speed_m3_s",
        "L/s",
    )
    assert "pumping_speed_L_s" not in inspect.getsource(pressure_from_equipment)
    result = migrate(root, write=False)
    pumping = next(iter(result.experiments.values())).pressure_environment.pumping
    assert pumping is not None, (
        "pressure_environment.pumping is missing; printed pumping_speed_L_s 10 was dropped"
    )
    located = pumping.get("pumping_speed_m3_s")
    assert located is not None and located.state.is_value, pumping
    assert located.state.value == as_decimal("10") / as_decimal("1000")
    assert located.locator is not None


def test_labparam_mapping_merges_numeric_and_string_leaves(tmp_path: Path) -> None:
    """Several rows targeting one Mapping container must merge, not replace.

    String leaves stay strings; they are not coerced to Decimal.
    """

    extract = _fixture_with_equipment(
        {
            "pumping_speed_L_s": 10,
            "backing_pump": "Leybold Scrollvac SC 5 D",
        }
    )
    root = _write_min_tree(tmp_path, extract)
    _append_vocab_row(
        root,
        "pumping_speed_L_s",
        "pressure_environment.pumping.pumping_speed_m3_s",
        "L/s",
    )
    _append_vocab_row(
        root,
        "backing_pump",
        "pressure_environment.pumping.backing_pump",
        None,
    )
    result = migrate(root, write=False)
    pumping = next(iter(result.experiments.values())).pressure_environment.pumping
    assert pumping is not None
    speed = pumping["pumping_speed_m3_s"]
    pump = pumping["backing_pump"]
    assert speed.state.is_value
    assert speed.state.value == as_decimal("0.01")
    assert pump.state.is_value
    assert pump.state.value == "Leybold Scrollvac SC 5 D"
    assert not isinstance(pump.state.value, Decimal)


def test_labparam_located_str_alias_reaches_cell_material(tmp_path: Path) -> None:
    """Located[str] destinations take the printed string; no unit conversion.

    Fail-proof: the hardcoded cell_material reader does not see other printed
    names, and the numeric hit path drops non-Decimal values.
    """

    extract = _fixture_with_equipment(
        {
            "cell_material_as_published": {
                "value": "Ir",
                "locator": {"table": "I", "page": 2},
            }
        }
    )
    root = _write_min_tree(tmp_path, extract)
    _append_vocab_row(
        root,
        "cell_material_as_published",
        "apparatus.cell_material_and_liner",
        None,
    )
    assert "cell_material_as_published" not in inspect.getsource(
        apparatus_from_equipment
    )
    result = migrate(root, write=False)
    apparatus = next(iter(result.experiments.values())).apparatus
    assert apparatus is not None, "apparatus missing; printed cell_material_as_published dropped"
    located = apparatus.cell_material_and_liner
    assert located is not None and located.state.is_value
    assert located.state.value == "Ir"
    assert located.locator is not None
    assert not isinstance(located.state.value, Decimal)


def test_labparam_ionization_mapping_without_cell_or_geometry(tmp_path: Path) -> None:
    """An ionization leaf must still create Apparatus when geometry is absent."""

    extract = _fixture_with_equipment({"electron_energy_eV": 70})
    root = _write_min_tree(tmp_path, extract)
    _append_vocab_row(
        root,
        "electron_energy_eV",
        "apparatus.ionization.electron_energy_eV",
        "eV",
    )
    result = migrate(root, write=False)
    apparatus = next(iter(result.experiments.values())).apparatus
    assert apparatus is not None
    ionization = apparatus.ionization
    assert ionization is not None
    located = ionization["electron_energy_eV"]
    assert located.state.is_value
    assert located.state.value == as_decimal("70")


def test_labparam_cell_material_still_lifts_via_vocabulary(tmp_path: Path) -> None:
    """equipment.cell_material must still reach cell_material_and_liner.

    The hardcoded reader was replaced by a vocabulary row of the same name.
    """

    extract = _fixture_with_equipment(
        {
            "cell_material": {
                "value": "Mo",
                "locator": {"table": "I", "page": 2},
            }
        }
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    located = next(iter(result.experiments.values())).apparatus.cell_material_and_liner
    assert located is not None and located.state.is_value
    assert located.state.value == "Mo"


def test_labparam_cell_material_wins_over_disagreeing_alias(tmp_path: Path) -> None:
    """SCHEMA equipment.cell_material is not dropped when an alias wording differs."""

    extract = _fixture_with_equipment(
        {
            "cell_material": {
                "value": "Mo",
                "locator": {"table": "I", "page": 2},
            },
            "cell_material_as_published": {
                "value": "Ir",
                "locator": {"table": "II", "page": 3},
            },
        }
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    located = next(iter(result.experiments.values())).apparatus.cell_material_and_liner
    assert located is not None and located.state.is_value
    assert located.state.value == "Mo"


def test_labparam_equipment_cell_material_wins_over_values_wording(
    tmp_path: Path,
) -> None:
    """The SCHEMA equipment block wins when values.cell_material is reworded."""

    extract = _fixture_with_equipment(
        {
            "cell_material": {
                "value": "unstated in this paper (referred to Ueshima et al. 1982)",
                "locator": {"published_page": 792, "pdf_page_index": 2},
            }
        }
    )
    extract["species"]["Na"]["observations"][0]["values"]["cell_material"] = (
        "unstated in this paper; apparatus details omitted and referred to ref 1"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    located = next(iter(result.experiments.values())).apparatus.cell_material_and_liner
    assert located is not None and located.state.is_value
    assert located.state.value == (
        "unstated in this paper (referred to Ueshima et al. 1982)"
    )


def test_labparam_vocabulary_covers_corpus_mapping_names() -> None:
    """Printed names that actually occur in extracts must have a vocab row."""

    path = REPO_ROOT / "data" / "literature" / "lab_parameter_vocabulary.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    printed = {str(entry["printed"]) for entry in doc["entries"]}
    required = {
        "cell_material",
        "cell_material_as_published",
        "sample_container",
        "pump",
        "pumping",
        "backing_pump",
        "high_vacuum_pump",
        "q_pump_l_s_assumed_as_printed",
        "electron_energy_eV",
        "ionizing_electron_energy_eV",
        "emission_current_mA",
        "thermocouple",
        "pyrometer",
        "pressure_gauge",
        "cell_wall_thickness_cm",
        "residual_pressure_Pa_as_printed",
    }
    missing = sorted(required - printed)
    assert missing == []
