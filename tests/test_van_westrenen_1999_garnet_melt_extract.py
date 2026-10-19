from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-westrenen-1999-garnet-melt-trace-partitioning.yaml"
RENDER_PINS = {"cao": "15.2", "mgo": "19.9", "al2o3": "15.9", "sio2": "48.9"}


def test_table4_glass_composition_matches_render_and_migrates_with_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    source_rows = extract["species"]["SiO2"]["observations"]
    assert len(source_rows) == 4
    assert {row["values"]["basis_as_published"].split()[0] for row in source_rows} == {
        "CaO", "MgO", "Al2O3", "SiO2"
    }
    assert all(row["phase"] == "l" and row["values"]["printed_phase_label"] == "Gl" for row in source_rows)
    assert all("fO2_log" not in row["point_conditions"] for row in source_rows)

    context = extract["species"]["SiO2"]["context"]
    table5 = next(row for row in context if row["observation_id"] == "vw99_table5_partition_coefficients")
    assert "0.53" in table5["values"]["printed_table"]
    assert "schema_gap" in table5["values"]

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert len(observations) == 4
    assert len(migrator.result.experiments) == 1
    assert not migrator.result.validation.hard_issues

    by_component = {row.observation_id.rsplit("_", 1)[-1]: row for row in observations}
    assert set(by_component) == set(RENDER_PINS)
    for suffix, printed in RENDER_PINS.items():
        row = by_component[suffix]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(printed)
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.temperature_K.value == Decimal("1838.15")
        assert row.point_conditions["temperature_K"].state.value == Decimal("1838.15")
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("3000000000")
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator.table == "4"
        assert row.locator.page == 5
        assert row.locator.published_page == 842
        assert row.locator.pdf_page_index == 4
