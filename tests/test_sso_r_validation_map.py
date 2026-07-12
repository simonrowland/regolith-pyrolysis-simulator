from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from simulator.chemistry.kernel import ProviderUnavailableError
from simulator.core import FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS
from simulator.optimize import sso_r_owner_surface
from scripts import sso_r_validation_map as validation_map


# stateful validation-map smoke fixture errors under xdist coscheduling.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

GOLDEN_PATH = Path("tests/goldens/sso_r_validation_map_lunar_mare_low_ti.json")


@pytest.fixture(scope="module")
def smoke_payload():
    return validation_map.run_validation_map(smoke=True)


def test_full_grid_count_is_1512_rows():
    assert len(validation_map.full_grid()) == 1512
    assert validation_map.expected_grid_count(smoke=False) == 1512
    assert validation_map.expected_grid_count(smoke=True) == 36


def test_owner_recipe_surface_constants_are_shared():
    assert validation_map.OWNER_RECIPE_PO2_MBAR == pytest.approx(
        sso_r_owner_surface.OWNER_RECIPE_PO2_MBAR
    )
    assert validation_map.OWNER_RECIPE_PN2_MBAR == pytest.approx(
        sso_r_owner_surface.OWNER_RECIPE_PN2_MBAR
    )
    assert validation_map.OWNER_RECIPE_TOTAL_PRESSURE_MBAR == pytest.approx(
        sso_r_owner_surface.OWNER_RECIPE_TOTAL_PRESSURE_MBAR
    )


def _assertion(payload, name):
    return {a["name"]: a for a in payload["assertions"]}[name]


def _calibrated_inputs():
    setpoints, feedstocks, vapor_pressures = validation_map._load_data()
    calibration = validation_map.calibrate_dose(
        setpoints,
        feedstocks,
        vapor_pressures,
    )
    return setpoints, feedstocks, vapor_pressures, calibration


def _is_owner_recipe_row(row):
    return (
        row["temperature_C"] == pytest.approx(validation_map.OWNER_RECIPE_T_C)
        and row["requested_pO2_mbar"] == pytest.approx(validation_map.OWNER_RECIPE_PO2_MBAR)
        and row["requested_pN2_mbar"] == pytest.approx(validation_map.OWNER_RECIPE_PN2_MBAR)
        and row["dose_fraction_of_full_FeO_equiv"] == pytest.approx(1.0)
    )


def _owner_recipe_row(payload):
    return next(row for row in payload["rows"] if _is_owner_recipe_row(row))


def test_manual_fO2_anchors_match_native_fe_design_window():
    anchors = {
        anchor["fO2_log"]: anchor
        for anchor in validation_map.manual_fO2_anchors()
    }

    assert anchors[-9.0]["reference_source"].startswith("docs-private/research/")
    assert anchors[-9.5]["reference_source"].startswith("docs-private/research/")
    assert anchors[-9.0]["native_fe_frac"] == pytest.approx(
        anchors[-9.0]["reference_native_fe_frac"],
        rel=0.0,
        abs=anchors[-9.0]["reference_abs_tolerance"],
    )
    assert anchors[-9.5]["native_fe_frac"] == pytest.approx(
        anchors[-9.5]["reference_native_fe_frac"],
        rel=0.0,
        abs=anchors[-9.5]["reference_abs_tolerance"],
    )
    assert anchors[-9.0]["diagnostic_only"] is True
    assert anchors[-9.5]["diagnostic_only"] is True


def test_grid_count_assertion_uses_requested_grid_size(smoke_payload):
    assert _assertion(smoke_payload, "grid_count")["passed"] is True
    assert _assertion(smoke_payload, "grid_count")["detail"] == (
        "rows=36 expected=36 scope=36-smoke"
    )

    truncated = smoke_payload["rows"][:-1]
    smoke_assertions = validation_map.evaluate_assertions(
        truncated,
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=True),
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
    )
    full_assertions = validation_map.evaluate_assertions(
        smoke_payload["rows"],
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=False),
        grid_scope_label=validation_map.GRID_SCOPE_FULL,
    )

    assert {a["name"]: a for a in smoke_assertions}["grid_count"]["passed"] is False
    full_grid_count = {a["name"]: a for a in full_assertions}["grid_count"]
    assert full_grid_count["passed"] is False
    assert full_grid_count["detail"] == "rows=36 expected=1512 scope=1512-full"


