from __future__ import annotations

import copy
import hashlib
import json

import pytest

from simulator.accounting.queries import wall_deposit_candidate_for_surface_kg
from simulator.coating_rate import continuous_wall_deposition_flux
from simulator.condensation import (
    CondensationModel,
    VAPOR_PRESSURE_DATA,
    WallSaturationPressureRefusal,
    _series_resistance_deposition_flux_mol_m2_s,
    _wall_deposition_driving_pressure_pa,
)
from simulator.core import CondensationTrain
from simulator.runner import (
    PyrolysisRun,
    _wall_deposit_rate_from_delta,
    _with_wall_deposit_rate_diagnostics,
)
from simulator.state import HourSnapshot, PipeSegment


def test_continuous_rate_matches_design_example_components() -> None:
    collision_coefficient = 3.1e-6 / 3.0
    sticking = 0.6
    gas_resistance = (1.86e-6 / 1.8e-6 - 1.0) / (
        sticking * collision_coefficient
    )

    rate = continuous_wall_deposition_flux(
        bulk_pressure_pa=3.0,
        equilibrium_pressure_pa=1.0,
        collision_coefficient_mol_m2_s_pa=collision_coefficient,
        sticking_coefficient=sticking,
        gas_resistance_pa_m2_s_mol=gas_resistance,
        wall_temperature_K=1123.15,
    )

    assert rate.collision_mol_m2_s == pytest.approx(3.1e-6)
    assert rate.stuck_mol_m2_s == pytest.approx(1.8e-6)
    assert rate.reevaporated_mol_m2_s == pytest.approx(0.6e-6)
    assert rate.net_mol_m2_s == pytest.approx(1.2e-6)


def test_continuous_rate_limiting_cases() -> None:
    common = {
        "bulk_pressure_pa": 4.0,
        "equilibrium_pressure_pa": 1.0,
        "collision_coefficient_mol_m2_s_pa": 2.0e-6,
        "wall_temperature_K": 1000.0,
    }
    free = continuous_wall_deposition_flux(
        **common,
        sticking_coefficient=0.5,
        gas_resistance_pa_m2_s_mol=0.0,
    )
    transport_limited = continuous_wall_deposition_flux(
        **common,
        sticking_coefficient=0.5,
        gas_resistance_pa_m2_s_mol=1.0e9,
    )
    non_sticking = continuous_wall_deposition_flux(
        **common,
        sticking_coefficient=0.0,
        gas_resistance_pa_m2_s_mol=0.0,
    )
    equilibrium = continuous_wall_deposition_flux(
        **{**common, "equilibrium_pressure_pa": 4.0},
        sticking_coefficient=0.5,
        gas_resistance_pa_m2_s_mol=0.0,
    )

    assert free.net_mol_m2_s == pytest.approx(3.0e-6)
    assert 0.0 < transport_limited.net_mol_m2_s < free.net_mol_m2_s
    assert non_sticking.net_mol_m2_s == 0.0
    assert equilibrium.net_mol_m2_s == pytest.approx(0.0)


def test_continuous_rate_accepts_distinct_reevaporation_flux() -> None:
    rate = continuous_wall_deposition_flux(
        bulk_pressure_pa=3.0,
        equilibrium_pressure_pa=1.0,
        collision_coefficient_mol_m2_s_pa=1.0e-6,
        sticking_coefficient=0.5,
        gas_resistance_pa_m2_s_mol=0.0,
        wall_temperature_K=1200.0,
        reevaporation_flux_mol_m2_s=0.2e-6,
    )

    assert rate.stuck_mol_m2_s == pytest.approx(1.5e-6)
    assert rate.reevaporated_mol_m2_s == pytest.approx(0.2e-6)
    assert rate.net_mol_m2_s == pytest.approx(1.3e-6)


def test_zero_sticking_preserves_explicit_reevaporation() -> None:
    rate = continuous_wall_deposition_flux(
        bulk_pressure_pa=3.0,
        equilibrium_pressure_pa=1.0,
        collision_coefficient_mol_m2_s_pa=1.0e-6,
        sticking_coefficient=0.0,
        gas_resistance_pa_m2_s_mol=5.0e5,
        wall_temperature_K=1200.0,
        reevaporation_flux_mol_m2_s=4.0e-7,
    )

    assert rate.stuck_mol_m2_s == 0.0
    assert rate.reevaporated_mol_m2_s == pytest.approx(4.0e-7)
    assert rate.net_mol_m2_s == pytest.approx(-4.0e-7)


