from __future__ import annotations

from pathlib import Path

import pytest

from simulator.config import load_config_bundle


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.mark.parametrize(
    ("source_id", "page", "quote", "sample", "temperature_K", "delta_fmq"),
    (
        pytest.param(
            "kems-041-sossi-fegley-2018",
            412,
            "alkali volatility is 10^4 times greater than the next most volatile components",
            "Apollo_12_12022",
            1400.0,
            0.5,
            id="F09-12022-1400K-FMQ-plus-0.5",
        ),
    ),
)
def test_f09_alkali_volatility_uses_the_printed_sample(
    source_id: str,
    page: int,
    quote: str,
    sample: str,
    temperature_K: float,
    delta_fmq: float,
) -> None:
    assert source_id == "kems-041-sossi-fegley-2018"
    assert page == 412
    assert len(quote.split()) <= 15
    assert sample == "Apollo_12_12022"
    assert 1396.0 <= temperature_K <= 1499.0
    assert delta_fmq == 0.5

    bundle = load_config_bundle(DATA_DIR)
    sample_feedstocks = [
        key
        for key, feedstock in bundle.feedstocks.items()
        if "12022" in key
        or "12022" in str(feedstock.get("source", ""))
        or feedstock.get("sample_id") == "12022"
    ]
    assert not sample_feedstocks
    pytest.skip(
        "typed-skip: no feedstock carries Apollo 12 12022; the available "
        "lunar_mare_low_ti composition is a modeled family representative, "
        "so it cannot stand in for the cited sample"
    )