def test_corrected_monotonic_metrics_report_native_response(smoke_payload):
    pO2 = _assertion(smoke_payload, "pO2_SiO_suppression_monotonicity")
    dose = _assertion(smoke_payload, "dose_reduction_monotonicity")

    assert pO2["passed"] is True
    assert "sio_nonincreasing_failures=0" in pO2["detail"]
    assert "native_response_nonmonotone_reported=" in pO2["detail"]
    assert dose["passed"] is True
    assert "feo_reduced_failures=0" in dose["detail"]
    assert "native_response_nonmonotone_reported=" in dose["detail"]


def test_monotonicity_assertions_are_not_vacuous(smoke_payload):
    # The smoke grid exercises the pO2 and dose axes but has a single pN2
    # point: the pN2 assertion must say so instead of passing silently.
    pO2 = _assertion(smoke_payload, "pO2_SiO_suppression_monotonicity")
    dose = _assertion(smoke_payload, "dose_reduction_monotonicity")
    pn2 = _assertion(smoke_payload, "pN2_escape_monotonicity")

    assert "qualifying_slices=9" in pO2["detail"]
    assert "qualifying_slices=12" in dose["detail"]
    assert pn2["passed"] is True
    assert "qualifying_slices=0" in pn2["detail"]
    assert "not_exercised_on_smoke_grid" in pn2["detail"]

    # Under the full-grid scope label, zero qualifying slices is a harness
    # defect and must FAIL loud, not pass vacuously.
    full_assertions = validation_map.evaluate_assertions(
        smoke_payload["rows"],
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=False),
        grid_scope_label=validation_map.GRID_SCOPE_FULL,
    )
    full_pn2 = {a["name"]: a for a in full_assertions}["pN2_escape_monotonicity"]
    assert full_pn2["passed"] is False
    assert "qualifying_slices=0" in full_pn2["detail"]


def test_smoke_rows_have_owner_readable_schema_and_source_label_reader(
    smoke_payload,
):
    assert smoke_payload["grid"]["row_count"] == 36
    assert smoke_payload["grid"]["expected_row_count"] == 36
    assert smoke_payload["grid_scope"] == validation_map.GRID_SCOPE_SMOKE
    row = smoke_payload["rows"][0]

    required = {
        "grid_scope",
        "sample_time_h",
        "temperature_C",
        "requested_pO2_mbar",
        "requested_pN2_mbar",
        "total_pressure_mbar",
        "gas_regime",
        "transport_property_basis",
        "map_scope_note",
        "dose_species",
        "dose_kg",
        "post_exchange_fO2_log_diagnostic",
        "post_exchange_delta_IW_diagnostic",
        "fO2_diagnostic_status",
        "redox_source_terms_mol_o2_equiv_by_label",
        "redox_source_reader",
        "native_fe_event_type",
        "native_fe_pool_mol",
        "native_fe_tap_mol",
        "native_fe_vapor_mol",
        "native_fe_vapor_escape_fraction_denominator",
        "retained_FeO_mol",
        "retained_Fe2O3_mol",
        "retained_native_Fe_mol",
        "redox_source_delta_ln_fO2",
        "redox_source_skip_reason",
        "Fe_vapor_kg_hr",
        "SiO_flux_kg_hr",
        "SiO_vapor_pressure_Pa",
        "SiO_P_reference_Antoine_Pa",
        "SiO_activity_factor",
        "SiO_provider_pO2_bar",
        "SiO_alpha_s",
        "SiO_alpha_effective",
        "SiO_r_interface",
        "SiO_r_gas",
        "SiO_r_melt",
        "melt_surface_area_m2",
        "freeze_gate_liquid_fraction_factor",
        "SiO_provider_flux_pre_depletion_kg_hr",
        "SiO_flux_pre_analytic_depletion_kg_hr",
        "SiO_flux_post_analytic_depletion_kg_hr",
        "redox_source_applied_terms_mol_o2_equiv_by_label",
        "redox_source_skipped_terms_mol_o2_equiv_by_label",
        "redox_source_skipped_reasons_by_label",
        "redox_source_refusal_context",
        "stage_3_Fe_wt_pct",
        "stage_3_SiO2_capture_kg",
        "oxygen_reservoir_exchange_direction",
        "oxygen_reservoir_exchange_o2_mol",
        "mass_balance_error_pct",
    }
    assert required <= set(row)
    assert "fO2_log" not in row
    assert row["fO2_diagnostic_status"].startswith("diagnostic_only")
    assert row["native_fe_vapor_escape_fraction_denominator"] == (
        "native_fe_pool_mol"
    )
    assert row["redox_source_reader"] == (
        "runner.build_per_hour_summary.redox_source_breakdown"
    )

    labeled = [
        r for r in smoke_payload["rows"]
        if r["redox_source_terms_mol_o2_equiv_by_label"]
    ]
    assert labeled
    for labeled_row in labeled:
        assert labeled_row["redox_source_reader"]


