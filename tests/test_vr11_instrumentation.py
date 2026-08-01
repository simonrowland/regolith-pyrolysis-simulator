"""VR-11 / t-485: instrumentation, B2/B3, flux cutover, source guard.

Acceptance (DECOMPOSITION VR-11 / DESIGN-REV5 U4):
- condensation_refusals_by_species has real consumers (b-111 / B2)
- three silent _condensation_efficiency zeros mint typed outcomes (b-112 / B3)
- evaporation consumes VapourBatch; flux consumers do not iterate compatibility
  pressure maps
- nine-row advisory ceiling table is typed and non-vacuous
- shadow_equal is a measured proved/mismatch/not-fixed outcome, never hardcoded
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from simulator.condensation import CondensationModel, CondensationTrain
from simulator.vapour_rail.batch import (
    FluxEligible,
    FluxRefusal,
    PressureRefusal,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
from simulator.vapour_rail.instrumentation import (
    AUDITED_OPERATOR_T_COND_SPECIES,
    CONTROL_FLUX_PRESSURES_KEY,
    SETPOINTS_T_COND_AUDIT,
    SHADOW_MISMATCH,
    SHADOW_MISSING_BATCH,
    SHADOW_MISSING_KEYS,
    SHADOW_PROVED,
    SHADOW_REFUSED_VS_LIVE,
    SHADOW_RESOLUTION_ERROR,
    SOURCE_VAPOUR_CEILING_ROWS,
    assert_no_flux_consumer_iterates_compatibility_maps,
    compare_legacy_vs_batch_flux_paths,
    condensation_refusals_payload,
    flux_pressures_from_batch_and_live,
    serialize_vapour_batch,
    source_vapour_ceiling_table,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# B3 — typed outcomes for the three silent efficiency zeros
# ---------------------------------------------------------------------------


def _efficiency_model() -> CondensationModel:
    return CondensationModel(CondensationTrain.create_default())


def test_b112_zero_residence_mints_typed_pass_through_outcome() -> None:
    model = _efficiency_model()
    stage = next(s for s in model.train.stages if s.stage_number == 3)
    outcomes: list[dict[str, Any]] = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=0.0,
        available_kg=1.0,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta == 0.0
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "pass_through"
    assert outcomes[0]["reason"] == "zero_residence_or_alpha"
    assert outcomes[0]["output_status"] == "status_bearing"


def test_b112_nonpositive_local_pressure_mints_typed_outcome(monkeypatch) -> None:
    import simulator.condensation as condensation_module

    model = _efficiency_model()
    stage = next(s for s in model.train.stages if s.stage_number == 3)
    monkeypatch.setattr(
        condensation_module,
        "_local_species_pressure_pa",
        lambda *args, **kwargs: 0.0,
    )
    outcomes: list[dict[str, Any]] = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=1.0,
        available_kg=1.0,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta == 0.0
    assert outcomes[0]["reason"] == "nonpositive_local_pressure"


def test_b112_nonpositive_reference_flux_mints_typed_outcome(monkeypatch) -> None:
    import simulator.condensation as condensation_module

    model = _efficiency_model()
    stage = next(s for s in model.train.stages if s.stage_number == 3)
    monkeypatch.setattr(
        condensation_module,
        "_local_species_pressure_pa",
        lambda *args, **kwargs: 10.0,
    )
    monkeypatch.setattr(
        condensation_module,
        "_hkl_impingement_flux_mol_m2_s",
        lambda *args, **kwargs: 0.0,
    )
    outcomes: list[dict[str, Any]] = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=1.0,
        available_kg=1.0,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta == 0.0
    assert outcomes[0]["reason"] == "nonpositive_reference_flux"


def test_b112_positive_efficiency_does_not_mint_outcome(monkeypatch) -> None:
    import simulator.condensation as condensation_module

    model = _efficiency_model()
    stage = next(s for s in model.train.stages if s.stage_number == 3)
    monkeypatch.setattr(
        condensation_module,
        "_local_species_pressure_pa",
        lambda *args, **kwargs: 1.0,
    )
    monkeypatch.setattr(
        condensation_module,
        "_hkl_impingement_flux_mol_m2_s",
        lambda *args, **kwargs: 1.0,
    )
    monkeypatch.setattr(
        condensation_module,
        "_series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: 2.0,
    )
    available_kg = (
        condensation_module._molecular_mass_kg_per_molecule("SiO")
        * condensation_module.AVOGADRO_MOL
        * 10.0
    )
    model.stage_area_m2_by_stage = {"sio_stage3": 1.0}
    outcomes: list[dict[str, Any]] = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=1.0,
        available_kg=available_kg,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta == pytest.approx(0.2)
    assert outcomes == []


# ---------------------------------------------------------------------------
# B2 — condensation_refusals_by_species consumers
# ---------------------------------------------------------------------------


def test_b111_condensation_refusals_payload_and_model_consumer() -> None:
    raw = {
        "X": {
            "status": "refused",
            "reason": "antoine_data_unavailable",
            "output_status": "status_bearing",
        }
    }
    payload = condensation_refusals_payload(raw)
    assert payload["has_refusals"] is True
    assert payload["n_species"] == 1
    assert payload["by_species"]["X"]["reason"] == "antoine_data_unavailable"

    model = _efficiency_model()
    model.last_condensation_refusals_by_species = dict(raw)
    sim = SimpleNamespace(condensation_model=model)
    from simulator.diagnostics import condensation_refusals_diagnostic
    from web.advisory import condensation_refusals_panel_payload

    diag = condensation_refusals_diagnostic(sim)
    assert diag["has_refusals"] is True
    panel = condensation_refusals_panel_payload(sim)
    assert panel["status"] == "ok"
    assert panel["by_species"]["X"]["reason"] == "antoine_data_unavailable"


def test_b111_efficiency_outcomes_fold_into_consumer_channel() -> None:
    """B3 stage outcomes roll up onto last_condensation_refusals_by_species."""

    model = _efficiency_model()
    stage = next(s for s in model.train.stages if s.stage_number == 3)
    outcomes: list[dict[str, Any]] = []
    model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=0.0,
        available_kg=1.0,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    # Mirror the route-path fold (golden-neutral diagnostic channel).
    condensation_refusals_by_species: dict[str, dict[str, Any]] = {}
    for species, stage_outcomes in {"SiO": outcomes}.items():
        primary = stage_outcomes[0]
        condensation_refusals_by_species[species] = {
            "status": "pass_through",
            "reason": str(primary.get("reason") or "condensation_efficiency_zero"),
            "output_status": "status_bearing",
            "stage_outcomes": list(stage_outcomes),
        }
    model.last_condensation_refusals_by_species = condensation_refusals_by_species
    payload = condensation_refusals_payload(
        model.last_condensation_refusals_by_species
    )
    assert payload["has_refusals"] is True
    assert payload["by_species"]["SiO"]["status"] == "pass_through"
    assert payload["by_species"]["SiO"]["reason"] == "zero_residence_or_alpha"
    assert payload["by_species"]["SiO"]["stage_outcomes"]


# ---------------------------------------------------------------------------
# Flux cutover + shadow equality + source guard
# ---------------------------------------------------------------------------


def _toy_answer(
    sid: str,
    *,
    pa: float = 1.0,
    refused: bool = False,
) -> VapourAnswer:
    if refused:
        return VapourAnswer(
            species_id=sid,
            pressure=PressureRefusal(code="test_refusal", detail="refused"),
            flux=FluxRefusal(code="test_refusal", detail="refused"),
            source_label="test",
            formula_id=sid,
            source_account="process.cleaned_melt",
            solve_group_id="g1",
            state_fingerprint="state:test",
            validation_status="pending_validation",
            refusal_code="test_refusal",
        )
    return VapourAnswer(
        species_id=sid,
        pressure=PressureValue(pa=pa),
        flux=FluxEligible(alpha_ref=f"alpha:{sid}"),
        source_label="test",
        formula_id=sid,
        source_account="process.cleaned_melt",
        solve_group_id="g1",
        state_fingerprint="state:test",
        validation_status="pending_validation",
    )


def _toy_batch(
    species_ids: set[str],
    *,
    pressures: dict[str, float] | None = None,
    refused: set[str] | None = None,
) -> VapourBatch:
    pressures = pressures or {}
    refused = refused or set()
    channels = {
        sid: _toy_answer(
            sid,
            pa=float(pressures.get(sid, 1.0)),
            refused=sid in refused,
        )
        for sid in species_ids
    }
    active = frozenset(species_ids - refused)
    return VapourBatch(
        requested_species_ids=frozenset(species_ids),
        channels_by_species=channels,
        flux_active_species_ids=active,
    )


def test_flux_pressures_from_batch_channel_unions() -> None:
    """Batch owns selection; live Pa used for shared eligible (golden-neutral)."""

    live = {"Na": 12.5, "SiO": 3.0, "Fe": 0.0}
    batch = _toy_batch(
        {"Na", "SiO", "Fe", "K"},
        pressures={"Na": 1.0, "SiO": 1.0, "Fe": 1.0, "K": 0.5},
    )
    flux, report = flux_pressures_from_batch_and_live(batch, live)
    # Shared eligible: live Pa (not catalog batch Pa).
    assert flux["Na"] == 12.5
    assert flux["SiO"] == 3.0
    assert flux["Fe"] == 0.0
    # Batch-only eligible does not expand the debiting multiset this epoch.
    assert "K" not in flux
    assert report["batch_pa_by_species"]["K"] == 0.5
    assert "K" in report["batch_flux_active_not_in_live"]
    assert report["selection_source"] == "vapour_batch_channel_unions"
    assert report["batch_pa_by_species"]["Na"] == 1.0
    assert report["shadow_equal"] is True
    assert report["shadow_outcome"] == SHADOW_PROVED
    assert report["catalog_pa_shadow_equal"] is False


def test_flux_pressures_proved_when_legacy_and_batch_agree() -> None:
    live = {"Na": 12.5, "SiO": 3.0}
    batch = _toy_batch(
        {"Na", "SiO"},
        pressures={"Na": 99.0, "SiO": 88.0},  # catalog Pa differ; live overlay
    )
    flux, report = flux_pressures_from_batch_and_live(batch, live)
    assert flux == live
    assert report["shadow_equal"] is True
    assert report["shadow_outcome"] == SHADOW_PROVED
    # Catalog pure-P vs live is a separate measured outcome.
    assert report["catalog_pa_shadow_equal"] is False


def test_flux_pressures_batch_only_eligible_does_not_expand_live() -> None:
    """Batch-only eligible channels are recorded, not added to flux map."""

    live: dict[str, float] = {}
    batch = _toy_batch({"Na"}, pressures={"Na": 1.0})
    flux, report = flux_pressures_from_batch_and_live(batch, live)
    assert flux == {}
    assert report["batch_pa_by_species"]["Na"] == 1.0
    assert report["shadow_equal"] is True  # empty vs empty
    assert report["shadow_outcome"] == SHADOW_PROVED


def test_flux_pressures_without_batch_is_typed_failure() -> None:
    """Absent batch must not resume the live compatibility map (fail-closed)."""

    live = {"Na": 1.0, "K": 2.0}
    flux, report = flux_pressures_from_batch_and_live(None, live)
    assert flux == {}
    assert report["batch_present"] is False
    assert report["shadow_equal"] is False
    assert report["shadow_outcome"] == SHADOW_MISSING_BATCH
    assert report["selection_source"] == "typed_failure_missing_batch"


def test_flux_pressures_resolution_error_is_typed_failure() -> None:
    live = {"Na": 1.0}
    flux, report = flux_pressures_from_batch_and_live(
        None,
        live,
        resolution_error={"reason": "vapour_batch_resolve_failed"},
    )
    assert flux == {}
    assert report["shadow_equal"] is False
    assert report["shadow_outcome"] == SHADOW_RESOLUTION_ERROR


def test_batch_refusal_drops_live_positive_species() -> None:
    """Refused channel must not retain live-positive pressure (Codex P1-1)."""

    live = {"Na": 12.5, "SiO": 3.0}
    batch = _toy_batch(
        {"Na", "SiO"},
        pressures={"Na": 12.5, "SiO": 3.0},
        refused={"Na"},
    )
    flux, report = flux_pressures_from_batch_and_live(batch, live)
    assert "Na" not in flux
    assert flux["SiO"] == 3.0
    assert "Na" in report["batch_refused_live_species"]
    assert report["shadow_equal"] is False
    assert report["shadow_outcome"] == SHADOW_REFUSED_VS_LIVE


def test_compare_legacy_vs_batch_is_not_stubbed() -> None:
    """Red if comparison always returns proved (reviewer poison case)."""

    equal = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa={"Na": 1.0},
        batch_flux_pressures_Pa={"Na": 1.0},
        batch_present=True,
    )
    assert equal["shadow_equal"] is True
    assert equal["shadow_outcome"] == SHADOW_PROVED

    mismatched = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa={"Na": 1.0},
        batch_flux_pressures_Pa={"Na": 2.0},
        batch_present=True,
    )
    assert mismatched["shadow_equal"] is False
    assert mismatched["shadow_outcome"] == SHADOW_MISMATCH

    missing = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa={"Na": 1.0},
        batch_flux_pressures_Pa={},
        batch_present=False,
    )
    assert missing["shadow_equal"] is False
    assert missing["shadow_outcome"] == SHADOW_MISSING_BATCH

    refused = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa={"Na": 1.0},
        batch_flux_pressures_Pa={},
        refused_live_species=["Na"],
        batch_present=True,
    )
    assert refused["shadow_outcome"] == SHADOW_REFUSED_VS_LIVE


def test_source_guard_no_flux_consumer_iterates_compatibility_maps() -> None:
    sources = {
        "engines/builtin/evaporation_flux.py": (
            ROOT / "engines/builtin/evaporation_flux.py"
        ).read_text(encoding="utf-8"),
        "simulator/evaporation.py": (
            ROOT / "simulator/evaporation.py"
        ).read_text(encoding="utf-8"),
    }
    assert_no_flux_consumer_iterates_compatibility_maps(sources)
    kernel = sources["engines/builtin/evaporation_flux.py"]
    assert "vapour_batch_flux_pressures_Pa" in kernel
    # Kernel must not fall back to the compatibility key.
    assert 'controls.get("vapor_pressures_Pa")' not in kernel
    assert "controls.get('vapor_pressures_Pa')" not in kernel
    assert CONTROL_FLUX_PRESSURES_KEY == "vapour_batch_flux_pressures_Pa"


def test_source_guard_flags_banned_controls_iteration() -> None:
    bad = 'for species, P in controls.get("vapor_pressures_Pa") or {}:\n    pass\n'
    with pytest.raises(AssertionError, match="compatibility pressure maps"):
        assert_no_flux_consumer_iterates_compatibility_maps(
            {"evil.py": bad}
        )


def test_source_guard_flags_alias_then_iterate() -> None:
    """Demonstrated evasion: assign then iterate must fail the guard."""

    bad = (
        'vapor_pressures = dict(controls.get("vapor_pressures_Pa") or {})\n'
        "for species, P in vapor_pressures.items():\n"
        "    pass\n"
    )
    with pytest.raises(AssertionError, match="alias-then-iterate|compatibility"):
        assert_no_flux_consumer_iterates_compatibility_maps(
            {"engines/builtin/evaporation_flux.py": bad}
        )


def test_source_guard_flags_parenthesized_items_form() -> None:
    bad = (
        'for s, p in (controls.get("vapor_pressures_Pa") or {}).items():\n'
        "    pass\n"
    )
    with pytest.raises(AssertionError, match="compatibility"):
        assert_no_flux_consumer_iterates_compatibility_maps(
            {"engines/builtin/evaporation_flux.py": bad}
        )


def test_kernel_refuses_legacy_key_only_controls() -> None:
    """Legacy-key-only controls must refuse — not silently drive flux."""

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
    from simulator.chemistry.kernel.dto import ProviderAccountView

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Na2O": 1.0}},
        species_formula_registry={},
    )
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=view,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            fO2_log=None,
            control_inputs={
                "vapor_pressures_Pa": {"Na": 100.0},
                "overhead_partials_Pa": {},
                "overhead_pressure_pa": 0.0,
                "molar_mass_kg_mol": {"Na": 0.023},
                "stoich_by_species": {
                    "Na": {
                        "parent_oxide": "Na2O",
                        "oxide_per_product_kg": 1.347,
                        "O2_per_product_kg": 0.347,
                    }
                },
                "available_oxide_kg": {"Na": 10.0},
                "melt_surface_area_m2": 0.2,
                "stir_factor": 1.0,
                "alpha": 0.5,
            },
        )
    )
    assert result.status == "refused"
    assert result.diagnostic["reason"] == "missing_vapour_batch_flux_pressures_Pa"


def test_evaporation_control_inputs_pack_batch_flux_key() -> None:
    """_evaporation_flux_control_inputs packs batch key; live is reporting only."""

    from simulator.evaporation import EvaporationMixin

    class _Host(EvaporationMixin):
        def __init__(self) -> None:
            self.vapor_pressures = {"metals": {}, "oxide_vapors": {}}
            self.setpoints = {}
            self.melt = SimpleNamespace(
                melt_surface_area_m2=1.0,
                stir_state=SimpleNamespace(axial=1.0, radial=1.0),
                temperature_C=1600.0,
            )
            self.overhead_model = SimpleNamespace(pipe_diameter_m=0.12)
            self.overhead = SimpleNamespace(headspace_temperature_K=0.0)
            self._last_vapor_pressure_diagnostic = {}

        def _build_evaporation_aux_maps(self, vapor_pressures):
            return {}, {}, {}

    host = _Host()
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Na": 5.0},
        vapor_pressures_source={"Na": "test"},
        activity_coefficients={},
    )
    controls, _ = EvaporationMixin._evaporation_flux_control_inputs(
        host,
        equilibrium,
        overhead_partials_Pa={"Na": 0.0},
        vapour_batch_flux_pressures_Pa={"Na": 5.0},
        vapour_batch_report={"schema": "vapour_batch.v1", "n_requested": 1},
        vapour_batch_flux_overlay={
            "shadow_equal": True,
            "shadow_outcome": SHADOW_PROVED,
        },
    )
    assert controls[CONTROL_FLUX_PRESSURES_KEY] == {"Na": 5.0}
    # Reporting projection keeps live; batch key is the flux authority.
    assert controls["vapor_pressures_Pa"] == {"Na": 5.0}
    assert controls["vapour_batch_flux_shadow_equal"] is True
    assert controls["vapour_batch_flux_shadow_outcome"] == SHADOW_PROVED


# ---------------------------------------------------------------------------
# Nine-row advisory ceiling
# ---------------------------------------------------------------------------


def test_nine_row_advisory_ceiling_table_shape() -> None:
    table = source_vapour_ceiling_table()
    assert len(table) == 9
    assert len(SOURCE_VAPOUR_CEILING_ROWS) == 9
    by_legacy = {row["legacy_key"]: row for row in table}
    assert by_legacy["SiO"]["lookup_gas_id"] == "SiO"
    assert by_legacy["SiO"]["ceiling_mol"] > 0.0
    for oxide in ("Na2O", "K2O", "FeO", "MgO", "CaO", "Al2O3", "TiO2"):
        assert by_legacy[oxide]["lookup_gas_id"] == f"{oxide}_gas"
        assert by_legacy[oxide]["status"] == "unvalidated_legacy"
        assert by_legacy[oxide]["ceiling_mol"] == 0.0
        assert by_legacy[oxide]["advisory_only"] is True
    assert by_legacy["CrO2"]["lookup_gas_id"] == "CrO2"


def test_lab_plume_ceiling_breach_includes_typed_table() -> None:
    from simulator.accounting.queries import (
        AccountingQueries,
        FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL,
    )
    from tests.test_lab_oxygen_diagnostics import _plume_diagnostic_sim

    partition = AccountingQueries(
        _plume_diagnostic_sim(
            overhead={"SiO": FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL + 1e-12}
        )
    ).lab_plume_product_partition()
    breach = partition["ceiling_breach"]
    assert breach["breached"] is True
    assert breach["offending_species"] == ["SiO"]
    assert breach["advisory_only"] is True
    table = breach["source_vapour_ceiling_table"]
    assert len(table) == 9
    sio_row = next(r for r in table if r["legacy_key"] == "SiO")
    assert sio_row["breached"] is True
    assert sio_row["lookup_gas_id"] == "SiO"


def test_ceiling_lookup_accepts_canonical_gas_id() -> None:
    """After _gas rename, FeO_gas in the species map still trips the FeO row."""

    from simulator.vapour_rail.instrumentation import (
        source_vapour_ceiling_lookup_keys,
    )

    feo_row = next(r for r in SOURCE_VAPOUR_CEILING_ROWS if r["legacy_key"] == "FeO")
    keys = source_vapour_ceiling_lookup_keys(feo_row)
    assert "FeO_gas" in keys
    assert "FeO" in keys
    near_melt = {"FeO_gas": 1e-9}
    source_mol = 0.0
    matched = None
    for key in keys:
        if key in near_melt:
            source_mol = float(near_melt[key])
            matched = key
            break
    assert matched == "FeO_gas"
    assert source_mol > float(feo_row["ceiling_mol"])


# ---------------------------------------------------------------------------
# Serialization / setpoints audit / instrumentation panel
# ---------------------------------------------------------------------------


def test_serialize_vapour_batch_channels() -> None:
    batch = _toy_batch({"Na", "K"})
    report = serialize_vapour_batch(batch)
    assert report is not None
    assert report["schema"] == "vapour_batch.v1"
    assert report["n_requested"] == 2
    assert set(report["channels_by_species"]) == {"Na", "K"}
    assert report["channels_by_species"]["Na"]["validation_status"] == (
        "pending_validation"
    )
    assert report["channels_by_species"]["Na"]["is_flux_active"] is True


def test_setpoints_t_cond_audit_covers_operator_overrides() -> None:
    setpoints = yaml.safe_load(
        (ROOT / "data" / "setpoints.yaml").read_text(encoding="utf-8")
    )
    temps = setpoints["condensation_train"]["condensation_temperatures_C"]
    assert set(temps) == AUDITED_OPERATOR_T_COND_SPECIES
    assert "Al" in temps and "Ti" in temps
    # No extra Al/Ti/trace product overrides beyond the audited set.
    assert set(temps) <= AUDITED_OPERATOR_T_COND_SPECIES
    assert SETPOINTS_T_COND_AUDIT["schema"] == "setpoints_t_cond_audit.v1"


def test_vapour_rail_instrumentation_diagnostic_shape() -> None:
    from simulator.diagnostics import vapour_rail_instrumentation_diagnostic
    from web.advisory import vapour_rail_instrumentation_panel_payload

    batch = _toy_batch({"Na"})
    sim = SimpleNamespace(
        _last_vapour_batch=batch,
        _last_vapour_batch_report=serialize_vapour_batch(batch),
        _last_vapour_batch_flux_overlay={
            "shadow_equal": True,
            "shadow_outcome": SHADOW_PROVED,
        },
        _last_vapour_batch_resolve_error={},
        condensation_model=SimpleNamespace(
            last_condensation_refusals_by_species={}
        ),
    )
    diag = vapour_rail_instrumentation_diagnostic(sim)
    assert diag["schema"] == "vapour_rail_instrumentation.v1"
    assert diag["shadow_equal"] is True
    assert diag["shadow_outcome"] == SHADOW_PROVED
    assert len(diag["source_vapour_ceiling_table"]) == 9
    panel = vapour_rail_instrumentation_panel_payload(sim)
    assert panel["diagnostic_only"] is True
    assert panel["n_requested"] == 1
    assert "Na" in panel["channels_by_species"]
    assert panel["channels_by_species"]["Na"]["is_flux_active"] is True


def test_absent_overlay_does_not_default_shadow_equal_true() -> None:
    from simulator.diagnostics import vapour_rail_instrumentation_diagnostic
    from web.advisory import vapour_rail_instrumentation_panel_payload

    sim = SimpleNamespace(
        _last_vapour_batch=None,
        _last_vapour_batch_report=None,
        _last_vapour_batch_flux_overlay={},
        _last_vapour_batch_resolve_error={},
        condensation_model=SimpleNamespace(
            last_condensation_refusals_by_species={}
        ),
    )
    diag = vapour_rail_instrumentation_diagnostic(sim)
    assert diag["shadow_equal"] is None
    panel = vapour_rail_instrumentation_panel_payload(sim)
    assert panel["shadow_equal"] is None


def test_run_artifact_forwards_vr11_terminal_keys() -> None:
    from simulator.accounting.run_artifact import build_run_artifact

    artifact = build_run_artifact(
        {
            "status": "ok",
            "run_metadata": {
                "run_id": "vr11-test",
                "started_at_utc": "2026-07-31T00:00:00Z",
                "feedstock_id": "test",
                "mass_kg": 1.0,
                "backend": "internal-analytical",
            },
            "per_hour_summary": [{"hour": 0, "mass_balance_pct": 0.0}],
            "vapor_pressure_source_report": {
                "total_species": 0,
                "vapor_pressure_backend_status": "ok",
                "authoritative_for_requested_vapor_pressure": True,
            },
            "vapour_rail_instrumentation": {
                "schema": "vapour_rail_instrumentation.v1",
                "shadow_equal": True,
            },
            "condensation_refusals_by_species": {
                "schema": "condensation_refusals.v1",
                "has_refusals": False,
                "n_species": 0,
                "by_species": {},
            },
        },
        run_id="vr11-test",
    )
    terminal = artifact["terminal"]
    assert terminal["vapour_rail_instrumentation"]["shadow_equal"] is True
    assert terminal["condensation_refusals_by_species"]["n_species"] == 0


# ---------------------------------------------------------------------------
# km P1-2 — real evaluator out_of_range / acquisition threading
# ---------------------------------------------------------------------------


def test_serialized_channel_reports_real_out_of_range() -> None:
    """out_of_range must be true when the evaluator conservatively continues."""

    from simulator.vapour_rail.catalog import (
        OUT_OF_RANGE_STATUS,
        compile_vapour_rail_catalog,
    )
    from simulator.vapour_rail.request import VapourResolveState
    from tests.test_vapour_batch_request import _minimal_family, _u0_stub

    payload = _minimal_family(
        "K",
        applicability="applicable",
        request_rule="source_inventory_present",
        source_account="process.cleaned_melt",
        with_reaction=True,
    )
    fam = next(iter(payload["families"].values()))
    # Narrow domain so a hot melt T is out of range.
    sp = fam["physical_properties"]["species"]["K"]
    models = sp.get("pressure_models") or []
    assert models, "fixture must expose pressure_models"
    models[0]["valid_domain"] = {"temperature_K": [900.0, 1100.0]}
    fam["vaporisation_coefficients"]["out_of_range_status"] = OUT_OF_RANGE_STATUS
    fam["vaporisation_coefficients"]["acquisition_flag"] = "acquire:test:K"

    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0}}
    batch = catalog.resolve_batch(
        ledger,
        VapourResolveState(temperature_K=1300.0, process_phase="hot_train"),
    )
    assert "K" in batch.channels_by_species
    answer = batch.channels_by_species["K"]
    # Live path should carry evaluation extras when not refused for other reasons.
    if answer.is_refused:
        pytest.skip(f"channel refused before evaluation: {answer.refusal_code}")
    assert answer.extra.get("out_of_range") is True
    assert answer.extra.get("acquisition_flag") == "acquire:test:K"
    report = serialize_vapour_batch(batch)
    channel = report["channels_by_species"]["K"]
    assert channel["out_of_range"] is True
    assert channel["acquisition_flag"] == "acquire:test:K"


def test_kernel_empty_batch_map_is_not_legacy_fallthrough() -> None:
    """Explicit empty batch map must not fall through to vapor_pressures_Pa."""

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
    from simulator.chemistry.kernel.dto import ProviderAccountView

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Na2O": 1.0}},
        species_formula_registry={},
    )
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=view,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            fO2_log=None,
            control_inputs={
                "vapour_batch_flux_pressures_Pa": {},
                "vapor_pressures_Pa": {"Na": 100.0},
                "overhead_partials_Pa": {},
                "overhead_pressure_pa": 0.0,
                "molar_mass_kg_mol": {"Na": 0.023},
                "stoich_by_species": {
                    "Na": {
                        "parent_oxide": "Na2O",
                        "oxide_per_product_kg": 1.347,
                        "O2_per_product_kg": 0.347,
                    }
                },
                "available_oxide_kg": {"Na": 10.0},
                "melt_surface_area_m2": 0.2,
                "stir_factor": 1.0,
                "alpha": 0.5,
            },
        )
    )
    # Empty batch map → empty flux (ok at T with empty map for low-T path is
    # separate; at 1500 C empty map still returns ok with empty diagnostic).
    assert result.diagnostic.get("evaporation_flux_kg_hr") == {}
    assert result.status in {"ok", "refused", "unavailable"}
    if result.status == "ok":
        # Must not have evaporated Na from the legacy map.
        assert "Na" not in (result.diagnostic.get("evaporation_flux_kg_hr") or {})


def test_b3_production_route_folds_stage_outcomes() -> None:
    """Production CondensationModel.route() folds B3 outcomes (red under revert)."""

    from simulator.state import EvaporationFlux, MeltState

    model = _efficiency_model()
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 10.0},
        gas_temperature_C=1500.0,
        campaign_name="C2A",
    )
    # Force zero residence on every stage so efficiency mints pass-through.
    model.residence_time_s = {
        int(stage.stage_number): 0.0 for stage in model.train.stages
    }
    melt = MeltState(temperature_C=1500.0)
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)
    route = model.route(flux, melt)
    assert model.last_condensation_refusals_by_species, (
        "production fold must populate last_condensation_refusals_by_species"
    )
    sio = model.last_condensation_refusals_by_species.get("SiO")
    assert sio is not None
    assert sio.get("status") == "pass_through"
    assert sio.get("stage_outcomes")
    assert getattr(route, "condensation_refusals_by_species", None) is not None
    assert "SiO" in route.condensation_refusals_by_species
