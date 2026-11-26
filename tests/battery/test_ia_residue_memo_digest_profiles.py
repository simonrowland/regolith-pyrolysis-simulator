import pytest

from tests.battery._ia_rhs_profile_support import profile_ia_residue_source


@pytest.mark.parametrize(
    "label,source_id,expected_count,expected_digest",
    (
        (
            "Mendybaev 2013",
            "mendybaev-2013-fun-cai-evaporation",
            96,
            "a203e1990ed396862048a6ef93002204dc083566ac11e5f840f372ad57e55e1e",
        ),
        (
            "Markova 1984",
            "kems-026-markova-1984",
            110,
            "60473d4bfe0e78b0b4a29c340ffad2d40ff885d9c6c6c0329ab8cdc91b7a8214",
        ),
    ),
)
@pytest.mark.parametrize("memoize", (True, False), ids=("memo", "no-memo"))
def test_ia_prior_study_digest_is_unchanged(
    label, source_id, expected_count, expected_digest, memoize, monkeypatch,
):
    report = profile_ia_residue_source(
        label, source_id, monkeypatch, memoize=memoize,
    )
    assert report["row_count"] == expected_count
    assert report["numeric_rows"] + report["typed_refusal_rows"] == expected_count
    assert report["residual_digest"] == expected_digest
    assert report["budget_refusal_rows"] == 0
