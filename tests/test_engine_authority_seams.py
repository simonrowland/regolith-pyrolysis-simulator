from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.alphamelts.thermoengine import (
    ThermoEngineIsolationError,
    ThermoEngineOutOfDomainError,
    ThermoEngineRefusalCause,
    ThermoEngineTransport,
)
from simulator.coating_lifespan import (
    FoulingTerminalSnapshot,
    merge_run_snapshot,
)
from simulator.diagnostics import coating_summary_with_grounded_authority
from simulator.melt_backend.thermoengine import ThermoEngineBackend
from simulator.melt_backend.liquidus import (
    build_equilibrium_crystallization_path,
    find_liquidus_solidus_by_fraction,
)
from simulator.optimize.objective import (
    _coating_authority_status,
    _cumulative_wall_deposit_by_segment_species_kg,
)


def test_thermoengine_liquidus_preserves_policy_refusal() -> None:
    backend = ThermoEngineBackend()
    backend._mode = "thermoengine"
    backend._thermoengine_transport = object()
    calls: list[float] = []

    def refuse(temperature_C: float, **_kwargs: object) -> None:
        calls.append(float(temperature_C))
        raise ThermoEngineOutOfDomainError(
            ThermoEngineRefusalCause.FO2_REQUIRES_IRON
        )

    backend.equilibrate = refuse  # type: ignore[method-assign]
    refused = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )

    assert calls == [800.0]
    assert refused.status == "refused"
    assert "fo2_requires_iron" in refused.warnings[0]
    assert refused.diagnostics["backend_status_reason"] == "fo2_requires_iron"

    def solver_failure(_temperature_C: float, **_kwargs: object) -> None:
        raise RuntimeError("solver iteration failed")

    backend.equilibrate = solver_failure  # type: ignore[method-assign]
    not_converged = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )

    assert not_converged.status == "not_converged"


def test_thermoengine_liquidus_preserves_isolation_policy_refusal() -> None:
    transport = ThermoEngineTransport(
        activity_converter=lambda _mu, _mu0, _temperature_K: 1.0,
    )
    backend = ThermoEngineBackend()
    backend._mode = "thermoengine"
    backend._thermoengine_transport = transport

    refused = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )

    assert refused.status == "refused"
    assert refused.status != "not_converged"
    assert (
        refused.diagnostics["backend_status_reason"]
        == "thermoengine_isolation_required"
    )
    assert backend.is_available() is False
    assert backend._thermoengine_transport is None
    assert backend.transport_close_count() == 1


def test_liquidus_library_boundaries_preserve_typed_policy_refusal() -> None:
    refusal = ThermoEngineIsolationError(
        "ThermoEngine equilibrium requires an isolated worker"
    )

    def refuse_fraction(_temperature_C: float) -> float:
        raise refusal

    finder_result = find_liquidus_solidus_by_fraction(
        refuse_fraction,
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )

    def refuse_state(_temperature_C: float) -> tuple[float, dict[str, float]]:
        raise refusal

    path_result = build_equilibrium_crystallization_path(
        refuse_state,
        solidus_T_C=800.0,
        liquidus_T_C=900.0,
        grid_step_C=100.0,
    )

    for result in (finder_result, path_result):
        assert result.status == "refused"
        assert result.status != "not_converged"
        assert (
            result.diagnostics["backend_status_reason"]
            == "thermoengine_isolation_required"
        )


@pytest.mark.parametrize(
    ("exception_name", "expected_reason"),
    [
        ("ThermoEngineFO2UndefinedError", "thermoengine_fo2_undefined"),
        ("ThermoEngineNonFiniteField", "thermoengine_nonfinite_field"),
        ("ThermoEngineFO2OmittedError", "thermoengine_fo2_omitted"),
    ],
)
def test_liquidus_preserves_typed_keep_handle_refusals(
    exception_name: str,
    expected_reason: str,
) -> None:
    from engines.alphamelts import thermoengine as thermoengine_module

    exception_type = getattr(thermoengine_module, exception_name)

    def refuse_fraction(_temperature_C: float) -> float:
        raise exception_type("typed row-local refusal")

    result = find_liquidus_solidus_by_fraction(
        refuse_fraction,
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )

    assert result.status == "refused"
    assert result.status != "not_converged"
    assert result.diagnostics["backend_status_reason"] == expected_reason


def test_grounded_coating_summary_distinguishes_zero_from_absence() -> None:
    proven_zero = coating_summary_with_grounded_authority(
        {
            "wall_deposit_kg_by_segment_species": {
                "hot_wall": {"SiO": 0.0},
            },
            "coating_authoritative": False,
            "coating_status": "warning",
        }
    )
    absent = coating_summary_with_grounded_authority(
        {
            "coating_authoritative": True,
            "coating_status": "available",
            "coating_output_status": "authoritative",
        }
    )

    assert proven_zero["coating_authoritative"] is True
    assert proven_zero["coating_status"] == "available"
    assert absent["coating_authoritative"] is False
    assert absent["coating_status"] == "warning"
    assert (
        absent["wall_deposit_sticking_authority"]["code"]
        == "wall_deposit_coverage_unknown"
    )


def test_lifespan_snapshot_distinguishes_zero_from_absence() -> None:
    proven_zero = FoulingTerminalSnapshot.from_trace(
        SimpleNamespace(
            wall_deposit_by_segment_species_kg={
                ("hot_wall", "SiO"): 0.0,
            },
            wall_deposit_sticking_authority={},
        )
    )
    absent = FoulingTerminalSnapshot.from_trace(
        SimpleNamespace(
            wall_deposit_by_segment_species_kg={},
            wall_deposit_sticking_authority={},
        )
    )

    zero_merged, _ = merge_run_snapshot(None, proven_zero)
    absent_merged, _ = merge_run_snapshot(None, absent)

    assert proven_zero.deposit_plain() == {"hot_wall": {"SiO": 0.0}}
    assert zero_merged.wall_deposit_sticking_authority[
        "authoritative_for_resinter"
    ] is True
    assert absent_merged.wall_deposit_sticking_authority[
        "authoritative_for_resinter"
    ] is False
    assert (
        absent_merged.wall_deposit_sticking_authority["code"]
        == "wall_deposit_coverage_unknown"
    )


def test_best_tap_projection_distinguishes_zero_from_absence() -> None:
    zero_snapshots = (
        SimpleNamespace(
            hour=1,
            wall_deposit_by_segment_species_delta={
                ("hot_wall", "SiO"): 0.0,
            },
        ),
    )
    absent_snapshots = (SimpleNamespace(hour=1),)
    run = SimpleNamespace(
        trace=SimpleNamespace(wall_deposit_sticking_authority={})
    )

    proven_zero = _cumulative_wall_deposit_by_segment_species_kg(
        zero_snapshots,
        1,
    )
    absent = _cumulative_wall_deposit_by_segment_species_kg(
        absent_snapshots,
        1,
    )
    zero_authority = _coating_authority_status(proven_zero, run)
    absent_authority = _coating_authority_status(absent, run)

    assert proven_zero == {("hot_wall", "SiO"): pytest.approx(0.0)}
    assert zero_authority["authoritative_for_coating"] is True
    assert absent == {}
    assert absent_authority["authoritative_for_coating"] is False
    assert absent_authority["code"] == "wall_deposit_coverage_unknown"
