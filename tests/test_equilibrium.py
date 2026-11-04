"""Guards and regressions for simulator.equilibrium."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import simulator.equilibrium as equilibrium_module
from simulator.core import (
    PyrolysisSimulator,
    _MeltRedoxLiquidusFloorFallback,
    _RESOLVE_MELT_REDOX_GATE_AUTHORITY,
)
from simulator.equilibrium import (
    EquilibriumMixin,
    _internal_analytical_control_refusal,
)
from simulator.evaporation import (
    _KRESS91_FLOOR_OPEN_SIDE_C,
    _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
)
from simulator.fe_redox import (
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    LiquidFeODomain,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.silent_zero import (
    CATEGORY_MARK,
    CATEGORY_PROVEN_ZERO,
    CATEGORY_REFUSE,
    ZeroBecause,
)


def _minimal_sim(*, temperature_C, p_total_mbar):
    return SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=temperature_C,
            p_total_mbar=p_total_mbar,
        )
    )


def _typed_omission_sim(*, metals=None, oxide_vapors=None, species=None):
    metals = {} if metals is None else metals
    return SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=726.85,
            p_total_mbar=1.0,
            oxygen_reservoir=None,
            melt_fO2_log=-9.0,
            fO2_log=-9.0,
            composition_wt_pct=lambda: {},
        ),
        vapor_pressures={
            "metals": metals,
            "oxide_vapors": oxide_vapors or {},
        },
        _ELLINGHAM_THERMO=(tuple(metals) if species is None else tuple(species)),
        _headspace_transport_pO2_bar=lambda: 1e-12,
        _oxygen_numerical_floor_bar=lambda: 1e-12,
        atom_ledger=None,
    )


def _run_typed_omission_sim(sim, monkeypatch, *, a_feo=1.0):
    monkeypatch.setattr(
        equilibrium_module,
        "calphad_ferrous_feo_activity_diagnostic",
        lambda **_kwargs: {
            "a_FeO_authoritative": a_feo,
            "sources": {},
        },
    )
    return EquilibriumMixin._internal_analytical_equilibrium(sim)


def test_internal_analytical_cold_path_still_ok_at_25_c():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=1.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == pytest.approx(0.001)
    assert result.vapor_pressures_Pa == {}
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False
    assert result.diagnostics["vapor_pressure_zero_reason"] == "internal_analytical_below_400K"


def test_internal_analytical_zero_mbar_is_admitted_vacuum():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=0.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == 0.0


@pytest.mark.parametrize(
    "temperature_C",
    (
        -CELSIUS_TO_KELVIN_OFFSET,
        -CELSIUS_TO_KELVIN_OFFSET - 1.0,
        float("-inf"),
        float("nan"),
        float("inf"),
    ),
)
def test_internal_analytical_refuses_non_positive_or_nonfinite_temperature(
    temperature_C,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=temperature_C, p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.fO2_log is None
    assert result.pressure_bar == 0.0
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "temperature_C" in fields


@pytest.mark.parametrize("p_total_mbar", (float("nan"), float("inf"), -1.0))
def test_internal_analytical_refuses_nonfinite_or_negative_total_pressure(
    p_total_mbar,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=1600.0, p_total_mbar=p_total_mbar)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.pressure_bar == 0.0
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "p_total_mbar" in fields


def test_internal_analytical_nan_temperature_does_not_raise_ellingham_valueerror():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=float("nan"), p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )


def test_internal_analytical_refuses_non_numeric_controls():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=True, p_total_mbar="1.0")
    )
    assert result.status == "out_of_domain"
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}


def test_internal_analytical_control_refusal_helper_shape():
    diag = _internal_analytical_control_refusal(float("nan"), -1.0)
    assert diag is not None
    fields = {row["field"] for row in diag["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}
    assert _internal_analytical_control_refusal(25.0, 0.0) is None
    assert _internal_analytical_control_refusal(
        -CELSIUS_TO_KELVIN_OFFSET, 1.0
    ) is not None


# Mutation B936_DROP_TYPED_OMISSION_NOTE removes the _sz_omit call from a
# targeted continue; the corresponding scenario must fail for its missing note.
def test_internal_analytical_inactive_and_parentless_metal_omissions_are_typed(
    monkeypatch,
):
    """Mutation B936_DROP_TYPED_OMISSION_NOTE restores both bare continues."""
    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "consumer_status": "inactive",
                "parent_oxide": "MgO",
            },
            "Na": {"parent_oxide": ""},
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    notes = {
        note["species"]: note
        for note in result.diagnostics["silent_zero_notes"]
    }
    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert notes["Mg"]["zero_because"] == ZeroBecause.REFUSED_UPSTREAM.value
    assert notes["Mg"]["doctrine_category"] == CATEGORY_REFUSE
    assert notes["Mg"]["field"] == "consumer_status"
    assert notes["Na"]["zero_because"] == ZeroBecause.MISSING_THERMO.value
    assert notes["Na"]["doctrine_category"] == CATEGORY_REFUSE
    assert notes["Na"]["field"] == "parent_oxide"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


@pytest.mark.parametrize(
    "metals",
    ({}, {"Fe": {}}),
    ids=("missing", "empty"),
)
def test_internal_analytical_missing_or_empty_metal_row_is_typed(
    metals,
    monkeypatch,
):
    sim = _typed_omission_sim(metals=metals, species=("Fe",))

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Fe"
    assert note["zero_because"] == ZeroBecause.MISSING_THERMO.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "metal_row"
    assert sim._silent_zero_notes == [note]


def test_internal_analytical_missing_antoine_intercepts_are_typed(monkeypatch):
    from engines.builtin import vapor_pressure as vapor_pressure_module
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    monkeypatch.setattr(
        vapor_pressure_module,
        "_is_noncertifying_pseudo_vapor_pressure_runtime",
        lambda *_args, **_kwargs: False,
    )
    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "antoine": {"B": 0.0, "C": 0.0},
            }
        },
        oxide_vapors={
            "SiO": {"antoine": {"B": 0.0, "C": 0.0}},
        },
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    notes = {
        note["species"]: note
        for note in result.diagnostics["silent_zero_notes"]
    }
    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert set(notes) == {"Mg", "SiO"}
    for species in ("Mg", "SiO"):
        assert notes[species]["zero_because"] == ZeroBecause.MISSING_COEFFICIENT.value
        assert notes[species]["doctrine_category"] == CATEGORY_REFUSE
        assert notes[species]["field"] == "antoine.A"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes
    assert any("species=Mg" in warning for warning in result.warnings)
    assert any("species=SiO" in warning for warning in result.warnings)


def test_internal_analytical_oxide_vapor_outside_valid_range_is_typed(
    monkeypatch,
):
    sim = _typed_omission_sim(
        oxide_vapors={
            "SiO": {
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
                "valid_range_K": [1100.0, 1200.0],
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "SiO"
    assert note["zero_because"] == ZeroBecause.OUT_OF_DOMAIN_MARKED.value
    assert note["doctrine_category"] == CATEGORY_MARK
    assert note["field"] == "valid_range_K"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


def test_internal_analytical_nonpositive_metal_activity_numerator_is_typed(
    monkeypatch,
):
    from engines.builtin import vapor_pressure as vapor_pressure_module
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    monkeypatch.setattr(
        equilibrium_module,
        "ellingham_delta_g_kj_per_mol_o2",
        lambda *_args: -1e9,
    )
    monkeypatch.setattr(
        equilibrium_module,
        "ellingham_authority_limit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "_is_noncertifying_pseudo_vapor_pressure_runtime",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "require_antoine_source_certified_temperature",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "vapor_pressure_valid_range_K",
        lambda *_args, **_kwargs: None,
    )
    sim = _typed_omission_sim(
        metals={
            "Fe": {
                "parent_oxide": "FeO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert result.activity_coefficients == {"Fe": 1.0}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Fe"
    assert note["zero_because"] == ZeroBecause.PROVEN_BELOW_THRESHOLD.value
    assert note["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert note["field"] == "metal_activity_numerator"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


def test_internal_analytical_noncertifying_pseudo_fallback_is_typed(
    monkeypatch,
):
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "residual_dex": 1.0,
                "valid_range_K": [900.0, 950.0],
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Mg"
    assert note["zero_because"] == ZeroBecause.REFUSED_UPSTREAM.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "valid_range_K"
    assert any(
        warning.startswith("non_certifying_vapor_pressure_fallback_omitted:")
        for warning in result.warnings
    )
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


def test_internal_analytical_source_certified_range_refusal_is_typed(
    monkeypatch,
):
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "extrapolation_policy": "refuse",
                "source_certified_range_K": [1100.0, 1200.0],
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Mg"
    assert note["zero_because"] == ZeroBecause.REFUSED_UPSTREAM.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "source_certified_range_K"
    assert any(
        warning.startswith("metal_vapor_pressure_out_of_source_certified_range:")
        for warning in result.warnings
    )
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


_FEO_MELT_WT = {
    "SiO2": 50.0,
    "Al2O3": 15.0,
    "FeO": 12.0,
    "MgO": 10.0,
    "CaO": 13.0,
}


def _feo_equilibrium_sim(*, temperature_C, domain=None, with_domain_method=True):
    melt = SimpleNamespace(
        temperature_C=temperature_C,
        p_total_mbar=1.0,
        oxygen_reservoir=None,
        melt_fO2_log=-12.0,
        fO2_log=-12.0,
        composition_wt_pct=lambda: dict(_FEO_MELT_WT),
    )
    sim = SimpleNamespace(
        melt=melt,
        vapor_pressures={"metals": {}, "oxide_vapors": {}},
        _ELLINGHAM_THERMO=(),
        _headspace_transport_pO2_bar=lambda: 1e-12,
        _oxygen_numerical_floor_bar=lambda: 1e-12,
        atom_ledger=None,
    )
    if with_domain_method:
        sim.liquid_feo_domain_at = lambda _T_K: domain
    return sim


def test_subliquidus_equilibrium_carries_liquid_feo_notice_without_changing_activity():
    domain = LiquidFeODomain(
        below_liquidus=True,
        temperature_K=225.0 + 273.15,
        liquidus_T_C=1700.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    plain = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=225.0, with_domain_method=False)
    )
    flagged = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=225.0, domain=domain)
    )
    plain_diag = plain.diagnostics["a_FeO_calphad"]
    flagged_diag = flagged.diagnostics["a_FeO_calphad"]

    assert flagged.status == "ok"
    assert flagged_diag["notices"] == [
        {
            "type": "typed_notice",
            "kind": "liquid_model_below_liquidus",
            "temperature_K": domain.temperature_K,
            "liquidus_T_C": 1700.0,
            "solidus_T_C": 1050.0,
            "source": "gate_liquid_fraction",
            "authority": "extrapolated",
        }
    ]
    assert {
        key: value for key, value in flagged_diag.items() if key != "notices"
    } == plain_diag
    assert flagged_diag["a_FeO_authoritative"] == plain_diag["a_FeO_authoritative"]
    assert flagged_diag["a_FeO_authoritative"] > 0.0
    assert "gamma_FeO" in flagged_diag


def test_above_liquidus_equilibrium_diagnostic_is_byte_identical():
    domain = LiquidFeODomain(
        below_liquidus=False,
        temperature_K=1600.0 + 273.15,
        liquidus_T_C=1200.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    plain = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=1600.0, with_domain_method=False)
    )
    liquid = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=1600.0, domain=domain)
    )

    assert liquid.diagnostics == plain.diagnostics
    assert liquid.vapor_pressures_Pa == plain.vapor_pressures_Pa
    assert "notices" not in liquid.diagnostics["a_FeO_calphad"]


def _gate_host(curve, *, pinned=True):
    def _resolve(gate_authority=_RESOLVE_MELT_REDOX_GATE_AUTHORITY, **_kwargs):
        if gate_authority is _RESOLVE_MELT_REDOX_GATE_AUTHORITY:
            raise AssertionError("FeO domain started a liquidus solve")
        return curve

    host = SimpleNamespace(
        melt=SimpleNamespace(hour=1),
        _resolved_melt_redox_gate_authority=_resolve,
    )
    if pinned:
        host._melt_redox_gate_authority_tick_hour = 1
        host._melt_redox_gate_authority_this_tick = (
            curve
            if not isinstance(curve, dict)
            else {"disposition": "live", "curve": curve}
        )
    else:
        host._melt_redox_gate_authority_tick_hour = None
        host._melt_redox_gate_authority_this_tick = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        )
    return host


def test_liquid_feo_domain_uses_silicate_liquidus_not_the_kress_floor():
    curve = {
        "liquidus_T_C": 1700.0,
        "solidus_T_C": 1050.0,
        "source": "gate_liquid_fraction",
    }
    host = _gate_host(curve)
    cold = PyrolysisSimulator.liquid_feo_domain_at(host, 225.0 + 273.15)
    assert cold.below_liquidus is True
    assert cold.liquidus_T_C == 1700.0
    assert cold.solidus_T_C == 1050.0
    assert cold.source == "gate_liquid_fraction"
    assert cold.temperature_K == pytest.approx(225.0 + 273.15)

    hot = PyrolysisSimulator.liquid_feo_domain_at(host, 1750.0 + 273.15)
    assert hot.below_liquidus is False

    at_liquidus_K = 1700.0 + 273.15
    at_curve = {
        "liquidus_T_C": at_liquidus_K - 273.15,
        "solidus_T_C": 1050.0,
        "source": "gate_liquid_fraction",
    }
    at = PyrolysisSimulator.liquid_feo_domain_at(_gate_host(at_curve), at_liquidus_K)
    assert at.below_liquidus is False

    # A fully molten melt between a sub-1200 liquidus and the Kress floor
    # is liquid for this model. The floor is a redox-capacity gate.
    low_liquidus = {
        "liquidus_T_C": 1100.0,
        "solidus_T_C": 900.0,
        "source": "gate_liquid_fraction",
    }
    between = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(low_liquidus),
        1150.0 + 273.15,
    )
    assert between.below_liquidus is False


def test_liquid_feo_domain_floor_fallback_and_unresolved_gate():
    fallback = _MeltRedoxLiquidusFloorFallback(
        source="none:liquidus_unavailable",
        reason="test",
        liquidus_status="invalid",
    )
    host = _gate_host(fallback)
    floor_C = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
    at_floor = PyrolysisSimulator.liquid_feo_domain_at(host, floor_C + 273.15)
    assert at_floor.below_liquidus is True
    assert at_floor.liquidus_T_C == floor_C
    assert at_floor.solidus_T_C == floor_C
    assert at_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert fallback.source == "none:liquidus_unavailable"
    above = PyrolysisSimulator.liquid_feo_domain_at(host, floor_C + 273.15 + 0.1)
    assert above.below_liquidus is False

    missing = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(None),
        1700.0 + 273.15,
    )
    assert missing.below_liquidus is True
    assert missing.liquidus_T_C is None
    assert missing.solidus_T_C is None
    assert missing.source == "none:liquidus_gate_in_progress"

    unresolved = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host({"source": "broken"}),
        1700.0 + 273.15,
    )
    assert unresolved.below_liquidus is True
    assert unresolved.source == "none:liquidus_unresolved"

    not_established = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(
            {
                "liquidus_T_C": 1700.0,
                "solidus_T_C": 1050.0,
                "source": "gate_liquid_fraction",
            },
            pinned=False,
        ),
        1750.0 + 273.15,
    )
    assert not_established is None


def _established_record_host(record):
    """Real establish, resolver, and curve unwrap. Only the record is supplied."""

    host = SimpleNamespace(
        melt=SimpleNamespace(hour=1),
        _melt_redox_liquidus_gate_record=lambda **_kwargs: record,
    )
    host._melt_redox_liquidus_gate_curve = (
        PyrolysisSimulator._melt_redox_liquidus_gate_curve.__get__(host)
    )
    host._melt_redox_gate_record_is_admitted = (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted.__get__(host)
    )
    return host


def test_replay_and_originless_live_curves_feed_the_liquid_feo_domain():
    """A resolved curve admission will not pin still has a liquidus.

    The redox resolver collapses both records to None. That None is not the
    FeO domain: 225 C is below the curve, and a temperature above the curve
    is not.
    """

    replay = {
        "disposition": "replay",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": "gate_liquid_fraction",
            "liquidus_T_C": 1700.0,
            "solidus_T_C": 1050.0,
        },
    }
    replay_host = _established_record_host(replay)
    established = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            replay_host
        )
    )
    assert established["liquidus_T_C"] == 1700.0
    assert (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted(
            replay_host, replay
        )
        is False
    )
    resolved = PyrolysisSimulator._resolved_melt_redox_gate_authority(
        replay_host
    )
    assert resolved["liquidus_T_C"] == 1700.0
    assert (
        PyrolysisSimulator._resolved_melt_redox_gate_authority(
            replay_host, replay
        )
        is None
    )
    assert getattr(
        replay_host, "_melt_redox_gate_authority_tick_hour", None
    ) is None
    cold = PyrolysisSimulator.liquid_feo_domain_at(
        replay_host, 225.0 + 273.15
    )
    assert cold.below_liquidus is True
    assert cold.liquidus_T_C == 1700.0
    assert cold.solidus_T_C == 1050.0
    assert cold.source == "gate_liquid_fraction"
    hot = PyrolysisSimulator.liquid_feo_domain_at(
        replay_host, 1750.0 + 273.15
    )
    assert hot.below_liquidus is False
    assert hot.liquidus_T_C == 1700.0
    assert hot.source == "gate_liquid_fraction"

    floor_C = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
    liquidus_T_C = floor_C + float(_KRESS91_FLOOR_OPEN_SIDE_C)
    live = {
        "disposition": "live",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
            "liquidus_T_C": liquidus_T_C,
            "solidus_T_C": floor_C,
        },
    }
    live_host = _established_record_host(live)
    live_curve = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            live_host
        )
    )
    assert live_curve["liquidus_T_C"] == pytest.approx(liquidus_T_C)
    assert (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted(live_host, live)
        is False
    )
    live_resolved = PyrolysisSimulator._resolved_melt_redox_gate_authority(
        live_host
    )
    assert live_resolved["source"] == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert (
        PyrolysisSimulator._resolved_melt_redox_gate_authority(live_host, live)
        is None
    )
    below_floor = PyrolysisSimulator.liquid_feo_domain_at(
        live_host, 225.0 + 273.15
    )
    assert below_floor.below_liquidus is True
    assert below_floor.liquidus_T_C == pytest.approx(liquidus_T_C)
    assert below_floor.solidus_T_C == pytest.approx(floor_C)
    assert below_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    above_floor = PyrolysisSimulator.liquid_feo_domain_at(
        live_host, 1750.0 + 273.15
    )
    assert above_floor.below_liquidus is False
    assert above_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE


def test_native_fe_saturation_carries_the_liquidus_notice_on_both_returns():
    comp = dict(_FEO_MELT_WT)
    cold_K = 225.0 + 273.15
    below = LiquidFeODomain(
        below_liquidus=True,
        temperature_K=cold_K,
        liquidus_T_C=1700.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    above = LiquidFeODomain(
        below_liquidus=False,
        temperature_K=1600.0 + 273.15,
        liquidus_T_C=1200.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )

    def _host(domain):
        return SimpleNamespace(
            _feot_equivalent_wt_pct=PyrolysisSimulator._feot_equivalent_wt_pct,
            liquid_feo_domain_at=(None if domain is None else (lambda _T: domain)),
        )

    def _state(domain, *, T_K, fO2_log):
        host = _host(domain)
        if domain is None:
            del host.liquid_feo_domain_at
        return PyrolysisSimulator._native_fe_saturation_state(
            host,
            comp,
            fe3_over_sigma_fe=0.1,
            T_K=T_K,
            pressure_bar=1e-6,
            fO2_log=fO2_log,
        )

    cold_fO2 = -14.0
    flagged = _state(below, T_K=cold_K, fO2_log=cold_fO2)
    plain = _state(None, T_K=cold_K, fO2_log=cold_fO2)
    assert flagged["notices"][0]["kind"] == "liquid_model_below_liquidus"
    assert flagged["notices"][0]["authority"] == "extrapolated"
    assert flagged["native_fe_activity"] == plain["native_fe_activity"]
    assert {
        key: value for key, value in flagged.items() if key != "notices"
    } == plain
    assert "notices" not in plain

    hot_K = 1600.0 + 273.15
    hot_plain = _state(None, T_K=hot_K, fO2_log=-10.0)
    hot_liquid = _state(above, T_K=hot_K, fO2_log=-10.0)
    assert hot_liquid == hot_plain
    assert "notices" not in hot_liquid

    zero = _state(below, T_K=cold_K, fO2_log=1000.0)
    zero_plain = _state(None, T_K=cold_K, fO2_log=1000.0)
    assert zero["native_fe_activity_source"] == "unavailable:no_positive_a_FeO"
    assert zero["notices"][0]["kind"] == "liquid_model_below_liquidus"
    assert {
        key: value for key, value in zero.items() if key != "notices"
    } == zero_plain
