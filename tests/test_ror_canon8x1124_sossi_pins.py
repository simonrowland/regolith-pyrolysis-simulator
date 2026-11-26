"""Internal-analytical Sossi and Aithala pins.

Scores only Engine.INTERNAL_ANALYTICAL. It does not start MAGEMin.
PINS is the studio table from request dadfe71b1b764b979eea781fe0af3225:
numeric value, status, eligibility, policy ids, notice kinds, and refusal.
"""

from decimal import Decimal

from simulator.battery.enums import Engine, NoticeKind, Rail
from simulator.battery.score import notice_backlog, score_store
from tests.battery.test_redox_extract_scoring import (
    _printed_fraction_sigma_r,
    _scale_unknown_oxygen,
    _sossi_aithala_redox_context,
)

# Studio table, internal-analytical only. sossi_2020_per_f_o2_1_xanes is
# the printed 1 bar pure-O2 end member. The twelve Sossi rows map their printed
# analytical_method "Fe K-edge XANES" to XANES_SPECTROSCOPY, so they carry no
# DP-106 method_unstated notice. The six Aithala rows and numeric pins are
# unaffected.
_EXCLUSIONS = (
    "source_relation_independent_complete_ancestry",
    "no_blocking_qualification",
    "not_flagged_stratum",
)
PINS: dict[str, tuple[str, str, bool, tuple[str, ...], tuple[str, ...], str]] = {
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes": (
        "-0.0346755650927675", "match", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1900_30_xanes": (
        "-0.07098292973815551", "mismatch", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_10_xanes": (
        "-0.12820440625089824", "mismatch", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_120_xanes": (
        "-0.15023002859917678", "mismatch", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_30_xanes": (
        "-0.18900293643035992", "mismatch", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2100_30_xanes": (
        "-0.0894917109147724", "mismatch", False, (),
        ("total_iron_reported_as_feo", "admission_defaulted", "out_of_certified_band", "input_omitted"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_10_xanes": (
        "-0.015048906919944335", "match", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "composition_projected", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_11_xanes": (
        "0.10013889718897892", "match", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_12_xanes": (
        "0.0697125628667097", "match", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes": (
        "-0.19443092435728185", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_2_xanes": (
        "-0.36461222518009667", "mismatch", False, (),
        ("total_iron_reported_as_feo", "input_omitted", "admission_defaulted", "engine_model_caveat", "input_omitted", "composition_projected", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_3_xanes": (
        "-0.32575700722591505", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_6_xanes": (
        "-0.2506658792245533", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "composition_projected", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_7_xanes": (
        "-0.30186470025058215", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_8_xanes": (
        "-0.22725532524443506", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_9_xanes": (
        "-0.22012641445601028", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_ts1_xanes": (
        "-0.3506159515474627", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "composition_projected", "out_of_certified_band"),
        "none",
    ),
    "sossi-2020-magma-ocean-redox::sossi_2020_per_ts2_xanes": (
        "-0.2525386811839257", "mismatch", False, (),
        ("total_iron_reported_as_feo", "fo2_frame_from_printed_atmosphere", "admission_defaulted", "engine_model_caveat", "input_omitted", "composition_projected", "out_of_certified_band"),
        "none",
    ),
}


def test_internal_analytical_sossi_aithala_unaffected_rows():
    context, observations = _sossi_aithala_redox_context()
    residuals, _metadata = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )
    by_reference = {residual.reference: residual for residual in residuals}
    assert set(by_reference) == set(observations)
    assert len(by_reference) == 18

    boundary = []
    unknown = []
    observed = {}
    for reference in sorted(observations):
        residual = by_reference[reference]
        observation = observations[reference]
        assert residual.numeric is not None
        assert residual.numeric.value.is_finite()
        assert tuple(residual.exclusions) == _EXCLUSIONS
        fraction = dict(observation.derivation.parameters)[
            "original_Fe3_over_FeT"
        ].state.value
        sigma_r = _printed_fraction_sigma_r(observation)
        assert isinstance(fraction, Decimal)
        assert isinstance(sigma_r, Decimal)
        crosses = (
            Decimal(2) * sigma_r >= fraction
            or Decimal(2) * sigma_r >= (Decimal(1) - fraction)
        )
        if observation.source_id == "sossi-2020-magma-ocean-redox" and crosses:
            boundary.append(reference)
        policies = tuple(
            notice.policy.id
            for notice in residual.notices
            if notice.policy is not None
        )
        kinds = tuple(notice.kind.value for notice in residual.notices)
        refusal = (
            "none"
            if residual.refusal is None
            else str(residual.refusal.detail.get("reason"))
        )
        observed[reference] = (
            format(residual.numeric.value, "f"),
            residual.status.value,
            residual.score_eligible,
            policies,
            kinds,
            refusal,
        )
        if _scale_unknown_oxygen(observation):
            unknown.append(reference)
            assert "DP-010" in policies
            assert not residual.score_eligible
        else:
            assert "DP-010" not in policies

    assert len(boundary) == 2
    assert any(reference.endswith("sossi_2020_per_f_o2_11_xanes") for reference in boundary)
    assert any(reference.endswith("sossi_2020_per_f_o2_12_xanes") for reference in boundary)
    assert unknown == []
    backlog = notice_backlog(residuals)
    counted = backlog.get(NoticeKind.ASSUMED_FO2.value, 0)
    assert counted == sum(
        1
        for residual in residuals
        if any(
            notice.kind is NoticeKind.ASSUMED_FO2
            and notice.policy is not None
            and notice.policy.id == "DP-010"
            for notice in residual.notices
        )
    )
    assert observed == PINS
