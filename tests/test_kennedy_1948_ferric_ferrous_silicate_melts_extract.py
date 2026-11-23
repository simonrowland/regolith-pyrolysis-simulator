from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kennedy-1948-ferric-ferrous-silicate-melts.yaml"


def _migrate_extract(path: Path = EXTRACT):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def test_kennedy_table2_runs_migrate_with_printed_conditions_and_composition():
    source_text = EXTRACT.read_text(encoding="utf-8")
    source = yaml.safe_load(source_text)
    assert yaml_mapping_source_issues(source_text) == []
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []

    result = _migrate_extract()
    assert len(result.observations) == 22
    assert result.registry_issues == []
    assert result.queue == []
    observations = list(result.observations.values())
    assert all(o.identity.quantity.value is Quantity.FE3_FE2_RATIO for o in observations)
    assert all(o.value.kind.value == "point" for o in observations)
    assert all(o.identity.temperature_K.is_value for o in observations)
    assert all(o.identity.composition.is_value for o in observations)
    assert all(o.identity.species.phase.value.value == "glass" for o in observations)
    assert all(
        o.point_conditions["T_C"].state.is_value
        and o.point_conditions["T_C"].locator.pdf_page_index == 5
        and o.point_conditions["T_C"].locator.table == "II"
        for o in observations
    )
    assert all(
        o.point_conditions["fO2_control"].state.value
        == "1/5 atmosphere of oxygen"
        and o.point_conditions["fO2_control"].locator.pdf_page_index == 3
        for o in observations
    )
    assert all(
        o.identity.fO2_Pa.is_value and o.identity.fO2_Pa.value == Decimal("20265")
        for o in observations
    )
    for observation in observations:
        pressure = observation.point_conditions["fO2_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value.point == Decimal("20265")
        assert pressure.inference is not None
        assert "(1/5 atm)*(101325 Pa/atm)" in pressure.inference.relation
        assert "fO2=pO2 under the ideal-gas convention" in pressure.inference.relation
        assert "atm_to_Pa" in pressure.inference.relation
        assert "printed=point_conditions.fO2_control" in pressure.inference.inputs
        assert pressure.locator.pdf_page_index == 3
        assert pressure.locator.published_page == 532
    assert all(
        o.point_conditions["duration_h"].state.is_value
        and o.point_conditions["duration_h"].locator.table == "II"
        for o in observations
    )
    assert len(source["species"]["Fe"]["observations"][0]["values"]["rows"]) == 22


def test_kennedy_fidelity_pin_detects_a_changed_table2_cell(tmp_path: Path):
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(source)
    row = mutated["species"]["Fe"]["observations"][0]["values"]["rows"][0]
    row["FeO_wt_pct"] = "3.33"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")

    errors = validate_extract_file(path, check_fidelity_match=True)
    assert any("fidelity sample value mismatch" in error for error in errors)