def test_sio_vapor_pressure_responds_to_requested_po2(smoke_payload):
    rows = [
        row for row in smoke_payload["rows"]
        if row["temperature_C"] == pytest.approx(1650.0)
        and row["requested_pN2_mbar"] == pytest.approx(10.0)
        and row["dose_fraction_of_full_FeO_equiv"] == pytest.approx(1.0)
    ]
    by_pO2 = {float(row["requested_pO2_mbar"]): row for row in rows}
    low = by_pO2[1.0e-6]
    high = by_pO2[1.0]

    assert low["SiO_provider_pO2_bar"] == pytest.approx(1.0e-9)
    assert high["SiO_provider_pO2_bar"] == pytest.approx(1.0e-3)
    assert low["SiO_P_reference_Antoine_Pa"] == pytest.approx(
        high["SiO_P_reference_Antoine_Pa"]
    )
    assert low["SiO_activity_factor"] == pytest.approx(
        high["SiO_activity_factor"]
    )
    assert low["SiO_vapor_pressure_Pa"] / high["SiO_vapor_pressure_Pa"] == (
        pytest.approx(math.sqrt(1.0e-3 / 1.0e-9), rel=1.0e-6)
    )
    assert low["SiO_flux_kg_hr"] > high["SiO_flux_kg_hr"] * 100.0


def test_exact_full_dose_oxidizing_pn2_row_refuses_absent_melt_redox_capacity():
    setpoints, feedstocks, vapor_pressures, calibration = _calibrated_inputs()

    row = validation_map.run_row(
        1600.0,
        validation_map.GasPoint(0.1, 5.0, "n2_carrier"),
        1.0,
        calibration,
        setpoints,
        feedstocks,
        vapor_pressures,
        grid_scope_label=validation_map.GRID_SCOPE_FULL,
    )

    assert math.isfinite(row["post_exchange_fO2_log_diagnostic"])
    assert math.isfinite(row["redox_source_delta_ln_fO2"])
    assert row["redox_source_skip_reason"] == "no_melt_redox_capacity"
    assert row["redox_source_skipped_terms_mol_o2_equiv_by_label"][
        "redox_source:evaporative_metal_loss"
    ] > 0.0
    assert row["redox_source_skipped_reasons_by_label"][
        "redox_source:evaporative_metal_loss"
    ] == "no_melt_redox_capacity"
    assert row["redox_source_refusal_context"] == {}


