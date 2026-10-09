"""Hastie 1981 (NBSIR 81-2279) Table 2 technique scope (backlog #38).

Printed p. 22 says the potassium data are TMS; Table 2 footnote d (printed
p. 25) says KMS. The live quoted Table 2 row stays on its own bench-free,
sample-free experiment so no apparatus or composition is inherited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import MethodToken, NoticeKind
from simulator.battery.records import SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX
from simulator.yaml_cache import load_cached_safe_yaml
from tests.battery.test_migrate import _migrate_real_extract

EXTRACTS = Path(__file__).resolve().parents[2] / "data/literature/extracts"
EXTRACT = "kems-020-hastie-1981-nbsir.yaml"
LIVE = "hastie_1981_table2_k_logP_coefficients_quoted_20260906"
PARENT = "hastie_1981_table2_k_logP_coefficients"
QUOTED_FITS = "hastie-1981-table2-quoted-fits"


@pytest.fixture(scope="module")
def hastie(tmp_path_factory):
    return _migrate_real_extract(tmp_path_factory.mktemp("hastie-t2"), EXTRACT)


def _experiment(result, local: str):
    rows = [
        exp
        for exp in result.experiments.values()
        if exp.experiment_id.endswith("::experiment::" + local)
    ]
    assert len(rows) == 1, local
    return rows[0]


def test_live_table2_rows_stay_on_bench_free_sample_free_experiment(hastie) -> None:
    live = [
        obs for obs in hastie.observations.values() if LIVE in obs.observation_id
    ]
    assert len(live) == 17
    experiment = _experiment(hastie, QUOTED_FITS)
    assert {obs.experiment_id for obs in live} == {experiment.experiment_id}
    assert experiment.bench_id is None
    assert experiment.sample.initial_composition is None
    assert experiment.sample.printed_composition is None


def test_parent_table2_row_keeps_tms_experiment(hastie) -> None:
    parent = [
        obs
        for obs in hastie.observations.values()
        if PARENT in obs.observation_id and LIVE not in obs.observation_id
    ]
    assert len(parent) == 17
    experiment = hastie.experiments[parent[0].experiment_id]
    assert experiment.experiment_id.endswith("::experiment::hastie-1981-pt-tms")
    assert experiment.bench_id is not None
    assert experiment.bench_id.endswith("::bench::hastie-1981-tms")


def test_live_table2_rows_emit_no_source_disagreement_notice(hastie) -> None:
    # The K-pressure-fit quantity is untyped, so the reader's
    # source_internally_inconsistent path cannot fire on these rows.
    live = [
        obs for obs in hastie.observations.values() if LIVE in obs.observation_id
    ]
    assert not [
        notice
        for obs in live
        for notice in obs.notices
        if notice.kind is NoticeKind.SOURCE_DISAGREEMENT
    ]


def test_quoted_fits_experiment_carries_transpiration_from_printed_p22(hastie) -> None:
    experiment = _experiment(hastie, QUOTED_FITS)
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.TRANSPIRATION
    assert experiment.locator is not None
    assert experiment.locator.page == 22
    assert experiment.locator.pdf_page_index == 27
    assert "TMS technique" in (experiment.locator.paragraph or "")


def test_live_table2_row_flags_technique_assignment_ambiguous() -> None:
    doc = load_cached_safe_yaml((EXTRACTS / EXTRACT).read_text(encoding="utf-8"))
    row = next(
        obs
        for obs in doc["species"]["K"]["observations"]
        if obs["observation_id"] == LIVE
    )
    reason = " ".join(row["reason"].split())
    assert reason.startswith(
        SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX
        + " technique_assignment_ambiguous."
    )
    assert (
        'Text p. 22 (PDF p. 28): "The potassium vapor pressure data were '
        'obtained under neutral conditions using the TMS technique."'
    ) in reason
    assert (
        'Table 2 footnote d, p. 25 (PDF p. 31): "Results obtained by KMS. '
        'See comments for applicable compositions."'
    ) in reason
    # Footnote d text already carried verbatim on the row agrees with the flag.
    assert row["values"]["footnotes"]["d"].startswith(
        "Results obtained by KMS. See comments for applicable compositions."
    )
