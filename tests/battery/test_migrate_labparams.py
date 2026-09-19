"""Lab-parameter vocabulary lift: printed extract names → Experiment schema."""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import (
    REPO_ROOT,
    migrate,
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