def test_axis_covering_validation_rows_never_emit_nonfinite_fo2():
    setpoints, feedstocks, vapor_pressures, calibration = _calibrated_inputs()
    exact_gas = validation_map.GasPoint(0.1, 5.0, "n2_carrier")
    cases = {
        (temperature_C, exact_gas, 1.0)
        for temperature_C in validation_map.MAP_TEMPERATURES_C
    }
    cases.update(
        (1600.0, gas, 1.0)
        for gas in validation_map.gas_grid()
    )
    cases.update(
        (1600.0, exact_gas, dose_fraction)
        for dose_fraction in validation_map.DOSE_FRACTIONS
    )

    for temperature_C, gas, dose_fraction in sorted(
        cases,
        key=lambda item: (
            item[0],
            item[1].pO2_mbar,
            item[1].pN2_mbar,
            item[2],
        ),
    ):
        row = validation_map.run_row(
            temperature_C,
            gas,
            dose_fraction,
            calibration,
            setpoints,
            feedstocks,
            vapor_pressures,
            grid_scope_label=validation_map.GRID_SCOPE_FULL,
        )
        assert math.isfinite(row["post_exchange_fO2_log_diagnostic"])
        assert math.isfinite(row["redox_source_delta_ln_fO2"])


def test_run_row_uses_internal_o_branch_when_metal_loss_capacity_remains(
    monkeypatch,
):
    setpoints, feedstocks, vapor_pressures = validation_map._load_data()
    calibration = validation_map.calibrate_dose(
        setpoints,
        feedstocks,
        vapor_pressures,
    )
    calls: list[str] = []
    original = validation_map.PyrolysisSimulator._apply_fe_redox_respeciation

    def wrapped(self, **kwargs):
        calls.append(str(kwargs.get("oxygen_source", "overhead_gas")))
        return original(self, **kwargs)

    monkeypatch.setattr(
        validation_map.PyrolysisSimulator,
        "_apply_fe_redox_respeciation",
        wrapped,
    )

    row = validation_map.run_row(
        1650.0,
        validation_map.GasPoint(1.0e-6, 10.0, "n2_carrier"),
        1.0,
        calibration,
        setpoints,
        feedstocks,
        vapor_pressures,
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
    )

    assert row["redox_source_terms_mol_o2_equiv_by_label"][
        "redox_source:evaporative_metal_loss"
    ] > 0.0
    assert calls[-1] == FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS


def test_run_row_establishes_one_production_authority_pin(monkeypatch):
    setpoints, feedstocks, vapor_pressures, calibration = _calibrated_inputs()
    curve_calls = 0
    pin_window = False
    established = []
    resolved = []
    cleared_hours = []
    simulator_type = validation_map.PyrolysisSimulator
    original_establish = (
        simulator_type._establish_melt_redox_gate_authority_for_current_hour
    )
    original_resolve = simulator_type._resolved_melt_redox_gate_authority
    original_clear = (
        simulator_type._clear_melt_redox_gate_authority_for_completed_hour
    )

    def fail_then_recover(self):
        nonlocal curve_calls
        if not pin_window:
            return {
                'source': 'test_setup_real_curve',
                'solidus_T_C': 1000.0,
                'liquidus_T_C': 1700.0,
                'path': ((1000.0, 0.0), (1700.0, 1.0)),
            }
        curve_calls += 1
        if curve_calls == 1:
            raise ProviderUnavailableError('validation row pin failure')
        return {
            'source': 'test_recovered_real_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
            'path': ((1000.0, 0.0), (1700.0, 1.0)),
        }

    def record_establish(self):
        nonlocal pin_window
        pin_window = True
        authority = original_establish(self)
        established.append(authority)
        return authority

    def record_resolve(self, *args, **kwargs):
        authority = original_resolve(self, *args, **kwargs)
        if pin_window:
            resolved.append(authority)
        return authority

    def record_clear(self, completed_hour):
        nonlocal pin_window
        cleared_hours.append(int(completed_hour))
        result = original_clear(self, completed_hour)
        pin_window = False
        return result

    monkeypatch.setattr(simulator_type, '_freeze_gate_curve', fail_then_recover)
    monkeypatch.setattr(
        simulator_type,
        '_establish_melt_redox_gate_authority_for_current_hour',
        record_establish,
    )
    monkeypatch.setattr(
        simulator_type,
        '_resolved_melt_redox_gate_authority',
        record_resolve,
    )
    monkeypatch.setattr(
        simulator_type,
        '_clear_melt_redox_gate_authority_for_completed_hour',
        record_clear,
    )

    row = validation_map.run_row(
        1650.0,
        validation_map.GasPoint(1.0e-6, 10.0, 'n2_carrier'),
        1.0,
        calibration,
        setpoints,
        feedstocks,
        vapor_pressures,
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
    )

    assert row['row_passes_base_integrity'] is True
    assert curve_calls == 1
    assert len(established) == 1
    assert resolved
    assert all(authority is established[0] for authority in resolved)
    assert cleared_hours == [0]


