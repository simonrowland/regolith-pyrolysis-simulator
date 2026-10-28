"""Guards and regressions for simulator.equilibrium."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import simulator.equilibrium as equilibrium_module
from simulator.equilibrium import (
    EquilibriumMixin,
    _internal_analytical_control_refusal,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.silent_zero import (
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


def test_internal_analytical_oxide_vapor_outside_fit_retains_flagged_value(
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
    assert result.vapor_pressures_Pa["SiO"] == pytest.approx(10.)
    notice = result.diagnostics["extrapolated_beyond_valid_range_K"]["SiO"]
    assert notice["reason"] == "out_of_domain_marked"
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"] == {"temperature_K": (1100., 1200.)}
    assert result.vapor_pressures_source["SiO"].startswith("builtin_extrapolation_limited")


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


def test_internal_analytical_source_range_continues_but_missing_activity_refuses(
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
    assert note["zero_because"] == ZeroBecause.MISSING_ACTIVITY.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "oxide_activity"
    notice = result.diagnostics["extrapolated_beyond_valid_range_K"]["Mg"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"] == {"temperature_K": (1100., 1200.)}
    assert any(
        warning.startswith("metal_vapor_pressure_out_of_source_certified_range:")
        for warning in result.warnings
    )
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes
