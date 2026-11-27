import json
from pathlib import Path

from _ia_rhs_profile_support import profile_ia_residue_source
from simulator.battery import score as score_module
from simulator.battery.migrate import LITERATURE, Migrator


def test_markova_partial_residue_bounded_rhs_cost_profile(monkeypatch, capsys):
    markova = Migrator()
    markova._migrate_extract(LITERATURE / "extracts" / "kems-026-markova-1984.yaml")
    result = markova.result
    context = score_module.ScoreContext(
        result.works, result.experiments, result.observations
    )
    monkeypatch.setattr(
        score_module, "load_score_context", lambda *, sources: context
    )
    with capsys.disabled():
        report = profile_ia_residue_source(
            "Markova 1984",
            "kems-026-markova-1984",
            monkeypatch,
            observation_ids=(
                "kems-026-markova-1984::"
                "markova_1984_table2_residual_melt_VI_lherzolite::"
                "T=2156.15:h=c98ab06cc333",
            ),
            cprofile_target=True,
            rhs_cap=50,
            direct_compile_residual=True,
        )
        print(
            "IA_RHS_PROFILE "
            + json.dumps(report, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    assert report["row_count"] == 1
    assert report["integration_count"] == 1
    profile = report["integrations"][0]
    assert profile["method"] == "DOP853"
    assert profile["rhs_cap"] == 50
    assert 0 < profile["rhs_calls"] <= 50
    assert report["profile_cap_reached"] or profile["rhs_calls"] < 50
    assert Path(profile["cprofile_path"]).is_file()
    assert Path(profile["raw_rhs_metrics_path"]).is_file()
    assert profile["per_rhs_summary"]["rhs_count"] == profile["rhs_calls"]
    assert profile["per_rhs_summary"]["mean_pressure_model_calls_per_rhs"] > 0
    assert profile["per_rhs_summary"]["mean_ia_vapor_pressure_calls_per_rhs"] > 0
