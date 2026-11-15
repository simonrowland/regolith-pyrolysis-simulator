"""Buffer-relative fO2 conversion uses the shared oxygen-buffer owner."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import NoticeKind, ValueKind
from simulator.battery.migrate import (
    PrintedOxygenFacts,
    _delta_iw_ideal_ratio_notice,
    _log_fO2_to_pa,
    _classify_buffer_phrase,
    _printed_fo2_interval,
    _point_condition_from_plain,
    printed_oxygen_decision,
)
from simulator.battery.records import Located, Value
from simulator.state_types import State
from simulator.oxygen_buffers import (
    frost_buffer_log10_fO2_bar,
)

ROOT = Path(__file__).resolve().parents[2]


def _decision(raw: dict, *, temperature_K: int = 1000, **kwargs):
    return printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item=raw,
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal(temperature_K),
        **kwargs,
    )


def _source_mapping_with_key(source_id: str, key: str) -> Mapping:
    source = yaml.safe_load(
        (ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if key in value:
                return value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    raise AssertionError(f"{source_id} has no source mapping with {key!r}")


def test_real_air_condition_is_retained_as_degenerate_interval() -> None:
    source = yaml.safe_load(
        (
            ROOT
            / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"
        ).read_text(encoding="utf-8")
    )
    pending = [source]
    air_text = None
    while pending:
        value = pending.pop()
        if isinstance(value, str) and "equilibrated in air" in value.casefold():
            air_text = value
            break
        if isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert air_text is not None
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"atmosphere_as_published": air_text},
        point_conditions=None,
        scope="row",
        total_pressure_pa=Decimal("100000"),
    )

    assert decision is not None and decision.kind == "interval"
    condition = decision.interval_condition
    assert condition is not None and condition.state.is_value
    value = condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.INTERVAL
    assert value.interval_low == value.interval_high
    assert abs(value.interval_low - Decimal("-0.6788989008021774")) < Decimal("1e-15")
    assert "pO2=0.20946*P_total" in condition.inference.relation
    assert "degenerate_air_interval" in decision.interval_notice_reason
    assert decision.state.is_unknown


@pytest.mark.parametrize(
    ("pressure_pa", "reason_part"),
    (
        (None, "missing input"),
        (Decimal("0"), "invalid total pressure"),
        (Decimal("-1"), "invalid total pressure"),
        (Decimal("NaN"), "invalid total pressure"),
    ),
)
def test_ambient_air_without_valid_total_pressure_stays_typed_unknown(
    pressure_pa: Decimal | None, reason_part: str
) -> None:
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"atmosphere_as_published": "equilibrated in ambient air"},
        point_conditions=None,
        scope="row",
        total_pressure_pa=pressure_pa,
    )

    assert decision is not None and decision.kind == "interval"
    assert decision.interval_condition is not None
    assert decision.interval_condition.state.is_unknown
    assert reason_part in decision.interval_condition.state.reason
    assert decision.state.is_unknown
    assert reason_part in decision.state.reason


def test_numeric_buffer_distances_are_exact_point_offsets() -> None:
    for field, offset, phrase in (
        ("delta_IW", "-2.0", "2 log units below IW"),
        ("delta_NNO", "+5.0", "5 log units above NNO"),
    ):
        expected = _decision(
            {field: offset, "oxygen_buffer_as_published": "Frost (1991)"}
        )
        decision = _decision(
            {
                "fO2_control": phrase,
                "oxygen_buffer_as_published": "Frost (1991)",
            }
        )

        assert expected is not None and expected.state.is_value
        assert decision is not None and decision.kind == "buffer"
        assert decision.state.is_value
        assert decision.state.value == expected.state.value


@pytest.mark.parametrize(
    ("relation", "calibration", "operator", "threshold"),
    (
        ("below IW+1", "IW; Frost (1991)", "<", Decimal("1")),
        ("above NNO-2", "NNO; Frost (1991)", ">", Decimal("-2")),
    ),
)
@pytest.mark.parametrize(
    ("temperature_K", "pressure_pa"),
    (
        (None, None),
        (Decimal("1000"), None),
        (None, Decimal("100000")),
        (Decimal("1000"), Decimal("100000")),
    ),
)
def test_buffer_inequality_preserves_printed_offset_frame(
    relation: str,
    calibration: str,
    operator: str,
    threshold: Decimal,
    temperature_K: Decimal | None,
    pressure_pa: Decimal | None,
) -> None:
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={
            "oxygen_buffer_as_published": calibration,
            "oxygen_fugacity_buffer_relation": relation,
        },
        point_conditions=None,
        scope="row",
        run_temperature_K=temperature_K,
        total_pressure_pa=pressure_pa,
    )

    assert decision is not None and decision.interval_condition is not None
    condition = decision.interval_condition
    assert condition.state.is_value
    value = condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.BOUND
    assert value.bound_operator == operator
    assert value.bound_value == threshold
    assert condition.inference.output_unit == (
        "log10(IW)" if operator == "<" else "log10(NNO)"
    )
    assert "calibration=" not in condition.inference.relation


def test_real_below_iw_numeric_offset_does_not_become_an_inequality() -> None:
    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/llnl-2021-cai-aerodynamic-levitation.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    relation = None
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if "oxygen_fugacity_buffer_relation" in value:
                relation = value["oxygen_fugacity_buffer_relation"]
                break
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert isinstance(relation, str)
    family, offset, _printed, is_prose = _classify_buffer_phrase(relation)
    assert (family, offset, is_prose) == ("IW", Decimal("-0.5"), False)
    raw_item = {"oxygen_fugacity_buffer_relation": relation}
    retained_interval = _printed_fo2_interval(
        raw_item,
        None,
        total_pressure_pa=None,
    )
    assert retained_interval is None

    printed_log = Decimal("-9.1")
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(
            log_fO2=Located(State.of(printed_log)),
            log_fO2_reference_Pa=Decimal("101325"),
        ),
        raw_item={"oxygen_fugacity_buffer_relation": relation},
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal("1873"),
    )

    assert decision is not None and decision.kind == "log"
    assert decision.state.value == _log_fO2_to_pa(printed_log, Decimal("101325"))


def test_real_explicit_fo2_inequality_stays_a_typed_bound() -> None:
    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/volovetskii-2012-iron-redox.yaml")
        .read_text(encoding="utf-8")
    )
    pending = [source]
    row = None
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            conditions = value.get("point_conditions")
            if isinstance(conditions, Mapping):
                oxygen = conditions.get("fO2_log")
                state = oxygen.get("state") if isinstance(oxygen, Mapping) else None
                typed = state.get("value") if isinstance(state, Mapping) else None
                if isinstance(typed, Mapping) and typed.get("kind") == "bound":
                    row = value
                    break
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert row is not None
    raw_condition = row["point_conditions"]["fO2_log"]
    condition = _point_condition_from_plain(raw_condition, key="fO2_log")
    decision = printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item={"point_conditions": {"fO2_log": raw_condition}},
        point_conditions={"fO2_log": condition},
        scope="row",
    )

    assert decision is not None and decision.interval_condition is not None
    value = decision.interval_condition.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.BOUND
    assert value.bound_operator == "<"
    assert value.bound_value == Decimal("-11.5")


def test_printed_calibration_is_used_exactly() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "IW; Frost (1991)",
        }
    )

    assert decision is not None and decision.state.is_value
    expected_log = Decimal(str(frost_buffer_log10_fO2_bar("IW", 1000.0))) + Decimal("1.0")
    assert decision.state.value == _log_fO2_to_pa(expected_log)
    assert "calibration=frost_1991_table_1" in decision.notice_reason
    assert "printed calibration used exactly" in decision.notice_reason
    assert "pressure=1 bar" in decision.notice_reason
    assert "pressure correction=none at or below 1 bar" in decision.notice_reason
    assert decision.notice_band == "not reported (printed calibration used exactly)"


def test_printed_absolute_log_wins_over_buffer_relative_offset() -> None:
    facts = PrintedOxygenFacts(
        log_fO2=Located(State.of(Decimal("-9"))),
    )
    decision = printed_oxygen_decision(
        facts,
        raw_item={"delta_IW": "+2.0", "oxygen_buffer_as_published": "IW"},
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal(1000),
    )

    assert decision is not None and decision.kind == "log"
    assert decision.state.value == _log_fO2_to_pa(Decimal("-9"))
    assert decision.notice_reason is None


def test_canonical_iw_conversion_carries_authority_and_calibration_band() -> None:
    decision = _decision({"delta_IW": "+1.0"})

    assert decision is not None and decision.state.is_value
    # O'Neill (1988) eq. 11 first segment gives μ°O2 = -605812 + 1366.718T
    # - 182.7955T ln(T) + 0.10359T² J/mol. At 1000 K, μ°/(R T ln 10) is
    # -20.799980169 log10(fO2/bar), then the printed +1.0 gives this literal.
    # J/mol divided by (J mol-1 K-1)K is dimensionless; ln(10) changes bases.
    expected_log = Decimal("-19.799980169")
    assert abs(decision.state.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert "buffer=IW" in decision.notice_reason
    assert "offset_as_printed=+1.0" in decision.notice_reason
    assert "calibration=oneill_1988_ammin_73_eq11" in decision.notice_reason
    assert "authority=primary_EMF" in decision.notice_reason
    assert "T=1000 K" in decision.notice_reason
    assert "band=±" in decision.notice_reason
    assert decision.notice_band.startswith("±")


def test_short_diw_field_uses_the_iw_calibration() -> None:
    decision = _decision({"dIW": "-0.5"})

    assert decision is not None and decision.state.is_value
    # The same O'Neill (1988) eq. 11 1000 K value, -20.799980169 log10(bar),
    # plus the source's printed -0.5 offset gives -21.299980169 log10(bar).
    expected_log = Decimal("-21.299980169")
    assert abs(decision.state.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert "buffer=IW" in decision.notice_reason
    assert "offset_as_printed=-0.5" in decision.notice_reason


def test_nno_uses_the_interim_compilation_and_names_its_authority() -> None:
    decision = _decision({"delta_NNO": "+0.0"})

    assert decision is not None and decision.state.is_value
    assert decision.notice_kind is NoticeKind.DERIVATION_USES_COMPILATION
    assert "calibration=frost_1991_nno_interim_compilation" in decision.notice_reason
    assert "authority=compilation_calibration" in decision.notice_reason
    assert "band=not available" in decision.notice_reason


def test_buffer_without_cited_canonical_calibration_refuses() -> None:
    decision = _decision({"delta_Mo-MoO2": "+1.0"})

    assert decision is not None and decision.state.is_unknown
    assert "no cited canonical calibration" in decision.state.reason
    assert "fO2_Pa assigned" in decision.state.reason


def test_out_of_range_iw_is_predicted_and_flagged_with_phase_boundary() -> None:
    decision = _decision({"delta_IW": "+1.0"}, temperature_K=1800)

    assert decision is not None and decision.state.is_value
    assert decision.notice_kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert "predict_and_flag_outside_range_833_1450_K" in decision.notice_reason
    assert "phase_boundary_extrapolation_above_1650_K_FeO_melting" in decision.notice_reason
    assert "calibration_range=833–1450 K" in decision.notice_reason
    assert "±" in decision.notice_band
    assert "outside calibration range 833–1450 K" in decision.notice_band


def test_ideal_ratio_definition_gets_the_named_notice() -> None:
    quoted_definition = "ΔIW = 2 log(x_FeO^sil / x_Fe^met)"
    assert _delta_iw_ideal_ratio_notice(quoted_definition) == (
        "delta_iw_from_ideal_feo_fe_ratio; source definition: "
        + quoted_definition
    )


def test_kems_delta_iw_is_a_printed_buffer_reference_not_an_ideal_ratio() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "O’Neill and Pownceby, 1993",
        },
        source_id="kems-137-bischof-2023",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_reference=iron_wustite_buffer" in decision.notice_reason
    assert "IW = iron-wüstite buffer" in decision.notice_reason
    assert "delta_iw_from_ideal_feo_fe_ratio" not in decision.notice_reason

    without_calibration = _decision(
        {"delta_IW": "+1.0"},
        source_id="kems-137-bischof-2023",
    )
    assert without_calibration is not None and without_calibration.state.is_unknown
    assert "no cited buffer calibration" in without_calibration.state.reason


def test_unresolved_year_citation_does_not_suppress_unknown_buffer_refusal() -> None:
    decision = _decision(
        {
            "delta_IW": "+1.0",
            "oxygen_buffer_as_published": "Myers and Eugster, 1983",
        }
    )

    assert decision is not None and decision.state.is_unknown
    assert "conflicting offsets" in decision.state.reason
    assert "missing input; no fO2_Pa assigned" in decision.state.reason
    assert "oneill_1988" not in decision.state.reason


def test_mavrogenes_activity_correction_names_the_holzheid_gamma_model() -> None:
    decision = _decision(
        {"log_fO2_as_delta_IW": "-1.56"},
        source_id="mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_activity_corrected_gamma_model=Holzheid1997_FeO_gamma_1.7" in decision.notice_reason


def test_piston_cylinder_pressure_pins_frost_iw_delta_v_term() -> None:
    one_bar = _decision({"delta_IW": "+1.0"}, run_pressure_bar=Decimal("1"))
    high_pressure = _decision(
        {"delta_IW": "+1.0"}, run_pressure_bar=Decimal("10000")
    )

    assert one_bar is not None and high_pressure is not None
    # Frost (1991), Table 1: log10(fO2/bar)=A/T+B+C(P-1)/T. The pressure
    # difference is exactly C(10000-1)/T = 0.055*9999/1000 = 0.549945 dex;
    # (K/bar)*bar/K is dimensionless. Compare logs so Pa/bar cancels.
    expected_delta = Decimal("0.055") * Decimal("9999") / Decimal("1000")
    actual_delta = high_pressure.state.value.log10() - one_bar.state.value.log10()
    assert abs(actual_delta - expected_delta) < Decimal("1e-25")
    assert "pressure correction=Frost 1991 molar-volume ΔV term" in high_pressure.notice_reason
