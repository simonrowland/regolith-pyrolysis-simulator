from types import SimpleNamespace

import pytest

from simulator.accounting import AccountingQueries, AtomLedger
from simulator.accounting.queries import FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL
from simulator.diagnostics import (
    pressure_coating_pareto_diagnostic,
    wall_deposit_remobilization_by_segment_species,
    wall_deposit_sticking_authority_status,
    wall_sticking_alpha_provenance_notice,
)
from simulator.runner import RunnerError, build_sio_yield_report


OXYGEN_CLOSURE_MAX_PCT = 5e-12


def _diagnostic_sim():
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"SiO2": 10.0},
        source="test residual oxygen",
    )
    ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0, "SiO": 2.0},
        source="test overhead oxygen",
    )
    ledger.load_external_mol(
        "terminal.offgas",
        {"O": 0.25, "CO": 0.5},
        source="test terminal offgas oxygen",
    )
    ledger.load_external_mol(
        "process.condensation_train",
        {"SiO2": 3.0, "O2": 0.1},
        source="test condensation oxygen",
    )
    ledger.load_external_mol(
        "process.wall_deposit_segment_holder",
        {"FeO": 4.0, "MgO": 5.0},
        source="test holder oxygen",
    )
    ledger.load_external_mol(
        "process.wall_deposit_segment_window",
        {"SiO2": 1.5},
        source="test window oxygen",
    )
    return SimpleNamespace(atom_ledger=ledger)


def _terminal_oxygen_sim():
    ledger = AtomLedger()
    ledger.load_external_mol(
        "terminal.oxygen_melt_offgas_captured",
        {"O2": 2.0},
        source="test captured terminal oxygen",
    )
    ledger.load_external_mol(
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
        {"O2": 1.0},
        source="test vented terminal oxygen",
    )
    ledger.load_external_mol(
        "terminal.oxygen_melt_offgas_stored",
        {"O2": 0.5},
        source="test stored terminal oxygen",
    )
    return SimpleNamespace(atom_ledger=ledger)


def test_lab_oxygen_atom_partition_closes_with_explicit_residual():
    partition = AccountingQueries(_diagnostic_sim()).lab_oxygen_atom_partition()

    allocated_plus_residual = (
        partition["free_analyzer_visible"]["oxygen_atom_mol"]
        + partition["overhead_vapor_bound"]["oxygen_atom_mol"]
        + partition["condensation_train"]["oxygen_atom_mol"]
        + partition["wall_deposit_segment_by_surface"][
            "total_oxygen_atom_mol"
        ]
        + partition["residual_unallocated_oxygen_atom_mol"]
    )

    assert allocated_plus_residual == pytest.approx(
        partition["total_oxygen_atom_mol"]
    )
    assert partition["free_analyzer_visible"]["oxygen_atom_mol"] == (
        pytest.approx(2.25)
    )
    assert partition["free_analyzer_visible"]["species_mol"] == {
        "O": pytest.approx(0.25),
        "O2": pytest.approx(1.0),
    }
    assert partition["overhead_vapor_bound"]["oxygen_atom_mol"] == (
        pytest.approx(2.5)
    )
    assert partition["overhead_vapor_bound"][
        "species_oxygen_atom_mol"
    ] == {
        "CO": pytest.approx(0.5),
        "SiO": pytest.approx(2.0),
    }
    assert partition["condensation_train"]["oxygen_atom_mol"] == (
        pytest.approx(6.2)
    )
    assert partition["condensation_train"]["species_oxygen_atom_mol"] == {
        "O2": pytest.approx(0.2),
        "SiO2": pytest.approx(6.0),
    }
    assert partition["closure"]["error_pct"] <= OXYGEN_CLOSURE_MAX_PCT
    assert partition["residual_unallocated_oxygen_atom_mol"] == pytest.approx(
        20.0
    )


