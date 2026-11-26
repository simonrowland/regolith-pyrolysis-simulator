from pathlib import Path

from tests.battery._ia_rhs_profile_support import profile_ia_residue_source


def test_hashimoto_c3_2_rhs_cost_profile(monkeypatch):
    report = profile_ia_residue_source(
        "Hashimoto 1983",
        "kems-015-hashimoto-1983",
        monkeypatch,
        observation_ids=(
            "kems-015-hashimoto-1983::"
            "hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::"
            "T=1973.15:row=c3-(2):h=0bc862a8ce7c",
        ),
        cprofile_target=True,
    )
    assert report["row_count"] == 1
    assert report["numeric_rows"] + report["typed_refusal_rows"] == 1
    assert report["budget_refusal_rows"] == 0
    assert report["integration_count"] == 1
    profile = report["integrations"][0]
    assert profile["method"] == "DOP853"
    assert Path(profile["cprofile_path"]).is_file()
    assert Path(profile["raw_rhs_metrics_path"]).is_file()
    assert profile["nfev"] > 0
    assert profile["per_rhs_summary"]["mean_pressure_model_calls_per_rhs"] > 0
    assert profile["per_rhs_summary"]["rhs_count"] == profile["rhs_calls"]
    assert report["scoring_wall_to_process_cpu_ratio"] is not None