def test_production_series_resistance_exports_rate_decomposition() -> None:
    diagnostic: dict[str, float] = {}
    flux = _series_resistance_deposition_flux_mol_m2_s(
        "SiO",
        100.0,
        1500.0,
        0.7,
        pipe_diameter_m=0.12,
        stir_factor=1.0,
        regime_factor=0.0,
        T_gas_K=1700.0,
        overhead_pressure_pa=1000.0,
        diagnostic_out=diagnostic,
    )

    assert diagnostic["net_mol_m2_s"] == pytest.approx(flux)
    assert diagnostic["stuck_mol_m2_s"] - diagnostic[
        "reevaporated_mol_m2_s"
    ] == pytest.approx(flux)
    assert diagnostic["wall_temperature_K"] == 1500.0


def test_cold_wall_below_certified_antoine_range_deposits() -> None:
    vapor_pressure_data = copy.deepcopy(VAPOR_PRESSURE_DATA)
    fe_antoine = vapor_pressure_data["metals"]["Fe"]["pure_component_antoine"]
    fe_antoine["source_certified_range_K"] = [1800.0, 3100.0]
    fe_antoine["extrapolation_policy"] = "refuse"
    diagnostic: dict[str, object] = {}

    flux = _series_resistance_deposition_flux_mol_m2_s(
        "Fe",
        100.0,
        900.0,
        0.7,
        regime_factor=1.0,
        T_gas_K=1700.0,
        vapor_pressure_data=vapor_pressure_data,
        diagnostic_out=diagnostic,
    )

    assert flux > 0.0
    assert diagnostic["net_mol_m2_s"] == pytest.approx(flux)
    assert diagnostic["wall_saturation_pressure_pa"] == pytest.approx(0.0)
    assert diagnostic["wall_saturation_pressure_refused"] is False

    model = CondensationModel(
        CondensationTrain.create_default(),
        vapor_pressure_data=vapor_pressure_data,
        wall_temperature_C=626.85,
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Fe": 1.0},
        gas_temperature_C=1426.85,
        campaign_name="C0",
    )
    segment = PipeSegment(
        name="cold_fe_test",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=626.85,
        length_m=1.0,
        inner_diameter_m=0.12,
    )
    wall_deposit_kg = wall_deposit_candidate_for_surface_kg(
        model,
        species="Fe",
        rate_kg_hr=1.0,
        T_cond_C=model.condensation_temperatures_C["Fe"],
        melt_temperature_C=1700.0,
        wall_temperature_C=segment.wall_temperature_C,
        surface_area_m2=segment.surface_area_m2,
        segment=segment,
    )

    assert wall_deposit_kg > 0.0


def test_hot_wall_above_certified_antoine_range_is_status_bearing() -> None:
    vapor_pressure_data = copy.deepcopy(VAPOR_PRESSURE_DATA)
    fe_antoine = vapor_pressure_data["metals"]["Fe"]["pure_component_antoine"]
    fe_antoine["source_certified_range_K"] = [1800.0, 3100.0]
    fe_antoine["extrapolation_policy"] = "refuse"
    diagnostic: dict[str, object] = {}

    flux = _series_resistance_deposition_flux_mol_m2_s(
        "Fe",
        100.0,
        3200.0,
        0.7,
        regime_factor=1.0,
        T_gas_K=3200.0,
        vapor_pressure_data=vapor_pressure_data,
        diagnostic_out=diagnostic,
    )

    assert flux == pytest.approx(0.0)
    assert diagnostic["wall_saturation_pressure_refused"] is True
    assert (
        diagnostic["wall_saturation_pressure_refusal_reason"]
        == "above_source_certified_range"
    )


def test_missing_wall_antoine_data_raises_typed_refusal(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.condensation._species_vapor_data",
        lambda *args, **kwargs: {
            "total_source_certified_range_K": [1800.0, 3100.0]
        },
    )

    with pytest.raises(
        WallSaturationPressureRefusal,
        match="reason=antoine_data_unavailable",
    ):
        _wall_deposition_driving_pressure_pa(
            "Fe",
            100.0,
            900.0,
        )


def test_unusable_wall_antoine_coefficients_raise_typed_refusal(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "simulator.condensation._species_vapor_data",
        lambda *args, **kwargs: {
            "pure_component_antoine": {"A": 1.0, "B": "invalid", "C": 0.0}
        },
    )

    with pytest.raises(
        WallSaturationPressureRefusal,
        match="reason=source_certified_range_refused",
    ):
        _wall_deposition_driving_pressure_pa(
            "Fe",
            100.0,
            900.0,
        )


def test_committed_rate_projection_exports_mol_s_and_kg_h() -> None:
    by_segment, by_species = _wall_deposit_rate_from_delta(
        {"duct": {"SiO": 0.00456}},
        dt_h=24.0,
    )

    sio = by_segment["duct"]["SiO"]
    assert sio["kg_h"] == pytest.approx(0.00019)
    assert sio["mol_s"] > 0.0
    assert by_species == {"SiO": pytest.approx(0.00019)}


def test_serialized_rate_fields_preserve_committed_and_shadow_values() -> None:
    snapshot = HourSnapshot(
        wall_deposition_rate_shadow_candidate={
            "duct": {
                "SiO": {
                    "mol_s": 1.2e-6,
                    "available_supply_mol_s": 2.0e-6,
                    "uncapped_transport_capacity_mol_s": 1.2e-6,
                    "uncertainty": {
                        "p05_mol_s": None,
                        "p50_mol_s": 1.2e-6,
                        "p95_mol_s": None,
                    },
                    "authoritative_for_lifespan": False,
                }
            }
        }
    )
    row = _with_wall_deposit_rate_diagnostics(
        {"wall_deposit_delta_kg": {"duct": {"SiO": 0.00456}}},
        snapshot,
        dt_h=24.0,
    )

    assert row["wall_deposition_rate_committed"]["dt_h"] == 24.0
    assert row["wall_deposition_rate_committed"]["by_segment_species"][
        "duct"
    ]["SiO"]["kg_h"] == pytest.approx(0.00019)
    assert row["wall_deposition_rate_shadow_candidate"][
        "by_segment_species"
    ]["duct"]["SiO"]["mol_s"] == pytest.approx(1.2e-6)
    assert row["wall_deposition_rate_shadow_candidate"]["dt_h"] == 24.0
    assert row["wall_deposit_rate_by_species_kg_h"]["SiO"] == pytest.approx(
        0.00019
    )


def test_opt_in_diagnostics_are_golden_neutral() -> None:
    kwargs = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C0",
        "hours": 1,
        "allow_fallback_vapor": True,
        "allow_unmeasured_alpha_fallback": True,
        "run_metadata_overrides": {
            "started_at_utc": "2026-07-19T00:00:00Z",
            "kernel_commit_sha": "t056-golden-neutral",
        },
    }
    baseline = PyrolysisRun(**kwargs).run()
    diagnostic = PyrolysisRun(
        **kwargs,
        include_wall_deposit_rate_diagnostics=True,
    ).run()

    assert "wall_deposition_rate_committed" not in baseline["final"]
    assert "wall_fouling_lifespan" not in baseline["final"]
    assert all(
        "wall_deposition_rate_committed" not in row
        and "wall_deposition_rate_shadow_candidate" not in row
        and "wall_deposit_rate_by_segment_species_kg_h" not in row
        and "wall_deposit_rate_by_species_kg_h" not in row
        for row in baseline["per_hour_summary"]
    )
    assert "wall_deposition_rate_committed" in diagnostic["final"]
    assert "wall_fouling_lifespan" in diagnostic["final"]
    assert all(
        "wall_deposition_rate_committed" in row
        and "wall_deposition_rate_shadow_candidate" in row
        and "wall_deposit_rate_by_segment_species_kg_h" in row
        and "wall_deposit_rate_by_species_kg_h" in row
        for row in diagnostic["per_hour_summary"]
    )

    stripped = copy.deepcopy(diagnostic)
    stripped["final"].pop("wall_deposition_rate_committed")
    lifespan = stripped["final"].pop("wall_fouling_lifespan")
    for row in stripped["per_hour_summary"]:
        row.pop("wall_deposition_rate_committed")
        row.pop("wall_deposition_rate_shadow_candidate")
        row.pop("wall_deposit_rate_by_segment_species_kg_h")
        row.pop("wall_deposit_rate_by_species_kg_h")

    assert stripped == baseline
    assert lifespan["input_basis"] == (
        "execution_local_committed_rate_per_campaign"
    )
    assert lifespan["authoritative_for_selection"] is False
    assert "campaigns_to_resinter" in lifespan
    assert "verdict" in lifespan


def test_coating_diagnostic_default_output_is_byte_identical_to_golden() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )

    payload = run.run()
    # b-238 (2026-08-26): melt_redox_gate_floor_fallback_engagement counts
    # _melt_redox_liquidus_floor_fallback engagements, and that count depends
    # on whether _freeze_gate_curve() hits its cross-run cache — measured
    # total_count 14 (cold) vs 0 (warm) from the SAME tree in back-to-back
    # processes. A byte-identity pin over a cache-sensitive counter flaps red
    # and green with zero code change, so the block is asserted by SHAPE here
    # and excluded from the hash; the hash keeps pinning everything else.
    engagement = payload.pop("melt_redox_gate_floor_fallback_engagement")
    assert set(engagement) == {"engaged", "total_count", "by_hour"}
    assert isinstance(engagement["engaged"], bool)
    assert isinstance(engagement["total_count"], int)
    assert engagement["total_count"] >= 0
    assert engagement["engaged"] == (engagement["total_count"] > 0)

    actual_bytes = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    # Rebaselined 2026-08-18 (t-686) with the cause named, not to make a red
    # test pass. The prior digest 6e75e35e... was produced by the pre-t-470
    # rail; TWO intentional rail commits moved this whole-run hash, both
    # bisected in one tree with the machine-local engines.local.toml held
    # constant so the comparison could not be confounded:
    #
    #   2918b531 feat(rail): six analytical carriers live -- FIRST digest
    #     mover. vapour_batch.n_requested 15 -> 21 (Al2O, AlO, CaO_gas, CrO3,
    #     TiO, TiO2_gas). Run still status=ok over 24 h. Its parent 313a7d49
    #     still hashes to 6e75e35e..., which is what pins the attribution.
    #
    #   dd62edf0 fix(rail): t-470 -- transitional-Knudsen band refuses instead
    #     of silently zeroing -- FIRST status mover. status ok -> refused,
    #     reason "" -> viscous_p_bulk_transport_out_of_domain, run length
    #     24 h -> 14 h. End-of-run Kn 0.000273 (viscous, 10 mbar) -> 4.8646
    #     (transitional, 0.000368 mbar). Its parent 23d48442 already reaches
    #     Kn 4.8646 at hour 14 and used to continue straight through it.
    #
    # So the refusal is the HONEST number and the old 24-hour completion was
    # the silent one. Nondeterminism is ruled out: two dumps at the tip are
    # byte-identical, and checking out the pin commit in this same tree
    # reproduces 6e75e35e... exactly.
    #
    # ★ WHAT THIS DIGEST NOW PINS: the default C0 recipe DIES AT HOUR 14
    # because overhead pressure falls through the viscous-flow band that
    # mandate section 4 requires (Kn well below 0.01). That is a recipe
    # question -- raise pN2 before Kn crosses -- not a golden question, and it
    # is tracked separately. This pin records what the code does; it does not
    # bless the recipe.
    #   sc130 wave 2 (2026-08-26) — THIRD intentional mover, prose-only.
    #     1487 string leaves changed and ZERO numeric leaves (verified by a
    #     typed structural diff of the full 6 MB payload, base vs wave-2):
    #     melt_activity.py bucket-A citation/limitation corrections flow into
    #     reported_activity_provenance / evidence_ref strings in this
    #     serialization. Same run, same physics, same numbers. The hash also
    #     changes shape here because the cache-sensitive engagement block
    #     moved out of the hashed payload (b-238, above).
    #     Recomputed on the CI machine class (mac-studio-256-1, ci-jobs tree).
    assert hashlib.sha256(actual_bytes).hexdigest() == (
        "368c71334cd96c450ae82171eb07dc5064addad0637ccbf83bbabe7eede839a8"
    )


def test_positive_production_diagnostic_and_resumed_session_are_local() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        sio_start_temperature_c=1600.0,
        sio_hold_temperature_c=1600.0,
        sio_liner_temperature_c=1050.0,
        sio_pO2_mbar=0.0,
        include_wall_deposit_rate_diagnostics=True,
    )
    session = run._start_session()

    first = run._run_session(session)
    second = run._run_session(session)

    for payload in (first, second):
        row = payload["per_hour_summary"][0]
        assert row["wall_deposit_rate_by_species_kg_h"]
        shadow = row["wall_deposition_rate_shadow_candidate"]
        assert shadow["dt_h"] == 1.0
        assert shadow["by_segment_species"]
        assert all(
            record["parameter_status"]
            in {
                "measured_same_surface",
                "measured_proxy_surface",
                "bounded_prior",
                "uncalibrated",
            }
            for species_records in shadow["by_segment_species"].values()
            for record in species_records.values()
        )
        assert all(
            record["supply_limited"]
            == (
                record["kg_h"]
                < record["uncapped_transport_capacity_kg_h"]
            )
            and record["transport_limited"]
            == (
                record["uncapped_transport_capacity_kg_h"]
                <= record["kg_h"]
            )
            for species_records in shadow["by_segment_species"].values()
            for record in species_records.values()
        )
        committed = payload["final"]["wall_deposition_rate_committed"]
        assert committed["dt_h"] == 1.0
        assert committed["by_segment_species"]
        lifespan = payload["final"]["wall_fouling_lifespan"]
        assert lifespan["authoritative_for_resinter"] is False
        assert lifespan["verdict"] == "non-authoritative"

    assert first["final"]["wall_fouling_lifespan"][
        "existing_wall_load_kg_by_segment"
    ] == {}
    assert second["final"]["wall_fouling_lifespan"][
        "existing_wall_load_kg_by_segment"
    ]
    first_lifespan = first["final"]["wall_fouling_lifespan"]
    second_lifespan = second["final"]["wall_fouling_lifespan"]
    assert first_lifespan["campaign_equivalents_observed"] == pytest.approx(1 / 30)
    assert second_lifespan["campaign_equivalents_observed"] == pytest.approx(1 / 30)
    assert second_lifespan["wall_deposit_kg_per_campaign"] == pytest.approx(
        sum(
            sum(values["kg_h"] for values in species.values())
            for species in second["final"]["wall_deposition_rate_committed"][
                "by_segment_species"
            ].values()
        )
        * 30
    )


def test_shadow_rate_shares_species_supply_across_segments() -> None:
    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        sio_start_temperature_c=1600.0,
        sio_hold_temperature_c=1600.0,
        sio_liner_temperature_c=1050.0,
        sio_pO2_mbar=0.0,
        include_wall_deposit_rate_diagnostics=True,
    ).run()

    shadow = payload["per_hour_summary"][0][
        "wall_deposition_rate_shadow_candidate"
    ]["by_segment_species"]
    records_by_species = {}
    for segment in shadow.values():
        for species, record in segment.items():
            records_by_species.setdefault(species, []).append(record)

    assert records_by_species["Na"]
    assert all(
        float(record["mol_s"]) == 0.0
        and float(record["species_partial_pressure_pa"])
        <= float(record["total_pressure_pa"])
        and record["supersaturated"] is False
        for record in records_by_species["Na"]
    )
    assert len(records_by_species["SiO"]) > 1
    for species, records in records_by_species.items():
        available_supply_mol_s = max(
            float(record["available_supply_mol_s"])
            for record in records
        )
        assert sum(float(record["mol_s"]) for record in records) <= (
            available_supply_mol_s * (1.0 + 1.0e-12)
        ), species
        assert all(
            record["uncertainty"]["p50_mol_s"]
            == pytest.approx(record["mol_s"])
            for record in records
        )


def test_opt_in_refusal_envelope_stays_serializable() -> None:
    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        runtime_campaign_overrides={"C0": {"o2_bubbler_kg_per_hr": -1.0}},
        include_wall_deposit_rate_diagnostics=True,
    ).run()

    assert payload["status"] == "refused"
    assert payload["final"]["wall_deposition_rate_committed"] == {
        "source": "committed_ledger_delta_divided_by_explicit_dt",
        "dt_h": 0,
        "by_segment_species": {},
    }
    assert all(
        "wall_deposition_rate_committed" not in row
        for row in payload["per_hour_summary"]
    )