def test_lab_oxygen_error_budget_is_warn_only_and_decomposed():
    partition = AccountingQueries(
        _terminal_oxygen_sim()
    ).lab_oxygen_atom_partition()
    budget = partition["robinot_o2_error_budget"]

    assert budget["schema"] == "robinot_o2_error_budget.v1"
    assert budget["status"] == "WARN_ONLY"
    assert budget["golden_neutral"] is True
    assert budget["comparison_target"]["exp1_analyzer_visible_o2_kg"] == (
        pytest.approx(35.0e-6)
    )
    assert budget["model_runtime"][
        "free_analyzer_visible_oxygen_atom_mol"
    ] == pytest.approx(7.0)
    assert budget["model_runtime"][
        "free_analyzer_visible_o2_equivalent_kg"
    ] == pytest.approx(7.0 * 15.999 / 1000.0)
    assert budget["model_runtime"]["terminal_oxygen_partition_kg"][
        "captured"
    ] == pytest.approx(2.0 * 2.0 * 15.999 / 1000.0)

    published = budget["published_normalizations"]
    assert published["raw_faithful_source_side_potential"][
        "factor_vs_exp1"
    ] == pytest.approx(0.881913e-3 / 35.0e-6)
    assert published["literature_alpha_top_area_source_side_potential"][
        "factor_vs_exp1"
    ] == pytest.approx(0.656204e-3 / 35.0e-6)
    assert published["literature_alpha_top_area_source_side_potential"][
        "factor_band_vs_exp1"
    ] == [18.25, 19.04]
    assert "different normalization" in published["normalization_note"]

    terms = budget["budget_terms"]
    assert set(terms) == {
        "plume_oxidation",
        "deposit_gettering",
        "melt_redox_retention",
        "post_run_air_oxidation",
        "analyzer_flow_baseline",
    }
    assert terms["plume_oxidation"]["magnitude"]["kind"] == "unquantified"
    assert terms["deposit_gettering"]["magnitude"]["kind"] == (
        "unquantified"
    )
    assert terms["melt_redox_retention"]["magnitude"]["kind"] == (
        "runtime_accounted"
    )
    assert terms["post_run_air_oxidation"]["direction"].startswith(
        "NO_IN_RUN_CLOSURE"
    )
    assert terms["analyzer_flow_baseline"]["magnitude"]["kind"] == (
        "quantified_anchor"
    )
    assert budget["unexplained_residual"][
        "central_missing_free_o2_equivalent_kg"
    ] == pytest.approx(0.621204e-3)
    assert "does not tune or close" in budget["unexplained_residual"][
        "statement"
    ]


def test_lab_oxygen_atom_partition_reports_wall_deposit_by_surface():
    partition = AccountingQueries(_diagnostic_sim()).lab_oxygen_atom_partition()
    surfaces = partition["wall_deposit_segment_by_surface"]["surfaces"]

    assert set(surfaces) == {"holder", "window"}
    assert surfaces["holder"]["account"] == (
        "process.wall_deposit_segment_holder"
    )
    assert surfaces["holder"]["oxygen_atom_mol"] == pytest.approx(9.0)
    assert surfaces["holder"]["species_oxygen_atom_mol"] == {
        "FeO": pytest.approx(4.0),
        "MgO": pytest.approx(5.0),
    }
    assert surfaces["window"]["oxygen_atom_mol"] == pytest.approx(3.0)


def test_sio_yield_report_lab_oxygen_diagnostics_disabled_keeps_report_shape():
    report = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=False,
        allow_unmeasured_alpha_fallback=True,
    )

    assert set(report) == {
        "feedstock_id",
        "campaign",
        "alpha_SiO",
        "alpha_provenance",
        "sio_evolved_kg",
        "sio_to_silica_fume_kg",
        "wall_deposit_kg",
        "fouling_rate",
        "sio_yield_pct_of_feedstock",
        "industrial_benchmark_pct",
        "verdict",
    }
    assert "lab_oxygen_atom_partition" not in report


def test_sio_yield_report_lab_oxygen_sidecar_is_explicit_opt_in():
    with pytest.raises(RunnerError):
        build_sio_yield_report(
            feedstock_id="lunar_mare_low_ti",
            hours=1,
            include_lab_oxygen_diagnostics=True,
            allow_unmeasured_alpha_fallback=True,
        )

    _, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=True,
        include_lab_oxygen_diagnostics=True,
        allow_unmeasured_alpha_fallback=True,
    )

    partition = diagnostics["lab_oxygen_atom_partition"]
    assert partition["closure"]["error_pct"] <= OXYGEN_CLOSURE_MAX_PCT
    assert "residual_unallocated_oxygen_atom_mol" in partition
    assert partition["robinot_o2_error_budget"]["status"] == "WARN_ONLY"
    plume = diagnostics["lab_plume_product_partition"]
    assert plume["schema"] == "rec_w1_02_qms_oes_position_resolved.v1"
    assert "discriminant" in plume


