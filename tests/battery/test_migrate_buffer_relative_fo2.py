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
    oneill_iw_log10_fO2_bar,
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
    expected_log = Decimal(str(oneill_iw_log10_fO2_bar(1000.0))) + Decimal("1.0")
    assert decision.state.value == _log_fO2_to_pa(expected_log)
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
    expected_log = Decimal(str(oneill_iw_log10_fO2_bar(1000.0))) - Decimal("0.5")
    assert decision.state.value == _log_fO2_to_pa(expected_log)
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


def test_iw_above_iron_melting_carries_both_phase_boundary_flags() -> None:
    decision = _decision({"delta_IW": "+1.0"}, temperature_K=1812)

    assert decision is not None and decision.state.is_value
    assert "phase_boundary_extrapolation_above_1650_K_FeO_melting" in decision.notice_reason
    assert "phase_boundary_extrapolation_above_1811_K_Fe_melting" in decision.notice_reason


def test_ideal_ratio_definition_gets_the_named_notice() -> None:
    quoted_definition = "ΔIW = 2 log(x_FeO^sil / x_Fe^met)"
    assert _delta_iw_ideal_ratio_notice(quoted_definition) == (
        "delta_iw_from_ideal_feo_fe_ratio; source definition: "
        + quoted_definition
    )


def test_kems_delta_iw_is_a_printed_buffer_reference_not_an_ideal_ratio() -> None:
    decision = _decision(
        {"delta_IW": "+1.0"},
        source_id="kems-137-bischof-2023",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_reference=iron_wustite_buffer" in decision.notice_reason
    assert "IW = iron-wüstite buffer" in decision.notice_reason
    assert "delta_iw_from_ideal_feo_fe_ratio" not in decision.notice_reason


def test_mavrogenes_activity_correction_names_the_holzheid_gamma_model() -> None:
    decision = _decision(
        {"log_fO2_as_delta_IW": "-1.56"},
        source_id="mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas",
    )

    assert decision is not None and decision.state.is_value
    assert "delta_iw_activity_corrected_gamma_model=Holzheid1997_FeO_gamma_1.7" in decision.notice_reason


def test_piston_cylinder_pressure_uses_frost_delta_v_correction() -> None:
    one_bar = _decision({"delta_IW": "+1.0"}, run_pressure_bar=Decimal("1"))
    high_pressure = _decision(
        {"delta_IW": "+1.0"}, run_pressure_bar=Decimal("10000")
    )

    assert one_bar is not None and high_pressure is not None
    assert one_bar.state.value != high_pressure.state.value
    assert "pressure correction=Frost 1991 molar-volume ΔV term" in high_pressure.notice_reason
