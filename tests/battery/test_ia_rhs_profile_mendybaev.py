from pathlib import Path

from tests.battery._ia_rhs_profile_support import profile_ia_residue_source


def test_mendybaev_fun2_6_rhs_cost_profile(monkeypatch):
    report = profile_ia_residue_source(
        "Mendybaev 2013",
        "mendybaev-2013-fun-cai-evaporation",
        monkeypatch,
        observation_ids=(
            "mendybaev-2013-fun-cai-evaporation::"
            "mendybaev_2013_table1_fun2_residue_composition_series::"
            "T=2173.15:row=fun2-6:h=8aaaae7d662d",
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
    assert report["scoring_wall_to_process_cpu_ratio"] is not None
