"""Source-grounded checks for the McKay, Morris and Jurewicz 1991 LPSC extract."""

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT / "data/literature/extracts/mckay-1991-simulated-lunar-glass-reduction.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _glass_context(document: dict) -> list[dict]:
    return document["species"]["synthetic_lunar_glass"]["context"]


def _row(document: dict, observation_id: str) -> dict:
    fe = document["species"]["Fe"]
    for row in fe["observations"] + fe["context"]:
        if row["observation_id"] == observation_id:
            return row
    for row in _glass_context(document):
        if row["observation_id"] == observation_id:
            return row
    raise AssertionError(f"missing {observation_id}")


def test_extract_pins_table1_and_printed_fe_conversions() -> None:
    document = _document()
    errors = validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert errors == []

    carbon = _row(document, "mckay_1991_fe_conversion_1000C_carbon")
    assert carbon["type"] == "composition_series"
    assert carbon["locator"]["published_page"] == "50"
    assert carbon["values"]["mass_yield_percent"] == 11
    assert carbon["values"]["T_C"] == 1000
    assert carbon["values"]["reductant_as_printed"] == "elemental carbon"
    assert carbon["values"]["method_class"] == "measured_direct"

    hydrogen = _row(document, "mckay_1991_fe_conversion_1000C_hydrogen")
    assert hydrogen["values"]["mass_yield_percent"] == 53
    assert hydrogen["values"]["T_C"] == 1000
    assert hydrogen["values"]["duration_h"] == 24
    assert hydrogen["values"]["reductant_as_printed"] == "hydrogen gas"

    co = _row(document, "mckay_1991_co_reduction_no_measurable_metal")
    assert co["values"]["semantics"] == "bound_not_point"
    assert co["values"]["value_as_printed"] == "none"

    synthetic = _row(document, "mckay_1991_table1_synthetic_lunar_glass")["values"]
    assert synthetic["SiO2"] == "44.65"
    assert synthetic["FeO"] == "13.95"
    assert synthetic["Total"] == "99.06"

    apollo11 = _row(document, "mckay_1991_table1_apollo11_soil_10084")["values"]
    assert apollo11["SiO2"] == "41.0"
    assert apollo11["FeO"] == "16.2"
    assert apollo11["P2O5"] == {"state": "not_printed"}
    assert apollo11["Total"] == "99.96"

    orange = _row(document, "mckay_1991_table1_apollo17_orange_glass")["values"]
    assert orange["SiO2"] == "38.57"
    assert orange["FeO"] == "22.04"
    assert orange["Total"] == "99.40"

    estimates = _row(document, "mckay_1991_author_estimate_o2_yields")["values"]
    assert estimates["method_class"] == "author_estimate"
    assert "24.5 kg" in estimates["orange_glass_conditional_yield"]["value_as_printed"]
    assert "about 2 kg" in estimates["unconcentrated_ilmenite_comparator"]["value_as_printed"]
    assert "about 100 kg" in estimates["concentrated_ilmenite_comparator"]["value_as_printed"]


def test_mutating_conversion_breaks_pin() -> None:
    document = _document()
    carbon = document["species"]["Fe"]["observations"][0]
    carbon["values"]["mass_yield_percent"] = 53
    with pytest.raises(AssertionError):
        assert carbon["values"]["mass_yield_percent"] == 11


def test_isolated_migration_has_two_yield_observations_and_no_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 2
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 8
