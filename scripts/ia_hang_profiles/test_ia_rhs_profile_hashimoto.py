from pathlib import Path

from _ia_rhs_profile_support import profile_ia_residue_source


def test_hashimoto_c3_2_rhs_cost_profile(monkeypatch, capsys):
    with capsys.disabled():
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
            rhs_cap=50,
            single_hashimoto_experiment=True,
        )
    assert report["row_count"] == 1
    assert report["numeric_rows"] + report["typed_refusal_rows"] == 1
    assert report["residual_digest"] == (
        "d55fabc5acdd91edde83f86f0527459783acbc4e9e3dc87d4fc2c70c61f286d6"
    )
    assert report["integration_count"] == 1
    profile = report["integrations"][0]
    assert profile["method"] == "Hashimoto finite-step inventory update"
    assert Path(profile["cprofile_path"]).is_file()
    assert Path(profile["raw_rhs_metrics_path"]).is_file()
    assert report["profile_cap_reached"]
    assert profile["rhs_cap"] == 50
    assert profile["nfev"] is None
    assert profile["per_rhs_summary"]["rhs_count"] == 1
    assert profile["per_rhs_summary"]["mean_pressure_model_calls_per_rhs"] > 0
    assert profile["pressure_model_calls"] < 25
    assert report["scoring_wall_to_process_cpu_ratio"] is not None
