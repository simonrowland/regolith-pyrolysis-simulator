from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_utils import isolated_root


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/ramteke-2026-alkali-vapor-corrosion-spinels.yaml"
)


def test_ramteke_wall_corrosion_context_is_preserved_by_isolated_migration(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    root = isolated_root(tmp_path, document)
    extract = root / "data/literature/extracts/ramteke-2026-alkali-vapor-corrosion-spinels.yaml"

    migrator = Migrator(root, index={}, aliases={})
    migrator._migrate_extract(extract)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 0
    assert result.experiments == {}
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    unavailable = sum(
        row.value.kind.value == "unavailable" for row in result.observations.values()
    )
    assert unavailable == 0
    quantity_unknown = {
        row.observation_id
        for row in result.observations.values()
        if row.quantity.is_unknown
    }
    phase_unknown = {
        row.observation_id
        for row in result.observations.values()
        if row.identity.species.phase.is_unknown
    }
    assert quantity_unknown == set()
    assert phase_unknown == set()

    contexts = [row for rows in result.context_by_work.values() for row in rows]
    assert len(contexts) == 10
    by_id = {row["observation_id"]: row for row in contexts}

    mg_depths = by_id["ramteke_2026_mgal2o4_corrosion_depths"]
    assert mg_depths["locator"]["page"] == 9
    assert mg_depths["locator"]["table"] == "4"
    assert mg_depths["values"]["corrosion_depth_as_printed"] == "254 ± 47"
    assert mg_depths["values"]["infiltration_depth_as_printed"] == "416 ± 15"

    zn_depths = by_id["ramteke_2026_znal2o4_corrosion_depths"]
    assert zn_depths["values"]["corrosion_depth_as_printed"] == "847 ± 264"
    assert zn_depths["values"]["corrosion_depth_more_precise_as_printed"] == "846.8 ± 263.9"
    assert zn_depths["values"]["corrosion_depth_more_precise_locator"] == (
        "page 13, section 4 Discussion"
    )

    mg_surface = by_id["ramteke_2026_mgal2o4_surface_eds_table1"]
    assert mg_surface["locator"]["page"] == 6
    assert mg_surface["locator"]["table"] == "1"
    assert mg_surface["values"]["measurements"]["Na"][3] == "29.9 ± 1.3"

    zn_cross_section = by_id["ramteke_2026_znal2o4_cross_section_eds_table6"]
    assert zn_cross_section["locator"]["page"] == 12
    assert zn_cross_section["locator"]["table"] == "6"
    assert zn_cross_section["values"]["measurements"]["Na"][5] == "4.2 ± 1.3"
    assert zn_cross_section["values"]["layer_thickness_um"]["U2_feature_3"] == "355 ± 16"

    conditions = by_id["ramteke_2026_znal2o4_test_conditions"]["values"]
    assert conditions["reported_test_temperature_state"] == "unknown_due_to_source_disagreement"
    assert conditions["gas_atmosphere"] == "not_reported"
    assert conditions["total_pressure"] == "not_reported_for_reported_results"