def _plume_diagnostic_sim(
    *,
    overhead: dict[str, float] | None = None,
    terminal_offgas: dict[str, float] | None = None,
    condensation: dict[str, float] | None = None,
):
    ledger = AtomLedger()
    if overhead:
        ledger.load_external_mol(
            "process.overhead_gas",
            overhead,
            source="test plume overhead",
        )
    if terminal_offgas:
        ledger.load_external_mol(
            "terminal.offgas",
            terminal_offgas,
            source="test plume terminal escape",
        )
    if condensation:
        ledger.load_external_mol(
            "process.condensation_train",
            condensation,
            source="test plume condensation",
        )
    return SimpleNamespace(atom_ledger=ledger)


def test_lab_plume_product_partition_stoichiometry_discriminant():
    near_melt_o2 = 1.0
    plume_extent = 0.02
    predicted_outlet_o2 = near_melt_o2 - 0.5 * plume_extent
    partition = AccountingQueries(
        _plume_diagnostic_sim(
            overhead={"O2": near_melt_o2, "SiO": plume_extent},
            condensation={
                "O2": predicted_outlet_o2,
                "SiO2": plume_extent,
            },
        )
    ).lab_plume_product_partition()

    assert partition["near_melt"]["sio"]["species_mol"] == pytest.approx(
        plume_extent
    )
    assert partition["near_melt"]["free_analyzer_oxygen"]["species_mol"] == {
        "O2": pytest.approx(near_melt_o2),
    }
    assert partition["outlet"]["plume_product_proxy"]["species_mol"] == (
        pytest.approx(plume_extent)
    )
    assert partition["outlet"]["plume_product_proxy"]["species"] == "SiO2"
    assert partition["outlet"]["plume_product_proxy"]["provenance"] == (
        "condensation_train_route_product_proxy"
    )
    assert partition["outlet"]["sio"]["species_mol"] == pytest.approx(0.0)
    assert partition["discriminant"]["plume_extent_mol"] == pytest.approx(
        plume_extent
    )
    assert partition["discriminant"]["predicted_outlet_o2_mol"] == (
        pytest.approx(predicted_outlet_o2)
    )
    assert partition["discriminant"]["observed_outlet_o2_mol"] == (
        pytest.approx(predicted_outlet_o2)
    )
    assert (
        partition["discriminant"]["predicted_minus_observed_outlet_o2_mol"]
        == pytest.approx(0.0)
    )
    assert partition["near_melt"]["sio"]["oxygen_atom_mol"] == pytest.approx(
        plume_extent
    )
    assert partition["outlet"]["plume_product_proxy"]["oxygen_atom_mol"] == (
        pytest.approx(2.0 * plume_extent)
    )
    assert partition["near_melt"]["account"] == "process.overhead_gas"


def test_lab_plume_product_partition_ceiling_breach_at_frozen_extent():
    at_ceiling = FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL
    below = AccountingQueries(
        _plume_diagnostic_sim(overhead={"SiO": at_ceiling})
    ).lab_plume_product_partition()
    above = AccountingQueries(
        _plume_diagnostic_sim(overhead={"SiO": at_ceiling + 1e-12})
    ).lab_plume_product_partition()

    assert below["ceiling_breach"]["breached"] is False
    assert below["ceiling_breach"]["offending_species"] == []
    assert above["ceiling_breach"]["breached"] is True
    assert above["ceiling_breach"]["offending_species"] == ["SiO"]


def test_lab_plume_product_partition_denied_major_oxide_nonzero_is_breach():
    partition = AccountingQueries(
        _plume_diagnostic_sim(overhead={"FeO": 1e-9})
    ).lab_plume_product_partition()

    assert partition["ceiling_breach"]["breached"] is True
    assert partition["ceiling_breach"]["offending_species"] == ["FeO"]


def test_lab_plume_product_partition_empty_accounts_are_honest_zeros():
    partition = AccountingQueries(
        _plume_diagnostic_sim()
    ).lab_plume_product_partition()

    assert partition["near_melt"]["free_analyzer_oxygen"]["species_mol"] == {}
    assert partition["near_melt"]["free_analyzer_oxygen"][
        "oxygen_atom_mol"
    ] == pytest.approx(0.0)
    assert partition["near_melt"]["sio"]["species_mol"] == pytest.approx(0.0)
    assert partition["outlet"]["plume_product_proxy"]["species_mol"] == (
        pytest.approx(0.0)
    )
    assert partition["terminal_escape"]["free_analyzer_oxygen"][
        "species_mol"
    ] == {}
    assert partition["discriminant"]["plume_extent_mol"] == pytest.approx(0.0)
    assert partition["discriminant"]["predicted_outlet_o2_mol"] == (
        pytest.approx(0.0)
    )
    assert partition["ceiling_breach"]["breached"] is False


