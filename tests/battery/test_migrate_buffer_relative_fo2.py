"""Buffer-relative fO2 conversion uses the shared oxygen-buffer owner."""

from __future__ import annotations

from decimal import Decimal

import pytest

from simulator.battery.enums import NoticeKind
from simulator.battery.migrate import (
    PrintedOxygenFacts,
    _delta_iw_ideal_ratio_notice,
    _log_fO2_to_pa,
    printed_oxygen_decision,
)
from simulator.battery.records import Located
from simulator.state_types import State
from simulator.oxygen_buffers import (
    frost_buffer_log10_fO2_bar,
)


def _decision(raw: dict, *, temperature_K: int = 1000, **kwargs):
    return printed_oxygen_decision(
        PrintedOxygenFacts(),
        raw_item=raw,
        point_conditions=None,
        scope="row",
        run_temperature_K=Decimal(temperature_K),
        **kwargs,
    )


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