def test_owner_pn2_anchor_reports_current_certification_state(smoke_payload):
    owner = [
        row for row in smoke_payload["rows"]
        if row["temperature_C"] == 1650.0
        and row["requested_pO2_mbar"] == pytest.approx(1.0e-6)
        and row["requested_pN2_mbar"] == pytest.approx(10.0)
        and row["dose_fraction_of_full_FeO_equiv"] == pytest.approx(1.0)
    ][0]
    assertions = {a["name"]: a for a in smoke_payload["assertions"]}

    assert owner["native_fe_pool_mol"] > 0.0
    assert owner["native_fe_tap_mol"] > owner["native_fe_vapor_mol"]
    assert owner["native_fe_vapor_escape_fraction_of_pool"] < 0.001
    assert owner["stage_3_Fe_wt_pct"] > 0.0
    assert owner["ferric_divergence_material"] is False
    assert abs(owner["mass_balance_error_pct"]) <= 5e-12
    assert owner["SiO_provider_pO2_bar"] == pytest.approx(1.0e-9)
    assert owner["SiO_flux_kg_hr"] >= validation_map.OWNER_RECIPE_MIN_SIO_KG_HR
    requested_pO2_assertion = assertions[
        "owner_pN2_recipe_point_requested_pO2_semantics"
    ]
    assert requested_pO2_assertion["passed"] is True
    assert "map/live share PN2 sweep transport-pO2 semantics" in (
        requested_pO2_assertion["detail"]
    )
    assert "map_live_semantics_parity" in requested_pO2_assertion["detail"]


def test_map_live_semantics_parity_is_computed_from_live_owner_tick(smoke_payload):
    parity = _assertion(smoke_payload, "map_live_semantics_parity")
    probe = smoke_payload["live_owner_probe"]

    assert probe["native_split_observed"] is True
    assert probe["native_fe_pool_mol"] > 0.0
    assert probe["native_fe_tap_mol"] + probe["native_fe_vapor_mol"] == pytest.approx(
        probe["native_fe_pool_mol"]
    )
    assert parity["passed"] is True
    assert "map_pO2_bar=" in parity["detail"]
    assert "live_pO2_bar=" in parity["detail"]
    assert "map_SiO_kg_hr=" in parity["detail"]
    assert "live_SiO_kg_hr=" in parity["detail"]
    assert "native_split_observed=True" in parity["detail"]


def test_map_live_semantics_parity_refuses_genuinely_absent_native_split(
    smoke_payload,
):
    live_probe = dict(smoke_payload["live_owner_probe"])
    live_probe["native_split_observed"] = False
    for field in (
        "native_fe_pool_mol",
        "native_fe_tap_mol",
        "native_fe_vapor_mol",
        "native_fe_vapor_escape_fraction_of_pool",
    ):
        live_probe[field] = 0.0

    assertions = validation_map.evaluate_assertions(
        smoke_payload["rows"],
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=True),
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
        live_owner_probe=live_probe,
    )
    parity = _assertion({"assertions": assertions}, "map_live_semantics_parity")

    assert parity["passed"] is False
    assert "native_split_observed=False" in parity["detail"]


