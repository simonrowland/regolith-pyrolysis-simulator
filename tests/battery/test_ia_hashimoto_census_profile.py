from tests.battery._ia_rhs_profile_support import profile_ia_residue_source


def test_ia_hashimoto_120_row_profile(monkeypatch):
    report = profile_ia_residue_source(
        "Hashimoto 1983",
        "kems-015-hashimoto-1983",
        monkeypatch,
        memoize=True,
    )
    assert report["row_count"] == 120
    assert report["numeric_rows"] + report["typed_refusal_rows"] == 120
    assert report["residual_digest"]
    assert report["budget_refusal_rows"] == 0
    assert report["integration_count"] > 0
    assert report["integration_totals"]["nfev"] > 0