def test_lab_plume_product_partition_terminal_offgas_lands_in_terminal_escape():
    partition = AccountingQueries(
        _plume_diagnostic_sim(
            overhead={"O2": 1.0, "SiO": 0.01},
            terminal_offgas={"O2": 0.5, "SiO": 0.2},
            condensation={"SiO2": 0.01},
        )
    ).lab_plume_product_partition()

    assert partition["near_melt"]["account"] == "process.overhead_gas"
    assert partition["near_melt"]["free_analyzer_oxygen"]["species_mol"] == {
        "O2": pytest.approx(1.0),
    }
    assert partition["near_melt"]["sio"]["species_mol"] == pytest.approx(0.01)
    assert partition["terminal_escape"]["account"] == "terminal.offgas"
    assert partition["terminal_escape"]["free_analyzer_oxygen"][
        "species_mol"
    ] == {"O2": pytest.approx(0.5)}
    assert partition["terminal_escape"]["sio"]["species_mol"] == (
        pytest.approx(0.2)
    )
    assert partition["discriminant"]["near_melt_o2_mol"] == pytest.approx(1.0)


def test_lab_plume_product_partition_outlet_only_sio2_trips_ceiling_breach():
    at_ceiling = FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL
    below = AccountingQueries(
        _plume_diagnostic_sim(condensation={"SiO2": at_ceiling})
    ).lab_plume_product_partition()
    above = AccountingQueries(
        _plume_diagnostic_sim(
            condensation={"SiO2": at_ceiling + 1e-12}
        )
    ).lab_plume_product_partition()

    assert below["ceiling_breach"]["sio_source_proxy_mol"] == pytest.approx(
        at_ceiling
    )
    assert below["ceiling_breach"]["breached"] is False
    assert below["ceiling_breach"]["offending_species"] == []
    assert above["ceiling_breach"]["breached"] is True
    assert above["ceiling_breach"]["offending_species"] == ["SiO"]


def _alpha_provenance(alpha_s):
    return {
        "Na": {
            "stage_1": {
                "alpha_s": alpha_s,
                "citation_status": "CITED",
                "status": "sourced",
                "output_status": "sourced_with_surface_proxy",
            }
        }
    }


@pytest.mark.parametrize("alpha_s", [0.0, 1.0])
def test_sticking_probability_boundaries_remain_authoritative(alpha_s):
    notice = wall_sticking_alpha_provenance_notice(
        {"Na": alpha_s}, _alpha_provenance(alpha_s)
    )
    assert notice["authoritative_for_deposit_mass"] is True


def test_impossible_sticking_probability_is_non_authoritative():
    notice = wall_sticking_alpha_provenance_notice(
        {"Na": 2.0}, _alpha_provenance(2.0)
    )
    authority = wall_deposit_sticking_authority_status(
        {("stage_1", "Na"): 1.0}, notice
    )

    assert notice["severity"] == "warning"
    assert notice["authoritative_for_deposit_mass"] is False
    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["authoritative_for_coating"] is False
    assert authority["authoritative_for_resinter"] is False


def test_segment_deposit_requires_matching_sticking_provenance_record():
    notice = wall_sticking_alpha_provenance_notice(
        {"Na": 0.5},
        {
            "Na": {
                "stage_1": {
                    "alpha_s": 0.5,
                    "citation_status": "CITED",
                    "status": "sourced",
                    "output_status": "sourced_with_surface_proxy",
                }
            }
        },
    )

    authority = wall_deposit_sticking_authority_status(
        {"stage_1": {"Na": 0.1}, "stage_2": {"Na": 0.2}},
        notice,
    )

    assert authority["authoritative_for_deposit_mass"] is False
    assert authority["code"] == "wall_deposit_sticking_alpha_provenance_missing"
    assert authority["missing_alpha_segment_species"] == [
        {"segment": "stage_2", "species": "Na"}
    ]


def test_missing_sticking_provenance_is_warning_and_non_authoritative():
    notice = wall_sticking_alpha_provenance_notice({"Na": 0.5}, {})

    assert notice["code"] == "wall_deposit_sticking_alpha_provenance_missing"
    assert notice["severity"] == "warning"
    assert notice["source_class"] == "assumption_ungrounded_fitted_coefficient"
    assert notice["authoritative_for_deposit_mass"] is False
    assert notice["deposit_output_status"] == "status_bearing"


def _remobilization_sim(snapshots, operating_history, temperatures=None):
    return SimpleNamespace(
        condensation_model=SimpleNamespace(
            operating_history=operating_history,
            condensation_temperatures_C=dict(temperatures or {"Na": 480.0}),
            vapor_pressure_data={},
        ),
        record=SimpleNamespace(snapshots=snapshots),
    )


def _wall_snapshot(hour, delta=None):
    return SimpleNamespace(
        hour=hour,
        wall_deposit_by_segment_species_delta=dict(delta or {}),
    )


def test_campaign_relative_history_aligns_to_global_snapshot_hours():
    segment = "stage_1_to_stage_2"
    sim = _remobilization_sim(
        (
            _wall_snapshot(5, {(segment, "Na"): 1.0}),
            _wall_snapshot(6),
        ),
        [
            {"campaign_hour": 0.0, "pipe_segment_temperatures_C": {segment: 400.0}},
            {"campaign_hour": 1.0, "pipe_segment_temperatures_C": {segment: 600.0}},
        ],
    )

    row = wall_deposit_remobilization_by_segment_species(sim)[segment]["Na"]
    assert row["later_max_T_C"] == pytest.approx(600.0)
    assert row["thermal_remobilization_threshold_exceeded"] is True


def test_late_small_deposit_does_not_erase_earlier_hot_excursion():
    segment = "stage_1_to_stage_2"
    sim = _remobilization_sim(
        (
            _wall_snapshot(1, {(segment, "Na"): 1.0}),
            _wall_snapshot(2),
            _wall_snapshot(3, {(segment, "Na"): 1.0e-6}),
        ),
        [
            {"hour": 1, "pipe_segment_temperatures_C": {segment: 400.0}},
            {"hour": 2, "pipe_segment_temperatures_C": {segment: 600.0}},
            {"hour": 3, "pipe_segment_temperatures_C": {segment: 400.0}},
        ],
    )

    row = wall_deposit_remobilization_by_segment_species(sim)[segment]["Na"]
    assert row["deposited_kg"] == pytest.approx(1.000001)
    assert row["deposit_first_hour"] == 1
    assert row["deposit_last_hour"] == 3
    assert row["later_max_T_C"] == pytest.approx(600.0)
    assert row["thermal_remobilization_threshold_exceeded"] is True
    assert row["re_evaporated_kg"] is None


def test_missing_wall_product_temperature_is_unavailable_not_500_c():
    segment = "stage_1_to_stage_2"
    sim = _remobilization_sim(
        (_wall_snapshot(1, {(segment, "SiO2"): 1.0}),),
        [{"hour": 1, "pipe_segment_temperatures_C": {segment: 600.0}}],
        temperatures={},
    )

    row = wall_deposit_remobilization_by_segment_species(sim)[segment]["SiO2"]
    assert row["status"] == "unavailable"
    assert row["condensation_T_C"] is None


def test_pressure_coating_pareto_refuses_missing_knudsen_provenance():
    diagnostic = pressure_coating_pareto_diagnostic(
        SimpleNamespace(), target_species=()
    )

    assert diagnostic["status"] == "unavailable"
    assert diagnostic["reason"] == "knudsen_regime_diagnostic_unavailable"
    assert diagnostic["gate"] == {"status": "unavailable"}
    assert diagnostic["current"] == {"status": "unavailable"}


def test_pressure_coating_current_kn_and_regime_share_controlling_segment():
    knudsen = {
        "gas_temperature_C": 1000.0,
        "carrier_gas": "N2",
        "overhead_pressure_mbar": 10.0,
        "pipe_diameter_m": 0.12,
        "knudsen_number": 0.001,
        "regime": "free_molecular",
        "segments": [
            {
                "name": "main",
                "characteristic_length_m": 0.12,
                "knudsen_number": 0.001,
                "regime": "viscous",
            },
            {
                "name": "narrow",
                "characteristic_length_m": 0.01,
                "knudsen_number": 0.12,
                "regime": "free_molecular",
            },
        ],
    }
    sim = SimpleNamespace(
        condensation_model=SimpleNamespace(
            last_knudsen_regime_diagnostic=knudsen,
            gas_temperature_C=1000.0,
            carrier_gas="N2",
        )
    )

    diagnostic = pressure_coating_pareto_diagnostic(sim, target_species=())

    assert diagnostic["gate"]["controlling_segment"] == "narrow"
    assert diagnostic["current"]["segment"] == "narrow"
    assert diagnostic["current"]["knudsen_number"] == pytest.approx(0.12)
    assert diagnostic["current"]["regime"] == "free_molecular"