def test_owner_live_probe_is_recipe_reachable(smoke_payload):
    probe = smoke_payload["live_owner_probe"]
    owner = _owner_recipe_row(smoke_payload)

    assert probe["recipe_reachable"] is True
    assert probe["recipe_stage_name"] == validation_map.OWNER_RECIPE_STAGE_NAME
    assert probe["recipe_gas_cover_mode"] == "pn2_sweep"
    assert probe["recipe_atmosphere"] == "PN2_SWEEP"
    assert probe["recipe_pO2_mbar"] == pytest.approx(
        validation_map.OWNER_RECIPE_PO2_MBAR
    )
    assert probe["recipe_pN2_mbar"] == pytest.approx(
        validation_map.OWNER_RECIPE_PN2_MBAR
    )
    assert probe["recipe_p_total_mbar"] == pytest.approx(
        validation_map.OWNER_RECIPE_PO2_MBAR
        + validation_map.OWNER_RECIPE_PN2_MBAR
    )
    assert probe["SiO_provider_pO2_bar"] == pytest.approx(
        owner["SiO_provider_pO2_bar"]
    )
    assert probe["SiO_flux_kg_hr"] == pytest.approx(
        owner["SiO_flux_kg_hr"],
        rel=validation_map.MAP_LIVE_PARITY_SIO_REL_TOL,
        abs=validation_map.MAP_LIVE_PARITY_SIO_ABS_TOL_KG_HR,
    )


def test_owner_live_pn2_tick_uses_sweep_floor_and_drains_o2(smoke_payload):
    probe = smoke_payload["live_owner_probe"]

    assert probe["SiO_provider_pO2_bar"] == pytest.approx(1.0e-9)
    assert probe["SiO_provider_pO2_bar"] < 1.0e-6
    assert probe["post_tick_overhead_o2_mol"] == pytest.approx(0.0, abs=1.0e-9)
    terminal_delta = (
        probe["terminal_stored_o2_delta_mol"] + probe["terminal_vented_o2_delta_mol"]
    )
    assert probe["native_split_o2_mol"] > 700.0
    assert probe["bled_o2_mol"] >= probe["native_split_o2_mol"]
    assert (
        terminal_delta
    ) == pytest.approx(probe["bled_o2_mol"], rel=0.0, abs=1.0e-6)


def test_grind_ready_target_window_opens_with_live_parity(smoke_payload):
    window = _assertion(smoke_payload, "grind_ready_target_window")
    parity = _assertion(smoke_payload, "map_live_semantics_parity")

    assert smoke_payload["live_owner_probe"]["native_split_observed"] is True
    assert parity["passed"] is True
    assert window["passed"] is False
    assert "first_passing_T_C=1600.0" in window["detail"]
    assert "window under PN2 sweep transport semantics" in window["detail"]
    assert "live parity=confirmed" in window["detail"]


def test_certification_surfaces_require_owner_pass_and_live_parity(
    smoke_payload,
    tmp_path,
):
    rows = []
    for row in smoke_payload["rows"]:
        updated = dict(row)
        if _is_owner_recipe_row(updated):
            updated["ferric_divergence_material"] = True
        rows.append(updated)
    assertions = validation_map.evaluate_assertions(
        rows,
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=True),
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
        live_owner_probe=smoke_payload["live_owner_probe"],
    )
    payload = dict(smoke_payload)
    payload["rows"] = rows
    payload["assertions"] = assertions
    report_path = tmp_path / "sso-r-report.md"

    by_name = {a["name"]: a for a in assertions}
    assert by_name["owner_pN2_recipe_point_requested_pO2_semantics"]["passed"] is False
    assert by_name["map_live_semantics_parity"]["passed"] is True
    assert by_name["grind_ready_target_window"]["passed"] is False
    validation_map.write_markdown(payload, report_path, command="pytest synthetic")
    report = report_path.read_text(encoding="utf-8")
    assert "classification=current_physics_blocker; live parity=confirmed" in report
    golden = validation_map.golden_payload(payload)
    assert golden["owner_pn2_row"]["owner_recipe_pass"] is False
    assert golden["owner_pn2_row"]["classification"] == "current_physics_blocker"


