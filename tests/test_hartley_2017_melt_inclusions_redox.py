from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = Path(
    "data/literature/extracts/hartley-2017-melt-inclusions-redox.yaml"
)


def _migrate(path: Path):
    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _context_by_id(result):
    return {
        row["observation_id"]: row
        for rows in result.context_by_work.values()
        for row in rows
    }


def test_hartley_context_values_survive_production_migration():
    doc = yaml.safe_load(EXTRACT.read_text())
    assert check_all_fidelity_samples_match(doc) == []

    result = _migrate(EXTRACT)
    contexts = _context_by_id(result)

    fig3a = contexts["hartley_2017_fig3a_xanes_readable_markers"]["values"]
    assert len(fig3a["points"]) == 32
    assert fig3a["points"][0] == {
        "H2O_wt_percent": 0.26,
        "Fe3_over_sum_Fe": 0.29,
    }
    assert fig3a["uncertainty"] == (
        "The paper reports external XANES precision 0.8% (absolute), 2σ; "
        "no separate per-point width is printed."
    )

    fig3c = contexts["hartley_2017_fig3c_qfm_readable_markers"]["values"]
    assert len(fig3c["points"]) == 30
    assert fig3c["points"][23]["delta_QFM"] == -0.031
    fig3b = contexts["hartley_2017_fig3b_pec_xanes_readable_markers"]["values"]
    assert len(fig3b["points"]) == 30
    assert fig3b["points"][0]["Fe3_over_sum_Fe_PEC"] == 0.21
    assert fig3b["uncertainty"] == "No per-point uncertainty is printed for these digitized markers."
    fig10a = contexts["hartley_2017_fig10a_qfm_readable_markers"]["values"]
    assert len(fig10a["points"]) == 20
    assert fig10a["derivation"]["model"] == "Equation (7) of Kress and Carmichael (1991)"

    summary = contexts["hartley_2017_results_summary_p196"]["values"]
    assert summary["tephra_PEC_corrected_Fe3_over_sum_Fe_uncertainty"] == "±0.008"
    assert summary["tephra_uncertainty_confidence"] == "1 sigma"
    assert summary["lava_PEC_corrected_Fe3_over_sum_Fe_uncertainty"] == "±0.012"
    assert summary["lava_uncertainty_confidence"] == "1 sigma"
    assert summary["tephra_delta_QFM_uncertainty"] == "±0.1"
    assert summary["tephra_delta_QFM_confidence"] == "1 sigma"
    assert summary["primary_melt_uncertainty"] == "±0.02"
    table_a2 = contexts["hartley_2017_table_a2_source_melt_compositions"]["values"]
    assert table_a2["points"][0]["La_Yb"] == 3.812
    assert table_a2["points"][0]["La_Yb_attribution"] == "Shorttle et al. (2014)"
    assert table_a2["points"][1]["delta_QFM_attribution"] == "Shorttle et al. (2015)"
    assert table_a2["method_class"] == "unknown"

    assert not [obs for obs in result.observations.values() if obs.source_id == EXTRACT.stem]
    assert sum(map(len, result.context_by_work.values())) == 14


def test_hartley_fidelity_pin_detects_mutated_migrated_point(tmp_path):
    doc = yaml.safe_load(EXTRACT.read_text())
    mutated = deepcopy(doc)
    point = mutated["species"]["Fe"]["context"][0]["values"]["points"][0]
    point["Fe3_over_sum_Fe"] = 0.28
    assert check_all_fidelity_samples_match(mutated)

    mutated_path = tmp_path / EXTRACT.name
    mutated_path.write_text(yaml.safe_dump(mutated, sort_keys=False))
    result = _migrate(mutated_path)
    migrated = _context_by_id(result)["hartley_2017_fig3a_xanes_readable_markers"]
    assert migrated["values"]["points"][0]["Fe3_over_sum_Fe"] == 0.28
    assert migrated["values"]["points"][0]["Fe3_over_sum_Fe"] != 0.29
