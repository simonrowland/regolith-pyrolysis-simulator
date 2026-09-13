"""Laptop-runnable tests for the binary-pot engine arm. No melt engines."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    DEFAULT_POTS_PATH,
    FINDING_CLASS_FALLBACK_VS_SPECIATION,
    QUANTITY_ACTIVITY,
    QUANTITY_PRESSURE,
    REFUSAL_COMPOSITION_PROJECTED,
    REFUSAL_TIMEOUT,
    REFUSAL_UNAVAILABLE,
    REFUSAL_VALUE_IS_FLOOR,
    _FLOOR_INVERSION_REASON,
    BinaryPot,
    EngineHandle,
    EquilibrateCell,
    Po2Request,
    classify_equilibrate_outcome,
    classify_reported_value,
    collect_floor_refusals,
    equilibrate_cell,
    extract_reported_quantities,
    extract_vapor_authority,
    finding_class_for_pair,
    load_binary_pots,
    pairwise_residuals,
    reclassify_projected_composition_cells,
    recompute_residuals_from_report,
    render_report_markdown,
    residual_log10,
    vapor_authority_kind,
    write_reports,
)
from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)


FIXTURE_REPORT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "binary_pot_battery"
    / "engine_arm_report.json"
)


def test_pots_load_and_sum_to_100_wt_pct() -> None:
    pots, grid = load_binary_pots(DEFAULT_POTS_PATH)
    assert len(pots) == 8
    ids = [pot.pot_id for pot in pots]
    assert ids == [
        "sio2_al2o3_50_50",
        "sio2_al2o3_80_20",
        "cao_sio2_40_60",
        "cao_al2o3_50_50",
        "mgo_sio2_40_60",
        "feo_sio2_40_60",
        "na2o_sio2_25_75",
        "feo_mgo_sio2_30_20_50",
    ]
    for pot in pots:
        total = sum(pot.composition_wt_pct.values())
        assert total == pytest.approx(100.0)
        assert len(pot.composition_wt_pct) >= 2
        assert pot.why
    assert grid.temperatures_K[0] == 1500.0
    assert grid.temperatures_K[-1] == 2300.0
    assert grid.temperatures_K == tuple(range(1500, 2301, 100))
    assert grid.engine_default_po2 is True
    assert grid.commanded_po2_bar == (1.0e-8,)
    kato = {
        pot.pot_id: pot.kato_1993_table4_system for pot in pots
    }
    assert kato["na2o_sio2_25_75"] is None
    assert kato["feo_mgo_sio2_30_20_50"] == "FeO-MgO-SiO2"
    assert kato["sio2_al2o3_50_50"] == "Al2O3-SiO2"


def test_refusing_engine_yields_typed_refusal_row_not_exception() -> None:
    pot = BinaryPot(
        pot_id="fixture_pot",
        kato_1993_table4_system="Al2O3-SiO2",
        why="fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )
    po2 = Po2Request(mode="engine_default", po2_bar=None)

    missing = EngineHandle(
        name="alphamelts",
        backend=None,
        available=False,
        unavailable_reason="backend unavailable",
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    missing_cell = equilibrate_cell(
        missing, pot, temperature_K=1500.0, po2=po2
    )
    assert missing_cell.status == "refusal"
    assert missing_cell.refusal_reason == REFUSAL_UNAVAILABLE
    assert missing_cell.melt_activities == {}

    class _BoomBackend:
        def equilibrate(self, **kwargs):
            raise RuntimeError("spin forever")

    exploding = EngineHandle(
        name="thermoengine",
        backend=_BoomBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    boom_cell = equilibrate_cell(
        exploding, pot, temperature_K=1600.0, po2=po2, timeout_s=1.0
    )
    assert boom_cell.status == "refusal"
    assert boom_cell.refusal_reason == REFUSAL_UNAVAILABLE
    assert boom_cell.engine_status == "RuntimeError"

    class _TimeoutBackend:
        def equilibrate(self, **kwargs):
            raise TimeoutError("equilibrate exceeded hard timeout of 1s")

    timed = EngineHandle(
        name="thermoengine",
        backend=_TimeoutBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    timeout_cell = equilibrate_cell(
        timed, pot, temperature_K=1700.0, po2=po2, timeout_s=1.0
    )
    assert timeout_cell.status == "refusal"
    assert timeout_cell.refusal_reason == REFUSAL_TIMEOUT


def test_classify_out_of_domain_and_no_liquid() -> None:
    status, reason, engine_reason = classify_equilibrate_outcome(
        SimpleNamespace(
            status="out_of_domain",
            diagnostics={"backend_status_reason": "major_sum"},
            warnings=["DomainGate rejected: major oxide sum <= 95 wt%"],
            activity_coefficients={},
            vapor_pressures_Pa={},
            liquid_fraction=None,
        )
    )
    assert status == "refusal"
    assert reason == "sum_below_95_wt_pct"
    assert engine_reason

    status, reason, _ = classify_equilibrate_outcome(
        SimpleNamespace(
            status="ok",
            diagnostics={},
            warnings=[],
            activity_coefficients={"SiO2": 0.4},
            vapor_pressures_Pa={},
            liquid_fraction=0.0,
            phase_assemblage_available=True,
        )
    )
    assert status == "refusal"
    assert reason == "no_liquid"


def test_classify_composition_projected_is_typed_refusal() -> None:
    status, reason, engine_reason = classify_equilibrate_outcome(
        SimpleNamespace(
            status="out_of_domain",
            diagnostics={
                "backend_status_reason": REFUSAL_COMPOSITION_PROJECTED,
                "input_composition_projection": {
                    "status": "projected",
                    "reason": "input_composition_projected",
                    "dropped_bulk_components": ["P2O5"],
                    "dropped_mass_fraction": 0.0154881,
                    "composition_projected": {
                        "dropped_components": ["P2O5"],
                        "dropped_mass_fraction": 0.0154881,
                    },
                },
            },
            warnings=["MAGEMin refused projected composition; dropped P2O5"],
            activity_coefficients={},
            vapor_pressures_Pa={},
            liquid_fraction=None,
        )
    )
    assert status == "refusal"
    assert reason == REFUSAL_COMPOSITION_PROJECTED
    assert "P2O5" in engine_reason
    assert "mass_fraction=" in engine_reason

    vaporock_status, vaporock_reason, _ = classify_equilibrate_outcome(
        SimpleNamespace(
            status="out_of_domain",
            diagnostics={
                "backend_status_reason": "forbidden_species",
                "input_composition_projection": {
                    "status": "projected",
                    "reason": "input_composition_projected",
                    "dropped_species": ["Fe", "FeS"],
                },
            },
            warnings=["VapoRock refused projected/dropped non-basis melt input"],
            activity_coefficients={},
            vapor_pressures_Pa={},
            liquid_fraction=None,
        )
    )
    assert vaporock_status == "refusal"
    assert vaporock_reason == "out_of_basis"


def test_reclassify_magemin_p2o5_ok_cell_is_composition_projected() -> None:
    pot = BinaryPot(
        pot_id="kambayashi_1985_feto_p2o5_s01",
        kato_1993_table4_system=None,
        why="fixture",
        composition_wt_pct={
            "P2O5": 1.548807826,
            "FeO": 88.19034033,
            "Fe2O3": 10.26085184,
        },
    )
    ok_cell = EquilibrateCell(
        pot_id=pot.pot_id,
        engine="magemin",
        temperature_K=1643.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=0.001,
        wall_s=0.2,
        cpu_s=0.0,
        hostname="test",
    )
    rewritten = reclassify_projected_composition_cells((ok_cell,), (pot,))
    assert len(rewritten) == 1
    cell = rewritten[0]
    assert cell.status == "refusal"
    assert cell.refusal_reason == REFUSAL_COMPOSITION_PROJECTED
    assert cell.engine_status == "out_of_domain"
    assert "P2O5" in (cell.engine_reason or "")
    assert cell.melt_activities == {}

    in_basis = BinaryPot(
        pot_id="feo_sio2_40_60",
        kato_1993_table4_system="FeO-SiO2",
        why="fixture",
        composition_wt_pct={"FeO": 40.0, "SiO2": 60.0},
    )
    kept = reclassify_projected_composition_cells(
        (
            EquilibrateCell(
                **{
                    **ok_cell.__dict__,
                    "pot_id": in_basis.pot_id,
                    "melt_activities": {"SiO2": 0.4},
                }
            ),
        ),
        (in_basis,),
    )
    assert kept[0].status == "ok"
    assert kept[0].refusal_reason is None
    assert kept[0].melt_activities == {"SiO2": 0.4}


def test_residual_function_is_antisymmetric() -> None:
    assert residual_log10(10.0, 1.0) == pytest.approx(1.0)
    assert residual_log10(1.0, 10.0) == pytest.approx(-1.0)
    assert residual_log10(1.0, 1.0) == pytest.approx(0.0)
    left = residual_log10(3.0, 2.0)
    right = residual_log10(2.0, 3.0)
    assert left == pytest.approx(-right)
    with pytest.raises(Exception):
        residual_log10(0.0, 1.0)

    cells = [
        EquilibrateCell(
            pot_id="p",
            engine="alphamelts",
            temperature_K=1500.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={"SiO2": 10.0},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        ),
        EquilibrateCell(
            pot_id="p",
            engine="thermoengine",
            temperature_K=1500.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={"SiO2": 1.0},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        ),
    ]
    rows = pairwise_residuals(cells)
    assert len(rows) == 1
    assert rows[0]["quantity"] == QUANTITY_ACTIVITY
    assert rows[0]["engine_a"] == "alphamelts"
    assert rows[0]["engine_b"] == "thermoengine"
    assert rows[0]["delta_log10_a_minus_b"] == pytest.approx(1.0)
    swapped_cells = [
        EquilibrateCell(
            **{**cells[0].__dict__, "melt_activities": {"SiO2": 1.0}}
        ),
        EquilibrateCell(
            **{**cells[1].__dict__, "melt_activities": {"SiO2": 10.0}}
        ),
    ]
    swapped = pairwise_residuals(swapped_cells)
    # Pair order is alphabetical; swapping values flips the signed dex.
    assert swapped[0]["engine_a"] == "alphamelts"
    assert swapped[0]["delta_log10_a_minus_b"] == pytest.approx(
        -rows[0]["delta_log10_a_minus_b"]
    )


def test_report_renders_from_fixture(tmp_path: Path) -> None:
    report = json.loads(FIXTURE_REPORT.read_text(encoding="utf-8"))
    markdown = render_report_markdown(report)
    assert markdown.startswith("# Binary-pot battery — engine arm")
    assert "cannot pass or fail" in markdown
    assert "descriptive magnitude band only" in markdown
    assert "## Refusal matrix (pot × engine)" in markdown
    assert "## Per-pot residual tables" in markdown
    assert "## Twenty largest in-envelope residuals" in markdown
    assert "`sio2_al2o3_50_50`" in markdown
    assert "large_1_to_2_dex" in markdown
    json_path, md_path = write_reports(report, tmp_path)
    assert json_path.name == "binary-pot-engine-arm.json"
    assert md_path.read_text(encoding="utf-8") == markdown


def test_extract_reads_vaporock_full_speciation_when_pressures_blank() -> None:
    activities, pressures = extract_reported_quantities(
        SimpleNamespace(
            activity_coefficients={"SiO2": 0.4},
            vapor_pressures_Pa={},
            vaporock_full_speciation_Pa={"Na": 12.0, "SiO": 0.0, "K": 3.5},
        )
    )
    assert activities == {"SiO2": 0.4}
    assert pressures == {"Na": 12.0, "K": 3.5}


def test_alphamelts_cell_passes_isothermal_subprocess_run_mode() -> None:
    pot = BinaryPot(
        pot_id="fixture_pot",
        kato_1993_table4_system="Al2O3-SiO2",
        why="fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )
    po2 = Po2Request(mode="engine_default", po2_bar=None)
    captured: dict[str, object] = {}

    class _SubprocessBackend:
        _mode = "subprocess"

        def equilibrate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                activity_coefficients={"SiO2": 0.5},
                vapor_pressures_Pa={"Na": 1.0},
                liquid_fraction=1.0,
                phase_assemblage_available=True,
            )

    handle = EngineHandle(
        name="alphamelts",
        backend=_SubprocessBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    cell = equilibrate_cell(handle, pot, temperature_K=1500.0, po2=po2)
    assert cell.status == "ok"
    assert captured["subprocess_run_mode"] == "isothermal"
    assert captured["pressure_bar"] == 1.0
    assert cell.melt_activities == {"SiO2": 0.5}
    assert cell.gas_partial_pressures_Pa == {"Na": 1.0}
    assert handle.available is True


def test_domain_exception_does_not_kill_engine_handle() -> None:
    pot = BinaryPot(
        pot_id="fixture_pot",
        kato_1993_table4_system="Al2O3-SiO2",
        why="fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )
    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)

    class _DomainBackend:
        closed = False

        def close(self) -> None:
            self.closed = True

        def equilibrate(self, **kwargs):
            raise RuntimeError(
                "fo2_requires_iron: ThermoEngine cannot impose absolute fO2"
            )

    backend = _DomainBackend()
    handle = EngineHandle(
        name="thermoengine",
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    cell = equilibrate_cell(handle, pot, temperature_K=1500.0, po2=po2)
    assert cell.status == "refusal"
    assert handle.available is True
    assert handle.backend is backend
    assert backend.closed is False


def test_battery_engine_names_match_backends_py_surface() -> None:
    assert BATTERY_ENGINE_NAMES == (
        "internal-analytical",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
        "cached-real",
    )


def _ok_cell(
    *,
    engine: str,
    gas: dict[str, float],
    activities: dict[str, float] | None = None,
    po2: Po2Request | None = None,
    temperature_K: float = 1700.0,
    vapor_pressures_source: dict[str, str] | None = None,
    vapor_pressure_backend_status: str | None = None,
    vapor_pressure_backend_status_reason: str | None = None,
    authoritative_for_requested_vapor_pressure: bool | None = None,
) -> EquilibrateCell:
    return EquilibrateCell(
        pot_id="feo_mgo_sio2_30_20_50",
        engine=engine,
        temperature_K=temperature_K,
        po2=po2 or Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities=dict(activities or {}),
        gas_partial_pressures_Pa=dict(gas),
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        vapor_pressures_source=dict(vapor_pressures_source or {}),
        vapor_pressure_backend_status=vapor_pressure_backend_status,
        vapor_pressure_backend_status_reason=vapor_pressure_backend_status_reason,
        authoritative_for_requested_vapor_pressure=(
            authoritative_for_requested_vapor_pressure
        ),
    )


def test_floor_value_is_typed_refusal_not_residual() -> None:
    floor_fill = MELT_DISSOCIATION_PO2_MIN_BAR
    floor_exploded = 4.527934710618984e20
    physical = 7.318136019588876e-12

    assert (
        classify_reported_value(
            floor_fill, quantity=QUANTITY_PRESSURE, engine="thermoengine"
        )
        is None
    )
    fill_refusal = classify_reported_value(
        floor_fill,
        quantity=QUANTITY_PRESSURE,
        engine="thermoengine",
        source_label=_FLOOR_INVERSION_REASON,
    )
    assert fill_refusal is not None
    assert fill_refusal["reason"] == REFUSAL_VALUE_IS_FLOOR
    assert fill_refusal["engine"] == "thermoengine"
    assert fill_refusal["floor_value"] == floor_fill
    assert fill_refusal["reported_value"] == floor_fill

    exploded_refusal = classify_reported_value(
        floor_exploded, quantity=QUANTITY_PRESSURE, engine="thermoengine"
    )
    assert exploded_refusal is not None
    assert exploded_refusal["reason"] == REFUSAL_VALUE_IS_FLOOR
    assert exploded_refusal["floor_value"] == MELT_DISSOCIATION_PO2_MIN_BAR
    assert exploded_refusal["reported_value"] == floor_exploded
    assert floor_exploded >= CATALOG_PHYSICAL_PRESSURE_CEILING_PA

    assert (
        classify_reported_value(
            physical, quantity=QUANTITY_PRESSURE, engine="alphamelts"
        )
        is None
    )

    synthetic = SimpleNamespace(
        activity_coefficients={"SiO2": 0.75},
        vapor_pressures_Pa={"Si": floor_fill, "SiO": 0.013},
        vaporock_full_speciation_Pa=None,
    )
    activities, pressures = extract_reported_quantities(synthetic)
    assert activities == {"SiO2": 0.75}
    assert pressures["Si"] == floor_fill
    assert pressures["SiO"] == pytest.approx(0.013)

    _speciation_builtin = {
        "Si": "builtin_authoritative:standard_reaction_term",
        "SiO": "builtin_authoritative:standard_reaction_term",
    }
    floor_cell = _ok_cell(
        engine="thermoengine",
        gas={"Si": floor_fill, "SiO": 0.013},
        vapor_pressures_source={
            "Si": _FLOOR_INVERSION_REASON,
            "SiO": "builtin_authoritative:standard_reaction_term",
        },
    )
    partner = _ok_cell(
        engine="alphamelts",
        gas={"Si": physical, "SiO": 0.0087},
        vapor_pressures_source=_speciation_builtin,
    )
    exploded_cell = _ok_cell(
        engine="thermoengine",
        gas={"Si": floor_exploded, "SiO": 0.013},
        vapor_pressures_source=_speciation_builtin,
    )
    vaporock = _ok_cell(
        engine="vaporock",
        gas={"Si": 9.540668602825991e-12, "SiO": 0.0127},
        vapor_pressures_source={"Si": "vaporock", "SiO": "vaporock"},
    )

    fill_rows = pairwise_residuals([floor_cell, partner])
    assert all(row["species"] != "Si" for row in fill_rows)
    assert any(row["species"] == "SiO" for row in fill_rows)

    exploded_rows = pairwise_residuals([exploded_cell, partner, vaporock])
    assert all(
        "thermoengine" not in (row["engine_a"], row["engine_b"])
        for row in exploded_rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    )
    assert any(
        row["species"] == "Si"
        and {row["engine_a"], row["engine_b"]} == {"alphamelts", "vaporock"}
        for row in exploded_rows
    )
    assert any(row["species"] == "SiO" for row in exploded_rows)

    refusals = collect_floor_refusals([floor_cell, exploded_cell, partner])
    assert {row["reason"] for row in refusals} == {REFUSAL_VALUE_IS_FLOOR}
    assert {row["engine"] for row in refusals} == {"thermoengine"}
    assert {row["species"] for row in refusals} == {"Si"}
    assert {row["floor_value"] for row in refusals} == {MELT_DISSOCIATION_PO2_MIN_BAR}

    rebuilt = recompute_residuals_from_report(
        {
            "pots": [
                {
                    "pot_id": "feo_mgo_sio2_30_20_50",
                    "kato_1993_table4_system": "FeO-MgO-SiO2",
                    "why": "fixture",
                    "composition_wt_pct": {
                        "FeO": 30.0,
                        "MgO": 20.0,
                        "SiO2": 50.0,
                    },
                }
            ],
            "cells": [
                floor_cell.as_payload(),
                exploded_cell.as_payload(),
                partner.as_payload(),
                vaporock.as_payload(),
            ],
        }
    )
    assert rebuilt["n_floor_refusals"] == 2
    assert all(
        "thermoengine" not in (row["engine_a"], row["engine_b"])
        for row in rebuilt["largest_in_envelope_residuals"]
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    )


def test_floor_labelled_subceiling_pressure_is_refused() -> None:
    """M05 confirm: floor provenance, not magnitude, refuses a 1 Pa source."""

    labelled = classify_reported_value(
        1.0,
        quantity=QUANTITY_PRESSURE,
        engine="thermoengine",
        source_label=_FLOOR_INVERSION_REASON,
    )
    assert labelled is not None
    assert labelled["reason"] == REFUSAL_VALUE_IS_FLOOR
    assert labelled["reported_value"] == 1.0

    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    floor_one = _ok_cell(
        engine="thermoengine",
        gas={"Si": 1.0},
        po2=po2,
        vapor_pressures_source={"Si": _FLOOR_INVERSION_REASON},
    )
    physical_tenth = _ok_cell(
        engine="alphamelts",
        gas={"Si": 0.1},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    rows = pairwise_residuals([floor_one, physical_tenth])
    assert all(row["species"] != "Si" for row in rows)
    refusals = collect_floor_refusals([floor_one, physical_tenth])
    assert {row["engine"] for row in refusals} == {"thermoengine"}
    assert {row["species"] for row in refusals} == {"Si"}
    assert {row["reported_value"] for row in refusals} == {1.0}

    tiny = classify_reported_value(
        1.0e-30, quantity=QUANTITY_PRESSURE, engine="alphamelts"
    )
    converted = classify_reported_value(
        1.0e-25, quantity=QUANTITY_PRESSURE, engine="alphamelts"
    )
    assert tiny is None
    assert converted is None
    physical_tiny = _ok_cell(
        engine="thermoengine",
        gas={"Si": 1.0e-30},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    physical_peer = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.0e-29},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    tiny_rows = pairwise_residuals([physical_tiny, physical_peer])
    si_tiny = [
        row
        for row in tiny_rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    ]
    assert len(si_tiny) == 1
    assert si_tiny[0]["engine_a"] == "alphamelts"
    assert si_tiny[0]["engine_b"] == "thermoengine"
    assert si_tiny[0]["delta_log10_a_minus_b"] == pytest.approx(1.0)
    converted_cell = _ok_cell(
        engine="thermoengine",
        gas={"Si": 1.0e-25},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    converted_peer = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.0e-24},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    converted_rows = pairwise_residuals([converted_cell, converted_peer])
    si_converted = [
        row
        for row in converted_rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    ]
    assert len(si_converted) == 1
    assert si_converted[0]["delta_log10_a_minus_b"] == pytest.approx(1.0)


def test_ordinary_sub_pascal_pressure_still_scores() -> None:
    """M05 control: unlabelled 1 Pa vs 0.1 Pa remains a 1 dex residual."""

    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    one = _ok_cell(
        engine="thermoengine",
        gas={"Si": 1.0},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    tenth = _ok_cell(
        engine="alphamelts",
        gas={"Si": 0.1},
        po2=po2,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    rows = pairwise_residuals([one, tenth])
    si_rows = [
        row
        for row in rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    ]
    assert len(si_rows) == 1
    assert si_rows[0]["engine_a"] == "alphamelts"
    assert si_rows[0]["engine_b"] == "thermoengine"
    assert si_rows[0]["delta_log10_a_minus_b"] == pytest.approx(-1.0)
    assert collect_floor_refusals([one, tenth]) == []


def test_fallback_vs_speciation_finding_class_reads_engine_flags() -> None:
    """Null hypothesis: the fallback is flagged; the harness must read it."""

    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    thermoengine = _ok_cell(
        engine="thermoengine",
        gas={"Si": 0.005055980955421236, "Mg": 85017.20906779557},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
            "Mg": "antoine_fallback_from_vaporock:legacy_pure_component_estimate",
        },
        vapor_pressure_backend_status="fallback",
        vapor_pressure_backend_status_reason="vaporock_to_antoine_fallback",
        authoritative_for_requested_vapor_pressure=False,
    )
    alphamelts = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.5412127415317158e-15, "Mg": 1.5650644335189703e-5},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
            "Mg": "builtin_authoritative:gas_standard_fugacity",
        },
    )
    vaporock = _ok_cell(
        engine="vaporock",
        gas={"Si": 2.6888346520180887e-15, "Mg": 1.077023000421466e-5},
        po2=po2,
        temperature_K=1600.0,
    )
    unflagged = _ok_cell(
        engine="thermoengine",
        gas={"Si": 0.005055980955421236, "Mg": 85017.20906779557},
        po2=po2,
        temperature_K=1600.0,
    )

    flagged = SimpleNamespace(
        activity_coefficients={"SiO2": 0.4},
        vapor_pressures_Pa={"Si": 0.005, "Mg": 85017.0},
        vaporock_full_speciation_Pa=None,
        vapor_pressures_source={
            "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
            "Mg": "antoine_fallback_from_vaporock:legacy_pure_component_estimate",
        },
        diagnostics={
            "vapor_pressure_backend_status": "fallback",
            "vapor_pressure_backend_status_reason": "vaporock_to_antoine_fallback",
            "authoritative_for_requested_vapor_pressure": False,
        },
    )
    authority = extract_vapor_authority(flagged)
    assert authority["vapor_pressure_backend_status"] == "fallback"
    assert authority["authoritative_for_requested_vapor_pressure"] is False
    assert authority["vapor_pressures_source"]["Si"].startswith(
        "antoine_fallback_from_vaporock"
    )

    assert (
        vapor_authority_kind(
            thermoengine.as_payload(), species="Si", quantity=QUANTITY_PRESSURE
        )
        == "fallback"
    )
    assert (
        vapor_authority_kind(
            alphamelts.as_payload(), species="Si", quantity=QUANTITY_PRESSURE
        )
        == "speciation"
    )
    assert (
        vapor_authority_kind(
            vaporock.as_payload(), species="Si", quantity=QUANTITY_PRESSURE
        )
        == "speciation"
    )
    assert (
        vapor_authority_kind(
            unflagged.as_payload(), species="Si", quantity=QUANTITY_PRESSURE
        )
        is None
    )

    assert (
        finding_class_for_pair(
            thermoengine.as_payload(),
            alphamelts.as_payload(),
            species="Si",
            quantity=QUANTITY_PRESSURE,
        )
        == FINDING_CLASS_FALLBACK_VS_SPECIATION
    )
    assert (
        finding_class_for_pair(
            alphamelts.as_payload(),
            vaporock.as_payload(),
            species="Si",
            quantity=QUANTITY_PRESSURE,
        )
        is None
    )
    assert (
        finding_class_for_pair(
            unflagged.as_payload(),
            vaporock.as_payload(),
            species="Si",
            quantity=QUANTITY_PRESSURE,
        )
        is None
    )

    rows = pairwise_residuals([thermoengine, alphamelts, vaporock])
    si_rows = [
        row
        for row in rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    ]
    assert si_rows
    te_pairs = [
        row
        for row in si_rows
        if "thermoengine" in (row["engine_a"], row["engine_b"])
    ]
    assert te_pairs == []
    am_vr = [
        row
        for row in si_rows
        if {row["engine_a"], row["engine_b"]} == {"alphamelts", "vaporock"}
    ]
    assert am_vr
    assert all(row["finding_class"] is None for row in am_vr)
    missing_rows = pairwise_residuals([unflagged, alphamelts])
    assert all(
        row["quantity"] != QUANTITY_PRESSURE
        for row in missing_rows
    )

    captured: dict[str, object] = {}

    class _FlaggedBackend:
        def equilibrate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="ok",
                diagnostics={
                    "vapor_pressure_backend_status": "fallback",
                    "vapor_pressure_backend_status_reason": (
                        "vaporock_to_antoine_fallback"
                    ),
                    "authoritative_for_requested_vapor_pressure": False,
                },
                warnings=[],
                activity_coefficients={"SiO2": 0.4},
                vapor_pressures_Pa={"Si": 0.005, "Mg": 85017.0},
                vapor_pressures_source={
                    "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
                    "Mg": "antoine_fallback_from_vaporock:legacy_pure_component_estimate",
                },
                liquid_fraction=1.0,
                phase_assemblage_available=True,
            )

    handle = EngineHandle(
        name="thermoengine",
        backend=_FlaggedBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    pot = BinaryPot(
        pot_id="feo_mgo_sio2_30_20_50",
        kato_1993_table4_system="FeO-MgO-SiO2",
        why="fixture",
        composition_wt_pct={"FeO": 30.0, "MgO": 20.0, "SiO2": 50.0},
    )
    cell = equilibrate_cell(handle, pot, temperature_K=1600.0, po2=po2)
    assert cell.status == "ok"
    assert cell.vapor_pressure_backend_status == "fallback"
    assert cell.authoritative_for_requested_vapor_pressure is False
    assert cell.vapor_pressures_source["Si"].startswith(
        "antoine_fallback_from_vaporock"
    )
    assert "vapor_pressures_source" in cell.as_payload()
    assert captured["temperature_C"] == pytest.approx(1326.85)

    rebuilt = recompute_residuals_from_report(
        {
            "pots": [
                {
                    "pot_id": "feo_mgo_sio2_30_20_50",
                    "kato_1993_table4_system": "FeO-MgO-SiO2",
                    "why": "fixture",
                    "composition_wt_pct": {
                        "FeO": 30.0,
                        "MgO": 20.0,
                        "SiO2": 50.0,
                    },
                }
            ],
            "cells": [
                thermoengine.as_payload(),
                alphamelts.as_payload(),
                vaporock.as_payload(),
            ],
        }
    )
    assert rebuilt["n_fallback_vs_speciation"] == 0
    assert all(
        row.get("finding_class") != FINDING_CLASS_FALLBACK_VS_SPECIATION
        for row in rebuilt["largest_in_envelope_residuals"]
    )
    fixture = json.loads(FIXTURE_REPORT.read_text(encoding="utf-8"))
    markdown = render_report_markdown(recompute_residuals_from_report(fixture))
    assert "finding_class" in markdown
    assert FINDING_CLASS_FALLBACK_VS_SPECIATION in markdown or (
        "fallback vs speciation" in markdown
    )


def test_fallback_and_missing_authority_excluded_from_speciation_ranking() -> None:
    """M02 confirm: 12-dex fallback and unflagged pairs must not rank."""

    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    fallback = _ok_cell(
        engine="thermoengine",
        gas={"Si": 0.005055980955421236},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
        },
        vapor_pressure_backend_status="fallback",
        vapor_pressure_backend_status_reason="vaporock_to_antoine_fallback",
        authoritative_for_requested_vapor_pressure=False,
    )
    speciation = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.5412127415317158e-15},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    unflagged = _ok_cell(
        engine="thermoengine",
        gas={"Si": 0.005055980955421236},
        po2=po2,
        temperature_K=1600.0,
    )
    below_ceiling = _ok_cell(
        engine="thermoengine",
        gas={"Si": 1.0},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
        },
        vapor_pressure_backend_status="fallback",
    )
    below_ceiling_peer = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.0e-12},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )

    assert (
        finding_class_for_pair(
            fallback.as_payload(),
            speciation.as_payload(),
            species="Si",
            quantity=QUANTITY_PRESSURE,
        )
        == FINDING_CLASS_FALLBACK_VS_SPECIATION
    )
    assert (
        finding_class_for_pair(
            unflagged.as_payload(),
            speciation.as_payload(),
            species="Si",
            quantity=QUANTITY_PRESSURE,
        )
        is None
    )
    assert vapor_authority_kind(
        unflagged.as_payload(), species="Si", quantity=QUANTITY_PRESSURE
    ) is None

    flagged_rows = pairwise_residuals([fallback, speciation])
    missing_rows = pairwise_residuals([unflagged, speciation])
    dex12_rows = pairwise_residuals([below_ceiling, below_ceiling_peer])
    assert flagged_rows == []
    assert missing_rows == []
    assert dex12_rows == []
    assert fallback.gas_partial_pressures_Pa["Si"] == 0.005055980955421236
    assert unflagged.gas_partial_pressures_Pa["Si"] == 0.005055980955421236


def test_like_for_like_speciation_pressure_residual_still_ranks() -> None:
    """M02 control: alphamelts vs vaporock speciation still produces a residual."""

    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    alphamelts = _ok_cell(
        engine="alphamelts",
        gas={"Si": 1.5412127415317158e-15},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "builtin_authoritative:standard_reaction_term",
        },
    )
    vaporock = _ok_cell(
        engine="vaporock",
        gas={"Si": 2.6888346520180887e-15},
        po2=po2,
        temperature_K=1600.0,
    )
    rows = pairwise_residuals([alphamelts, vaporock])
    si_rows = [
        row
        for row in rows
        if row["species"] == "Si" and row["quantity"] == QUANTITY_PRESSURE
    ]
    assert len(si_rows) == 1
    assert si_rows[0]["finding_class"] is None
    assert si_rows[0]["delta_log10_a_minus_b"] == pytest.approx(
        residual_log10(
            alphamelts.gas_partial_pressures_Pa["Si"],
            vaporock.gas_partial_pressures_Pa["Si"],
        )
    )


def test_equilibrate_cell_payload_round_trip_keeps_flags() -> None:
    po2 = Po2Request(mode="commanded", po2_bar=1.0e-8)
    flagged = _ok_cell(
        engine="thermoengine",
        gas={"Si": 0.005, "Mg": 85017.0},
        po2=po2,
        temperature_K=1600.0,
        vapor_pressures_source={
            "Si": "antoine_fallback_from_vaporock:standard_reaction_term",
            "Mg": "antoine_fallback_from_vaporock:legacy_pure_component_estimate",
        },
        vapor_pressure_backend_status="fallback",
        vapor_pressure_backend_status_reason="vaporock_to_antoine_fallback",
        authoritative_for_requested_vapor_pressure=False,
    )
    projected = EquilibrateCell(
        pot_id="kambayashi_1985_feto_p2o5_s01",
        engine="magemin",
        temperature_K=1643.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="refusal",
        refusal_reason=REFUSAL_COMPOSITION_PROJECTED,
        engine_status="out_of_domain",
        engine_reason="dropped P2O5 (mass_fraction=0.0154881)",
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.2,
        cpu_s=0.0,
        hostname="test",
    )
    for cell in (flagged, projected):
        rebuilt = EquilibrateCell.from_payload(cell.as_payload())
        assert rebuilt.as_payload() == cell.as_payload()
    assert rebuilt.refusal_reason == REFUSAL_COMPOSITION_PROJECTED
    assert flagged.as_payload()["vapor_pressure_backend_status"] == "fallback"
    assert EquilibrateCell.from_payload(flagged.as_payload()).vapor_pressures_source[
        "Si"
    ].startswith("antoine_fallback_from_vaporock")