def test_map_live_semantics_parity_tolerances_bind(smoke_payload, monkeypatch):
    owner = _owner_recipe_row(smoke_payload)
    canonical_pO2_abs_tol_bar = 1.0e-15
    canonical_sio_rel_tol = 1.0e-9
    canonical_sio_abs_tol_kg_hr = 1.0e-12
    assert validation_map.MAP_LIVE_PARITY_PO2_ABS_TOL_BAR <= canonical_pO2_abs_tol_bar
    assert validation_map.MAP_LIVE_PARITY_SIO_REL_TOL <= canonical_sio_rel_tol
    assert (
        validation_map.MAP_LIVE_PARITY_SIO_ABS_TOL_KG_HR
        <= canonical_sio_abs_tol_kg_hr
    )
    live_probe = dict(smoke_payload["live_owner_probe"])
    live_probe["SiO_provider_pO2_bar"] = (
        owner["SiO_provider_pO2_bar"] + canonical_pO2_abs_tol_bar * 10.0
    )
    live_probe["SiO_flux_kg_hr"] = (
        owner["SiO_flux_kg_hr"]
        + max(
            canonical_sio_abs_tol_kg_hr * 10.0,
            abs(owner["SiO_flux_kg_hr"]) * canonical_sio_rel_tol * 10.0,
        )
    )

    assertions = validation_map.evaluate_assertions(
        smoke_payload["rows"],
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=True),
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
        live_owner_probe=live_probe,
    )
    assert _assertion({"assertions": assertions}, "map_live_semantics_parity")[
        "passed"
    ] is False

    monkeypatch.setattr(validation_map, "MAP_LIVE_PARITY_PO2_ABS_TOL_BAR", 1.0)
    monkeypatch.setattr(validation_map, "MAP_LIVE_PARITY_SIO_REL_TOL", 1.0)
    monkeypatch.setattr(validation_map, "MAP_LIVE_PARITY_SIO_ABS_TOL_KG_HR", 1.0)
    widened_assertions = validation_map.evaluate_assertions(
        smoke_payload["rows"],
        smoke_payload["manual_fO2_anchors"],
        expected_rows=validation_map.expected_grid_count(smoke=True),
        grid_scope_label=validation_map.GRID_SCOPE_SMOKE,
        live_owner_probe=live_probe,
    )
    widened_parity = _assertion(
        {"assertions": widened_assertions}, "map_live_semantics_parity"
    )
    assert live_probe["native_split_observed"] is True
    assert widened_parity["passed"] is True
    assert "native_split_observed=True" in widened_parity["detail"]


def test_distilled_golden_fixture_matches_current_anchors(smoke_payload):
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    current = validation_map.golden_payload(smoke_payload)

    assert golden["schema_version"] == validation_map.GOLDEN_SCHEMA_VERSION
    assert golden["grid_scope"] == validation_map.GRID_SCOPE_SMOKE
    assert current["grid_scope"] == golden["grid_scope"]
    assert current["grid_expected_row_count"] == golden["grid_expected_row_count"]
    assert current["manual_fO2_anchors"] == golden["manual_fO2_anchors"]
    assert current["owner_pn2_row"]["classification"] == (
        golden["owner_pn2_row"]["classification"]
    )
    assert current["owner_pn2_row"]["native_fe_pool_mol"] == pytest.approx(
        golden["owner_pn2_row"]["native_fe_pool_mol"],
        rel=0.0,
        abs=1e-9,
    )
    assert current["owner_pn2_row"]["SiO_flux_kg_hr"] == pytest.approx(
        golden["owner_pn2_row"]["SiO_flux_kg_hr"],
        rel=0.0,
        abs=1e-15,
    )
    # The distilled monotonic slices are regression pins, not write-only
    # payload: compare them against the golden (SC-50 consumption).
    for slice_name in (
        "pO2_sio_suppression_slice",
        "dose_reduction_slice",
        "pN2_monotonic_slice",
    ):
        assert current[slice_name] == golden[slice_name], slice_name
