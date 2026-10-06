"""Ladder rungs for trace-parent activity. Injected rows exercise order.

Production numbers live in test_t1139_b2_activity_pins.py. These tests call
the production resolver and compare verdicts, flags, and identities. They do
not reimplement log10(gamma) = A + B/T.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from simulator.chemistry.melt_activity import (
    molecular_mole_fractions,
    pure_liquid_reference_coefficient,
    single_cation_mole_fractions,
)
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    ActivityRefusalCode,
    ActivityTier,
    ActivityVerdictKind,
    BoundDirection,
    CondensedPhaseActivityProvider,
    StandardStateIdentity,
    load_fegley2023_gamma_table,
    resolve_trace_parent_activity,
    trace_parent_formulas,
    validation_row_may_certify,
)


_ROOT = Path(__file__).resolve().parents[1]
_EXTRACT = (
    _ROOT
    / "data"
    / "literature"
    / "extracts-v2"
    / "fegley-2023-chemical-equilibrium-calculations-bu.yaml"
)
_TABLE = _ROOT / "data" / "vapour_rail" / "fegley2023_table2_gamma.json"
_SS = StandardStateIdentity(
    convention="raoultian_pure_endmember",
    phase="liquid",
    reference_pressure_bar=1.0,
)


def _builder():
    path = _ROOT / "tools" / "build_fegley2023_gamma_table.py"
    spec = importlib.util.spec_from_file_location(
        "build_fegley2023_gamma_table", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    formula: str,
    *,
    row_id: str,
    A: str = "0",
    B: str = "0",
    notes: str = "",
    band: list[float] | None = None,
    standard_state_as_printed: str | None = None,
    stated_convention: str | None = None,
    stated_phase: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_row_id": row_id,
        "formula": formula,
        "A": A,
        "B": B,
        "validity_range_K": band,
        "notes_as_printed": notes,
    }
    if standard_state_as_printed is not None:
        payload["standard_state_as_printed"] = standard_state_as_printed
    if stated_convention is not None:
        payload["stated_convention"] = stated_convention
    if stated_phase is not None:
        payload["stated_phase"] = stated_phase
    return payload


def _resolve(
    formula: str,
    rows: list[dict[str, object]] | None,
    *,
    temperature_K: float = 1500.0,
    mole_fraction: float | None = 1e-6,
    activity_exponent: float = 1.0,
    standard_state: StandardStateIdentity | None = None,
):
    return resolve_trace_parent_activity(
        formula,
        temperature_K=temperature_K,
        activity_exponent=activity_exponent,
        standard_state=_SS if standard_state is None else standard_state,
        mole_fraction=mole_fraction,
        rows=rows,
    )


def _point_only(activity):
    if activity.verdict is not ActivityVerdictKind.POINT:
        return None
    return activity.as_pressure_activity()


def test_generated_table_matches_the_extract_sha() -> None:
    built = _builder().build_table(_EXTRACT)
    committed = json.loads(_TABLE.read_text(encoding="utf-8"))
    assert built == committed
    digest = hashlib.sha256(_EXTRACT.read_bytes()).hexdigest()
    assert committed["provenance"]["extract_sha256"] == digest
    assert committed["provenance"]["extract"].endswith(
        "fegley-2023-chemical-equilibrium-calculations-bu.yaml"
    )
    assert committed["provenance"]["row_count"] == len(committed["rows"])
    assert committed["rows"]
    assert "origin" not in committed["rows"][0]
    phrases = {row["standard_state_as_printed"] for row in committed["rows"]}
    assert phrases <= {
        "not stated in Table 2 row",
        "liquid standard state",
    }
    assert "not stated in Table 2 row" in phrases


def test_published_row_is_rung_2_point() -> None:
    row = _row("SnO", row_id="published-sno", B="-90.4", notes="measured")
    answer = _resolve("SnO", [row])
    assert answer.verdict is ActivityVerdictKind.POINT
    assert answer.tier is ActivityTier.B
    assert answer.authority is False
    assert answer.may_certify() is False
    assert answer.derivation["rung"] == 2
    assert answer.derivation["flag"] == "published"
    assert answer.derivation["origin"] == "published"
    assert answer.derivation["source_row_id"] == "published-sno"
    assert answer.derivation["homologue"] is None
    assert answer.derivation["gamma"] is not None
    assert answer.derivation["gamma"] != 1.0
    assert answer.value == answer.derivation["gamma"] * 1e-6


def test_proxy_phrases_are_flagged_estimates() -> None:
    for notes in ("See text, set = g(Na2O)", "interpolated between neighbours"):
        answer = _resolve(
            "B2O3",
            [_row("B2O3", row_id="proxy", B="-100", notes=notes)],
        )
        assert answer.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
        assert answer.verdict is not ActivityVerdictKind.POINT
        assert answer.derivation["rung"] == 2
        assert answer.derivation["flag"] == "proxy_estimate"
        assert answer.derivation["origin"] == "proxy_estimate"
        assert answer.derivation["source_row_id"] == "proxy"


def test_set_log_phrase_stays_published() -> None:
    answer = _resolve(
        "B2O3",
        [
            _row(
                "B2O3",
                row_id="log-phrase",
                B="-100",
                notes="Set log g(B2O3) = -3.2",
            )
        ],
    )
    assert answer.derivation["flag"] == "published"
    assert answer.derivation["origin"] == "published"
    assert answer.verdict is ActivityVerdictKind.POINT


def test_gamma_equality_to_another_oxide_is_a_proxy() -> None:
    answer = _resolve(
        "GeO2",
        [
            _row(
                "GeO2",
                row_id="equality",
                A="-0.01",
                B="-580.4",
                notes="g(GeO2) = g(SiO2) from FactSage",
            )
        ],
    )
    assert answer.derivation["origin"] == "proxy_estimate"
    assert answer.derivation["flag"] == "proxy_estimate"
    assert answer.verdict is not ActivityVerdictKind.POINT
    assert answer.derivation["source_row_id"] == "equality"


def test_numeric_gamma_assignment_stays_published() -> None:
    answer = _resolve(
        "GeO2",
        [
            _row(
                "GeO2",
                row_id="numeric",
                A="0.8692",
                B="0",
                notes="g(GeO2) = 7.4, FactSage",
            )
        ],
    )
    assert answer.derivation["origin"] == "published"
    assert answer.derivation["flag"] == "published"
    assert answer.verdict is ActivityVerdictKind.POINT


def test_production_geo2_equality_row_is_a_proxy() -> None:
    table = load_fegley2023_gamma_table()["rows"]
    matched = [
        row
        for row in table
        if row["formula"] == "GeO2"
        and "g(SiO2)" in str(row["notes_as_printed"])
    ]
    assert len(matched) == 1
    answer = _resolve("GeO2", matched)
    assert answer.derivation["origin"] == "proxy_estimate"
    assert answer.derivation["source_row_id"] == matched[0]["source_row_id"]


def test_rows_without_a_stated_band_fall_through_to_the_homologue() -> None:
    published = [
        _row("Rb2O", row_id="a", B="-1000", notes="model one"),
        _row("Rb2O", row_id="b", B="-2000", notes="model two"),
        _row("K2O", row_id="k", B="-13400", notes="homologue bait"),
    ]
    answer = _resolve("Rb2O", published)
    assert answer.derivation["rung"] == 3
    assert answer.derivation["flag"] == "homologue"
    assert answer.derivation["homologue"] == "K2O"
    assert answer.derivation["gamma"] is not None
    assert answer.value is not None
    assert answer.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert answer.derivation["source_row_id"] == "k"

    proxies = [
        _row("V2O3", row_id="p1", B="-1", notes="set = Ti2O3"),
        _row("V2O3", row_id="p2", B="-2", notes="set = Fe2O3"),
    ]
    proxy_answer = _resolve("V2O3", proxies)
    assert proxy_answer.verdict is ActivityVerdictKind.UPPER_BOUND
    assert proxy_answer.derivation["flag"] == "henrian_gamma_unmeasured"
    assert proxy_answer.derivation["gamma"] == 1.0
    assert proxy_answer.derivation["rung"] == 4
    assert proxy_answer.value is not None
    assert _point_only(proxy_answer) is None


def test_covering_band_selects_that_row() -> None:
    rows = [
        _row("PbO", row_id="far", B="-100", band=[2000.0, 2200.0]),
        _row("PbO", row_id="cover", B="-5000", band=[1400.0, 1600.0]),
        _row("PbO", row_id="null-band", B="0", notes="no stated range"),
    ]
    answer = _resolve("PbO", rows, temperature_K=1500.0)
    assert answer.derivation["source_row_id"] == "cover"
    assert answer.derivation["flag"] == "published"
    assert answer.verdict is ActivityVerdictKind.POINT
    assert answer.derivation["gamma"] is not None
    assert answer.value is not None


def test_nearest_band_is_not_the_lowest_residual() -> None:
    # 1500 K. The nearer band is the more extreme coefficient. The farther
    # band is gamma = 1, which would win a "closest to ideal" residual.
    rows = [
        _row("PbO", row_id="near", B="-20000", band=[1800.0, 1900.0]),
        _row("PbO", row_id="far", B="0", band=[1000.0, 1050.0]),
    ]
    answer = _resolve("PbO", rows, temperature_K=1500.0)
    assert answer.derivation["source_row_id"] == "near"
    assert answer.derivation["flag"] == "extrapolated"
    assert answer.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert answer.derivation["gamma"] is not None
    assert answer.value is not None
    notice = answer.derivation["extrapolation_notice"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["temperature_K"] == [1800.0, 1900.0]


def test_tied_nearest_bands_fall_through() -> None:
    rows = [
        _row("Rb2O", row_id="low", B="-100", band=[1000.0, 1200.0]),
        _row("Rb2O", row_id="high", B="-9000", band=[1800.0, 2000.0]),
        _row("K2O", row_id="k", B="-13400", notes="homologue"),
    ]
    answer = _resolve("Rb2O", rows, temperature_K=1500.0)
    assert answer.derivation["rung"] == 3
    assert answer.derivation["homologue"] == "K2O"
    assert answer.derivation["source_row_id"] == "k"
    assert answer.derivation["gamma"] is not None


def test_li2o_without_a_band_follows_na2o() -> None:
    rows = [
        _row("Li2O", row_id="air", B="3973", notes="air point"),
        _row("Li2O", row_id="low-fo2", B="853.7", notes="reduced point"),
        _row("Na2O", row_id="na", B="-8000", notes="sodium fit"),
    ]
    answer = _resolve("Li2O", rows, temperature_K=1673.0)
    assert answer.derivation["rung"] == 3
    assert answer.derivation["flag"] == "homologue"
    assert answer.derivation["homologue"] == "Na2O"
    assert answer.derivation["source_row_id"] == "na"
    assert answer.derivation["gamma"] is not None
    assert answer.value is not None
    assert answer.verdict is not ActivityVerdictKind.UPPER_BOUND


def test_production_li2o_uses_its_nearest_stated_band() -> None:
    table = load_fegley2023_gamma_table()["rows"]
    banded = [
        row
        for row in table
        if row["formula"] == "Li2O" and row["validity_range_K"] is not None
    ]
    assert len(banded) == 1
    for temperature_K in (1500.0, 1673.0):
        answer = _resolve("Li2O", None, temperature_K=temperature_K)
        assert answer.derivation["source_row_id"] == banded[0]["source_row_id"]
        assert answer.derivation["flag"] == "extrapolated"
        assert answer.derivation["homologue"] is None
        assert answer.derivation["gamma"] is not None
        assert answer.value is not None


def test_one_published_row_wins_over_a_proxy() -> None:
    rows = [
        _row("Cs2O", row_id="proxy", B="-1", notes="set = g(Na2O)"),
        _row("Cs2O", row_id="published", B="-12144", notes="Bennour"),
    ]
    answer = _resolve("Cs2O", rows)
    assert answer.derivation["flag"] == "published"
    assert answer.derivation["origin"] == "published"
    assert answer.derivation["source_row_id"] == "published"
    assert answer.verdict is ActivityVerdictKind.POINT


def test_alkali_homologue_is_reached_before_unity() -> None:
    rows = [
        _row("K2O", row_id="k2o", B="-13400", notes="one published fit"),
        _row("Na2O", row_id="na2o", B="-8000", notes="one published fit"),
    ]
    followed = {
        "Rb2O": "K2O",
        "Cs2O": "K2O",
        "Li2O": "Na2O",
    }
    for formula, target in followed.items():
        answer = _resolve(formula, rows)
        target_answer = _resolve(target, rows)
        assert answer.derivation["rung"] == 3, formula
        assert answer.derivation["flag"] == "homologue", formula
        assert answer.derivation["homologue"] == target, formula
        assert answer.derivation["gamma"] == target_answer.derivation["gamma"]
        assert answer.derivation["gamma"] != 1.0
        assert answer.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
        assert answer.verdict is not ActivityVerdictKind.UPPER_BOUND
        assert answer.tier is ActivityTier.C
        assert answer.derivation["source_row_id"] == target_answer.derivation[
            "source_row_id"
        ]


def test_oxide_homologues_name_the_immediate_target() -> None:
    al = _row("Al2O3", row_id="al", B="-6253", notes="one fit")
    si = _row("SiO2", row_id="si", B="-100", notes="one fit")
    ga = _resolve("Ga2O3", [al])
    ge = _resolve("GeO2", [si])
    assert ga.derivation["rung"] == 3
    assert ga.derivation["homologue"] == "Al2O3"
    assert ga.derivation["gamma"] == _resolve("Al2O3", [al]).derivation["gamma"]
    assert ge.derivation["homologue"] == "SiO2"
    assert ge.derivation["rung"] == 3

    ga_proxy = _row(
        "Ga2O3", row_id="ga-proxy", B="-6314", notes="interpolated from Al2O3"
    )
    indium = _resolve("In2O3", [ga_proxy, al])
    assert indium.derivation["rung"] == 3
    assert indium.derivation["homologue"] == "Ga2O3"
    assert indium.derivation["target_rung"] == 2
    assert indium.derivation["target_flag"] == "proxy_estimate"
    assert indium.derivation["gamma"] == _resolve(
        "Ga2O3", [ga_proxy, al]
    ).derivation["gamma"]

    chained = _resolve("In2O3", [al])
    assert chained.derivation["homologue"] == "Ga2O3"
    assert chained.derivation["rung"] == 3
    assert chained.derivation["target_rung"] == 3
    assert chained.derivation["gamma"] == _resolve("Al2O3", [al]).derivation["gamma"]
    assert chained.derivation["gamma"] != 1.0


def test_own_rung_2_row_blocks_the_homologue() -> None:
    rows = [
        _row("Rb2O", row_id="own", B="-100", notes="measured"),
        _row("K2O", row_id="k", B="-13400", notes="measured"),
    ]
    answer = _resolve("Rb2O", rows)
    assert answer.derivation["rung"] == 2
    assert answer.derivation["homologue"] is None
    assert answer.derivation["source_row_id"] == "own"


def test_missing_homologue_row_is_the_unity_upper_bound() -> None:
    answer = _resolve("Rb2O", [], mole_fraction=1e-6)
    assert answer.verdict is ActivityVerdictKind.UPPER_BOUND
    assert answer.derivation["rung"] == 4
    assert answer.derivation["flag"] == "henrian_gamma_unmeasured"
    assert answer.derivation["gamma"] == 1.0


def test_upper_bound_is_never_consumed_as_a_point() -> None:
    answer = _resolve("B2O3", [], mole_fraction=1e-6)
    assert answer.verdict is ActivityVerdictKind.UPPER_BOUND
    assert answer.verdict is not ActivityVerdictKind.POINT
    assert answer.bound_direction is BoundDirection.UPPER
    assert answer.reason == "henrian_gamma_unmeasured"
    assert answer.derivation["rung"] == 4
    assert answer.derivation["flag"] == "henrian_gamma_unmeasured"
    assert answer.derivation["gamma"] == 1.0
    assert answer.value is not None
    assert answer.value.hex() == (1e-6).hex()
    assert answer.may_certify() is False
    assert answer.as_pressure_activity() == answer.value
    assert _point_only(answer) is None
    assert (
        validation_row_may_certify(validation_status="validated", activity=answer)
        is False
    )


def test_invalid_homologue_row_refuses_instead_of_unity() -> None:
    rows = [_row("K2O", row_id="bad", A="not-a-number", B="1")]
    answer = _resolve("Rb2O", rows)
    assert answer.verdict is ActivityVerdictKind.REFUSAL
    assert answer.refusal_code is ActivityRefusalCode.MISSING_EVIDENCE
    assert answer.value is None
    assert answer.derivation.get("rung") is None


def test_missing_or_invalid_temperature_refuses() -> None:
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id="SnO",
        standard_state=_SS,
        activity_model="source_reaction_activity",
        allow_henrian_upper_bound=True,
    )
    missing = provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        mole_fraction=1e-6,
    )
    assert missing.verdict is ActivityVerdictKind.REFUSAL
    assert missing.refusal_code is ActivityRefusalCode.MISSING_EVIDENCE
    for temperature_K in (0.0, -10.0, float("nan")):
        answer = _resolve("SnO", [], temperature_K=temperature_K)
        assert answer.verdict is ActivityVerdictKind.REFUSAL, temperature_K


def test_out_of_band_fit_extrapolates_with_the_certified_band() -> None:
    table = load_fegley2023_gamma_table()["rows"]
    for formula in ("SnO", "B2O3"):
        matched = [row for row in table if row["formula"] == formula]
        assert len(matched) == 1, formula
        band = matched[0]["validity_range_K"]
        assert band is not None
        low, high = band
        below = _resolve(formula, None, temperature_K=low - 1.0)
        assert below.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
        assert below.verdict is not ActivityVerdictKind.REFUSAL
        notice = below.derivation["extrapolation_notice"]
        assert notice["authority_level"] == "extrapolated"
        assert notice["reason"]
        assert notice["certified_band"]["temperature_K"] == [float(low), float(high)]
        assert below.derivation["flag"] == "extrapolated"
        assert below.derivation["rung"] == 2
        assert below.derivation["source_row_id"] == matched[0]["source_row_id"]
        inside = _resolve(formula, None, temperature_K=(low + high) / 2.0)
        assert inside.verdict is not ActivityVerdictKind.POINT
        assert inside.derivation["flag"] == "standard_state_basis_unestablished"
        assert (
            inside.derivation["standard_state_as_printed"]
            == "not stated in Table 2 row"
        )
        assert inside.derivation["extrapolation_notice"] is None
        assert inside.derivation["gamma"] is not None


def test_activity_basis_derives_the_single_cation_activity() -> None:
    rows = [
        _row(
            "Ga2O3",
            row_id="ga-proxy",
            B="-6314",
            notes="interpolated from Al2O3",
        )
    ]
    parent = _resolve("Ga2O3", rows)
    alias = _resolve("GaO1.5", rows)
    assert alias.component_id == "GaO1.5"
    assert alias.derivation["coefficient_formula"] == "Ga2O3"
    assert alias.derivation["row_formula"] == "Ga2O3"
    assert alias.derivation["source_row_id"] == parent.derivation["source_row_id"]
    assert alias.derivation["flag"] == parent.derivation["flag"]
    assert alias.derivation["gamma"] != parent.derivation["gamma"]
    converted = pure_liquid_reference_coefficient(
        "Ga2O3", "GaO1.5", parent.derivation["gamma"]
    )
    assert converted is not None
    assert alias.derivation["gamma"] == converted
    assert parent.value is not None and alias.value is not None
    assert parent.value == pytest.approx(alias.value**2)
    assert alias.value != pytest.approx(alias.derivation["gamma"] * 1e-6)
    assert alias.derivation["candidate_rows"][0]["gamma"] == parent.derivation["gamma"]


def test_parent_activity_is_the_single_cation_activity_to_the_cation_count() -> None:
    """Dilute melt {In2O3: 1e-6, SiO2: 1} at 1923 K. Both spellings agree."""

    inventory = {"In2O3": 1.0e-6, "SiO2": 1.0}
    mole_fraction = molecular_mole_fractions(inventory)["In2O3"]
    parent = _resolve(
        "In2O3", None, temperature_K=1923.0, mole_fraction=mole_fraction
    )
    single = _resolve(
        "InO1.5", None, temperature_K=1923.0, mole_fraction=mole_fraction
    )
    assert parent.value is not None and single.value is not None
    assert parent.value == pytest.approx(single.value**2)
    assert parent.value == pytest.approx(
        parent.derivation["gamma"] * mole_fraction
    )
    cation = single_cation_mole_fractions(inventory)["In2O3"]
    assert parent.value != pytest.approx(parent.derivation["gamma"] * cation)


def test_ino15_reproduces_the_published_anchor_at_1923_k() -> None:
    # Wood and Wade 2013, cited by Fegley 2023 Group 13: 0.02 at 1923 K.
    parent = _resolve("In2O3", None, temperature_K=1923.0, mole_fraction=1.0)
    single = _resolve("InO1.5", None, temperature_K=1923.0, mole_fraction=1.0)
    assert "aaa9829cdabd" in str(parent.derivation["source_row_id"])
    assert single.derivation["source_row_id"] == parent.derivation["source_row_id"]
    assert single.derivation["coefficient_formula"] == "In2O3"
    assert abs(single.derivation["gamma"] - 0.02) < 5e-5
    assert abs(parent.derivation["gamma"] - 0.02) > 1e-3
    assert single.derivation["candidate_rows"][0]["gamma"] == parent.derivation["gamma"]
    assert single.value == single.derivation["gamma"]
    assert single.verdict is not ActivityVerdictKind.POINT
    assert parent.verdict is not ActivityVerdictKind.POINT
    assert single.derivation["flag"] == "standard_state_basis_unestablished"
    assert parent.derivation["flag"] == "standard_state_basis_unestablished"


def test_unstated_standard_state_is_not_a_point_on_solid_or_liquid() -> None:
    liquid = _resolve("In2O3", None, temperature_K=1923.0)
    solid = _resolve(
        "In2O3",
        None,
        temperature_K=1923.0,
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="solid",
            reference_pressure_bar=1.0,
        ),
    )
    assert liquid.verdict is not ActivityVerdictKind.POINT
    assert solid.verdict is not ActivityVerdictKind.POINT
    assert liquid.derivation["gamma"] == solid.derivation["gamma"]
    assert liquid.derivation["target_phase"] == "liquid"
    assert solid.derivation["target_phase"] == "solid"
    assert (
        liquid.derivation["standard_state_as_printed"]
        == "not stated in Table 2 row"
    )


def test_stated_basis_distinguishes_solid_from_liquid() -> None:
    row = _row(
        "SnO",
        row_id="typed-liquid",
        B="-90.4",
        notes="measured",
        stated_convention="raoultian_pure_endmember",
        stated_phase="liquid",
    )
    liquid = _resolve("SnO", [row])
    token_l = _resolve(
        "SnO",
        [row],
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="l",
            reference_pressure_bar=1.0,
        ),
    )
    solid = _resolve(
        "SnO",
        [row],
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="solid",
            reference_pressure_bar=1.0,
        ),
    )
    embedded = _resolve(
        "SnO",
        [row],
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="liquid-solid",
            reference_pressure_bar=1.0,
        ),
    )
    assert liquid.verdict is ActivityVerdictKind.POINT
    assert token_l.verdict is ActivityVerdictKind.POINT
    assert solid.verdict is not ActivityVerdictKind.POINT
    assert solid.derivation["flag"] == "standard_state_basis_unestablished"
    assert embedded.verdict is not ActivityVerdictKind.POINT
    assert liquid.derivation["gamma"] == solid.derivation["gamma"]


def test_printed_liquid_phrase_does_not_establish_the_convention() -> None:
    row = _row(
        "SnO",
        row_id="printed-liquid",
        B="-90.4",
        notes="measured",
        standard_state_as_printed="liquid standard state",
    )
    answer = _resolve("SnO", [row])
    assert answer.verdict is not ActivityVerdictKind.POINT
    assert answer.derivation["flag"] == "standard_state_basis_unestablished"
    assert answer.derivation["gamma"] is not None


def test_unestablished_component_basis_does_not_reuse_the_row_gamma(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "simulator.vapour_rail.activity.pure_liquid_reference_coefficient",
        lambda *_args, **_kwargs: None,
    )
    rows = [
        _row("In2O3", row_id="in-row", B="-6534.2", notes="regular solution"),
        _row("Ga2O3", row_id="ga-row", B="-100", notes="measured"),
    ]
    parent = _resolve("In2O3", rows)
    single = _resolve("InO1.5", rows)
    assert parent.derivation["rung"] == 2
    assert single.derivation["rung"] == 3
    assert single.derivation["homologue"] == "Ga2O3"
    assert single.derivation["gamma"] != parent.derivation["gamma"]
    assert single.derivation["gamma"] is not None
    assert single.verdict is not ActivityVerdictKind.POINT


def test_cuo05_converts_a_published_parent_row() -> None:
    rows = [_row("Cu2O", row_id="cu", B="-3000", notes="measured")]
    parent = _resolve("Cu2O", rows)
    single = _resolve("CuO0.5", rows)
    converted = pure_liquid_reference_coefficient(
        "Cu2O", "CuO0.5", parent.derivation["gamma"]
    )
    assert converted is not None
    assert single.derivation["gamma"] == converted
    assert single.derivation["gamma"] != parent.derivation["gamma"]
    assert single.derivation["coefficient_formula"] == "Cu2O"
    assert single.derivation["source_row_id"] == "cu"


def test_missing_cation_relationship_is_not_a_conversion() -> None:
    assert pure_liquid_reference_coefficient("As2O3", "AsO1.5", 0.25) is None
    assert pure_liquid_reference_coefficient("In2O3", "GaO1.5", 0.25) is None
    assert pure_liquid_reference_coefficient("SnO", "SnO", 0.3) == 0.3
    # Inverse of the 0.02 anchor: the parent coefficient converts back.
    parent = pure_liquid_reference_coefficient("InO1.5", "In2O3", 0.02)
    assert parent is not None
    restored = pure_liquid_reference_coefficient("In2O3", "InO1.5", parent)
    assert restored is not None
    assert abs(restored - 0.02) < 1e-12


def test_phase_tag_resolves_on_the_ledger_key() -> None:
    rows = [
        _row(
            "SnO",
            row_id="sno",
            A="0.47",
            B="-90.4",
            band=[1800.0, 2200.0],
            notes="measured",
        )
    ]
    answer = _resolve("SnO(l)", rows, temperature_K=1500.0)
    assert answer.component_id == "SnO(l)"
    assert answer.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert answer.derivation["flag"] == "extrapolated"
    assert answer.derivation["source_row_id"] == "sno"
    notice = answer.derivation["extrapolation_notice"]
    assert notice["certified_band"]["temperature_K"] == [1800.0, 2200.0]


def test_production_trace_parents_have_an_executable_activity() -> None:
    for formula in trace_parent_formulas():
        answer = _resolve(formula, None, temperature_K=1673.0)
        assert answer.verdict is not ActivityVerdictKind.REFUSAL, formula
        assert answer.derivation["gamma"] is not None, formula
        assert answer.value is not None, formula
        assert answer.derivation["rung"] in {2, 3, 4}, formula
        assert "flag" in answer.derivation
