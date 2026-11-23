"""Mol-native Langmuir and Knudsen inventory integration for residues."""

from __future__ import annotations

import atexit
import json
import math
import multiprocessing
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from multiprocessing.util import Finalize
from pathlib import Path
from typing import Any

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.accounting.formulas import parse_formula
from simulator.alpha_kinetics import _evaluate_extracted_arrhenius_alpha
from simulator.battery.oxygen_balance import (
    _CHROMIUM_OXYGEN_PER_CR_CANDIDATES,
    OxygenBalanceBracketBoundary,
    _VacuumOxygenChannel,
    _channel_parent_oxygen_demand,
    _chromium_oxygen_per_cr_from_valence,
    _net_evaporation_pressure_Pa,
    _solve_vacuum_oxygen_balance,
)
from simulator.vacuum_oxygen_balance import (
    PressureEvaluation,
    PressureLawChannelDiagnostic,
    PressureLawExponentMismatch,
)
from simulator.battery.records import nominal_pressure_pa
from simulator.source_ids import (
    HASHIMOTO_SOURCE_ID,
    SOSSI_SOURCE_ID as _SOSSI_SOURCE_ID,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.fe_redox import _engine_fe_speciation
from simulator.physical_constants import PA_PER_BAR
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView


_SECONDS_PER_HOUR = 3_600.0
_OXYGEN_CHANNELS = frozenset({"O", "O2"})
_KRESS91_FALLBACK_SOURCE = "kress91_fallback"
_KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE = (
    "kress91_unavailable_stoichiometric_feo"
)
_KNUDSEN_MAX_RHS_EVALUATIONS = 1024
_KNUDSEN_MAX_LINEAR_RHS_EVALUATIONS = 512
_KNUDSEN_EXTENT_EVALUATION_LIMIT = "knudsen_extent_evaluation_limit"
_HASHIMOTO_PRIMARY_ALPHA_ARM = "alpha_common_unity_sensitivity"
_HASHIMOTO_PRIMARY_GEOMETRY = "sphere_shrinking"
_HASHIMOTO_GEOMETRIES = (
    "sphere_constant",
    "sphere_shrinking",
    "disk_4mm_constant",
)
_HASHIMOTO_DENSITY_KG_M3 = 2_700.0
_HASHIMOTO_N_CAP = 256
_HASHIMOTO_REFINEMENT_TOLERANCE_WT_PCT = 0.05
_HASHIMOTO_DENSITY_SOURCE = (
    "labelled 2700 kg/m3 fallback from the r2 geometry sensitivity probe; "
    "not printed by Hashimoto"
)
_HASHIMOTO_COMMON_UNITY_SOURCE = (
    "Hashimoto assumed alpha_i/alpha_j = 1 only; absolute alpha=1 is a "
    "declared sensitivity choice, not an author-established kinetic model"
)


class ResidueInventoryRefusal(ValueError):
    """Typed absence or invalid input at the residue integration boundary."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def _residue_oxygen_balance_failure(exc: ValueError) -> ResidueInventoryRefusal:
    detail = str(exc)
    reason = (
        "residue_pressure_law_exponent_mismatch"
        if isinstance(exc, PressureLawExponentMismatch)
        else "residue_oxygen_balance_failed"
    )
    return ResidueInventoryRefusal(reason, detail)


class ResidueEngineNonconvergence(RuntimeError):
    """An engine failed to converge at a valid residue composition."""

    reason = "engine_nonconvergence"

    def __init__(
        self,
        engine: str,
        detail: str,
        *,
        composition_mol: Mapping[str, float] | None = None,
        step: int | None = None,
        time_reached_s: float | None = None,
        retry: str | None = None,
        reason: str = "engine_nonconvergence",
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.engine = engine
        self.detail = detail
        self.composition_mol = (
            dict(composition_mol) if composition_mol is not None else None
        )
        self.step = step
        self.time_reached_s = time_reached_s
        self.retry = retry

    def as_record(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "engine": self.engine,
            "composition_mol": self.composition_mol,
            "step": self.step,
            "time_reached_s": self.time_reached_s,
            "message": self.detail,
            "retry": self.retry,
        }


class ResidueEngineTransportError(ResidueEngineNonconvergence):
    """Infrastructure failure moving an engine result across its worker pipe."""

    def __init__(self, detail: str) -> None:
        super().__init__("openimcc", detail, reason="engine_transport_error:openimcc")


_OPENIMCC_EVALUATION_WORKER_STARTUP_TIMEOUT_S = 120.0
# Each step of worker teardown waits this long. The ladder is sentinel,
# SIGTERM, then SIGKILL. An unbounded join here deadlocks interpreter
# exit when the child ignores SIGTERM and stays in recv.
_OPENIMCC_WORKER_SHUTDOWN_JOIN_S = 1.0


def _openimcc_evaluation_worker(connection, evaluate) -> None:
    try:
        from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult

        connection.send(("ready",))
    except BaseException as exc:  # noqa: BLE001 - report startup failures to parent
        try:
            connection.send(("startup_error", type(exc).__name__, str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
        connection.close()
        return
    while True:
        kwargs = connection.recv()
        if kwargs is None:
            break
        try:
            result = evaluate(**kwargs)
            if isinstance(result, OpenImccBridgeResult):
                connection.send((True, "openimcc_bridge_result", result.to_transport()))
            else:
                connection.send((True, "value", result))
        except BaseException as exc:  # noqa: BLE001 - report worker failures to parent
            connection.send((False, type(exc).__name__, str(exc)))
    connection.close()


_OPENIMCC_EVALUATION_WORKER = None


def _openimcc_process_pid(process: Any) -> int | None:
    try:
        pid = process.pid
    except (AssertionError, AttributeError, ValueError):
        return None
    if pid is None:
        return None
    return int(pid)


def _openimcc_process_alive(process: Any) -> bool:
    if _openimcc_process_pid(process) is None:
        return False
    try:
        return bool(process.is_alive())
    except (AssertionError, ValueError, OSError):
        return False


def _join_openimcc_process(process: Any, timeout_s: float) -> None:
    if _openimcc_process_pid(process) is None:
        return
    try:
        process.join(timeout=timeout_s)
    except (AssertionError, ValueError, OSError):
        return


def _shutdown_openimcc_worker(
    process: Any,
    connection: Any = None,
    *,
    join_timeout_s: float = _OPENIMCC_WORKER_SHUTDOWN_JOIN_S,
) -> None:
    """Reap one OpenIMCC worker. Every wait is bounded.

    SIGTERM is not enough to finish the child: it can mask or ignore the
    signal and remain blocked in ``connection.recv``. The following
    ``join()`` with no timeout then never returns, including from atexit.
    multiprocessing's own exit handler also ``join()``s whatever is still
    alive, so this must return only after the child is dead or the last
    bounded wait has elapsed.
    """
    if _openimcc_process_alive(process) and connection is not None:
        try:
            connection.send(None)
        except (BrokenPipeError, EOFError, OSError):
            pass
    _join_openimcc_process(process, join_timeout_s)
    if _openimcc_process_alive(process):
        try:
            process.terminate()
        except (AttributeError, OSError, ValueError):
            pass
        _join_openimcc_process(process, join_timeout_s)
    if _openimcc_process_alive(process):
        try:
            process.kill()
        except (AttributeError, OSError, ValueError):
            pass
        _join_openimcc_process(process, join_timeout_s)


def _close_openimcc_evaluation_worker() -> None:
    global _OPENIMCC_EVALUATION_WORKER
    worker = _OPENIMCC_EVALUATION_WORKER
    _OPENIMCC_EVALUATION_WORKER = None
    if worker is None:
        return
    process, connection, _evaluate = worker
    try:
        _shutdown_openimcc_worker(process, connection)
    finally:
        connection.close()


atexit.register(_close_openimcc_evaluation_worker)
# Runs inside multiprocessing's exit handler, before that handler's
# unbounded join, even when get_logger() has moved that handler ahead of
# the atexit registration above.
Finalize(None, _close_openimcc_evaluation_worker, exitpriority=10)


def _evaluate_openimcc_isolated(
    evaluate: Callable[..., Any], *, timeout_s: float, **kwargs: Any
) -> Any:
    """Run one residue-engine call in a killable process with a hard deadline."""
    global _OPENIMCC_EVALUATION_WORKER
    worker = _OPENIMCC_EVALUATION_WORKER
    if worker is not None and (
        not worker[0].is_alive() or worker[2] is not evaluate
    ):
        _close_openimcc_evaluation_worker()
        worker = None
    if worker is None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_openimcc_evaluation_worker,
            args=(child, evaluate),
            daemon=True,
        )
        try:
            # Spawn re-imports __main__; score_store scripts need an __main__ guard.
            process.start()
        except Exception as exc:
            try:
                _shutdown_openimcc_worker(process, parent)
            except (AssertionError, OSError, ValueError):
                pass
            try:
                process.close()
            except (AssertionError, OSError, ValueError):
                pass
            finally:
                parent.close()
                child.close()
                _OPENIMCC_EVALUATION_WORKER = None
            raise ResidueEngineTransportError(
                f"OpenIMCC worker failed to start: {exc}"
            ) from exc
        child.close()
        worker = (process, parent, evaluate)
        _OPENIMCC_EVALUATION_WORKER = worker
        if not parent.poll(_OPENIMCC_EVALUATION_WORKER_STARTUP_TIMEOUT_S):
            _close_openimcc_evaluation_worker()
            raise ResidueEngineTransportError(
                "OpenIMCC worker readiness timed out after "
                f"{_OPENIMCC_EVALUATION_WORKER_STARTUP_TIMEOUT_S:g}s"
            )
        try:
            startup = parent.recv()
        except (EOFError, OSError) as exc:
            _close_openimcc_evaluation_worker()
            raise ResidueEngineTransportError(
                f"OpenIMCC worker exited before readiness: {exc}"
            ) from exc
        if startup != ("ready",):
            _close_openimcc_evaluation_worker()
            if (
                isinstance(startup, tuple)
                and len(startup) == 3
                and startup[0] == "startup_error"
            ):
                raise ResidueEngineTransportError(
                    f"OpenIMCC worker startup failed: {startup[1]}: {startup[2]}"
                )
            raise ResidueEngineTransportError(
                f"unexpected OpenIMCC worker startup response: {startup!r}"
            )
    process, parent, _evaluate = worker
    try:
        try:
            parent.send(kwargs)
        except (OSError, TypeError, ValueError) as exc:
            raise ResidueEngineTransportError(str(exc)) from exc
        if not parent.poll(timeout_s):
            _close_openimcc_evaluation_worker()
            raise TimeoutError(f"OpenIMCC call timed out after {timeout_s:g}s")
        try:
            response = parent.recv()
        except (EOFError, OSError) as exc:
            raise ResidueEngineTransportError(str(exc)) from exc
        if response[0]:
            result_kind, payload = response[1:]
            if result_kind == "openimcc_bridge_result":
                from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult

                try:
                    return OpenImccBridgeResult.from_transport(payload)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ResidueEngineTransportError(str(exc)) from exc
            return payload
        error_name, detail = response[1:]
        if error_name in {"PicklingError", "TypeError", "AttributeError"} and (
            "pickl" in str(detail).lower() or "serializ" in str(detail).lower()
        ):
            raise ResidueEngineTransportError(str(detail))
        if error_name == "ImccNonconvergenceError":
            raise ResidueEngineNonconvergence("openimcc", str(detail))
        raise RuntimeError(f"isolated OpenIMCC {error_name}: {detail}")
    finally:
        if not process.is_alive():
            _close_openimcc_evaluation_worker()


def _component_exhaustion_floor_mol(sample_total_mol: float) -> float:
    """Set the oxide cutoff to sqrt(binary64 epsilon), with a 16-ULP floor."""
    total = float(sample_total_mol)
    if not math.isfinite(total) or total <= 0.0:
        raise ResidueInventoryRefusal("residue_inventory_invalid")
    return max(math.sqrt(math.ulp(1.0)) * total, 16.0 * math.ulp(total))


@dataclass(frozen=True)
class ResidueChannel:
    """Runtime evaporation channel data needed by oxygen and flux kernels."""

    species: str
    formula: str
    parent_oxide: str | None
    alpha: float
    alpha_source: str


@dataclass(frozen=True)
class ResidueInventoryResult:
    residue_mol: Mapping[str, float]
    evaporated_mol: Mapping[str, float]
    pO2_bar_by_step: tuple[float | None, ...]
    pO2_balance_bracket_boundary_by_step: tuple[
        OxygenBalanceBracketBoundary | None, ...
    ]
    pO2_balance_floor_steps: int
    pO2_balance_floor_first_step: int | None
    pO2_balance_floor_time_fraction: float
    pO2_balance_floor_time_s: float
    pO2_balance_floor_pO2_bar: float | None
    atom_closure_mol: Mapping[str, float]
    buffer_oxygen_exchange_mol: float = 0.0
    pressure_law_diagnostics: tuple[PressureLawChannelDiagnostic, ...] = ()


@dataclass(frozen=True)
class ResidueMassLossPrediction:
    mass_loss_fraction: float
    engine_flags: tuple[str, ...]
    engine_metadata: Mapping[str, str]
    pressure_law_diagnostics: tuple[PressureLawChannelDiagnostic, ...] = ()


@dataclass(frozen=True)
class _HashimotoGeometryIntegration:
    composition_wt_pct: dict[str, float]
    pO2_steps: tuple[float, ...]
    exhaustion_notices: tuple[dict[str, float | int | str], ...]
    refusal: ResidueEngineNonconvergence | None
    atom_closure: dict[str, float]
    inventory_mol: dict[str, float]
    evaporated_mol: Mapping[str, float]
    pO2_balance_floor: dict[str, float | int | None]
    pressure_law_diagnostics: tuple[PressureLawChannelDiagnostic, ...] = ()

    def __iter__(self):
        # Cohort callers unpack six results, with the balance floor last.
        # len() stays 5 so index 3 is still the refusal for the sequence test.
        yield self.composition_wt_pct
        yield self.pO2_steps
        yield self.exhaustion_notices
        yield self.refusal
        yield self.atom_closure
        yield self.pO2_balance_floor

    def __getitem__(self, index: int | slice) -> object:
        return tuple(self)[index]

    def __len__(self) -> int:
        return 5


@dataclass(frozen=True)
class _HashimotoRunSetup:
    experiment_id: str
    temperature_K: float
    duration_s: float
    sample_mass_kg: float
    total_pressure_Pa: float
    starting_composition_wt_pct: Mapping[str, Any]
    initial_inventory_mol: dict[str, float]
    steps: int
    geometry_areas: dict[str, float]
    gas_channels: tuple[Any, ...]
    liquid_rows: tuple[dict[str, Any], ...]
    common_channels: tuple[ResidueChannel, ...]
    common_alpha_records: tuple[dict[str, Any], ...]
    common_liquid_rows: tuple[dict[str, Any], ...]
    catalog_matches: Mapping[str, tuple[str, Mapping[str, Any]]]
    alpha_specs: Mapping[str, Any]
    pressure_model: Any
    engine_flags: set[str]
    engine_floor_inversion_diagnostics: dict[str, dict[str, object]]


@dataclass(frozen=True)
class _HashimotoResiduePrediction:
    """One run and alpha arm, with the declared geometry sensitivity surface."""

    experiment_id: str
    alpha_arm: str
    primary_geometry_policy_id: str
    primary_oxide_wt_pct: Mapping[str, float]
    geometry_oxide_wt_pct: Mapping[str, Mapping[str, float]]
    sensitivity_band_wt_pct: Mapping[str, tuple[float, float]]
    initial_inventory_mol: Mapping[str, float]
    primary_inventory_mol: Mapping[str, float] | None
    provenance: Mapping[str, object]


@dataclass(frozen=True)
class _SossiResiduePrediction:
    experiment_id: str
    primary_element_ppm: Mapping[str, float]
    geometry_element_ppm: Mapping[str, Mapping[str, float]]
    sensitivity_band_ppm: Mapping[str, tuple[float, float]]
    channel_missing_elements: tuple[str, ...]
    provenance: Mapping[str, object]


@dataclass(frozen=True)
class _ChannelTerms:
    source: ResidueChannel
    molar_mass_kg_mol: float
    atom_counts: Mapping[str, float]
    parent_moles_per_product: float
    parent_oxygen_demand: float
    oxide_per_product_kg: float
    o2_per_product_kg: float

    def is_active(self, inventory: Mapping[str, float]) -> bool:
        """Parentless oxygen stays active, including the gas pack's '' token."""
        return (
            self.parent_moles_per_product == 0.0
            or inventory.get(self.source.parent_oxide or "", 0.0) > 0.0
        )


def mean_oxygen_per_metal_atom(
    valence_fractions: Sequence[tuple[float, float]],
) -> float:
    """Melt mean oxygen per metal atom, o_bar_M = sum_v x_v * (v/2).

    v is the cation charge and x_v is the fraction of metal atoms in that
    charge. The result is O atoms per metal atom (dimensionless count).
    Fe2+ (v = 2) contributes 1; Fe3+ (v = 3) contributes 3/2.
    """

    total = 0.0
    for valence, fraction in valence_fractions:
        total += float(fraction) * float(valence) / 2.0
    return total


def iron_mean_oxygen_per_atom(fe3_over_sum_fe: float) -> float:
    """o_bar_Fe = 1 + q/2, q = Fe3+/sumFe. q = 0 is exactly 1 O per Fe."""

    q = float(fe3_over_sum_fe)
    if not math.isfinite(q) or q < 0.0 or q > 1.0:
        raise ValueError(f"Fe3+/sumFe must be in [0, 1], got {fe3_over_sum_fe!r}")
    return mean_oxygen_per_metal_atom(((2.0, 1.0 - q), (3.0, q)))


def _kress91_fe_speciation(
    inventory: Mapping[str, float],
    log10_pO2_bar: float,
    *,
    temperature_K: float,
    total_pressure_Pa: float,
) -> tuple[float | None, float | None, str | None]:
    """Kress91 q and x_FeO at this inventory and pO2.

    x_FeO is the ferrous mole fraction from the same split, used as the
    activity whose log slope corrects the Fe pressure exponent. It is not
    the CALPHAD FeO activity. Both numbers are dimensionless. No iron means
    no speciation. A Kress failure keeps the stoichiometric FeO demand and
    names that default.
    """

    from simulator.fe_redox import (
        Kress91InvalidControls,
        floor_vacuum_pressure_bar,
        kress91_split,
        melt_mol_fractions_for_kress91,
    )

    try:
        wt_pct = _hashimoto_project_oxide_wt_pct(inventory)
        fractions = melt_mol_fractions_for_kress91(wt_pct)
        feot = float(fractions.get("FeOt", 0.0) or 0.0)
    except (
        ResidueInventoryRefusal,
        Kress91InvalidControls,
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None, None, _KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE
    if not math.isfinite(feot) or feot <= 0.0:
        return None, None, None
    try:
        split = kress91_split(
            fO2_log=float(log10_pO2_bar),
            mol_fractions=fractions,
            T_K=float(temperature_K),
            pressure_bar=floor_vacuum_pressure_bar(
                float(total_pressure_Pa) / PA_PER_BAR
            ),
        )
        q = float(split["fe3"])
        activity = float(split["x_feo"])
    except (
        Kress91InvalidControls,
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None, None, _KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE
    if not math.isfinite(q):
        return None, None, _KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE
    if not math.isfinite(activity):
        activity = None
    return q, activity, _KRESS91_FALLBACK_SOURCE


def _record_fe_speciation_source(
    model: object, source: str | None, engine_flags: set[str],
) -> None:
    """Record each speciation source once per pressure model, not per root probe."""

    from simulator.vacuum_oxygen_balance import (
        RESIDUE_FE_SPECIATION_KRESS91_FALLBACK,
        RESIDUE_FE_SPECIATION_UNAVAILABLE_STOICHIOMETRIC_FEO,
    )

    if not source:
        return
    seen = getattr(model, "fe_speciation_sources", None)
    if not isinstance(seen, set):
        seen = set()
        setattr(model, "fe_speciation_sources", seen)
    if source in seen:
        return
    seen.add(source)
    if source == _KRESS91_FALLBACK_SOURCE:
        engine_flags.add(RESIDUE_FE_SPECIATION_KRESS91_FALLBACK)
    elif source == _KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE:
        engine_flags.add(RESIDUE_FE_SPECIATION_UNAVAILABLE_STOICHIOMETRIC_FEO)


def _pressure_with_fe_speciation(
    model: object,
    pressures: Mapping[str, float],
    inventory: Mapping[str, float],
    log10_pO2_bar: float,
    *,
    temperature_K: float,
    total_pressure_Pa: float,
    engine_flags: set[str],
    engine_response: object | None = None,
    below_threshold_omissions: Mapping[str, Mapping[str, object]] | None = None,
) -> PressureEvaluation:
    """Attach Fe speciation from this evaluation to the species pressures.

    The engine's own q at this pO2 wins. Otherwise Kress91 supplies q and
    x_FeO and the call is flagged. No q leaves the stoichiometric demand.
    """

    q = None
    activity = None
    source = None
    if engine_response is not None:
        q, activity = _engine_fe_speciation(engine_response)
        if q is not None:
            source = "engine"
    if source is None:
        q, activity, source = _kress91_fe_speciation(
            inventory,
            log10_pO2_bar,
            temperature_K=temperature_K,
            total_pressure_Pa=total_pressure_Pa,
        )
    _record_fe_speciation_source(model, source, engine_flags)
    evaluation = PressureEvaluation(
        pressures,
        fe3_over_sum_fe=q,
        fe_activity=activity,
        speciation_source=source,
        below_threshold_omissions=below_threshold_omissions,
    )
    reported_cr_valence = getattr(engine_response, "cr_valence", None)
    if reported_cr_valence is not None:
        evaluation.cr_valence = reported_cr_valence
    return evaluation


def _formula_terms(formula_text: str) -> tuple[dict[str, float], float]:
    try:
        formula = parse_formula(formula_text)
        atoms = {str(element): float(count) for element, count in formula.elements.items()}
        molar_mass = float(formula.molar_mass_kg_per_mol())
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ResidueInventoryRefusal(
            "residue_formula_invalid", f"invalid formula {formula_text!r}: {exc}"
        ) from exc
    if (
        not atoms
        or not math.isfinite(molar_mass)
        or molar_mass <= 0.0
        or any(not math.isfinite(value) or value <= 0.0 for value in atoms.values())
    ):
        raise ResidueInventoryRefusal(
            "residue_formula_invalid", f"formula {formula_text!r} is not finite and positive"
        )
    return atoms, molar_mass


def _channel_terms(
    channels: Sequence[ResidueChannel], *, require_oxygen_channels: bool = True
) -> tuple[_ChannelTerms, ...]:
    terms: list[_ChannelTerms] = []
    seen: set[str] = set()
    for channel in channels:
        if not channel.species or channel.species in seen:
            raise ResidueInventoryRefusal(
                "residue_channel_invalid", f"missing or duplicate channel {channel.species!r}"
            )
        seen.add(channel.species)
        if (
            not math.isfinite(float(channel.alpha))
            or float(channel.alpha) < 0.0
            or float(channel.alpha) > 1.0
        ):
            raise ResidueInventoryRefusal(
                "residue_alpha_invalid", f"alpha for {channel.species!r} must be in [0, 1]"
            )
        if not isinstance(channel.alpha_source, str) or not channel.alpha_source.strip():
            raise ResidueInventoryRefusal(
                "residue_alpha_source_missing", f"alpha source missing for {channel.species!r}"
            )
        atom_counts, molar_mass = _formula_terms(channel.formula)
        parent_moles_per_product = 0.0
        parent_oxygen_demand = 0.0
        oxide_per_product_kg = 0.0
        o2_per_product_kg = 0.0
        if channel.parent_oxide:
            parent_atoms, parent_molar_mass = _formula_terms(channel.parent_oxide)
            gas_metals = {
                element: count
                for element, count in atom_counts.items()
                if element != "O"
            }
            parent_metals = {
                element: count
                for element, count in parent_atoms.items()
                if element != "O"
            }
            if not gas_metals or set(gas_metals) != set(parent_metals):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} and parent {channel.parent_oxide!r} do not share the same non-oxygen elements",
                )
            ratios = [gas_metals[element] / parent_metals[element] for element in gas_metals]
            parent_moles_per_product = ratios[0]
            if any(
                not math.isclose(ratio, parent_moles_per_product, rel_tol=1.0e-12, abs_tol=1.0e-14)
                for ratio in ratios[1:]
            ):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} does not preserve parent non-oxygen atom ratios",
                )
            parent_oxygen_demand = parent_moles_per_product * parent_atoms.get("O", 0.0)
            oxide_per_product_kg = parent_moles_per_product * parent_molar_mass / molar_mass
            oxygen_difference = parent_oxygen_demand - atom_counts.get("O", 0.0)
            _oxygen_atoms, oxygen_molar_mass = _formula_terms("O2")
            o2_per_product_kg = (
                oxygen_difference * 0.5 * oxygen_molar_mass / molar_mass
            )
            if not math.isclose(
                oxide_per_product_kg,
                1.0 + o2_per_product_kg,
                rel_tol=1.0e-6,
                abs_tol=1.0e-9,
            ):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} mass and atom stoichiometry disagree",
                )
        elif channel.species not in _OXYGEN_CHANNELS:
            raise ResidueInventoryRefusal(
                "residue_parent_missing",
                f"evaporation channel {channel.species!r} has no parent oxide",
            )
        terms.append(
            _ChannelTerms(
                source=channel,
                molar_mass_kg_mol=molar_mass,
                atom_counts=atom_counts,
                parent_moles_per_product=parent_moles_per_product,
                parent_oxygen_demand=parent_oxygen_demand,
                oxide_per_product_kg=oxide_per_product_kg,
                o2_per_product_kg=o2_per_product_kg,
            )
        )
    if not terms:
        raise ResidueInventoryRefusal("residue_channels_missing")
    if require_oxygen_channels and not _OXYGEN_CHANNELS.intersection(seen):
        raise ResidueInventoryRefusal(
            "residue_oxygen_channels_missing", "vacuum closure requires an O or O2 channel"
        )
    return tuple(terms)


def _atom_totals(
    residue_mol: Mapping[str, float], evaporated_mol: Mapping[str, float]
) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for species, amount in (*residue_mol.items(), *evaporated_mol.items()):
        atoms, _molar_mass = _formula_terms(species)
        for element, count in atoms.items():
            totals[element] += float(amount) * count
    return dict(totals)


def _residue_oxygen_channels(
    terms: Sequence[_ChannelTerms], inventory: Mapping[str, float],
) -> tuple[_VacuumOxygenChannel, ...]:
    channels: list[_VacuumOxygenChannel] = []
    for term in terms:
        metals = {
            element: count
            for element, count in term.atom_counts.items()
            if element != "O"
        }
        redox_atoms = 0.0
        oxygen_per_metal = None
        if set(metals) == {"Fe"}:
            redox_atoms = float(metals["Fe"])
            oxygen_per_metal = iron_mean_oxygen_per_atom
        elif term.source.species == "CrO2" and set(metals) == {"Cr"}:
            # CrO2 uses optional producer valence in the shared oxygen-demand
            # owner; no Cr valence model is supplied when the response omits it.
            redox_atoms = float(metals["Cr"])
        channels.append(
            _VacuumOxygenChannel(
                species=term.source.species,
                molar_mass_kg_per_mol=term.molar_mass_kg_mol,
                oxygen_atoms=term.atom_counts.get("O", 0.0),
                parent_oxygen_demand=term.parent_oxygen_demand,
                pO2_exponent=(term.atom_counts.get("O", 0.0) - term.parent_oxygen_demand) / 2.0,
                alpha=float(term.source.alpha),
                alpha_source=term.source.alpha_source,
                active=term.is_active(inventory),
                oxygen_per_metal_atom=oxygen_per_metal,
                redox_metal_atoms=redox_atoms,
            )
        )
    return tuple(channels)

def _check_residue_atom_residual(element, residual, scale, context):
    """Shared atom-conservation acceptance; callers retain their arithmetic."""
    if abs(residual) > max(1.0e-15, 5.0e-12 * scale):
        raise ResidueInventoryRefusal(
            "residue_atom_balance_failed",
            f"{element} atom residual {residual:.17g} mol {context}",
        )


def _residue_atom_closure(initial_totals, final_totals, buffer_oxygen_exchange_mol=0.0):
    """One scale-aware atom-closure check for both residue paths."""
    closure = {
        element: initial_totals.get(element, 0.0)
        - final_totals.get(element, 0.0)
        + (buffer_oxygen_exchange_mol if element == "O" else 0.0)
        for element in sorted(set(initial_totals) | set(final_totals))
    }
    for element, residual in closure.items():
        scale = max(abs(initial_totals.get(element, 0.0)), abs(final_totals.get(element, 0.0)), 1.0e-300)
        _check_residue_atom_residual(element, residual, scale, "exceeds scale-aware tolerance")
    return closure


def _finite_step_fluxes(
    terms: Sequence[_ChannelTerms],
    inventory_mol: Mapping[str, float],
    *,
    temperature_K: float,
    area_m2: float,
    pressures_Pa: Mapping[str, float],
    background_pressure_Pa: float = 0.0,
    include_below_threshold: bool = False,
) -> dict[str, float]:
    """Run the authoritative HKL provider at the solved channel pressures."""
    parent_channels = [term for term in terms if term.parent_moles_per_product > 0.0]
    if not parent_channels:
        return {}
    provider = BuiltinEvaporationFluxProvider()
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(inventory_mol)},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=background_pressure_Pa / PA_PER_BAR,
        control_inputs={
            "vapour_batch_flux_pressures_Pa": {
                term.source.species: _net_evaporation_pressure_Pa(
                    float(pressures_Pa.get(term.source.species, 0.0)), background_pressure_Pa
                )
                for term in parent_channels
            },
            "overhead_partials_Pa": {
                term.source.species: 0.0 for term in parent_channels
            },
            "molar_mass_kg_mol": {
                term.source.species: term.molar_mass_kg_mol for term in parent_channels
            },
            "stoich_by_species": {
                term.source.species: {
                    "parent_oxide": term.source.parent_oxide,
                    "oxide_per_product_kg": term.oxide_per_product_kg,
                    "O2_per_product_kg": term.o2_per_product_kg,
                }
                for term in parent_channels
            },
            "available_oxide_kg": {
                term.source.species: float(inventory_mol.get(term.source.parent_oxide or "", 0.0))
                * _formula_terms(term.source.parent_oxide or "")[1]
                for term in parent_channels
            },
            "melt_surface_area_m2": area_m2,
            "alpha": {
                term.source.species: float(term.source.alpha) for term in parent_channels
            },
            "evaporation_series_resistance": {
                "gas_resistance_enabled": False,
                "melt_resistance_enabled": False,
            },
            "overhead_pressure_pa": background_pressure_Pa,
            "commanded_pressure_pa": background_pressure_Pa,
            "stir_factor": 0.0,
        },
    )
    result = provider.dispatch(request)
    diagnostic = dict(result.diagnostic or {})
    if str(result.status) != "ok":
        raise ResidueInventoryRefusal(
            "residue_flux_refused",
            f"builtin HKL provider returned {result.status}: {diagnostic}",
        )
    if diagnostic.get("missing_alpha"):
        raise ResidueInventoryRefusal(
            "missing_evaporation_alpha",
            f"catalog channels have no executable alpha: {sorted(diagnostic['missing_alpha'])}",
        )
    rates = dict(diagnostic.get("evaporation_flux_kg_hr") or {})
    if include_below_threshold:
        rates.update((diagnostic.get("below_threshold_evaporation_flux") or {}).get("fluxes_kg_hr") or {})
    return {str(species): float(rate) / _SECONDS_PER_HOUR for species, rate in rates.items()}


def _residue_oxygen_balance(
    terms,
    inventory,
    pressure_at,
    temperature,
    background_pressure_Pa=0.0,
    on_accepted_evaluation=None,
):
    """The shared channel stoichiometry and pressure-root call for both paths."""
    try:
        root_kwargs = {}
        if background_pressure_Pa:
            root_kwargs["background_pressure_Pa"] = background_pressure_Pa
        if on_accepted_evaluation is not None:
            root_kwargs["on_accepted_evaluation"] = on_accepted_evaluation
        return _solve_vacuum_oxygen_balance(
            pressure_at,
            _residue_oxygen_channels(terms, inventory),
            temperature_K=temperature,
            **root_kwargs,
        )
    except ResidueInventoryRefusal:
        raise
    except PressureLawExponentMismatch as exc:
        raise _residue_oxygen_balance_failure(exc) from exc


def integrate_residue_inventory(
    initial_inventory_mol: Mapping[str, float],
    channels: Sequence[ResidueChannel],
    pressure_model: Callable[[Mapping[str, float], float], Mapping[str, float]],
    *,
    temperature_K: float,
    duration_s: float,
    area_evolution_m2: Sequence[float] | None = None,
    buffered_fO2_log: float | None = None,
    background_pressure_Pa: float = 0.0,
) -> ResidueInventoryResult:
    """Integrate free evaporation with an engine-specific pressure callback.

    Premise: a vacuum melt loses gas while each vapor channel debits the oxide
    parent that supplies its non-oxygen atoms. At the start of every finite
    sub-step the callback evaluates that engine's pressure laws at trial
    ``log10(pO2 / bar)`` and the shared R1a balance selects the congruent root.
    The builtin HKL provider then evaluates
    ``J_i = alpha_i * (p_i - p_bulk,i) / sqrt(2*pi*M_i*R*T)`` in mol m^-2 s^-1;
    the explicit background ceiling is subtracted once in the shared root and
    HKL driver (zero for existing vacuum callers). The provider receives
    net driving pressures and the declared area, so its
    returned kg h^-1 is converted at the boundary back to mol s^-1.

    For a parent with inventory ``N`` mol and requested parent draw ``r`` mol
    s^-1, analytic depletion debits ``N * (1 - exp(-r*dt/N))`` mol during the
    step. Concurrent channels sharing a parent divide that debit in proportion
    to their HKL parent draw. Inventory and atom closure stay mol-native; gas
    mass is never subtracted as oxide mass. After those finite parent debits,
    the oxygen atom remainder is computed from the actual gas products and
    divided between O and O2 in the solved pressure-root flux ratio. This is
    the finite-step correction: ``nO_coproduct = Σ(nO_parent * parent_debit) -
    Σ(nO_gas * product_moles)`` and ``nO + 2*nO2 = nO_coproduct``. The frozen
    instantaneous O/O2 rates set only the partition ratio; they are not
    multiplied by dt and called the committed coproduct.

    Temperatures are K, pressure-model outputs are Pa, area is m^2, duration
    and areas are seconds and m^2, inventories and gas amounts are mol, and
    formula masses are kg mol^-1. A zero alpha or zero pressure gives zero
    channel flux; as dt tends to zero the analytic debit tends to ``r*dt``;
    for large dt no parent can be depleted below zero. Missing area evolution
    is a typed absence because the integrator must not choose geometry policy.
    """
    if area_evolution_m2 is None or len(area_evolution_m2) == 0:
        raise ResidueInventoryRefusal(
            "melt_surface_area_evolution_missing",
            "free evaporation requires an explicit per-step area evolution",
        )
    if not math.isfinite(background_pressure_Pa) or background_pressure_Pa < 0.0:
        raise ResidueInventoryRefusal("residue_background_pressure_invalid")
    temperature = float(temperature_K)
    duration = float(duration_s)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ResidueInventoryRefusal("residue_temperature_invalid")
    if not math.isfinite(duration) or duration <= 0.0:
        raise ResidueInventoryRefusal("residue_duration_invalid")
    areas = tuple(float(area) for area in area_evolution_m2)
    if any(not math.isfinite(area) or area <= 0.0 for area in areas):
        raise ResidueInventoryRefusal("melt_surface_area_invalid")
    inventory = {str(species): float(amount) for species, amount in initial_inventory_mol.items()}
    if not inventory or any(not math.isfinite(value) or value < 0.0 for value in inventory.values()):
        raise ResidueInventoryRefusal("residue_inventory_invalid")
    buffered_logp = None if buffered_fO2_log is None else float(buffered_fO2_log)
    if buffered_logp is not None and (
        not math.isfinite(buffered_logp) or buffered_logp > 0.0
    ):
        raise ResidueInventoryRefusal("oxygen_condition_invalid")
    terms = _channel_terms(
        channels, require_oxygen_channels=buffered_logp is None
    )
    term_by_species = {term.source.species: term for term in terms}
    dt_s = duration / len(areas)
    evaporated: defaultdict[str, float] = defaultdict(float)
    pO2_steps: list[float | None] = []
    bracket_boundary_steps: list[OxygenBalanceBracketBoundary | None] = []
    buffer_oxygen_exchange_mol = 0.0
    pressure_law_diagnostics: dict[str, PressureLawChannelDiagnostic] = {}

    # Supplied channels define the supported pressure bundle. Root metadata
    # uses formula atom counts, matching R1a's (nO_gas - nO_parent) balance.
    for area_m2 in areas:
        active_parent_terms = [
            term
            for term in terms
            if term.parent_moles_per_product > 0.0
            and term.source.alpha > 0.0
            and inventory.get(term.source.parent_oxide or "", 0.0) > 0.0
        ]
        if not active_parent_terms:
            pO2_steps.append(None)
            bracket_boundary_steps.append(None)
            continue

        def pressure_at(log10_pO2_bar: float) -> Mapping[str, float]:
            engine_inventory = {
                species: amount
                for species, amount in inventory.items()
                if amount > 0.0
            }
            pressures = pressure_model(engine_inventory, float(log10_pO2_bar))
            _inherit_residue_pressure_contract(pressure_at, pressure_model)
            return pressures

        def accepted_pressure_evaluation(log10_pO2_bar: float) -> None:
            accepted_callback = getattr(
                pressure_model, "_on_accepted_evaluation", None
            )
            if callable(accepted_callback):
                accepted_callback(float(log10_pO2_bar))

        balance = None
        if buffered_logp is None:
            try:
                balance = _residue_oxygen_balance(
                    terms,
                    inventory,
                    pressure_at,
                    temperature,
                    background_pressure_Pa,
                    on_accepted_evaluation=accepted_pressure_evaluation,
                )
            except ResidueInventoryRefusal:
                raise
            except ValueError as exc:
                # No positive parent pressure is the zero-flux limit, not a
                # fabricated zero oxygen root. Other missing roots remain typed.
                low_pressure = pressure_at(-15.0)
                if not any(
                    float(low_pressure.get(term.source.species, 0.0) or 0.0) > 0.0
                    for term in active_parent_terms
                ):
                    pO2_steps.append(None)
                    bracket_boundary_steps.append(None)
                    continue
                raise _residue_oxygen_balance_failure(exc) from exc
            pO2_steps.append(balance.pO2_bar)
            bracket_boundary_steps.append(balance.bracket_boundary)
            pressures = balance.pressures_Pa
            for diagnostic in balance.pressure_law_diagnostics:
                pressure_law_diagnostics[diagnostic.species] = diagnostic
        else:
            pressures = pressure_at(buffered_logp)
            for term in terms:
                if term.source.species not in pressures:
                    raise ResidueInventoryRefusal(
                        "channel_missing", f"no buffered pressure for {term.source.species}"
                    )
                pressure = float(pressures[term.source.species])
                if not math.isfinite(pressure) or pressure < 0.0:
                    raise ResidueInventoryRefusal(
                        "residue_buffered_pressure_invalid",
                        f"invalid pressure for {term.source.species!r}",
                    )
            pO2_steps.append(10.0**buffered_logp)
            bracket_boundary_steps.append(None)

        requested_rates_kg_s = _finite_step_fluxes(
            terms,
            inventory,
            temperature_K=temperature,
            area_m2=area_m2,
            pressures_Pa=pressures,
            background_pressure_Pa=background_pressure_Pa,
        )
        requested_product_mol_s = {
            species: rate_kg_s / term_by_species[species].molar_mass_kg_mol
            for species, rate_kg_s in requested_rates_kg_s.items()
        }
        channels_by_parent: defaultdict[str, list[tuple[_ChannelTerms, float]]] = defaultdict(list)
        for species, product_rate in requested_product_mol_s.items():
            term = term_by_species[species]
            parent = term.source.parent_oxide
            if parent is None or product_rate <= 0.0:
                continue
            channels_by_parent[parent].append((term, product_rate))

        actual_parent_debits: dict[str, float] = {}
        actual_products: dict[str, float] = {}
        for parent, parent_channels in channels_by_parent.items():
            available_parent = float(inventory.get(parent, 0.0))
            requested_parent_rate = math.fsum(
                product_rate * term.parent_moles_per_product
                for term, product_rate in parent_channels
            )
            if available_parent <= 0.0 or requested_parent_rate <= 0.0:
                continue
            # Analytic first-order depletion in mol: 1-exp(-k*dt), with
            # k = requested parent mol s^-1 / current parent mol.
            debit = available_parent * -math.expm1(
                -requested_parent_rate * dt_s / available_parent
            )
            debit = min(available_parent, max(0.0, debit))
            actual_parent_debits[parent] = debit
            inventory[parent] = max(0.0, available_parent - debit)
            for term, product_rate in parent_channels:
                share = (
                    product_rate * term.parent_moles_per_product
                    / requested_parent_rate
                )
                product_mol = (
                    debit * share / term.parent_moles_per_product
                )
                actual_products[term.source.species] = product_mol
                evaporated[term.source.species] += product_mol

        oxygen_removed_mol = math.fsum(
            debit
            * _formula_terms(parent)[0].get("O", 0.0)
            for parent, debit in actual_parent_debits.items()
        )
        oxygen_in_parent_gases_mol = math.fsum(
            amount * term_by_species[species].atom_counts.get("O", 0.0)
            for species, amount in actual_products.items()
        )
        if buffered_logp is not None:
            buffer_oxygen_exchange_mol += (
                oxygen_in_parent_gases_mol - oxygen_removed_mol
            )
        else:
            assert balance is not None
            finite_step_oxygen_mol = oxygen_removed_mol - oxygen_in_parent_gases_mol
            oxygen_scale = max(oxygen_removed_mol, oxygen_in_parent_gases_mol, 1.0e-300)
            if finite_step_oxygen_mol < -max(1.0e-15, 1.0e-12 * oxygen_scale):
                raise ResidueInventoryRefusal(
                    "finite_step_oxygen_requires_inbound_oxygen",
                    f"actual parent debit requires {-finite_step_oxygen_mol:.17g} mol oxygen input",
                )
            finite_step_oxygen_mol = max(0.0, finite_step_oxygen_mol)
            raw_o = balance.channel_fluxes_mol_m2_s.get("O", 0.0) * area_m2 * dt_s
            raw_o2 = balance.channel_fluxes_mol_m2_s.get("O2", 0.0) * area_m2 * dt_s
            raw_oxygen_atoms = raw_o + 2.0 * raw_o2
            if finite_step_oxygen_mol > 0.0:
                if raw_oxygen_atoms <= 0.0:
                    raise ResidueInventoryRefusal(
                        "finite_step_oxygen_partition_missing",
                        "solved O/O2 pressure partition has no positive oxygen flux",
                    )
                partition_scale = finite_step_oxygen_mol / raw_oxygen_atoms
                actual_o = raw_o * partition_scale
                actual_o2 = raw_o2 * partition_scale
                if actual_o > 0.0:
                    evaporated["O"] += actual_o
                if actual_o2 > 0.0:
                    evaporated["O2"] += actual_o2

    initial_totals = _atom_totals(initial_inventory_mol, {})
    final_totals = _atom_totals(inventory, evaporated)
    closure = _residue_atom_closure(
        initial_totals, final_totals, buffer_oxygen_exchange_mol
    )
    floor_step_indices = [
        index
        for index, boundary in enumerate(bracket_boundary_steps, start=1)
        if boundary == "lower"
    ]
    floor_steps = len(floor_step_indices)
    floor_time_s = duration * floor_steps / len(areas)
    first_floor_step = floor_step_indices[0] if floor_step_indices else None
    return ResidueInventoryResult(
        residue_mol=dict(inventory),
        evaporated_mol=dict(evaporated),
        pO2_bar_by_step=tuple(pO2_steps),
        pO2_balance_bracket_boundary_by_step=tuple(bracket_boundary_steps),
        pO2_balance_floor_steps=floor_steps,
        pO2_balance_floor_first_step=first_floor_step,
        pO2_balance_floor_time_fraction=floor_time_s / duration,
        pO2_balance_floor_time_s=floor_time_s,
        pO2_balance_floor_pO2_bar=(
            pO2_steps[first_floor_step - 1]
            if first_floor_step is not None
            else None
        ),
        atom_closure_mol=closure,
        buffer_oxygen_exchange_mol=buffer_oxygen_exchange_mol,
        pressure_law_diagnostics=tuple(pressure_law_diagnostics.values()),
    )


def _hashimoto_primary_policy() -> tuple[str, str]:
    """Return the predeclared arm and geometry; this policy has no data input."""
    return _HASHIMOTO_PRIMARY_ALPHA_ARM, _HASHIMOTO_PRIMARY_GEOMETRY


@dataclass(frozen=True)
class KnudsenResidueResult:
    residue_mol: Mapping[str, float]
    evaporated_mol: Mapping[str, float]
    retained_oxygen_atoms_mol: float
    atom_closure_mol: Mapping[str, float]
    pO2_bar_by_step: tuple[float, ...]
    pressure_law_diagnostics: tuple[PressureLawChannelDiagnostic, ...] = ()


def integrate_knudsen_residue_inventory(
    initial_inventory_mol: Mapping[str, float],
    channels: Sequence[ResidueChannel],
    pressure_model: Callable[[Mapping[str, float], float, float], Mapping[str, float]],
    *,
    temperature_path: Sequence[tuple[float, float]],
    measured_mass_loss_fraction: float,
    near_saturated: bool,
    source_fO2_log_by_interval: Sequence[float | None],
    linear_temperature: bool = False,
) -> KnudsenResidueResult:
    """Integrate molecular effusion on the measured *total* mass-loss extent.

    Each path pair is (cumulative kg/kg loss, printed step temperature K).
    Near saturation, q_i = P_i/sqrt(M_i); the common orifice, Clausing and
    sqrt(2*pi*R*T) factors cancel. All escaping gases, INCLUDING O and O2,
    contribute to D = sum(M_i*q_i). Then dn_i = m0*q_i/D * dxi, so
    sum(M_i*dn_i)/m0 = dxi [kg/kg]. For a single volatile gas this reduces
    exactly to n_lost = m0*xi/M, independent of its pressure or apparatus.

    Oxide moles track cations for the existing pressure/composition callbacks.
    Oxygen atoms are tracked separately: source-conditioned O/O2 escape need
    not match the oxygen in those oxide equivalents. No external oxygen
    supply or stoichiometric replacement of the effusing O flux is assumed.
    """
    if not near_saturated:
        raise ResidueInventoryRefusal("knudsen_near_saturation_invalid")
    try:
        target = float(measured_mass_loss_fraction)
    except (TypeError, ValueError) as exc:
        raise ResidueInventoryRefusal("measured_mass_loss_fraction_missing_or_invalid") from exc
    if not math.isfinite(target) or not 0.0 <= target < 1.0:
        raise ResidueInventoryRefusal("measured_mass_loss_fraction_missing_or_invalid")
    inventory = {str(k): float(v) for k, v in initial_inventory_mol.items()}
    if not inventory or any(not math.isfinite(v) or v < 0.0 for v in inventory.values()):
        raise ResidueInventoryRefusal("initial_composition_invalid")
    initial_atoms = _atom_totals(inventory, {})
    m0 = math.fsum(n * _formula_terms(k)[1] for k, n in inventory.items())
    if m0 <= 0.0:
        raise ResidueInventoryRefusal("initial_composition_invalid")
    try:
        path = tuple((float(x), float(t)) for x, t in temperature_path)
    except (TypeError, ValueError) as exc:
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid") from exc
    if (
        not path or len(path) != len(source_fO2_log_by_interval)
        or any(not math.isfinite(x) or not math.isfinite(t) or t <= 0.0
               or x <= 0.0 or x >= 1.0 for x, t in path)
        or any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(path, path[1:]))
        or path[-1][0] < target
    ):
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid")
    terms = _channel_terms(channels)
    if any(term.source.alpha != 1.0 for term in terms):
        raise ResidueInventoryRefusal("knudsen_equilibrium_channels_required")
    from scipy.integrate import solve_ivp
    from simulator.chemistry.langmuir_knudsen import (
        knudsen_effusion_relative_weight,
    )

    parents = tuple(inventory)
    gases = tuple(term.source.species for term in terms)
    oxygen_index = len(parents)
    gas_offset = oxygen_index + 1
    state = [inventory[k] for k in parents] + [initial_atoms.get("O", 0.0)] + [0.0] * len(gases)
    pO2_steps: list[float] = []
    # Last root per species. solve_ivp probes rejected stages; the latest
    # accepted evaluation overwrites them, matching pO2_steps' last entry.
    pressure_law_diagnostics: dict[str, PressureLawChannelDiagnostic] = {}
    xi = 0.0
    previous_endpoint, previous_temperature = 0.0, path[0][1]
    initial_total_mol = math.fsum(inventory.values())
    absolute_tolerance = 1e-12 * initial_total_mol
    volatile_parents = {term.source.parent_oxide for term in terms}
    active_parents = {
        k for k in parents if k in volatile_parents and inventory[k] > 0.0
    }
    evaluation_limit = (
        _KNUDSEN_MAX_LINEAR_RHS_EVALUATIONS if linear_temperature else _KNUDSEN_MAX_RHS_EVALUATIONS
    )
    evaluations = 0
    for (endpoint, temperature), logp in zip(path, source_fO2_log_by_interval):
        if logp is not None and (not math.isfinite(logp) or logp > 0.0):
            raise ResidueInventoryRefusal("oxygen_condition_invalid")
        stop = min(target, endpoint)
        # The supplementary linear path shares one budget across its ramp.
        # A slow sensitivity solve must not withhold a step-hold prediction.
        if not linear_temperature:
            evaluations = 0

        def derivative(extent, amounts):
            nonlocal evaluations
            if evaluations >= evaluation_limit:
                raise ResidueInventoryRefusal(
                    _KNUDSEN_EXTENT_EVALUATION_LIMIT,
                    f"RHS evaluation limit in {temperature:g} K interval "
                    f"ending at mass-loss fraction {stop:g}",
                )
            evaluations += 1
            # A trial stage can overshoot zero. Omit those parents from the
            # pressure solve and let the accepted zero event deactivate them.
            current = {
                k: float(v) for k, v in zip(parents, amounts)
                if v > 0.0 and (k in active_parents or k not in volatile_parents)
            }
            current_temperature = temperature
            if linear_temperature:
                fraction = (extent - previous_endpoint) / (endpoint - previous_endpoint)
                current_temperature = previous_temperature + fraction * (temperature - previous_temperature)

            def pressure_at(p):
                pressures = pressure_model(current, p, current_temperature)
                _inherit_residue_pressure_contract(pressure_at, pressure_model)
                return pressures

            if logp is None:
                try:
                    balance = _residue_oxygen_balance(terms, current, pressure_at, current_temperature)
                except ResidueInventoryRefusal:
                    raise
                except ValueError as exc:
                    raise _residue_oxygen_balance_failure(exc) from exc
                pressures = balance.pressures_Pa
                pO2_steps.append(balance.pO2_bar)
                for diagnostic in balance.pressure_law_diagnostics:
                    pressure_law_diagnostics[diagnostic.species] = diagnostic
            else:
                pressures = pressure_at(logp)
                pO2_steps.append(10.0**logp)
            rates = {}
            for term in terms:
                species = term.source.species
                if not term.is_active(current):
                    rates[species] = 0.0
                    continue
                if species not in pressures:
                    raise ResidueInventoryRefusal("channel_missing", species)
                pressure = float(pressures[species])
                if not math.isfinite(pressure) or pressure < 0.0:
                    raise ResidueInventoryRefusal("residue_pressure_invalid", species)
                rates[species] = knudsen_effusion_relative_weight(
                    pressure, term.molar_mass_kg_mol
                )
            denominator = math.fsum(term.molar_mass_kg_mol * rates[term.source.species] for term in terms)
            if not math.isfinite(denominator) or denominator <= 0.0:
                raise ResidueInventoryRefusal("knudsen_extent_unreachable")
            products = {k: m0 * q / denominator for k, q in rates.items()}
            parent_draw: defaultdict[str, float] = defaultdict(float)
            oxygen_draw = 0.0
            for term in terms:
                rate = products[term.source.species]
                if term.source.parent_oxide is not None:
                    parent_draw[term.source.parent_oxide] += rate * term.parent_moles_per_product
                oxygen_draw += rate * term.atom_counts.get("O", 0.0)
            if amounts[oxygen_index] == 0.0 and oxygen_draw > 0.0:
                raise ResidueInventoryRefusal("knudsen_near_saturation_oxygen_exhausted", "oxygen absent from the weighed charge")
            return ([-parent_draw[k] for k in parents] + [-oxygen_draw]
                    + [products[k] for k in gases])

        while xi < stop:
            event_indices = [i for i, k in enumerate(parents) if k in active_parents]
            if state[oxygen_index] > 0.0:
                event_indices.append(oxygen_index)
            events = []
            for index in event_indices:
                def exhausted(extent, amounts, index=index):
                    return amounts[index]
                exhausted.terminal = True
                exhausted.direction = -1
                events.append(exhausted)
            solution = solve_ivp(
                derivative, (xi, stop), state, method="DOP853", events=events,
                rtol=1e-8, atol=absolute_tolerance,
            )
            if not solution.success:
                raise ResidueInventoryRefusal("knudsen_extent_nonconvergence", solution.message)
            state = [float(v) for v in solution.y[:, -1]]
            xi = float(solution.t[-1])
            if any(v < -absolute_tolerance for v in state):
                raise ResidueInventoryRefusal("knudsen_inventory_negative")
            for index, hits in zip(event_indices, solution.t_events):
                if index == oxygen_index:
                    if len(hits) and xi < stop:
                        raise ResidueInventoryRefusal(
                            "knudsen_near_saturation_oxygen_exhausted",
                            "source-conditioned effusion requires more oxygen than the weighed charge contains",
                        )
                    if len(hits):
                        state[index] = 0.0
                elif len(hits) or state[index] <= 0.0:
                    state[index] = 0.0
                    active_parents.remove(parents[index])
            state = [max(0.0, v) for v in state]
        previous_endpoint, previous_temperature = endpoint, temperature
        if xi >= target:
            break
    inventory = dict(zip(parents, state[:oxygen_index]))
    oxygen = state[oxygen_index]
    evaporated = dict(zip(gases, state[gas_offset:]))
    final_atoms = _atom_totals(inventory, evaporated)
    final_atoms["O"] = oxygen + _atom_totals({}, evaporated).get("O", 0.0)
    closure = _residue_atom_closure(initial_atoms, final_atoms)
    return KnudsenResidueResult(
        dict(inventory),
        dict(evaporated),
        oxygen,
        closure,
        tuple(pO2_steps),
        tuple(pressure_law_diagnostics.values()),
    )


def _hashimoto_point_value(record: Mapping[str, Any]) -> float:
    state = record.get("state")
    if not isinstance(state, Mapping) or state.get("tag") != "value":
        raise ResidueInventoryRefusal("hashimoto_printed_input_missing")
    # The same serialized point is a temperature, a mass, or a pressure.
    # The nominal reader is the one pressure identity uses, so a band's
    # point is the call pressure and a bare decimal stays a bare decimal.
    point = nominal_pressure_pa(state)
    if point is None:
        raise ResidueInventoryRefusal("hashimoto_printed_input_invalid")
    try:
        numeric = float(point)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResidueInventoryRefusal("hashimoto_printed_input_invalid") from exc
    if not math.isfinite(numeric):
        raise ResidueInventoryRefusal("hashimoto_printed_input_invalid")
    return numeric


def _omission_proof(record: object) -> tuple[float, float] | None:
    """Return threshold and computed pressure when the record is a real proof."""

    if not isinstance(record, Mapping):
        return None
    try:
        threshold_pa = float(record.get("reporting_threshold_Pa"))
        computed_pa = float(record.get("computed_pressure_Pa"))
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(threshold_pa)
        or not math.isfinite(computed_pa)
        or computed_pa < 0.0
        or computed_pa > threshold_pa
    ):
        return None
    return threshold_pa, computed_pa


def _translate_recorded_pressure_omissions(
    pressures: dict[str, float],
    response: object,
    required: set[str],
    notices: list[Any],
    records_out: dict[str, dict[str, Any]],
    noticed: set[str],
) -> dict[str, dict[str, Any]]:
    """Turn only provider-recorded below-threshold omissions into zero pressure.

    Any other absent key stays absent, so the caller still refuses it as missing.
    """

    diagnostic = getattr(response, "diagnostics", None)
    if not isinstance(diagnostic, Mapping):
        return {}
    vapor = diagnostic.get("vapor_pressure_diagnostic")
    if not isinstance(vapor, Mapping):
        return {}
    omissions = vapor.get("below_reporting_threshold_omissions")
    if not isinstance(omissions, Mapping) or not omissions:
        return {}
    from simulator.battery.enums import NoticeKind, Quantity
    from simulator.battery.records import Notice
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OxygenBalanceRefusal,
        _gas_formula_for_oxygen_balance,
    )

    current_omissions: dict[str, dict[str, Any]] = {}
    for key, record in omissions.items():
        proof = _omission_proof(record)
        if proof is None or not isinstance(record, Mapping):
            continue
        if str(record.get("species")) != str(key):
            continue
        try:
            formula = _gas_formula_for_oxygen_balance(key)
        except _OxygenBalanceRefusal:
            continue
        if formula not in required or formula in pressures:
            continue
        threshold_pa, computed_pa = proof
        pressures[formula] = 0.0
        record_out = {
            "species": str(key),
            "reporting_threshold_Pa": threshold_pa,
            "computed_pressure_Pa": computed_pa,
            "field": record.get("field"),
            "pressure_rail": record.get("pressure_rail"),
            "provenance": (
                "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
            ),
        }
        records_out[formula] = record_out
        current_omissions[formula] = record_out
        if formula in noticed:
            continue
        noticed.add(formula)
        notices.append(
            Notice(
                kind=NoticeKind.INPUT_OMITTED,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason=(
                    f"{formula} pressure omitted below reporting threshold "
                    f"{threshold_pa!r} Pa; computed_pressure_Pa={computed_pa!r}; "
                    f"field={record.get('field')!r}; "
                    f"pressure_rail={record.get('pressure_rail')!r}; "
                    "provenance=BuiltinVaporPressureProvider."
                    "below_reporting_threshold_omissions"
                ),
                origin="Knudsen residue pressure adapter",
            )
        )
    return current_omissions


def _append_omission_notices(notices: list[Any], callbacks: Mapping[Any, Any]) -> None:
    for callback in callbacks.values():
        for notice in getattr(callback, "below_threshold_omission_notices", ()):
            if notice not in notices:
                notices.append(notice)


def _append_redox_pressure_notices(
    notices: list[Any],
    diagnostics: Sequence[PressureLawChannelDiagnostic],
    callbacks: Mapping[Any, Any],
) -> None:
    """Surface the demoted exponent identity and the Kress91 speciation fallback.

    A fixed-activity mismatch never reaches here: the solver still refuses it.
    """

    from simulator.battery.enums import NoticeKind, Quantity
    from simulator.battery.records import Notice
    from simulator.vacuum_oxygen_balance import (
        RESIDUE_FE_SPECIATION_KRESS91_FALLBACK,
        RESIDUE_FE_SPECIATION_UNAVAILABLE_STOICHIOMETRIC_FEO,
    )

    for diagnostic in diagnostics:
        notices.append(
            Notice(
                kind=NoticeKind.ENGINE_MODEL_CAVEAT,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason=diagnostic.reason,
                origin=(
                    "congruent vapour oxygen balance; "
                    f"{diagnostic.evidence_text()}"
                ),
            )
        )
    sources: set[str] = set()
    for callback in callbacks.values():
        seen = getattr(callback, "fe_speciation_sources", ())
        if isinstance(seen, set):
            sources.update(seen)
    if _KRESS91_FALLBACK_SOURCE in sources:
        notices.append(
            Notice(
                kind=NoticeKind.FALLBACK,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason=RESIDUE_FE_SPECIATION_KRESS91_FALLBACK,
                origin="simulator.fe_redox:kress91_split",
            )
        )
    if _KRESS91_UNAVAILABLE_STOICHIOMETRIC_FEO_SOURCE in sources:
        notices.append(
            Notice(
                kind=NoticeKind.FALLBACK,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason=RESIDUE_FE_SPECIATION_UNAVAILABLE_STOICHIOMETRIC_FEO,
                origin=(
                    "simulator.fe_redox:kress91_split; default=1 O/Fe"
                ),
            )
        )


def _inherit_residue_pressure_contract(target: object, source: object) -> None:
    """Forward explicit pressure-law declarations through callback wrappers."""

    for name in (
        "declares_fixed_activities",
        "declares_redox_active",
        "engine_flags",
    ):
        if hasattr(source, name):
            setattr(target, name, getattr(source, name))
        elif hasattr(target, name):
            delattr(target, name)


def _make_residue_pressure_model(
    engine: str,
    gas_channels: Sequence[tuple[str, Sequence[Any]]],
    gas_pack: object,
    *,
    temperature_K: float,
    total_pressure_Pa: float,
    engine_metadata: dict[str, str],
    engine_flags: set[str],
    starting_inventory: Mapping[str, float],
    missing_channels_reason: str,
    caller: str,
    floor_inversion_diagnostics: dict[str, dict[str, object]] | None = None,
) -> Callable[[Mapping[str, float], float], Mapping[str, float]]:
    """Build the engine pressure closure shared by the residue cohorts."""
    pressure_exponents = {
        str(species): (
            1.0
            if str(species) == "O2"
            else -float(reaction[2]) / float(reaction[1])
        )
        for species, reaction in gas_channels
    }
    starting_parent_oxides = {
        str(oxide)
        for oxide, amount in starting_inventory.items()
        if float(amount) > 0.0
    }
    if engine == "openimcc":
        from openimcc.kernel import ImccNonconvergenceError

        from simulator.diagnostic_helpers.binary_pot_battery import (
            _ENGINE_OUTER_TIMEOUT_S,
        )
        from simulator.melt_backend import openimcc_bridge

        pressure_state_cache: dict[
            tuple[tuple[str, float], ...], Mapping[str, float]
        ] = {}

        def pressure_model(
            inventory: Mapping[str, float], log10_pO2_bar: float
        ) -> Mapping[str, float]:
            key = tuple(
                sorted((str(oxide), float(amount)) for oxide, amount in inventory.items())
            )
            base_pressure_bar = pressure_state_cache.get(key)
            if base_pressure_bar is None:
                current_composition = {
                    oxide: amount for oxide, amount in key if amount > 0.0
                }
                try:
                    timeout_s = _ENGINE_OUTER_TIMEOUT_S["openimcc"]
                    from simulator.engine_cache import cached_engine_call
                    from simulator.melt_backend.openimcc_bridge import (
                        OpenImccBridgeResult,
                    )

                    state_payload = cached_engine_call(
                        "openimcc",
                        openimcc_bridge._evaluate_request_vector(
                            composition_mol=current_composition,
                            temperature_K=temperature_K,
                            allow_extrapolation=True,
                            allow_out_of_envelope=True,
                        ),
                        lambda: _evaluate_openimcc_isolated(
                            openimcc_bridge._evaluate_uncached,
                            timeout_s=timeout_s,
                            composition_mol=current_composition,
                            temperature_K=temperature_K,
                            allow_extrapolation=True,
                            allow_out_of_envelope=True,
                        ).to_hot(),
                    )
                    state = OpenImccBridgeResult.from_hot(state_payload)
                except ImccNonconvergenceError as exc:
                    raise ResidueEngineNonconvergence("openimcc", str(exc)) from exc
                except TimeoutError as exc:
                    raise ResidueEngineNonconvergence(
                        "openimcc",
                        f"{caller} OpenIMCC timeout: {exc}",
                        reason=f"engine_timeout:openimcc:{caller.lower().replace(' ', '_')}",
                    ) from exc
                engine_flags.update(str(flag) for flag in state.flags)
                if state.extrapolated:
                    engine_flags.add("openimcc_extrapolated")
                engine_metadata.update(
                    {
                        "model_id": state.pack_model_id,
                        "datapack_version": state.pack_version,
                        "pack_digest": state.pack_digest,
                        "openimcc_version": state.openimcc_version,
                    }
                )
                base_pressure_bar = openimcc_bridge.evaluate_gas(
                    state.parent_oxide_activities,
                    temperature_K,
                    1.0,
                    gas_pack,
                    parent_oxides=state.parent_oxides,
                    allow_extrapolation=True,
                    gas_pack_selection=(
                        openimcc_bridge.DEFAULT_GAS_PACK_SELECTION
                    ),
                )
                pressure_state_cache[key] = base_pressure_bar
            pO2_bar = 10.0**float(log10_pO2_bar)
            # Activities are frozen at the reference state, then scaled by the
            # stoichiometric power of pO2. That is a declared fixed-activity
            # law: the hard exponent identity still applies. The frozen state
            # is not Fe speciation at this trial pO2, so q comes from Kress91.
            pressures = {
                str(species): (
                    float(base_pressure_bar.get(species, 0.0))
                    * pO2_bar ** pressure_exponents[str(species)]
                    * PA_PER_BAR
                )
                for species, _reaction in gas_channels
            }
            return _pressure_with_fe_speciation(
                pressure_model,
                pressures,
                inventory,
                float(log10_pO2_bar),
                temperature_K=temperature_K,
                total_pressure_Pa=total_pressure_Pa,
                engine_flags=engine_flags,
            )

        pressure_model.declares_fixed_activities = True
        pressure_model.engine_flags = engine_flags
        return pressure_model

    if engine == "internal-analytical":
        from simulator.diagnostic_helpers.binary_pot_battery import (
            PO2_COMMANDED,
            Po2Request,
            _InternalAnalyticalInputRefusal,
            _OxygenBalanceRefusal,
            _gas_formula_for_oxygen_balance,
            internal_analytical_residue_vapor_pressure,
            _new_internal_analytical_core,
        )
        from simulator.chemistry.offgas_fo2 import OffgasFO2Unavailable, _oxygen_atom_partial_pressure_bar
        from simulator.vapour_rail.nasa_cea import NasaCeaError

        analytical_core = _new_internal_analytical_core()
        accepted_diagnostics_by_logp: dict[
            float, tuple[dict[str, object], ...]
        ] = {}

        def accept_pressure_diagnostics(log10_pO2_bar: float) -> None:
            if floor_inversion_diagnostics is None:
                return
            for diagnostic in accepted_diagnostics_by_logp.get(log10_pO2_bar, ()):
                floor_inversion_diagnostics.setdefault(
                    str(diagnostic["species"]), diagnostic
                )

        omission_notices: list[Any] = []
        omission_records: dict[str, dict[str, Any]] = {}
        noticed_omissions: set[str] = set()
        def pressure_model(
            inventory: Mapping[str, float], log10_pO2_bar: float
        ) -> Mapping[str, float]:
            try:
                response = internal_analytical_residue_vapor_pressure(
                    core=analytical_core,
                    temperature_C=temperature_K - 273.15,
                    pressure_bar=total_pressure_Pa / PA_PER_BAR,
                    composition_kg=None,
                    composition_mol=inventory,
                    fO2_log=log10_pO2_bar,
                    po2_request=Po2Request(
                        mode=PO2_COMMANDED,
                        po2_bar=10.0**float(log10_pO2_bar),
                    ),
                    include_diagnostic_shadows=False,
                )
            except _InternalAnalyticalInputRefusal as exc:
                raise ResidueInventoryRefusal(exc.code, str(exc)) from exc
            pressures: dict[str, float] = {}
            for channel, pressure in response.vapor_pressures_Pa.items():
                try:
                    formula = _gas_formula_for_oxygen_balance(channel)
                except _OxygenBalanceRefusal as exc:
                    raise ResidueInventoryRefusal(exc.reason_code, str(exc)) from exc
                if formula in pressures:
                    raise ResidueInventoryRefusal("residue_pressure_channels_ambiguous", formula)
                pressures[formula] = float(pressure)
            required = {str(species) for species, _reaction in gas_channels}
            pO2_bar = 10.0**float(log10_pO2_bar)
            if "O2" in required and "O2" not in pressures:
                pressures["O2"] = pO2_bar * PA_PER_BAR
            if "O" in required and "O" not in pressures:
                try:
                    pressures["O"] = _oxygen_atom_partial_pressure_bar(
                        temperature_K, pO2_bar,
                        extract_path=Path(__file__).resolve().parents[2] / "data/literature/extracts/nasa-cea-thermo.yaml",
                    ) * PA_PER_BAR
                except (OffgasFO2Unavailable, NasaCeaError, OSError) as exc:
                    raise ResidueInventoryRefusal("residue_oxygen_pressure_unavailable", str(exc)) from exc
            current_omissions = _translate_recorded_pressure_omissions(
                pressures,
                response,
                required,
                omission_notices,
                omission_records,
                noticed_omissions,
            )
            if floor_inversion_diagnostics is not None:
                vapor_diagnostic = response.diagnostics.get(
                    "vapor_pressure_diagnostic"
                )
                floor_notices = (
                    vapor_diagnostic.get("pO2_floor_inversion_notices_by_species")
                    if isinstance(vapor_diagnostic, Mapping)
                    else None
                )
                evaluation_diagnostics: list[dict[str, object]] = []
                if isinstance(floor_notices, Mapping):
                    for species, detail in floor_notices.items():
                        if not isinstance(detail, Mapping):
                            continue
                        evaluation_diagnostics.append(
                            {
                                "kind": "floor_inversion",
                                "species": str(species),
                                "reason": str(
                                    detail.get("reason") or "floor_inversion"
                                ),
                                "original": detail.get("pO2_bar_used"),
                                "band": detail.get("certified_band"),
                                "detail": dict(detail),
                            }
                        )
                accepted_diagnostics_by_logp[float(log10_pO2_bar)] = tuple(
                    evaluation_diagnostics
                )
            # An exhausted parent is not a missing calibration channel. The
            # engine omits that parent's vapours; their flux is zero. OpenIMCC
            # already returns 0 for an absent species. A vapour missing while
            # its parent is still in the inventory stays a typed refusal.
            for species, reaction in gas_channels:
                name = str(species)
                if name in pressures or not reaction:
                    continue
                parent_name = "" if reaction[0] is None else str(reaction[0])
                if not parent_name:
                    continue
                try:
                    parent_amount = float(inventory.get(parent_name, 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if (
                    parent_name in starting_parent_oxides
                    and parent_amount <= 0.0
                ):
                    pressures[name] = 0.0
            missing = [
                str(species)
                for species, _reaction in gas_channels
                if species not in pressures
            ]
            if missing:
                if floor_inversion_diagnostics is None:
                    detail = ", ".join(missing)
                else:
                    detail = json.dumps(
                        {
                            "missing_channels": missing,
                            "engine_floor_inversion_diagnostics": tuple(
                                floor_inversion_diagnostics.values()
                            ),
                        },
                        sort_keys=True,
                        default=str,
                        separators=(",", ":"),
                    )
                raise ResidueInventoryRefusal(missing_channels_reason, detail)
            evaluated = {
                str(species): float(pressures[str(species)])
                for species, _reaction in gas_channels
            }
            return _pressure_with_fe_speciation(
                pressure_model,
                evaluated,
                inventory,
                float(log10_pO2_bar),
                temperature_K=temperature_K,
                total_pressure_Pa=total_pressure_Pa,
                engine_flags=engine_flags,
                engine_response=response,
                below_threshold_omissions=current_omissions,
            )

        if floor_inversion_diagnostics is not None:
            pressure_model._on_accepted_evaluation = accept_pressure_diagnostics
        pressure_model.declares_redox_active = True
        pressure_model.engine_flags = engine_flags
        pressure_model.below_threshold_omission_notices = omission_notices
        pressure_model.below_threshold_omission_records = omission_records
        return pressure_model

    raise ResidueInventoryRefusal(
        "engine_capability_unsupported",
        f"engine={engine}; capability=free_evaporation_residue_integrator",
    )


def _located_point(located: object | None) -> float | None:
    if located is None:
        return None
    state = getattr(located, "state", None)
    if state is None or not getattr(state, "is_value", False):
        return None
    value = getattr(state, "value", None)
    kind = getattr(value, "kind", None)
    if getattr(kind, "value", kind) != "point":
        return None
    try:
        number = float(value.point)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _initial_oxide_wt_percent(sample: object) -> dict[str, float]:
    initial = getattr(sample, "initial_composition", None)
    state = None if initial is None else getattr(initial, "state", None)
    if state is None or not getattr(state, "is_value", False):
        raise ResidueInventoryRefusal(
            "initial_composition_not_stated", "sample.initial_composition"
        )
    composition = getattr(state, "value", None)
    components = getattr(composition, "components", None)
    if not components:
        raise ResidueInventoryRefusal(
            "initial_composition_not_stated", "sample.initial_composition"
        )
    raw_basis = getattr(composition, "amount_basis", "")
    amount_basis = str(getattr(raw_basis, "value", raw_basis))
    if amount_basis not in {"mole_fraction", "moles", "mole"}:
        raise ResidueInventoryRefusal(
            "initial_composition_basis_not_supported", amount_basis
        )
    try:
        amounts = {str(oxide): float(amount) for oxide, amount in components}
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResidueInventoryRefusal("initial_composition_invalid") from exc
    if any(not math.isfinite(x) or x < 0.0 for x in amounts.values()):
        raise ResidueInventoryRefusal("initial_composition_invalid")
    if not any(amount > 0.0 for amount in amounts.values()):
        raise ResidueInventoryRefusal("initial_composition_invalid")
    return _hashimoto_project_oxide_wt_pct(amounts)


def _mass_loss_sourced_alphas(
    experiment: object,
    source_document: Mapping[str, Any],
    temperature_K: float,
) -> dict[str, dict[str, Any]]:
    """Resolve only the experiment-linked fits in supported extract context."""
    from simulator.battery.extract_context import iter_extract_context_containers

    experiment_id = str(getattr(experiment, "experiment_id", ""))
    local_id = experiment_id.rsplit("::", 1)[-1]
    fits: dict[str, dict[str, Any]] = {}
    for _formula, rows in iter_extract_context_containers(source_document):
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            applies = row.get("applies_to_experiments") or ()
            if local_id not in applies and experiment_id not in applies:
                continue
            values = row.get("values")
            if not isinstance(values, Mapping) or "alpha_form" not in values:
                continue
            form = values["alpha_form"]
            try:
                if form["type"] != "arrhenius" or row["units"] != "dimensionless":
                    raise ValueError("unsupported alpha fit")
                species = str(values["gas_species"]).removesuffix("(g)")
                prefactor = float(form["gamma_o"])
                energy = float(form["E_J_per_mol"])
                gas_constant = float(form["R_J_per_mol_K"])
                low, high = map(float, row["T_range_K"])
                locator = row["locator"]
                if (
                    not species or not row["observation_id"] or not locator
                    or not all(math.isfinite(x) for x in (
                        prefactor, energy, gas_constant, low, high, temperature_K
                    ))
                    or min(prefactor, gas_constant, low, temperature_K) <= 0.0
                    or energy < 0.0 or high < low
                ):
                    raise ValueError("invalid alpha fit")
                alpha, _sigma = _evaluate_extracted_arrhenius_alpha({
                    "gamma_o": prefactor, "E_J_per_mol": energy,
                    "R_J_per_mol_K": gas_constant,
                }, temperature_K)
                if not math.isfinite(alpha) or not 0.0 < alpha <= 1.0:
                    raise ValueError("alpha outside physical interval")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ResidueInventoryRefusal(
                    "evaporation_coefficients_invalid", str(row.get("observation_id"))
                ) from exc
            if species in fits:
                raise ResidueInventoryRefusal(
                    "evaporation_coefficients_ambiguous", species
                )
            fits[species] = {
                "alpha": alpha,
                "source_observation_id": row["observation_id"],
                "locator": dict(locator),
                "T_range_K": [low, high],
                "extrapolated": not low <= temperature_K <= high,
            }
    if not fits:
        raise ResidueInventoryRefusal(
            "evaporation_coefficients_not_sourced",
            "per-species evaporation coefficients are not sourced for this work",
        )
    return fits


def _mass_loss_area_inputs(
    experiment: object, observation: object, source: Mapping[str, Any],
) -> tuple[float, float, float]:
    """Read the two printed endpoints and test the approved shape premise."""
    # Raw table row locators migrate to the observation paragraph; Experiment
    # Locator has no row field (its raw row is retained only as a note).
    row_name = getattr(getattr(observation, "locator", None), "paragraph", None)
    point = getattr(getattr(observation, "value", None), "point", None)
    if point is None or not 0.0 <= float(point) < 1.0:
        raise ResidueInventoryRefusal("observed_mass_loss_not_stated")
    observed_loss = float(point)
    matches = set()
    for block in (source.get("species") or {}).values():
        for row in block.get("observations") or ():
            surface = (row.get("equipment") or {}).get("sample_surface_area") or {}
            samples = surface.get("value")
            if not isinstance(samples, list):
                continue
            for sample in samples:
                if sample.get("sample") != row_name:
                    continue
                try:
                    uncertainty = surface["uncertainty"]
                    diameter = float(sample["diameter_mm"])
                    percent = float(uncertainty[
                        "one_mm_percent" if diameter == 1.0 else "larger_samples_percent"
                    ])
                    initial = float(sample["initial_area_mm2"]) * 1.0e-6
                    final = float(sample["final_area_mm2"]) * 1.0e-6
                    if not all(math.isfinite(x) and x > 0 for x in (
                        initial, final, diameter, percent
                    )) or diameter < 1.0 or not uncertainty.get("locator"):
                        raise ValueError("invalid area evidence")
                    matches.add((initial, final, percent / 100.0))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ResidueInventoryRefusal("sample_area_uncertainty_not_stated") from exc
    if len(matches) != 1:
        raise ResidueInventoryRefusal("sample_area_endpoints_not_stated")
    initial, final, relative_uncertainty = matches.pop()
    predicted_final = _self_similar_area_m2(initial, 1.0 - observed_loss, 1.0)
    if abs(final - predicted_final) > relative_uncertainty * predicted_final:
        raise ResidueInventoryRefusal("area_shape_not_self_similar", json.dumps({
            "initial_area_m2": initial, "final_area_m2": final,
            "self_similar_final_area_m2": predicted_final,
            "relative_area_uncertainty": relative_uncertainty,
        }, sort_keys=True))
    return initial, final, relative_uncertainty


def _mass_loss_thermal_segments(
    experiment: object, temperature_K: float,
) -> tuple[float, float, tuple[tuple[float, float], ...]]:
    """Return preheat rectangle and documented (T midpoint K, duration s)."""
    schedule = getattr(experiment, "thermal_schedule", None)
    ramps = getattr(schedule, "ramps", None)
    method = getattr(getattr(schedule, "method", None), "state", None)
    method_text = str(getattr(method, "value", ""))
    if not ramps or "about an hour" not in method_text:
        raise ResidueInventoryRefusal("preheat_bound_not_stated")
    maximum = _located_point(ramps[0].start_temperature_K)
    if maximum is None:
        raise ResidueInventoryRefusal("preheat_maximum_not_stated")
    holds = {}
    for hold in getattr(schedule, "setpoints_and_holds", None) or ():
        at = _located_point(hold.temperature_K)
        duration = _located_point(hold.hold_duration_s)
        if at is None or duration is None or duration <= 0.0 or at in holds:
            raise ResidueInventoryRefusal("thermal_schedule_invalid")
        holds[at] = duration
    segments = []
    last = maximum
    for ramp in ramps:
        start = _located_point(ramp.start_temperature_K)
        end = _located_point(ramp.end_temperature_K)
        rate = _located_point(ramp.rate_K_s)
        if start != last or end is None or rate is None or rate <= 0.0 or end <= start:
            raise ResidueInventoryRefusal("thermal_schedule_invalid")
        duration = (end - start) / rate
        steps = max(1, math.ceil(duration / 60.0))
        for index in range(steps):
            segments.append((start + (end - start) * (index + 0.5) / steps, duration / steps))
        if end in holds:
            segments.append((end, holds.pop(end)))
        last = end
    if holds or last != temperature_K:
        raise ResidueInventoryRefusal("thermal_schedule_invalid")
    duration = _located_point(schedule.total_duration_s)
    segments.append((temperature_K, duration))
    # The source calls the preheat "about an hour". The rectangle uses that
    # printed approximate duration, never an invented start T or ramp rate.
    return maximum, _SECONDS_PER_HOUR, tuple(segments)


def _mass_loss_bound_gate(
    bound_kg: float, point_loss_kg: float, uncertainty_kg: float | None,
    *, channels: Sequence[str], preheat_bound_kg: float,
) -> dict[str, Any]:
    # L is the sourced-channel point loss on the documented schedule [kg].
    # Each unsourced channel uses its alpha=1 worst-pO2-corner flux, including
    # its stoichiometric oxygen mass. Sourced loss never cancels that envelope.
    # Their summed parent-oxide removal plus the WHOLE preheat rectangle is
    # extra loss B [kg], an envelope, not a jointly realizable trajectory.
    # The gate B/L has units kg/kg. Neither unsourced removal nor preheat
    # enters L. No unsourced channels and no preheat give B=0; B=L fails.
    # Physics r2 drops only the uncertainty clause when the source states none.
    if not all(math.isfinite(x) and x >= 0.0 for x in (bound_kg, point_loss_kg)):
        raise ResidueInventoryRefusal("mass_loss_fraction_invalid")
    fraction = (bound_kg / point_loss_kg if point_loss_kg > 0.0
                else 0.0 if bound_kg == 0.0 else None)
    fraction_clause_passed = fraction is not None and fraction <= 0.01
    uncertainty_clause_passed = None if uncertainty_kg is None else bound_kg < uncertainty_kg
    diagnostics = {
        "fraction_clause_passed": fraction_clause_passed,
        "uncertainty_clause_passed": uncertainty_clause_passed,
        "point_loss_kg": point_loss_kg, "bound_kg": bound_kg, "bound_fraction": fraction,
        "preheat_bound_kg": preheat_bound_kg, "unsourced_channels": list(channels),
        "bulk_uncertainty_kg": uncertainty_kg,
        "uncertainty_clause": (
            "bulk mass-loss uncertainty not stated by source"
            if uncertainty_kg is None else "bound must be below stated bulk uncertainty"
        ),
    }
    if not fraction_clause_passed or uncertainty_clause_passed is False:
        reason = "alpha_not_sourced" if channels else "preheat_bound_not_negligible"
        raise ResidueInventoryRefusal(reason, json.dumps(diagnostics, sort_keys=True))
    return diagnostics


def _mass_loss_corner_bounds(
    inventory: Mapping[str, float], channels: Sequence[ResidueChannel],
    pressure_model: Callable[[Mapping[str, float], float], Mapping[str, float]],
    *, unsourced: Sequence[str], temperature_K: float, area_m2: float, duration_s: float,
    include_sourced: bool = False,
    check_neighbours: bool = False,
) -> tuple[dict[str, float], dict[str, Any]]:
    terms = _channel_terms(channels)
    oxygen_channels = _residue_oxygen_channels(terms, inventory)
    variable_alpha_channels = tuple(
        channel for channel in oxygen_channels
        if channel.active and channel.species in unsourced
    )
    variable_species = {channel.species for channel in variable_alpha_channels}
    pressure_roots: list[float] = []
    bounds: dict[str, float] = {}
    inventory_caps: dict[str, float] = {}
    pressure_exponents = {
        channel.species: channel.pO2_exponent for channel in oxygen_channels
    }

    def pressure_at(logp: float) -> Mapping[str, float]:
        pressures = pressure_model(inventory, logp)
        _inherit_residue_pressure_contract(pressure_at, pressure_model)
        return pressures

    # Binding oxygen balance for an alpha vector is
    # F(pO2; alpha) = sum_j alpha_j J_j(pO2) c_j(pO2) + O2 terms,
    # where c_j = o_vap,j - o_bar,j(pO2), the net O carried per molecule;
    # the sign check guarantees dF/dpO2 > 0.
    # Implicitly, d pO2*/d alpha_j = -c_j J_j / (dF/dpO2), so
    # sign(d pO2*/d alpha_j) = -sign(c_j), evaluated at the root.
    # For target s, rate_s = alpha_s p_s(pO2*), so
    # d ln rate_s / d alpha_j = n_s * d ln pO2*/d alpha_j,
    # with n_s the pressure-law exponent (A).
    # The extreme corner for s has alpha_s in {0, 1}, since its rate is
    # monotone increasing despite its own feedback (Fe(g) with O2 scales as
    # alpha^(2/3) in the single-channel limit). Each other unsourced j is 1
    # when n_s*(-c_j) raises the requested maximum or lowers the minimum, and
    # 0 otherwise. c_j=0 (FeO(g) at q=0) is immaterial. For a negative-
    # exponent target, also evaluate alpha_s=0 with the maximum assignments:
    # this captures the zero-pO2 inventory-cap limit without attaching that
    # cap to the minimum corner. Reuse the vector when it coincides with the
    # minimum. The ordinary endpoints are 2 per target, O(n); cap candidates
    # add at most one per negative-exponent target, also O(n).
    # The all-on solve supplies c at a positive root for the stable signs;
    # bracket-end evaluations below route sign-changing channels to binaries.
    # Apply the existing parent-inventory ceiling to each maximizing root-zero,
    # negative-exponent corner, including the alpha_s=0 candidate. A sign
    # change is physically possible only when o_vap,j lies strictly within the
    # melt valence range (CrO2 with Cr6+ is the live example). Opposite
    # bracket signs add a binary and corner_sign_ambiguous. Composition and q
    # move along an extent, so its endpoint checks the selected extreme's
    # single-flip neighbours; a larger one replaces it and sets
    # corner_not_extremal_on_path. Thirteen
    # negative-exponent variable alphas use at most 39 primary corners, one
    # sign probe and 169 endpoint flips (209 total root solves).
    try:
        all_on = tuple(
            replace(
                channel, alpha=1.0,
                alpha_source="alpha<=1 oxygen-corner sign probe",
            ) if channel.species in variable_species else channel
            for channel in oxygen_channels
        )
        sign_probe = _solve_vacuum_oxygen_balance(
            pressure_at, all_on, temperature_K=temperature_K,
        )
    except ValueError as exc:
        raise _residue_oxygen_balance_failure(exc) from exc
    pressure_roots.append(sign_probe.pO2_bar)

    low_pressures = pressure_at(-30.0)
    high_pressures = pressure_at(0.0)
    chromium_in_inventory = any(
        float(amount) > 0.0 and "Cr" in _formula_terms(oxide)[0]
        for oxide, amount in inventory.items()
    )
    root_pressures = (
        pressure_at(math.log10(sign_probe.pO2_bar) if sign_probe.pO2_bar > 0.0 else -30.0)
        if chromium_in_inventory
        and any(channel.species == "CrO2" for channel in variable_alpha_channels)
        else {}
    )
    term_by_species = {term.source.species: term for term in terms}
    transfer_sign: dict[str, int] = {}
    ambiguous_channels: set[str] = set()
    ambiguity_details: dict[str, dict[str, Any]] = {}
    for channel in variable_alpha_channels:
        if channel.species == "CrO2" and chromium_in_inventory:
            low_cr_oxygen = _chromium_oxygen_per_cr_from_valence(
                getattr(low_pressures, "cr_valence", None)
            )
            high_cr_oxygen = _chromium_oxygen_per_cr_from_valence(
                getattr(high_pressures, "cr_valence", None)
            )
            root_cr_oxygen = _chromium_oxygen_per_cr_from_valence(
                getattr(root_pressures, "cr_valence", None)
            )
            if (
                low_cr_oxygen is None
                or high_cr_oxygen is None
                or root_cr_oxygen is None
            ):
                ambiguous_channels.add(channel.species)
                transfer_sign[channel.species] = 0
                ambiguity_details[channel.species] = {
                    "species": channel.species,
                    "reason": "Cr valence not reported by the producer",
                    "candidate_o_bar_per_Cr": list(
                        _CHROMIUM_OXYGEN_PER_CR_CANDIDATES
                    ),
                    "context": (
                        "Cr6+ is material in oxidising, alkali-rich melts near air; "
                        "at Knudsen/vacuum pO2 Cr2+/Cr3+ dominate "
                        "(o_bar_Cr in [1.0, 1.5], so c_CrO2 > 0). No sourced "
                        "Cr6+ stability bound narrows this union; certification "
                        "item t-1962."
                    ),
                }
                continue
            c_low = channel.oxygen_atoms - _channel_parent_oxygen_demand(
                channel, low_pressures,
            )
            c_high = channel.oxygen_atoms - _channel_parent_oxygen_demand(
                channel, high_pressures,
            )
            c_root = channel.oxygen_atoms - _channel_parent_oxygen_demand(
                channel, root_pressures,
            )
        else:
            c_low = channel.oxygen_atoms - _channel_parent_oxygen_demand(
                channel, low_pressures,
            )
            c_high = channel.oxygen_atoms - _channel_parent_oxygen_demand(
                channel, high_pressures,
            )
            root_demand = sign_probe.parent_oxygen_demand_at_root.get(
                channel.species, channel.parent_oxygen_demand,
            )
            c_root = channel.oxygen_atoms - root_demand
        if c_low * c_high < 0.0:
            ambiguous_channels.add(channel.species)
            transfer_sign[channel.species] = 0
            ambiguity_details[channel.species] = {
                "species": channel.species,
                "reason": "net oxygen transfer changes sign across the pO2 bracket",
            }
        elif c_root != 0.0:
            transfer_sign[channel.species] = 1 if c_root > 0.0 else -1
        elif c_low != 0.0:
            transfer_sign[channel.species] = 1 if c_low > 0.0 else -1
        elif c_high != 0.0:
            transfer_sign[channel.species] = 1 if c_high > 0.0 else -1
        else:
            transfer_sign[channel.species] = 0

    target_channels = list(variable_alpha_channels)
    if include_sourced:
        target_channels.extend(
            channel for channel in oxygen_channels
            if channel.active and channel.species not in variable_species
            and term_by_species[channel.species].source.parent_oxide is not None
        )

    corner_count = 0
    primary_corner_count = 0
    neighbour_evaluations = 0
    best_corner_by_species: dict[str, dict[str, float]] = {}
    path_non_extremal_species: set[str] = set()

    def evaluate_corner(
        alpha_by_species: Mapping[str, float],
    ) -> tuple[float, Mapping[str, float]]:
        nonlocal corner_count
        selected = tuple(
            replace(
                channel,
                alpha=alpha_by_species[channel.species],
                alpha_source="alpha<=1 oxygen-corner bound",
            ) if channel.species in alpha_by_species else channel
            for channel in oxygen_channels
        )
        try:
            balance = _solve_vacuum_oxygen_balance(
                pressure_at, selected, temperature_K=temperature_K,
            )
        except ValueError as exc:
            raise _residue_oxygen_balance_failure(exc) from exc
        root = balance.pO2_bar
        corner_count += 1
        pressure_roots.append(root)
        root_logp = math.log10(root) if root > 0.0 else -30.0
        corner_pressures = dict(pressure_at(root_logp if root > 0.0 else 0.0))
        if root == 0.0:
            for channel in selected:
                if channel.pO2_exponent != 0.0:
                    corner_pressures[channel.species] = 0.0
        corner_terms = tuple(
            replace(
                term,
                source=replace(
                    term.source,
                    alpha=channel.alpha,
                    alpha_source=channel.alpha_source,
                ),
            )
            for term, channel in zip(terms, selected)
        )
        rates = _finite_step_fluxes(
            corner_terms,
            inventory,
            temperature_K=temperature_K,
            area_m2=area_m2,
            pressures_Pa=corner_pressures,
            include_below_threshold=True,
        )
        return root, rates

    for target in target_channels:
        bounds.setdefault(target.species, 0.0)
        variable_target = target.species in variable_species
        ambiguous_others = tuple(
            channel for channel in variable_alpha_channels
            if channel.species != target.species
            and channel.species in ambiguous_channels
        )
        stable_others = tuple(
            channel for channel in variable_alpha_channels
            if channel.species != target.species
            and channel.species not in ambiguous_channels
        )
        exponent = pressure_exponents[target.species]
        corner_specs = [(target.alpha, True)]
        if variable_target:
            corner_specs = [(1.0, True), (0.0, False)]
            if exponent < 0.0:
                corner_specs.append((0.0, True))
        evaluated_vectors: dict[
            tuple[tuple[str, float], ...], tuple[float, Mapping[str, float]]
        ] = {}
        for target_alpha, maximize in corner_specs:
            fixed_alpha = {
                target.species: target_alpha,
                **{
                    channel.species: float(
                        (exponent * -transfer_sign[channel.species] > 0.0)
                        == maximize
                    ) if transfer_sign[channel.species] else 0.0
                    for channel in stable_others
                },
            }
            ambiguous_assignments = 1 << len(ambiguous_others)
            for mask in range(ambiguous_assignments):
                alpha_by_species = dict(fixed_alpha)
                alpha_by_species.update({
                    channel.species: float(bool(mask & (1 << index)))
                    for index, channel in enumerate(ambiguous_others)
                })
                vector_key = tuple(sorted(alpha_by_species.items()))
                if vector_key in evaluated_vectors:
                    root, rates = evaluated_vectors[vector_key]
                else:
                    root, rates = evaluate_corner(alpha_by_species)
                    evaluated_vectors[vector_key] = (root, rates)
                    primary_corner_count += 1
                term = term_by_species[target.species]
                amount = 0.0
                if (
                    root == 0.0
                    and target.pO2_exponent < 0.0
                    and (variable_target or target_alpha > 0.0)
                    and maximize
                ):
                    amount = (
                        float(inventory.get(term.source.parent_oxide or "", 0.0))
                        * _formula_terms(term.source.parent_oxide)[1]
                    )
                    inventory_caps[target.species] = max(
                        inventory_caps.get(target.species, 0.0), amount,
                    )
                elif target_alpha > 0.0 and maximize:
                    amount = (
                        rates.get(target.species, 0.0)
                        * duration_s * term.oxide_per_product_kg
                    )
                if amount > bounds.get(target.species, 0.0):
                    bounds[target.species] = amount
                    best_corner_by_species[target.species] = dict(alpha_by_species)

    if check_neighbours:
        for target in target_channels:
            species = target.species
            selected_corner = best_corner_by_species.get(species)
            if selected_corner is None:
                continue
            term = term_by_species[species]
            for flipped in variable_alpha_channels:
                neighbour = dict(selected_corner)
                neighbour[flipped.species] = 1.0 - neighbour[flipped.species]
                root, rates = evaluate_corner(neighbour)
                neighbour_evaluations += 1
                if (
                    root == 0.0
                    and target.pO2_exponent < 0.0
                    and (species in variable_species or target.alpha > 0.0)
                ):
                    amount = (
                        float(inventory.get(term.source.parent_oxide or "", 0.0))
                        * _formula_terms(term.source.parent_oxide)[1]
                    )
                    inventory_caps[species] = max(
                        inventory_caps.get(species, 0.0), amount,
                    )
                elif neighbour.get(species, target.alpha) > 0.0:
                    amount = (
                        rates.get(species, 0.0)
                        * duration_s * term.oxide_per_product_kg
                    )
                else:
                    amount = 0.0
                if amount > bounds.get(species, 0.0):
                    bounds[species] = amount
                    best_corner_by_species[species] = neighbour
                    path_non_extremal_species.add(species)

    low, high = min(pressure_roots), max(pressure_roots)
    bounds.update(inventory_caps)
    return bounds, {"pO2_low_bar": low, "pO2_high_bar": high,
                    "inventory_capped_channels": sorted(inventory_caps),
                    "corner_count": corner_count,
                    "corner_root_solves": corner_count + 1,
                    "primary_corner_count": primary_corner_count,
                    "corner_neighbour_evaluations": neighbour_evaluations,
                    "corner_sign_ambiguous": sorted(
                        ambiguous_channels
                    ),
                    "corner_sign_ambiguity_details": [
                        ambiguity_details[name]
                        for name in sorted(ambiguity_details)
                    ],
                    "corner_not_extremal_on_path": sorted(
                        path_non_extremal_species
                    )}

def predict_knudsen_residue_composition(
    engine: str, reference, experiment, observations, bench=None,
) -> dict[str, object]:
    """Predict a residue at its own printed loss, never at its measured composition."""
    from dataclasses import replace

    from simulator.battery.enums import AdmissionStatus, MEASURED_EVIDENCE, MethodToken, NoticeKind, Quantity, RegimeClass, ValueKind
    from simulator.battery.identity import quantity_token
    from simulator.battery.records import Notice
    from simulator.battery.source_oxygen import (
        SourceOxygenCondition, SourceOxygenFallback, _printed_source_oxygen_at_point, resolve_source_oxygen,
        source_oxygen_closure_notice,
    )
    from simulator.battery.validity import effusion_regime_unverified

    if engine not in {"openimcc", "internal-analytical"}:
        raise ResidueInventoryRefusal("engine_capability_unsupported", engine)
    if experiment is None or not experiment.method.is_value or experiment.method.value is not MethodToken.KNUDSEN_EFFUSION:
        raise ResidueInventoryRefusal("knudsen_near_saturation_invalid")
    environment = experiment.pressure_environment
    regime = environment.regime
    rows = tuple(observations)
    # Reuse the gas-pressure regime owner's printed Kn and p/d routes.
    # Absence remains the labelled Knudsen-method assumption; contradictory
    # numeric evidence is a refusal, not a reason to assume saturation.
    gas_regime = effusion_regime_unverified(
        experiment, Quantity.P_PARTIAL, observation=reference,
        point_observations=rows,
    )
    contradicted = any(
        not check.passed and (
            check.name == "orifice_pressure_to_diameter"
            or "knudsen_number_orifice" in check.detail
            or "printed_partial_pressure_sum_Pa" in check.detail
        ) for check in gas_regime.checks
    )
    if (regime.regime_class.is_value and regime.regime_class.value is not RegimeClass.MOLECULAR) or contradicted:
        raise ResidueInventoryRefusal("knudsen_near_saturation_invalid", "non-molecular orifice regime")
    wt_pct = _initial_oxide_wt_percent(experiment.sample)
    # A unit reference mass fixes only the mol normalization; no sample mass
    # or apparatus scale is inferred from it. xi is invariant to this scale.
    initial = {oxide: weight / 100.0 / _formula_terms(oxide)[1] for oxide, weight in wt_pct.items()}
    anchors = {}
    for row in rows:
        if (row.experiment_id != reference.experiment_id or row.source_id != reference.source_id
            or quantity_token(row.identity) is not Quantity.MASS_LOSS_FRACTION
            or row.engine is not None or row.admission.status is not AdmissionStatus.ADMITTED
            or row.locator is None
            or not row.evidence.class_.is_value or row.evidence.class_.value not in MEASURED_EVIDENCE
            or row.value.kind is not ValueKind.POINT or row.value.point is None):
            continue
        temperature = row.identity.temperature_K
        if temperature is None or not temperature.is_value:
            continue  # T=0 initial-charge row is not a run temperature.
        t, x = float(temperature.value), float(row.value.point)
        if not math.isfinite(x) or not math.isfinite(t) or not 0.0 < x < 1.0 or t <= 0.0:
            raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid")
        if t in anchors and anchors[t][0] != x:
            raise ResidueInventoryRefusal("measured_mass_loss_fraction_ambiguous")
        anchors[t] = (x, row)
    temperature = reference.identity.temperature_K
    if temperature is None or not temperature.is_value:
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid")
    target_temperature = float(temperature.value)
    if target_temperature not in anchors:
        raise ResidueInventoryRefusal("measured_mass_loss_fraction_missing_or_invalid")
    if any(
        row.experiment_id == reference.experiment_id
        and row.source_id == reference.source_id
        and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.engine is None and row.admission.status is AdmissionStatus.ADMITTED
        and row.identity.temperature_K is not None and row.identity.temperature_K.is_value
        and 0.0 < float(row.identity.temperature_K.value) <= target_temperature
        and float(row.identity.temperature_K.value) not in anchors
        for row in rows
    ):
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid", "a printed temperature has no measured mass-loss coordinate")
    path = [(x, t) for t, (x, row) in sorted(anchors.items()) if t <= target_temperature]
    target = anchors[target_temperature][0]
    if not path or any(b[0] <= a[0] for a, b in zip(path, path[1:])):
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid")
    schedule = experiment.thermal_schedule
    if schedule is None or schedule.method is None or not schedule.method.state.is_value:
        raise ResidueInventoryRefusal("printed_temperature_path_missing_or_invalid", "thermal_schedule.method")

    notices = [Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
        reason="T path is the printed step temperatures held over each mass-loss interval; ramp time between steps not modelled",
        origin="Knudsen residue extent",
    ), Notice(
        kind=NoticeKind.UNVERIFIED_APPARATUS,
        affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
        reason="near-saturation and molecular effusion assumed from the printed Knudsen method; unprinted geometry cannot verify cell saturation",
        origin="Knudsen residue extent",
    )]
    oxygen_sources, source_logs = [], []
    for x, t in path:
        anchor = anchors[t][1]
        # The current residue row may carry a directly printed oxygen condition;
        # its composition value is never consumed by the prediction.
        condition_row = reference if t == target_temperature else anchor
        printed = _printed_source_oxygen_at_point(condition_row, rows)
        pressure = printed[0] if isinstance(printed, tuple) else None
        source = "source_printed"
        used = printed[1] if isinstance(printed, tuple) else ()
        if pressure is None:
            oxygen = printed if isinstance(printed, SourceOxygenFallback) else resolve_source_oxygen(condition_row, rows, bench)
            if isinstance(oxygen, SourceOxygenCondition):
                pressure = oxygen.observation.value.point
                source = "cell_material_gas_ratio" if "cell-metal activity" in oxygen.observation.derivation.relation else "source_printed"
                used = tuple(sorted(oxygen.used_observations))
            else:
                source = "d142_closure"
                flagged = replace(condition_row, provenance={
                    **(condition_row.provenance or {}), "source_oxygen_fallback": oxygen.reason.value,
                })
                notice = source_oxygen_closure_notice(Quantity.RESIDUE_COMPONENT_COMPOSITION, flagged)
                notice = replace(notice, reason=notice.reason + "; SC-360: d-142 is about 3 dex (W) / 2.5 dex (Mo) above measured cell pO2; pO2 sets relative volatility via P_i proportional to pO2^-nu")
                if notice not in notices:
                    notices.append(notice)
        source_logs.append(None if pressure is None else math.log10(float(pressure) / PA_PER_BAR))
        oxygen_sources.append({"mass_loss_fraction": x, "temperature_K": t, "source": source, "used_observations": used})
    provenance = {
        "measured_mass_loss_fraction": target, "temperature_path": path,
        "oxygen_sources": oxygen_sources,
    }
    pressure_callbacks: dict[float, Callable[..., Mapping[str, float]]] = {}
    try:
        try:
            from openimcc import load_gas_datapack
        except ImportError as exc:
            raise ResidueInventoryRefusal("openimcc_not_importable", str(exc)) from exc
        from simulator.diagnostic_helpers.binary_pot_battery import _openimcc_gas_channels_and_omission_notices

        gas_pack = load_gas_datapack()
        gas_channels, omissions = _openimcc_gas_channels_and_omission_notices(tuple(initial), gas_pack)
        if omissions:
            raise ResidueInventoryRefusal("residue_gas_channel_missing", str(omissions))
        channels = tuple(ResidueChannel(str(s), str(s), parent, 1.0,
                        "near-saturated molecular effusion; common orifice factor cancels")
                         for s, (parent, _, _) in gas_channels)
        flags, metadata = set(), {}

        def pressure_model(inventory, logp, temperature_K):
            if temperature_K not in pressure_callbacks:
                pressure_callbacks[temperature_K] = _make_residue_pressure_model(
                    engine, gas_channels, gas_pack, temperature_K=temperature_K,
                    total_pressure_Pa=_located_point(environment.total_pressure_Pa) or 0.0,
                    engine_metadata=metadata, engine_flags=flags,
                    starting_inventory=initial,
                    missing_channels_reason="residue_pressure_channels_missing",
                    caller="Knudsen residue prediction",
                )
            callback = pressure_callbacks[temperature_K]
            pressures = callback(inventory, logp)
            _inherit_residue_pressure_contract(pressure_model, callback)
            return pressures

        # OpenIMCC freezes parent activities and applies the stoichiometric
        # power. Internal-analytical pressures are the engine's own law.
        kwargs = dict(temperature_path=path, measured_mass_loss_fraction=target,
                      near_saturated=True, source_fO2_log_by_interval=source_logs)
        primary = integrate_knudsen_residue_inventory(initial, channels, pressure_model, **kwargs)
        composition = _hashimoto_project_oxide_wt_pct(primary.residue_mol)
        diagnostic = {}
        try:
            linear = integrate_knudsen_residue_inventory(initial, channels, pressure_model, **kwargs, linear_temperature=True)
            linear_composition = _hashimoto_project_oxide_wt_pct(linear.residue_mol)
            diagnostic = {"composition_wt_pct": linear_composition,
                          "difference_from_step_hold_wt_pct": {k: linear_composition[k] - v for k, v in composition.items()}}
        except (ResidueInventoryRefusal, ResidueEngineNonconvergence) as exc:
            diagnostic = {"refusal": getattr(exc, "reason", str(exc))}
        _append_omission_notices(notices, pressure_callbacks)
        _append_redox_pressure_notices(
            notices, primary.pressure_law_diagnostics, pressure_callbacks,
        )
        formula_oxygen_atoms_mol = math.fsum(
            amount * _formula_terms(oxide)[0].get("O", 0.0)
            for oxide, amount in primary.residue_mol.items()
        )
        return {
            "composition_wt_pct": composition, "linear_temperature_diagnostic": diagnostic,
            "measured_mass_loss_fraction": target, "temperature_path": path,
            "oxygen_sources": oxygen_sources, "notices": tuple(notices),
            "initial_inventory_mol": dict(initial),
            "final_inventory_mol": dict(primary.residue_mol),
            "retained_oxygen_atoms_mol": primary.retained_oxygen_atoms_mol,
            "final_bulk_mass_adjustment_kg": (
                primary.retained_oxygen_atoms_mol - formula_oxygen_atoms_mol
            ) * _formula_terms("O")[1],
            "atom_closure_mol": dict(primary.atom_closure_mol),
            "escaped_mass_fraction": math.fsum(n * _formula_terms(s)[1] for s, n in primary.evaporated_mol.items()) / math.fsum(n * _formula_terms(s)[1] for s, n in initial.items()),
            "engine_flags": tuple(sorted(flags)), "engine_metadata": metadata,
        }
    except (ResidueInventoryRefusal, ResidueEngineNonconvergence) as exc:
        _append_omission_notices(notices, pressure_callbacks)
        exc.provenance = provenance
        exc.notices = tuple(notices)
        raise


def predict_mass_loss_fraction(
    engine: str,
    experiment: object,
    *,
    temperature_K: float | None,
    source_document: Mapping[str, Any] | None = None,
    observation: object | None = None,
) -> ResidueMassLossPrediction:
    """Predict one bulk mass-loss point through the existing residue integrator."""
    if engine not in {"openimcc", "internal-analytical"}:
        raise ResidueInventoryRefusal(
            "engine_capability_unsupported",
            f"engine={engine}; capability=free_evaporation_residue_integrator",
        )

    sample = getattr(experiment, "sample", None)
    wt_pct = _initial_oxide_wt_percent(sample)
    schedule = getattr(experiment, "thermal_schedule", None)
    duration_s = _located_point(
        None if schedule is None else getattr(schedule, "total_duration_s", None)
    )
    if duration_s is None or duration_s <= 0.0:
        raise ResidueInventoryRefusal(
            "duration_not_stated", "thermal_schedule.total_duration_s"
        )
    if temperature_K is None or not math.isfinite(float(temperature_K)):
        raise ResidueInventoryRefusal("temperature_not_stated", "identity.temperature_K")
    sample_mass_kg = _located_point(getattr(sample, "mass_kg", None))
    if sample_mass_kg is None or sample_mass_kg <= 0.0:
        raise ResidueInventoryRefusal("sample_mass_not_stated", "sample.mass_kg point")
    apparatus = getattr(experiment, "apparatus", None)
    geometry = None if apparatus is None else getattr(apparatus, "geometry", None)
    area_m2 = _located_point(
        None if geometry is None else getattr(geometry, "exposed_area_m2", None)
    )
    if area_m2 is None or area_m2 <= 0.0:
        raise ResidueInventoryRefusal(
            "sample_geometry_not_stated",
            "apparatus.geometry.exposed_area_m2 point",
        )

    method_state = getattr(experiment, "method", None)
    method = getattr(method_state, "value", None) if getattr(
        method_state, "is_value", False
    ) else None
    if getattr(method, "value", method) != "langmuir_free_evaporation":
        raise ResidueInventoryRefusal(
            "residue_method_unsupported",
            "capability=free_evaporation_residue_integrator; "
            f"experiment.method={getattr(method, 'value', method)!r}",
        )

    pressure_environment = getattr(experiment, "pressure_environment", None)
    total_pressure_Pa = _located_point(
        None
        if pressure_environment is None
        else getattr(pressure_environment, "total_pressure_Pa", None)
    )
    pressure_value = getattr(getattr(
        getattr(pressure_environment, "total_pressure_Pa", None), "state", None
    ), "value", None)
    pressure_is_bound = (
        getattr(getattr(pressure_value, "kind", None), "value", None) == "bound"
        and getattr(pressure_value, "bound_operator", None) in {"<", "<="}
    )
    if pressure_is_bound:
        total_pressure_Pa = float(pressure_value.bound_value)
    if engine == "internal-analytical" and total_pressure_Pa is None:
        raise ResidueInventoryRefusal("total_pressure_not_stated")

    total_wt_pct = math.fsum(wt_pct.values())
    if (
        not math.isfinite(total_wt_pct)
        or total_wt_pct <= 0.0
        or any(not math.isfinite(value) or value < 0.0 for value in wt_pct.values())
    ):
        raise ResidueInventoryRefusal("initial_composition_invalid")
    # Printed oxide wt% are normalized only for source rounding: each m_i is
    # m0*w_i/Σw. Dividing kg by kg mol^-1 yields mol on the sample-mass basis.
    initial_inventory_mol = {
        oxide: sample_mass_kg * (amount / total_wt_pct)
        / _formula_terms(oxide)[1]
        for oxide, amount in wt_pct.items()
    }
    try:
        from openimcc import load_gas_datapack
    except ImportError as exc:
        raise ResidueInventoryRefusal("openimcc_not_importable", str(exc)) from exc
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _openimcc_gas_channels_and_omission_notices,
    )

    gas_pack = load_gas_datapack()
    gas_channels, omissions = _openimcc_gas_channels_and_omission_notices(
        tuple(initial_inventory_mol), gas_pack
    )
    if omissions:
        raise ResidueInventoryRefusal("residue_gas_channel_missing", str(omissions))
    if not gas_channels:
        raise ResidueInventoryRefusal("residue_gas_channels_missing")
    fits = _mass_loss_sourced_alphas(
        experiment, source_document or {}, float(temperature_K)
    )
    if total_pressure_Pa is None or not math.isfinite(total_pressure_Pa) or total_pressure_Pa < 0:
        raise ResidueInventoryRefusal("total_pressure_not_stated")
    area_m2, final_area_m2, area_uncertainty = _mass_loss_area_inputs(
        experiment, observation, source_document or {}
    )
    preheat_maximum_K, preheat_duration_s, segments = _mass_loss_thermal_segments(
        experiment, float(temperature_K)
    )
    flags: set[str] = set()
    metadata: dict[str, str] = {}
    pressure_law_diagnostics_by_arm: dict[
        str, dict[str, PressureLawChannelDiagnostic]
    ] = {}
    flags.add(
        "mass_loss_oxygen_escape_assumption: alpha_O=1.0; alpha_O2=1.0; "
        "declared free-desorption assumption, not independently sourced coefficients; "
        "sensitivity: oxygen escape changes the shared alpha-weighted pO2 root "
        "and therefore the sourced-channel loss"
    )
    if pressure_is_bound:
        flags.add(f"mass_loss_pressure_upper_bound: evaluated at {total_pressure_Pa:.17g} Pa; "
                  "channel bulk partial ceiling; zero-pressure arms are bounds only")
    flags.add(
        f"mass_loss_preheat_rectangle: maximum_K={preheat_maximum_K}; "
        f"duration_s={preheat_duration_s}; approximate printed duration; "
        "excluded from point loss; rectangle enters B only"
    )
    flags.add(
        f"mass_loss_area_self_similarity: initial_m2={area_m2}; final_m2={final_area_m2}; "
        f"relative_area_uncertainty={area_uncertainty}"
    )
    unsourced = tuple(str(species) for species, (parent, *_rest) in gas_channels
                      if parent and str(species) not in fits)

    bounds_by_channel = {species: 0.0 for species in unsourced}
    preheat_bounds_by_channel: defaultdict[str, float] = defaultdict(float)
    corner_records = []
    corner_evaluations = 0
    corner_root_solves = 0
    corner_neighbour_evaluations = 0
    corner_evaluations_by_arm: defaultdict[str, int] = defaultdict(int)
    corner_sign_ambiguities: set[str] = set()
    corner_sign_ambiguity_details: dict[str, dict[str, Any]] = {}
    corner_path_non_extremal: set[str] = set()
    inventory_capped_channels = set()

    def record_corner_sign_ambiguities(corners: Mapping[str, Any]) -> None:
        for detail in corners["corner_sign_ambiguity_details"]:
            species = str(detail["species"])
            corner_sign_ambiguities.add(species)
            corner_sign_ambiguity_details[species] = dict(detail)
            flags.add("corner_sign_ambiguous")
            flags.add(
                "corner_sign_ambiguous:" + json.dumps(
                    detail, sort_keys=True, separators=(",", ":"),
                )
            )
    # The shared callback names residue composition for Knudsen and Hashimoto.
    # This path keeps those notices and retargets only the mass-loss quantity.
    omission_notices: list[Any] = []
    omission_records: dict[str, list[dict[str, Any]]] = {}

    def retain_omissions(pressure_model: object) -> None:
        from simulator.battery.enums import Quantity

        for notice in getattr(pressure_model, "below_threshold_omission_notices", ()) or ():
            adapted = replace(
                notice, affected_quantities=(Quantity.MASS_LOSS_FRACTION,)
            )
            if adapted not in omission_notices:
                omission_notices.append(adapted)
        for formula, record in (
            getattr(pressure_model, "below_threshold_omission_records", {}) or {}
        ).items():
            bucket = omission_records.setdefault(str(formula), [])
            copied = dict(record)
            if copied not in bucket:
                bucket.append(copied)

    def omission_payload() -> str | None:
        if not omission_notices and not omission_records:
            return None
        return json.dumps(
            {
                "notices": [
                    {
                        "kind": notice.kind.value,
                        "reason": notice.reason,
                        "origin": notice.origin,
                    }
                    for notice in omission_notices
                ],
                "records": omission_records,
            },
            sort_keys=True,
        )

    def attach_omissions(exc: BaseException) -> None:
        payload = omission_payload()
        if payload is None:
            return
        setattr(exc, "notices", tuple(omission_notices))
        setattr(exc, "below_threshold_omissions", payload)

    def run_segment(
        inventory: Mapping[str, float], at_K: float, seconds: float, *,
        rectangle: bool = False, unsourced_unity: bool = False,
    ) -> tuple[Mapping[str, float], float]:
        at_fits = _mass_loss_sourced_alphas(experiment, source_document or {}, at_K)
        for species, fit in at_fits.items():
            if fit["extrapolated"]:
                flags.add(f"alpha_extrapolated:{species}:source_domain_K={fit['T_range_K']}")
        channels = []
        for species, (parent, *_rest) in gas_channels:
            species = str(species)
            if not parent:
                alpha, provenance = 1.0, "declared unity oxygen escape; stoichiometric coproduct"
            elif species in at_fits:
                alpha = at_fits[species]["alpha"]
                provenance = at_fits[species]["source_observation_id"]
            else:
                alpha = 1.0 if unsourced_unity else 0.0
                provenance = ("alpha<=1 joint/preheat arm; not sourced coefficient" if unsourced_unity
                              else "unsourced channel excluded from point loss")
            channels.append(ResidueChannel(species, species, parent or None, alpha, provenance))
        pressure_model = _make_residue_pressure_model(
            engine, gas_channels, gas_pack, temperature_K=at_K,
            total_pressure_Pa=total_pressure_Pa, engine_metadata=metadata,
            engine_flags=flags, starting_inventory=initial_inventory_mol,
            missing_channels_reason="residue_pressure_channels_missing",
            caller="Mass-loss residue prediction",
        )
        arm = "PREHEAT" if rectangle else "JOINT" if unsourced_unity else "POINT"
        last_step_bounds: dict[str, float] = {}
        last_step_duration = 0.0
        def measure(incoming, active_channels, area, duration):
            nonlocal corner_evaluations, corner_root_solves
            nonlocal corner_neighbour_evaluations, last_step_duration
            bounds, corners = _mass_loss_corner_bounds(
                incoming, active_channels, pressure_model, unsourced=unsourced,
                temperature_K=at_K, area_m2=area, duration_s=duration,
                include_sourced=rectangle,
            )
            record_corner_sign_ambiguities(corners)
            corner_evaluations += corners["corner_count"]
            corner_root_solves += corners["corner_root_solves"]
            corner_evaluations_by_arm[arm] += corners["corner_count"]
            last_step_bounds.clear()
            last_step_bounds.update(bounds)
            last_step_duration = duration
            inventory_capped_channels.update(corners["inventory_capped_channels"])
            target = preheat_bounds_by_channel if rectangle else bounds_by_channel
            for species, amount in bounds.items():
                target[species] += amount
            if not rectangle:
                corner_records.append(corners)

        try:
            result = _hashimoto_integrate_geometry(
                inventory, channels, pressure_model, temperature_K=at_K,
                duration_s=seconds, steps=min(_HASHIMOTO_N_CAP, max(1, math.ceil(seconds / 60.0))),
                geometry_policy_id="sphere_constant" if rectangle else "sphere_shrinking",
                initial_area_m2=area_m2, initial_sample_mass_kg=sample_mass_kg,
                background_pressure_Pa=0.0 if unsourced_unity else total_pressure_Pa,
                **({"on_step": measure} if rectangle or (not unsourced_unity and unsourced) else {}),
            )
            if result.refusal is not None:
                raise result.refusal
            if rectangle or (not unsourced_unity and unsourced):
                final_inventory = result.inventory_mol
                final_mass_kg = math.fsum(
                    amount * _formula_terms(oxide)[1]
                    for oxide, amount in final_inventory.items()
                )
                endpoint_area = (
                    area_m2 if rectangle else _self_similar_area_m2(
                        area_m2, final_mass_kg, sample_mass_kg,
                    )
                )
                endpoint_channels = tuple(
                    channel for channel in channels
                    if channel.parent_oxide is None
                    or float(final_inventory.get(channel.parent_oxide, 0.0)) > 0.0
                )
                endpoint_bounds, endpoint_corners = _mass_loss_corner_bounds(
                    final_inventory, endpoint_channels, pressure_model,
                    unsourced=unsourced, temperature_K=at_K,
                    area_m2=endpoint_area,
                    duration_s=last_step_duration or seconds,
                    include_sourced=rectangle,
                    check_neighbours=True,
                )
                corner_evaluations += endpoint_corners["corner_count"]
                corner_root_solves += endpoint_corners["corner_root_solves"]
                corner_neighbour_evaluations += endpoint_corners[
                    "corner_neighbour_evaluations"
                ]
                corner_evaluations_by_arm[arm] += endpoint_corners["corner_count"]
                inventory_capped_channels.update(
                    endpoint_corners["inventory_capped_channels"]
                )
                record_corner_sign_ambiguities(endpoint_corners)
                if endpoint_corners["corner_not_extremal_on_path"]:
                    flags.add("corner_not_extremal_on_path")
                    corner_path_non_extremal.update(
                        endpoint_corners["corner_not_extremal_on_path"]
                    )
                destination = (
                    preheat_bounds_by_channel if rectangle else bounds_by_channel
                )
                for species, amount in endpoint_bounds.items():
                    destination[species] += max(
                        0.0, amount - last_step_bounds.get(species, 0.0),
                    )
            arm_diagnostics = pressure_law_diagnostics_by_arm.setdefault(arm, {})
            for diagnostic in result.pressure_law_diagnostics:
                arm_diagnostics[diagnostic.species] = replace(
                    diagnostic, arm=arm,
                )
            terms = _channel_terms(channels)
            parent_loss = {
                term.source.species: result.evaporated_mol.get(term.source.species, 0.0)
                * term.parent_moles_per_product * _formula_terms(term.source.parent_oxide)[1]
                for term in terms if term.source.parent_oxide is not None
            }
            return result.inventory_mol, math.fsum(parent_loss.values())
        finally:
            retain_omissions(pressure_model)

    # The unknown preheat is an upper rectangle only: do not debit it as a
    # measured trajectory. All its removal, not just unsourced channels, joins B.
    try:
        _unused_inventory, _unused_loss = run_segment(
            initial_inventory_mol, preheat_maximum_K, preheat_duration_s,
            rectangle=True, unsourced_unity=True,
        )
        inventory = initial_inventory_mol
        joint_inventory = initial_inventory_mol
        documented_loss_kg = 0.0
        joint_loss_kg = 0.0
        joint_refusal = None
        for at_K, seconds in segments:
            inventory, loss = run_segment(
                inventory, at_K, seconds,
            )
            if joint_refusal is None:
                try:
                    joint_inventory, joint_loss = run_segment(
                        joint_inventory, at_K, seconds, unsourced_unity=True,
                    )
                    joint_loss_kg += joint_loss
                except (ResidueInventoryRefusal, ResidueEngineNonconvergence) as exc:
                    joint_refusal = {"reason": exc.reason, "detail": exc.detail}
            documented_loss_kg += loss
        channel_parents = {str(species): parent for species, (parent, *_rest) in gas_channels if parent}
        inventory_bounds_by_parent = {}
        # A divergent channel can remove at most its initial parent mass, ONCE:
        # splitting time or sharing that parent between gases creates no matter.
        # Cap the accumulated parent sum, including preheat, rather than shadow-
        # debiting the point state. This leaves the original trajectories intact.
        # Proportional attribution preserves B=sum(channels)+preheat; it is not a
        # physical debit. Two 60 s full-parent claims become m0, never 2*m0.
        for parent in sorted({channel_parents[species] for species in inventory_capped_channels}):
            species = [name for name, oxide in channel_parents.items() if oxide == parent]
            total = math.fsum(mapping.get(name, 0.0)
                              for mapping in (bounds_by_channel, preheat_bounds_by_channel)
                              for name in species)
            ceiling = initial_inventory_mol[parent] * _formula_terms(parent)[1]
            scale = min(1.0, ceiling / total) if total > 0.0 else 1.0
            inventory_bounds_by_parent[parent] = {
                "uncapped_bound_kg": total, "ceiling_kg": ceiling,
                "applied_bound_kg": min(total, ceiling),
            }
            for mapping in (bounds_by_channel, preheat_bounds_by_channel):
                for name in species:
                    if name in mapping:
                        mapping[name] *= scale
        unsourced_bound_kg = math.fsum(bounds_by_channel.values())
        preheat_bound_kg = math.fsum(preheat_bounds_by_channel.values())
        point_loss_kg = documented_loss_kg
        uncertainty = getattr(observation, "uncertainty", None)
        uncertainty_kg = None
        if getattr(getattr(uncertainty, "kind", None), "value", "none") != "none":
            value = getattr(uncertainty, "value", None)
            basis = getattr(uncertainty, "basis", None)
            if value is None or isinstance(value, tuple):
                raise ResidueInventoryRefusal("bulk_mass_loss_uncertainty_not_numeric")
            if basis in {"absolute", "absolute_fraction_of_initial_mass"}:
                uncertainty_kg = float(value) * sample_mass_kg
            elif basis in {"relative", "fraction"}:
                uncertainty_kg = float(value) * float(observation.value.point) * sample_mass_kg
            else:
                raise ResidueInventoryRefusal("bulk_mass_loss_uncertainty_basis_not_stated")
            if not math.isfinite(uncertainty_kg) or uncertainty_kg <= 0.0:
                raise ResidueInventoryRefusal("bulk_mass_loss_uncertainty_invalid")
        evidence = {
            "unsourced_bound_kg_by_channel": bounds_by_channel,
            "joint_arm_loss_kg_non_gating": joint_loss_kg if joint_refusal is None else None,
            "joint_arm_refusal_non_gating": joint_refusal,
            "joint_arm_background_pressure_Pa": 0.0,
            "corner_steps": len(corner_records),
            "corner_evaluations": corner_evaluations,
            "corner_root_solves": corner_root_solves,
            "corner_neighbour_evaluations": corner_neighbour_evaluations,
            "corner_evaluations_by_arm": dict(corner_evaluations_by_arm),
            "corner_sign_ambiguous": sorted(corner_sign_ambiguities),
            "corner_sign_ambiguity_details": [
                corner_sign_ambiguity_details[species]
                for species in sorted(corner_sign_ambiguity_details)
            ],
            "corner_not_extremal_on_path": sorted(corner_path_non_extremal),
            "corner_ordering_verified": all(c["pO2_low_bar"] <= c["pO2_high_bar"] for c in corner_records),
            "pO2_corner_ranges_bar": {
                name: [min(c[name] for c in corner_records), max(c[name] for c in corner_records)]
                for name in ("pO2_low_bar", "pO2_high_bar")
            } if corner_records else {},
            "inventory_capped_channels": sorted(inventory_capped_channels),
            "inventory_bound_kg_by_parent": inventory_bounds_by_parent,
        }
        try:
            diagnostics = _mass_loss_bound_gate(
                unsourced_bound_kg + preheat_bound_kg, point_loss_kg, uncertainty_kg,
                channels=tuple(species for species, bound in bounds_by_channel.items() if bound > 0.0),
                preheat_bound_kg=preheat_bound_kg,
            )
        except ResidueInventoryRefusal as exc:
            if exc.reason not in {"alpha_not_sourced", "preheat_bound_not_negligible"}:
                raise
            diagnostics = json.loads(exc.detail)
            diagnostics.update(evidence)
            raise ResidueInventoryRefusal(exc.reason, json.dumps(diagnostics, sort_keys=True)) from exc
        diagnostics.update(evidence)
        flags.add("mass_loss_bound:" + json.dumps(diagnostics, sort_keys=True))
        metadata["mass_loss_bound"] = json.dumps(diagnostics, sort_keys=True)
        metadata["sourced_alphas"] = json.dumps(fits, sort_keys=True)
        metadata["alpha_sources"] = json.dumps([fit["source_observation_id"] for fit in fits.values()])
        payload = omission_payload()
        if payload is not None:
            metadata["below_threshold_omissions"] = payload
        return ResidueMassLossPrediction(
            mass_loss_fraction=point_loss_kg / sample_mass_kg,
            engine_flags=tuple(sorted(flags)), engine_metadata=metadata,
            pressure_law_diagnostics=tuple(
                diagnostic
                for arm in ("POINT", "JOINT", "PREHEAT")
                for diagnostic in pressure_law_diagnostics_by_arm.get(arm, {}).values()
            ),
        )
    except (ResidueInventoryRefusal, ResidueEngineNonconvergence) as exc:
        attach_omissions(exc)
        raise


def _hashimoto_table_row(table: Any, key: str, temperature_K: float) -> Mapping[str, Any]:
    try:
        selected = table.loc[key]
    except KeyError as exc:
        raise ResidueInventoryRefusal(
            "hashimoto_openimcc_row_missing", f"OpenIMCC row {key!r} is unavailable"
        ) from exc
    rows = list(selected.iterrows())[0:] if hasattr(selected, "iterrows") else [(key, selected)]
    row_records = [row for _index, row in rows]

    def distance(row: Mapping[str, Any]) -> float:
        try:
            low = float(row["T_min"])
            high = float(row["T_max"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return 0.0
        if low <= temperature_K <= high:
            return 0.0
        return min(abs(temperature_K - low), abs(temperature_K - high))

    return min(row_records, key=distance)


def _hashimoto_liquid_row_provenance(
    gas_pack: Any,
    gas_channels: Sequence[tuple[str, tuple[str, float, float]]],
    temperature_K: float,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for species, (parent, _parent_moles, _pO2_exponent) in gas_channels:
        gas_row = _hashimoto_table_row(
            gas_pack.gas_df, f"{species}(g)", temperature_K
        )
        gas_low = float(gas_row["T_min"])
        gas_high = float(gas_row["T_max"])
        liquid_record: dict[str, Any] | None = None
        if parent:
            liquid_name = f"{parent}(l)"
            liquid_row = _hashimoto_table_row(
                gas_pack.oxide_df, liquid_name, temperature_K
            )
            liquid_low = float(liquid_row["T_min"])
            liquid_high = float(liquid_row["T_max"])
            liquid_record = {
                "row": liquid_name,
                "source": str(liquid_row["Ref"]),
                "T_fit_K": [liquid_low, liquid_high],
                "extrapolated_below_floor": temperature_K < liquid_low,
            }
        rows.append(
            {
                "channel": species,
                "parent": parent or None,
                "openimcc_gas_row": f"{species}(g)",
                "gas_source": str(gas_row["Ref"]),
                "gas_T_interval": int(gas_row.get("T_interval", 1)),
                "gas_T_fit_K": [gas_low, gas_high],
                "gas_extrapolated": not gas_low <= temperature_K <= gas_high,
                "liquid_row": liquid_record,
            }
        )
    return tuple(rows)


def _hashimoto_geometry_areas(
    sample_mass_kg: float, preform_dimensions_mm: Mapping[str, float]
) -> dict[str, float]:
    density = _HASHIMOTO_DENSITY_KG_M3
    radius_m = (3.0 * sample_mass_kg / (4.0 * math.pi * density)) ** (1.0 / 3.0)
    diameter_mm = float(preform_dimensions_mm["diameter"])
    sphere_area_m2 = 4.0 * math.pi * radius_m**2
    disk_area_m2 = math.pi * (diameter_mm / 2_000.0) ** 2
    return {
        "sphere_constant": sphere_area_m2,
        "sphere_shrinking": sphere_area_m2,
        "disk_4mm_constant": disk_area_m2,
    }


def _hashimoto_project_oxide_wt_pct(inventory_mol: Mapping[str, float]) -> dict[str, float]:
    oxide_mass_kg = {
        oxide: float(amount) * _formula_terms(oxide)[1]
        for oxide, amount in inventory_mol.items()
    }
    total_mass_kg = math.fsum(oxide_mass_kg.values())
    if not math.isfinite(total_mass_kg) or total_mass_kg <= 0.0:
        raise ResidueInventoryRefusal("hashimoto_residue_mass_invalid")
    # This is the run-end liquid's bulk oxide inventory; the Hashimoto source
    # reports the same whole-spherule inventory after quenching, so score.py
    # carries the source-resolved Phase.L identity onto this prediction.
    return {
        oxide: mass_kg / total_mass_kg * 100.0
        for oxide, mass_kg in oxide_mass_kg.items()
    }


def _self_similar_area_m2(
    initial_area_m2: float, remaining_mass_kg: float, initial_mass_kg: float
) -> float:
    # Constant density and self-similar shape: A=k V^(2/3), V=m/rho,
    # hence A=A0*(m/m0)^(2/3). m/m0 is dimensionless and A is m2.
    # Equal masses give equal areas; a sphere with V=4*pi*r^3/3 has
    # k=(36*pi)^(1/3) and therefore A=4*pi*r^2. For R-11, Af/A0=0.876
    # implies mf/m0=0.876^1.5=0.820; test the printed endpoints against
    # the source area uncertainty before applying this shape premise.
    return initial_area_m2 * (
        max(0.0, remaining_mass_kg / initial_mass_kg) ** (2.0 / 3.0)
    )


def _hashimoto_integrate_geometry(
    initial_inventory_mol: Mapping[str, float],
    channels: Sequence[ResidueChannel],
    pressure_model: Callable[[Mapping[str, float], float], Mapping[str, float]],
    *,
    temperature_K: float,
    duration_s: float,
    steps: int,
    geometry_policy_id: str,
    initial_area_m2: float,
    initial_sample_mass_kg: float | None = None,
    background_pressure_Pa: float = 0.0,
    on_step: Callable[[Mapping[str, float], Sequence[ResidueChannel], float, float], None] | None = None,
) -> _HashimotoGeometryIntegration:
    inventory = {str(oxide): float(amount) for oxide, amount in initial_inventory_mol.items()}
    sample_total_mol = math.fsum(inventory.values())
    exhaustion_floor_mol = _component_exhaustion_floor_mol(sample_total_mol)
    initial_mass_kg = math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in inventory.items()
    )
    if initial_sample_mass_kg is not None:
        # Thermal segments retain the original printed A0/m0 reference.
        initial_mass_kg = initial_sample_mass_kg
    dt_s = duration_s / steps
    pO2_steps: list[float] = []
    evaporated_mol: defaultdict[str, float] = defaultdict(float)
    exhausted_mol: dict[str, float] = {}
    exhaustion_notices: list[dict[str, float | int | str]] = []
    refusal: ResidueEngineNonconvergence | None = None
    elapsed_s = 0.0
    pO2_balance_floor_steps = 0
    pO2_balance_floor_first_step: int | None = None
    pO2_balance_floor_time_s = 0.0
    pO2_balance_floor_pO2_bar: float | None = None
    pressure_law_diagnostics: dict[str, PressureLawChannelDiagnostic] = {}

    def remove_exhausted(*, step_number: int, time_s: float, substep: int) -> None:
        for oxide, amount in tuple(inventory.items()):
            if (
                float(initial_inventory_mol.get(oxide, 0.0)) > 0.0
                and oxide not in exhausted_mol
                and amount < exhaustion_floor_mol
            ):
                exhausted_mol[oxide] = amount
                exhaustion_notices.append(
                    {
                        "reason": "component_exhausted",
                        "component": oxide,
                        "step": step_number,
                        "substep": substep,
                        "time_reached_s": time_s,
                        "remaining_moles": amount,
                        "exhaustion_floor_mol": exhaustion_floor_mol,
                    }
                )
                inventory[oxide] = 0.0

    def area_for_current_inventory() -> float:
        if geometry_policy_id != "sphere_shrinking":
            return initial_area_m2
        remaining_mass_kg = math.fsum(
            amount * _formula_terms(oxide)[1]
            for oxide, amount in inventory.items()
        )
        return _self_similar_area_m2(
            initial_area_m2, remaining_mass_kg, initial_mass_kg
        )

    def advance(duration: float, *, step_number: int, substep: int) -> None:
        nonlocal inventory
        nonlocal pO2_balance_floor_steps
        nonlocal pO2_balance_floor_first_step
        nonlocal pO2_balance_floor_time_s
        nonlocal pO2_balance_floor_pO2_bar
        remove_exhausted(
            step_number=step_number,
            time_s=elapsed_s,
            substep=substep,
        )
        active_channels = tuple(
            channel
            for channel in channels
            if channel.parent_oxide is None
            or channel.parent_oxide not in exhausted_mol
        )
        area_m2 = area_for_current_inventory()
        step = integrate_residue_inventory(
            inventory,
            active_channels,
            pressure_model,
            temperature_K=temperature_K,
            duration_s=duration,
            area_evolution_m2=(area_m2,),
            **({"background_pressure_Pa": background_pressure_Pa}
               if background_pressure_Pa else {}),
        )
        if on_step is not None:
            on_step(inventory, active_channels, area_m2, duration)
        inventory = dict(step.residue_mol)
        for species, amount in step.evaporated_mol.items():
            evaporated_mol[str(species)] += float(amount)
        pO2_steps.extend(
            float(value) for value in step.pO2_bar_by_step if value is not None
        )
        for diagnostic in step.pressure_law_diagnostics:
            pressure_law_diagnostics[diagnostic.species] = diagnostic
        pO2_balance_floor_steps += step.pO2_balance_floor_steps
        pO2_balance_floor_time_s += step.pO2_balance_floor_time_s
        if (
            pO2_balance_floor_first_step is None
            and step.pO2_balance_floor_first_step is not None
        ):
            pO2_balance_floor_first_step = step_number
            pO2_balance_floor_pO2_bar = step.pO2_balance_floor_pO2_bar

    for step_number in range(1, steps + 1):
        try:
            advance(dt_s, step_number=step_number, substep=0)
            elapsed_s += dt_s
        except ResidueEngineNonconvergence as exc:
            # Retry the failed interval at half size, completing both halves
            # only if the engine converges at each refined composition.
            half_dt_s = dt_s / 2.0
            retry_failed = False
            for substep in (1, 2):
                try:
                    advance(half_dt_s, step_number=step_number, substep=substep)
                    elapsed_s += half_dt_s
                except ResidueEngineNonconvergence as retry_exc:
                    exc = retry_exc
                    retry_failed = True
                    break
            if retry_failed:
                refusal = ResidueEngineNonconvergence(
                    exc.engine,
                    exc.detail,
                    composition_mol=inventory,
                    step=step_number,
                    time_reached_s=elapsed_s,
                    retry="one interval halving; refined substep still failed",
                    reason=exc.reason,
                )
                break

    remove_exhausted(
        step_number=step_number if steps else 0,
        time_s=elapsed_s,
        substep=0,
    )
    final_atoms = _atom_totals(inventory, evaporated_mol)
    exhausted_atoms = _atom_totals(exhausted_mol, {})
    initial_atoms = _atom_totals(initial_inventory_mol, {})
    closure: dict[str, float] = {}
    for element in sorted(
        set(initial_atoms) | set(final_atoms) | set(exhausted_atoms), key=str
    ):
        residual = (
            initial_atoms.get(element, 0.0)
            - final_atoms.get(element, 0.0)
            - exhausted_atoms.get(element, 0.0)
        )
        closure[element] = residual
        scale = max(
            abs(initial_atoms.get(element, 0.0)),
            abs(final_atoms.get(element, 0.0) + exhausted_atoms.get(element, 0.0)),
            1.0e-300,
        )
        _check_residue_atom_residual(element, residual, scale, "after exhaustion projection")
    pO2_balance_floor = {
        "steps_at_floor": pO2_balance_floor_steps,
        "first_step": pO2_balance_floor_first_step,
        "fraction_of_time_at_floor": (
            pO2_balance_floor_time_s / elapsed_s if elapsed_s > 0.0 else 0.0
        ),
        "time_at_floor_s": pO2_balance_floor_time_s,
        "pO2_bar": pO2_balance_floor_pO2_bar,
    }
    return _HashimotoGeometryIntegration(
        composition_wt_pct=_hashimoto_project_oxide_wt_pct(inventory),
        pO2_steps=tuple(pO2_steps),
        exhaustion_notices=tuple(exhaustion_notices),
        refusal=refusal,
        atom_closure=closure,
        inventory_mol=dict(inventory),
        evaporated_mol=dict(evaporated_mol),
        pO2_balance_floor=pO2_balance_floor,
        pressure_law_diagnostics=tuple(pressure_law_diagnostics.values()),
    )


def _hashimoto_run_setup(
    experiment: Mapping[str, Any],
    *,
    gas_pack: Any,
    catalog_rows: Sequence[tuple[str, Mapping[str, Any]]],
    alpha_specs: Mapping[str, Any],
    preform_dimensions_mm: Mapping[str, float],
    engine: str,
    engine_metadata: Mapping[str, str],
    reference_printed_wt_pct: Mapping[str, Any],
) -> _HashimotoRunSetup:
    """Build the shared per-run inputs used by cohort and refinement checks."""
    from simulator.battery.oxygen_balance import _OXYGEN_GAS_ALPHA_SOURCE
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _openimcc_gas_channels_and_omission_notices,
    )

    experiment_id = str(experiment["experiment_id"])
    temperature_K = _hashimoto_point_value(experiment["conditions"]["temperature_K"])
    duration_s = _hashimoto_point_value(
        experiment["thermal_schedule"]["total_duration_s"]
    )
    sample_mass_kg = _hashimoto_point_value(experiment["sample"]["mass_kg"])
    total_pressure_Pa = _hashimoto_point_value(
        experiment["pressure_environment"]["total_pressure_Pa"]
    )
    if not math.isclose(sample_mass_kg, 1.0e-4, rel_tol=0.0, abs_tol=1.0e-12):
        raise ResidueInventoryRefusal("hashimoto_printed_mass_changed", experiment_id)
    run_wt_pct = experiment["sample"]["printed_composition"]["state"]["value"]
    if dict(run_wt_pct) != dict(reference_printed_wt_pct):
        raise ResidueInventoryRefusal(
            "hashimoto_printed_composition_changed", experiment_id
        )
    initial_inventory_mol = {
        str(oxide): float(wt_pct) * sample_mass_kg / 100.0
        / _formula_terms(str(oxide))[1]
        for oxide, wt_pct in run_wt_pct.items()
    }
    gas_channels, channel_omissions = _openimcc_gas_channels_and_omission_notices(
        tuple(initial_inventory_mol), gas_pack
    )
    if channel_omissions:
        raise ResidueInventoryRefusal(
            "hashimoto_openimcc_channel_missing", str(channel_omissions)
        )
    if not gas_channels:
        raise ResidueInventoryRefusal("hashimoto_openimcc_channels_missing")

    steps = min(_HASHIMOTO_N_CAP, max(8, math.ceil(duration_s / 60.0)))
    geometry_areas = _hashimoto_geometry_areas(
        sample_mass_kg, preform_dimensions_mm
    )
    liquid_rows = _hashimoto_liquid_row_provenance(
        gas_pack, gas_channels, temperature_K
    )
    catalog_matches: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for species, (parent, _parent_moles, _pO2_exponent) in gas_channels:
        if not parent:
            continue
        matches = [
            (name, row)
            for name, row in catalog_rows
            if row.get("formula") == species
            and row.get("parent_oxide") == parent
        ]
        active_matches = [
            item for item in matches if item[1].get("flux_dormant") is not True
        ]
        if not matches:
            raise ResidueInventoryRefusal(
                "hashimoto_runtime_catalog_channel_missing",
                f"no catalog row for {species}/{parent}",
            )
        catalog_matches[species] = (active_matches or matches)[0]

    common_channels: list[ResidueChannel] = []
    common_alpha_records: list[dict[str, Any]] = []
    common_liquid_rows: list[dict[str, Any]] = []
    row_by_channel = {str(row["channel"]): dict(row) for row in liquid_rows}
    for species, (parent, _parent_moles, _pO2_exponent) in gas_channels:
        if not parent:
            alpha_source = (
                _OXYGEN_GAS_ALPHA_SOURCE
                if species in _OXYGEN_CHANNELS
                else "OpenIMCC gas channel with no parent oxide"
            )
            common_channels.append(
                ResidueChannel(species, species, None, 1.0, alpha_source)
            )
            common_alpha_records.append(
                {
                    "channel": species,
                    "alpha_value": 1.0,
                    "alpha_assumed": True,
                    "alpha_source": alpha_source,
                }
            )
            common_liquid_rows.append(row_by_channel[species])
            continue

        catalog_name, catalog_row = catalog_matches[species]
        if catalog_row.get("flux_dormant") is True:
            continue
        common_source = (
            f"declared alpha=1 sensitivity; {catalog_name} runtime catalog row "
            "is provenance only, not the common-unity alpha source"
        )
        common_channels.append(
            ResidueChannel(species, species, parent, 1.0, common_source)
        )
        common_alpha_records.append(
            {
                "channel": species,
                "catalog_row": catalog_name,
                "alpha_value": 1.0,
                "alpha_source": common_source,
                "alpha_assumed": True,
                "author_ratio_assumption": "alpha_i/alpha_j=1",
            }
        )
        common_liquid_rows.append(row_by_channel[species])

    if not any(channel.parent_oxide for channel in common_channels):
        raise ResidueInventoryRefusal("hashimoto_common_unity_channels_missing")
    engine_floor_inversion_diagnostics: dict[str, dict[str, object]] = {}
    engine_flags: set[str] = set()
    pressure_model = _make_residue_pressure_model(
        engine,
        gas_channels,
        gas_pack,
        temperature_K=temperature_K,
        total_pressure_Pa=total_pressure_Pa,
        engine_metadata=engine_metadata,
        engine_flags=engine_flags,
        starting_inventory=initial_inventory_mol,
        missing_channels_reason="hashimoto_internal_analytical_channels_missing",
        caller="Hashimoto residue cohort",
        floor_inversion_diagnostics=(
            engine_floor_inversion_diagnostics
            if engine == "internal-analytical"
            else None
        ),
    )
    return _HashimotoRunSetup(
        experiment_id=experiment_id,
        temperature_K=temperature_K,
        duration_s=duration_s,
        sample_mass_kg=sample_mass_kg,
        total_pressure_Pa=total_pressure_Pa,
        starting_composition_wt_pct=run_wt_pct,
        initial_inventory_mol=initial_inventory_mol,
        steps=steps,
        geometry_areas=geometry_areas,
        gas_channels=tuple(gas_channels),
        liquid_rows=tuple(liquid_rows),
        common_channels=tuple(common_channels),
        common_alpha_records=tuple(common_alpha_records),
        common_liquid_rows=tuple(common_liquid_rows),
        catalog_matches=catalog_matches,
        alpha_specs=alpha_specs,
        pressure_model=pressure_model,
        engine_flags=engine_flags,
        engine_floor_inversion_diagnostics=engine_floor_inversion_diagnostics,
    )


def _residue_catalog_rows(
    legacy_catalog: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    return [
        (str(name), row)
        for group in legacy_catalog.values()
        if isinstance(group, Mapping)
        for name, row in group.items()
        if isinstance(row, Mapping) and row.get("formula")
    ]


def _predict_hashimoto_residue_cohort(
    experiments: Sequence[Mapping[str, Any]],
    runtime_catalog: Mapping[str, Any],
    *,
    preform_dimensions_mm: Mapping[str, float],
    engine: str = "openimcc",
    selected_experiment_ids: frozenset[str] | None = None,
    _allow_partial_cohort_for_test: bool = False,
) -> tuple[_HashimotoResiduePrediction, ...]:
    """Produce both alpha arms and geometry bands, without reading residues."""
    if engine not in {"openimcc", "internal-analytical"}:
        raise ResidueInventoryRefusal("hashimoto_engine_unsupported", engine)
    experiment_ids = {
        str(experiment.get("experiment_id", "")) for experiment in experiments
    }
    if (
        not experiments
        or len(experiment_ids) != len(experiments)
        or (len(experiments) != 24 and not _allow_partial_cohort_for_test)
    ):
        raise ResidueInventoryRefusal(
            "hashimoto_cohort_invalid", "R2 requires 24 distinct physical runs"
        )
    if selected_experiment_ids is not None and (
        not selected_experiment_ids or not selected_experiment_ids <= experiment_ids
    ):
        raise ResidueInventoryRefusal(
            "hashimoto_experiment_selection_invalid",
            "selected runs must be a non-empty subset of the validated cohort",
        )
    try:
        import openimcc
        from openimcc import load_gas_datapack
        from openimcc.gas import _default_reactions
    except ImportError as exc:
        raise ResidueInventoryRefusal(
            "openimcc_not_importable", str(exc)
        ) from exc

    from simulator.melt_backend import openimcc_bridge
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view
    from simulator.evaporation import _load_evaporation_alpha_by_species
    from simulator.condensation import alpha_s

    gas_pack = load_gas_datapack()
    legacy_catalog = vapor_pressure_legacy_view(runtime_catalog)
    catalog_rows = _residue_catalog_rows(legacy_catalog)
    alpha_specs = _load_evaporation_alpha_by_species(legacy_catalog)
    first = experiments[0]
    printed_wt_pct = first["sample"]["printed_composition"]["state"]["value"]
    engine_binding_identity = (
        openimcc_bridge.engine_binding_identity(
            openimcc_bridge._load_pack(
                openimcc_bridge.DEFAULT_BACKEND_PACK_NAME
            ),
            gas_pack,
        )
        if engine == "openimcc"
        else None
    )
    melt_pack_identity: dict[str, str] = {}
    primary_alpha_arm, primary_geometry = _hashimoto_primary_policy()
    prediction_rows: list[_HashimotoResiduePrediction] = []

    for experiment in experiments:
        experiment_id = str(experiment["experiment_id"])
        if (
            selected_experiment_ids is not None
            and experiment_id not in selected_experiment_ids
        ):
            continue
        setup = _hashimoto_run_setup(
            experiment,
            gas_pack=gas_pack,
            catalog_rows=catalog_rows,
            alpha_specs=alpha_specs,
            preform_dimensions_mm=preform_dimensions_mm,
            engine=engine,
            engine_metadata=melt_pack_identity,
            reference_printed_wt_pct=printed_wt_pct,
        )
        temperature_K = setup.temperature_K
        duration_s = setup.duration_s
        sample_mass_kg = setup.sample_mass_kg
        total_pressure_Pa = setup.total_pressure_Pa
        run_wt_pct = setup.starting_composition_wt_pct
        run_initial_mol = setup.initial_inventory_mol
        initial_steps = setup.steps
        geometry_areas = setup.geometry_areas
        gas_channels = setup.gas_channels
        row_by_channel = {str(row["channel"]): dict(row) for row in setup.liquid_rows}
        common_channels = setup.common_channels
        common_alpha_records = setup.common_alpha_records
        pressure_model = setup.pressure_model
        engine_floor_inversion_diagnostics = setup.engine_floor_inversion_diagnostics
        runtime_channels: list[ResidueChannel] = []
        runtime_alpha_records: list[dict[str, Any]] = []
        runtime_omissions: list[dict[str, str]] = []
        active_liquid_rows: dict[str, list[dict[str, Any]]] = {
            "common": list(setup.common_liquid_rows),
            "runtime": [],
        }
        for species, (parent, _parent_moles, _pO2_exponent) in gas_channels:
            if not parent:
                runtime_channels.append(
                    ResidueChannel(
                        species,
                        species,
                        None,
                        1.0,
                        "assumed oxygen-gas alpha=1.0; not measured",
                    )
                )
                runtime_alpha_records.append(
                    {"channel": species, "alpha_value": 1.0, "alpha_assumed": True,
                     "alpha_source": "assumed oxygen-gas alpha=1.0; not measured"}
                )
                active_liquid_rows["runtime"].append(
                    row_by_channel[species]
                )
                continue

            catalog_name, catalog_row = setup.catalog_matches[species]
            if catalog_row.get("flux_dormant") is True:
                runtime_omissions.append(
                    {"channel": species, "reason": "flux_dormant_reporting_only"}
                )
                continue
            alpha_spec = setup.alpha_specs.get(catalog_name)
            if alpha_spec is None:
                runtime_omissions.append(
                    {"channel": species, "reason": "runtime_catalog_alpha_missing"}
                )
                continue
            alpha_context: dict[str, Any] = {"coefficient_spec": alpha_spec}
            try:
                runtime_alpha = float(alpha_s(catalog_name, temperature_K, alpha_context))
            except (TypeError, ValueError, ArithmeticError) as exc:
                runtime_omissions.append(
                    {
                        "channel": species,
                        "reason": f"runtime_catalog_alpha_invalid:{exc}",
                    }
                )
                continue
            raw_alpha = catalog_row.get("evaporation_alpha")
            source = (
                str(raw_alpha.get("source") or raw_alpha.get("cite") or "runtime catalog")
                if isinstance(raw_alpha, Mapping)
                else "runtime catalog"
            )
            runtime_channels.append(
                ResidueChannel(
                    species,
                    species,
                    parent,
                    runtime_alpha,
                    f"runtime catalog {catalog_name}: {source}; assumed outside Hashimoto system",
                )
            )
            runtime_alpha_records.append(
                {
                    "channel": species,
                    "catalog_row": catalog_name,
                    "alpha_value": runtime_alpha,
                    "alpha_source": source,
                    "alpha_assumed": True,
                    "alpha_assumed_runtime_catalog_system_mismatch": True,
                    "alpha_spec": dict(alpha_spec)
                    if isinstance(alpha_spec, Mapping)
                    else alpha_spec,
                    "alpha_evaluation": dict(
                        alpha_context.get("alpha_s_evaluation") or {}
                    ),
                }
            )
            active_liquid_rows["runtime"].append(
                row_by_channel[species]
            )

        if not any(channel.parent_oxide for channel in runtime_channels):
            raise ResidueInventoryRefusal("hashimoto_runtime_alpha_channels_missing")

        arm_channels = (
            ("alpha_common_unity_sensitivity", common_channels, common_alpha_records, "common"),
            ("alpha_runtime_catalog", runtime_channels, runtime_alpha_records, "runtime"),
        )
        for alpha_arm, channels, alpha_records, row_key in arm_channels:
            geometry_predictions: dict[str, dict[str, float]] = {}
            geometry_inventory_mol: dict[str, Mapping[str, float] | None] = {}
            geometry_pressure_law_diagnostics: dict[
                str, tuple[PressureLawChannelDiagnostic, ...]
            ] = {}
            pO2_ranges: dict[str, list[float] | None] = {}
            pO2_balance_floor_by_geometry: dict[
                str, dict[str, float | int | None]
            ] = {}
            geometry_exhaustions: dict[str, tuple[dict[str, float | int | str], ...]] = {}
            geometry_refusals: dict[str, dict[str, object] | None] = {}
            geometry_atom_closure: dict[str, dict[str, float]] = {}
            refinement_steps_by_geometry: dict[str, tuple[int, ...]] = {}
            refinement_differences_by_geometry: dict[str, tuple[dict[str, float | int], ...]] = {}
            refinement_status_by_geometry: dict[str, str] = {}
            refinement_accepted_steps_by_geometry: dict[str, int] = {}
            refinement_notices: list[dict[str, str]] = []
            for geometry in _HASHIMOTO_GEOMETRIES:
                steps = initial_steps
                tried_steps: list[int] = []
                level_differences: list[dict[str, float | int]] = []
                previous_values: dict[str, float] | None = None
                values: dict[str, float] = {}
                pO2_steps: tuple[float, ...] = ()
                exhaustions: tuple[dict[str, float | int | str], ...] = ()
                refusal: ResidueEngineNonconvergence | None = None
                atom_closure: dict[str, float] = {}
                pO2_balance_floor: dict[str, float | int | None] = {}
                refinement_status = "unconverged_at_cap"
                while True:
                    integration_result = _hashimoto_integrate_geometry(
                        run_initial_mol,
                        channels,
                        pressure_model,
                        temperature_K=temperature_K,
                        duration_s=duration_s,
                        steps=steps,
                        geometry_policy_id=geometry,
                        initial_area_m2=geometry_areas[geometry],
                    )
                    geometry_pressure_law_diagnostics[geometry] = (
                        integration_result.pressure_law_diagnostics
                    )
                    (
                        values,
                        pO2_steps,
                        exhaustions,
                        refusal,
                        atom_closure,
                        pO2_balance_floor,
                    ) = integration_result
                    integrated_inventory = getattr(
                        integration_result, "inventory_mol", None
                    )
                    geometry_inventory_mol[geometry] = (
                        dict(integrated_inventory)
                        if isinstance(integrated_inventory, Mapping)
                        else None
                    )
                    tried_steps.append(steps)
                    if refusal is not None:
                        refinement_status = "engine_nonconvergence"
                        break
                    if previous_values is not None:
                        max_difference = max(
                            abs(values[oxide] - previous_values[oxide])
                            for oxide in run_initial_mol
                        )
                        level_differences.append(
                            {
                                "coarse_steps": tried_steps[-2],
                                "fine_steps": steps,
                                "max_abs_difference_wt_pct": max_difference,
                            }
                        )
                        if max_difference < _HASHIMOTO_REFINEMENT_TOLERANCE_WT_PCT:
                            refinement_status = "converged"
                            break
                    if steps >= _HASHIMOTO_N_CAP:
                        refinement_status = "unconverged_at_cap"
                        refinement_notices.append(
                            {
                                "reason": "residue_time_refinement_unconverged",
                                "geometry_policy_id": geometry,
                            }
                        )
                        break
                    previous_values = values
                    steps = min(_HASHIMOTO_N_CAP, 2 * steps)
                refinement_steps_by_geometry[geometry] = tuple(tried_steps)
                refinement_differences_by_geometry[geometry] = tuple(level_differences)
                refinement_status_by_geometry[geometry] = refinement_status
                refinement_accepted_steps_by_geometry[geometry] = tried_steps[-1]
                geometry_predictions[geometry] = values
                geometry_exhaustions[geometry] = exhaustions
                pO2_balance_floor_by_geometry[geometry] = pO2_balance_floor
                geometry_refusals[geometry] = (
                    {
                        **refusal.as_record(),
                        "geometry_policy_id": geometry,
                    }
                    if refusal is not None
                    else None
                )
                geometry_atom_closure[geometry] = atom_closure
                pO2_ranges[geometry] = (
                    [min(pO2_steps), max(pO2_steps)] if pO2_steps else None
                )
            for notice in getattr(pressure_model, "below_threshold_omission_notices", ()):
                row = {"reason": notice.reason, "kind": str(notice.kind.value)}
                if row not in refinement_notices:
                    refinement_notices.append(row)
            band = {
                oxide: (
                    min(values[oxide] for values in geometry_predictions.values()),
                    max(values[oxide] for values in geometry_predictions.values()),
                )
                for oxide in run_initial_mol
            }
            primary_area = geometry_areas[primary_geometry]
            geometry_provenance = {
                "sphere_constant": {
                    "area_m2_initial": geometry_areas["sphere_constant"],
                    "area_evolution": "constant equal-volume sphere area",
                    "flag": "area_assumed_sphere_constant",
                },
                "sphere_shrinking": {
                    "area_m2_initial": geometry_areas["sphere_shrinking"],
                    "area_evolution": "A(t)=A0*(remaining_oxide_mass/initial_mass)^(2/3), updated each step",
                    "flag": "area_assumed_sphere_shrinking",
                },
                "disk_4mm_constant": {
                    "area_m2_initial": geometry_areas["disk_4mm_constant"],
                    "area_evolution": "constant 4 mm diameter disk; hole not subtracted",
                    "flag": "area_assumed_disk_4mm_constant",
                },
            }
            extrapolated_liquids = [
                row
                for row in active_liquid_rows[row_key]
                if row.get("liquid_row")
                and row["liquid_row"].get("extrapolated_below_floor")
            ]
            assumption_flags = {
                "alpha_assumed_unity_ratio_not_measured_alpha": (
                    alpha_arm == "alpha_common_unity_sensitivity"
                ),
                "alpha_assumed_common_unity_sensitivity": (
                    alpha_arm == "alpha_common_unity_sensitivity"
                ),
                "alpha_assumed_runtime_catalog_system_mismatch": (
                    alpha_arm == "alpha_runtime_catalog"
                ),
                "alpha_assumed_oxygen_gas_unity": True,
                "area_assumed_sphere_constant": True,
                "area_assumed_sphere_shrinking": True,
                "area_assumed_disk_4mm_constant": True,
                "density_fallback_2700_kg_m3": True,
                "oxygen_unbuffered_vacuum": True,
                "pressure_on_throw_ignored_for_bc": True,
                "feo_melt_redox_stack2_pending": True,
                "d060_pending": True,
                "openimcc_liquid_row_extrapolated": bool(extrapolated_liquids),
            }
            provenance = {
                "experiment_id": experiment_id,
                "source_id": HASHIMOTO_SOURCE_ID,
                "run_locator": dict(experiment.get("locator") or {}),
                "temperature_K": temperature_K,
                "duration_s": duration_s,
                "sample_mass_kg": sample_mass_kg,
                "starting_composition_wt_pct": dict(run_wt_pct),
                "starting_preform_mm": dict(preform_dimensions_mm),
                "initial_inventory_mol": dict(run_initial_mol),
                "sample_surface_area_status": "not_tabulated",
                "primary_geometry_policy_id": primary_geometry,
                "geometry_primary_argument": (
                    "A freely receding melt is represented as a fixed-density sphere, "
                    "so area scales with remaining melt mass^(2/3); the cold preform "
                    "and quenched spherule do not establish the molten area. This choice "
                    "was declared without using residue observations."
                ),
                "geometry_policies": geometry_provenance,
                "geometry_policy_id": primary_geometry,
                "area_m2_initial": primary_area,
                "area_evolution": geometry_provenance[primary_geometry]["area_evolution"],
                "density_kg_m3": _HASHIMOTO_DENSITY_KG_M3,
                "density_source": _HASHIMOTO_DENSITY_SOURCE,
                "alpha_arm": alpha_arm,
                "alpha_source": (
                    "declared_common_unity_sensitivity"
                    if alpha_arm == "alpha_common_unity_sensitivity"
                    else "runtime_catalog_alpha_values"
                ),
                "author_alpha_assumption": {
                    "extract_assumption": "assumed_unity_ratio_not_measured_alpha",
                    "assumed_ratio": "alpha_i/alpha_j=1",
                    "absolute_alpha_established": False,
                    "active_for_this_arm": alpha_arm == "alpha_common_unity_sensitivity",
                },
                "alpha_by_channel": tuple(alpha_records),
                "runtime_catalog_omissions": tuple(runtime_omissions)
                if alpha_arm == "alpha_runtime_catalog"
                else (),
                "oxygen_model": (
                    f"R1a engine-consistent alpha-weighted congruent vacuum oxygen balance ({engine})"
                ),
                "engine": engine,
                "oxygen_boundary": "unbuffered vacuum; fO2 control none",
                "total_pressure_Pa": total_pressure_Pa,
                "surface_pO2_bar_range_by_geometry": pO2_ranges,
                "surface_pO2_balance_floor": pO2_balance_floor_by_geometry[
                    primary_geometry
                ],
                "surface_pO2_balance_floor_by_geometry": (
                    pO2_balance_floor_by_geometry
                ),
                "engine_floor_inversion_diagnostics": tuple(
                    engine_floor_inversion_diagnostics.values()
                ),
                "engine_flags": tuple(sorted(setup.engine_flags)),
                "pressure_law_diagnostics": tuple(
                    diagnostic.as_record()
                    for diagnostic in geometry_pressure_law_diagnostics[
                        primary_geometry
                    ]
                ),
                "liquid_rows_used": tuple(active_liquid_rows[row_key]),
                "d060_pending": True,
                "openimcc_version": str(getattr(openimcc, "__version__", "unknown")),
                "datapack_version": melt_pack_identity.get("datapack_version"),
                "openimcc_model_id": melt_pack_identity.get("model_id"),
                "pack_digest": {
                    "melt_datapack": melt_pack_identity.get("pack_digest", ""),
                },
                **(
                    {"engine_binding_identity": engine_binding_identity}
                    if engine_binding_identity is not None
                    else {}
                ),
                "openimcc_pin": openimcc_bridge.OPENIMCC_RECORDED_PIN,
                "integration": {
                    "steps": refinement_accepted_steps_by_geometry[primary_geometry],
                    "steps_tried_by_geometry": refinement_steps_by_geometry,
                    "max_difference_wt_pct_by_geometry": refinement_differences_by_geometry,
                    "accepted_steps_by_geometry": refinement_accepted_steps_by_geometry,
                    "refinement_status_by_geometry": refinement_status_by_geometry,
                    "refinement_status": refinement_status_by_geometry[primary_geometry],
                    "refinement_notices": tuple(refinement_notices),
                    "substep_duration_s": duration_s
                    / refinement_accepted_steps_by_geometry[primary_geometry],
                    "component_exhaustion_rule": (
                        "remaining component moles below sqrt(binary64 epsilon) "
                        "times initial sample oxide moles, floored at 16 ULPs; "
                        "the rounded remainder is retained in atom-closure accounting"
                    ),
                    "component_exhaustion_floor_mol": _component_exhaustion_floor_mol(
                        math.fsum(run_initial_mol.values())
                    ),
                    "engine_nonconvergence_retry": (
                        "retry the failed interval as two half steps; a second "
                        "failure stops this geometry run with a partial diagnostic"
                    ),
                },
                "notices": tuple(refinement_notices),
                "component_exhausted_by_geometry": geometry_exhaustions,
                "atom_closure_mol_by_geometry": geometry_atom_closure,
                "geometry_refusal_by_geometry": geometry_refusals,
                "run_refusal": geometry_refusals[primary_geometry] or next(
                    (refusal for refusal in geometry_refusals.values() if refusal),
                    None,
                ),
                "prediction_status": (
                    "partial_diagnostic"
                    if any(geometry_refusals.values())
                    else "complete"
                ),
                "assumption_flags": assumption_flags,
            }
            if geometry_inventory_mol[primary_geometry] is not None:
                provenance["integrated_inventory_mol"] = dict(
                    geometry_inventory_mol[primary_geometry]
                )
            prediction_rows.append(
                _HashimotoResiduePrediction(
                    experiment_id=experiment_id,
                    alpha_arm=alpha_arm,
                    primary_geometry_policy_id=primary_geometry,
                    primary_oxide_wt_pct=dict(geometry_predictions[primary_geometry]),
                    geometry_oxide_wt_pct=geometry_predictions,
                    sensitivity_band_wt_pct=band,
                    initial_inventory_mol=dict(run_initial_mol),
                    primary_inventory_mol=(
                        None
                        if geometry_inventory_mol[primary_geometry] is None
                        else dict(geometry_inventory_mol[primary_geometry])
                    ),
                    provenance=provenance,
                )
            )

    # Keep the predeclared primary first; selection is independent of measured rows.
    prediction_rows.sort(
        key=lambda row: (
            row.alpha_arm != primary_alpha_arm,
            row.experiment_id,
        )
    )
    return tuple(prediction_rows)


_SOSSI_TRACE_PARENTS = {"Mn": "MnO", "Ti": "TiO2"}
_SOSSI_GEOMETRIES = (
    "pt_loop_bead_sphere_constant",
    "pt_loop_bead_sphere_shrinking",
    "pt_loop_bead_disk_equivalent_volume",
)
_SOSSI_PRIMARY_GEOMETRY = _SOSSI_GEOMETRIES[0]
_SOSSI_DENSITY_SOURCE = (
    "labelled 2700 kg/m3 fallback for the Sossi bead geometry sensitivity; "
    "density is not printed by Sossi"
)


def _sossi_initial_inventory(
    experiment: Mapping[str, Any], starting_trace_ppm: Mapping[str, float]
) -> tuple[dict[str, float], float, float]:
    """Build the 25 mg FCMAS host plus the run's two measured trace additions."""
    sample = experiment.get("sample")
    if not isinstance(sample, Mapping):
        raise ResidueInventoryRefusal("sossi_host_composition_missing")
    printed = sample.get("printed_composition")
    if not isinstance(printed, Mapping):
        raise ResidueInventoryRefusal("sossi_host_composition_missing")
    state = printed.get("state")
    wt_pct = state.get("value") if isinstance(state, Mapping) else None
    if not isinstance(wt_pct, Mapping) or not wt_pct:
        raise ResidueInventoryRefusal("sossi_host_composition_missing")
    mass_kg = _hashimoto_point_value(sample.get("mass_kg", {}))
    if not math.isclose(mass_kg, 25.0e-6, rel_tol=0.0, abs_tol=1.0e-12):
        raise ResidueInventoryRefusal("sossi_sample_mass_invalid")
    inventory = {
        str(oxide): float(value) * mass_kg / 100.0 / _formula_terms(str(oxide))[1]
        for oxide, value in wt_pct.items()
    }
    for element, parent in _SOSSI_TRACE_PARENTS.items():
        ppm = float(starting_trace_ppm.get(element, math.nan))
        if not math.isfinite(ppm) or ppm <= 0.0:
            raise ResidueInventoryRefusal(
                "starting_component_ppm_missing", f"missing positive {element} starting ppm"
            )
        _atoms, element_molar_mass = _formula_terms(element)
        element_mass_kg = ppm * 1.0e-6 * mass_kg
        inventory[parent] = inventory.get(parent, 0.0) + (
            element_mass_kg / element_molar_mass
        )
    return inventory, mass_kg, math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in inventory.items()
    )


def _sossi_geometry_areas(sample_mass_kg: float) -> dict[str, float]:
    density_kg_m3 = _HASHIMOTO_DENSITY_KG_M3
    volume_m3 = sample_mass_kg / density_kg_m3
    radius_m = (3.0 * volume_m3 / (4.0 * math.pi)) ** (1.0 / 3.0)
    sphere_area = 4.0 * math.pi * radius_m**2
    # The comparison disk has the same bead volume and diameter as its height.
    disk_area = volume_m3 / (2.0 * radius_m)
    return {
        "pt_loop_bead_sphere_constant": sphere_area,
        "pt_loop_bead_sphere_shrinking": sphere_area,
        "pt_loop_bead_disk_equivalent_volume": disk_area,
    }


def _inventory_bulk_and_element_masses_kg(
    inventory_mol: Mapping[str, float],
    buffer_oxygen_exchange_mol: float,
    elements: Sequence[str],
) -> tuple[float, dict[str, float]]:
    """Return the integrated bulk mass and requested elemental masses."""
    total_mass_kg = math.fsum(
        float(amount) * _formula_terms(oxide)[1]
        for oxide, amount in inventory_mol.items()
    ) + min(0.0, buffer_oxygen_exchange_mol) * _formula_terms("O")[1]
    if not math.isfinite(total_mass_kg) or total_mass_kg <= 0.0:
        raise ResidueInventoryRefusal("sossi_residue_mass_invalid")
    element_mass_kg: dict[str, float] = {}
    for element in elements:
        element_moles = math.fsum(
            float(amount) * _formula_terms(oxide)[0].get(element, 0.0)
            for oxide, amount in inventory_mol.items()
        )
        element_mass_kg[element] = element_moles * _formula_terms(element)[1]
    return total_mass_kg, element_mass_kg


_FM_CONCENTRATION_BASES = frozenset({"mass_fraction", "mole_fraction_single_cation_oxide"})


def _printed_fm_definition(located_definition: object | None) -> tuple[str, Mapping[str, Any]]:
    state = getattr(located_definition, "state", None)
    value = getattr(state, "value", None) if getattr(state, "is_value", False) else None
    if not isinstance(value, Mapping):
        raise ResidueInventoryRefusal(
            "fo_definition_unprinted", "missing point_conditions.fm_definition"
        )
    definition = value.get("definition")
    quote = value.get("quote")
    page = value.get("page")
    # The generic point-condition migrator preserves numeric metadata as Decimal.
    if isinstance(page, Decimal) and page.is_finite() and page == page.to_integral_value():
        page = int(page)
    if (
        definition not in {"concentration_retention", "mass_retention"}
        or not isinstance(quote, str)
        or not quote.strip()
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ResidueInventoryRefusal(
            "fo_definition_unprinted",
            "fm_definition needs a named definition, printed quote, and page",
        )
    if (
        definition == "concentration_retention"
        and value.get("concentration_basis") not in _FM_CONCENTRATION_BASES
    ):
        raise ResidueInventoryRefusal(
            "fm_concentration_basis_unprinted_or_unknown",
            "fm_definition needs a printed concentration_basis",
        )
    return str(definition), value


def _require_element_retention_transport_inputs(inputs: Mapping[str, object]) -> None:
    def has_value(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return bool(value) and all(has_value(item) for item in value.values())
        if isinstance(value, (tuple, list)):
            return bool(value) and all(has_value(item) for item in value)
        kind = getattr(value, "kind", None)
        if getattr(kind, "value", kind) == "unavailable":
            return False
        state = getattr(value, "state", None)
        if state is None and hasattr(value, "is_value"):
            state = value
        if state is not None and hasattr(state, "is_value"):
            return bool(state.is_value) and has_value(state.value)
        return True

    labels = (
        ("duration", "duration_missing", "duration"),
        ("temperature", "temperature_missing", "T"),
        ("fO2", "fO2_missing", "fO2"),
        ("starting_composition", "starting_composition_missing", "starting composition"),
        ("sample_area_or_geometry", "sample_area_or_geometry_missing", "sample area/geometry"),
        ("gas_conditions", "gas_conditions_missing", "gas conditions"),
    )
    for key, reason, label in labels:
        value = inputs.get(key)
        if not has_value(value):
            raise ResidueInventoryRefusal(reason, f"missing {label}")


def _element_retention_ratio_from_inventories(
    initial_inventory_mol: Mapping[str, float],
    final_inventory_mol: Mapping[str, float],
    element: str,
    definition: str,
    *,
    concentration_basis: str | None = None,
    initial_oxygen_exchange_mol: float = 0.0,
    final_oxygen_exchange_mol: float = 0.0,
    initial_bulk_mass_adjustment_kg: float = 0.0,
    final_bulk_mass_adjustment_kg: float = 0.0,
) -> float:
    if definition not in {"concentration_retention", "mass_retention"}:
        raise ResidueInventoryRefusal("fo_definition_unprinted")
    if (
        definition == "concentration_retention"
        and concentration_basis not in _FM_CONCENTRATION_BASES
    ):
        raise ResidueInventoryRefusal("fm_concentration_basis_unprinted_or_unknown")
    if not any(
        float(amount) > 0.0 and _formula_terms(formula)[0].get(element, 0.0) > 0.0
        for formula, amount in initial_inventory_mol.items()
    ):
        raise ResidueInventoryRefusal(
            "element_unavailable", f"integrator does not carry {element}"
        )
    initial_bulk_mass_kg, initial_element_mass_kg = (
        _inventory_bulk_and_element_masses_kg(
            initial_inventory_mol, initial_oxygen_exchange_mol, (element,)
        )
    )
    final_bulk_mass_kg, final_element_mass_kg = (
        _inventory_bulk_and_element_masses_kg(
            final_inventory_mol, final_oxygen_exchange_mol, (element,)
        )
    )
    initial_bulk_mass_kg += initial_bulk_mass_adjustment_kg
    final_bulk_mass_kg += final_bulk_mass_adjustment_kg
    initial_element_mass = initial_element_mass_kg[element]
    final_element_mass = final_element_mass_kg[element]
    if initial_element_mass <= 0.0:
        raise ResidueInventoryRefusal(
            "starting_component_missing", f"no initial {element} in integrated inventory"
        )
    if final_element_mass < 0.0 or not math.isfinite(final_element_mass):
        raise ResidueInventoryRefusal(
            "element_inventory_invalid", f"invalid final {element} inventory"
        )
    mass_retention = final_element_mass / initial_element_mass
    if definition == "mass_retention":
        return mass_retention
    if concentration_basis == "mole_fraction_single_cation_oxide":
        # Norris 2016 Eq. 5.1 (physical p.232 / folio 202) prints
        # X_i = n_i / N, N = sum of single-cation oxide moles. Each oxide
        # contributes its non-oxygen atom count (Al2O3 -> 2 AlO1.5,
        # Na2O -> 2 NaO0.5); oxygen exchanged with the buffer adds no cations.
        # Thus FM = (n_i,f/n_i,0) / (N_f/N_0). The elemental molar mass
        # cancels in n_i,f/n_i,0, so mass_retention supplies that ratio.
        # Units: (mol/mol)/(mol/mol) is dimensionless. Sanity: FeO + MgO,
        # 1 + 1 -> 0.8 + 0.4, gives 0.8/(1.2/2) = 1.333333, whereas
        # mass fractions give 1.219053 (independently pinned in the test).
        cation_totals = [
            math.fsum(
                float(amount) * math.fsum(
                    count for atom, count in _formula_terms(oxide)[0].items()
                    if atom != "O"
                )
                for oxide, amount in inventory.items()
            )
            for inventory in (initial_inventory_mol, final_inventory_mol)
        ]
        if any(not math.isfinite(total) or total <= 0.0 for total in cation_totals):
            raise ResidueInventoryRefusal("residue_cation_inventory_invalid")
        return mass_retention / (cation_totals[1] / cation_totals[0])
    bulk_mass_factor = final_bulk_mass_kg / initial_bulk_mass_kg
    if not math.isfinite(bulk_mass_factor) or bulk_mass_factor <= 0.0:
        raise ResidueInventoryRefusal("residue_bulk_mass_invalid")
    return mass_retention / bulk_mass_factor


def _sossi_project_element_ppm(
    inventory_mol: Mapping[str, float],
    buffer_oxygen_exchange_mol: float,
    elements: Sequence[str],
) -> dict[str, float]:
    total_mass_kg, element_mass_kg = _inventory_bulk_and_element_masses_kg(
        inventory_mol, buffer_oxygen_exchange_mol, elements
    )
    return {
        element: element_mass_kg[element] / total_mass_kg * 1.0e6
        for element in elements
    }


def _predict_sossi_residue_cohort(
    experiments: Sequence[Mapping[str, Any]],
    runtime_catalog: Mapping[str, Any],
    *,
    starting_trace_ppm_by_experiment: Mapping[str, Mapping[str, float]],
    buffered_fO2_log_by_experiment: Mapping[str, float],
    engine: str,
    selected_experiment_ids: frozenset[str] | None = None,
) -> tuple[_SossiResiduePrediction, ...]:
    """Predict Sossi Mn/Ti with the shared buffered inventory integrator."""
    if engine not in {"openimcc", "internal-analytical"}:
        raise ResidueInventoryRefusal("sossi_engine_unsupported", engine)
    from simulator.battery.oxygen_balance import _OXYGEN_GAS_ALPHA_SOURCE
    from simulator.condensation import alpha_s
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _openimcc_gas_channels_and_omission_notices,
    )
    from simulator.evaporation import _load_evaporation_alpha_by_species
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    legacy_catalog = vapor_pressure_legacy_view(runtime_catalog)
    catalog_rows = _residue_catalog_rows(legacy_catalog)
    alpha_specs = _load_evaporation_alpha_by_species(legacy_catalog)
    experiment_ids = {
        str(experiment.get("experiment_id") or "") for experiment in experiments
    }
    if selected_experiment_ids is not None and (
        not selected_experiment_ids
        or "" in experiment_ids
        or not selected_experiment_ids <= experiment_ids
    ):
        raise ResidueInventoryRefusal(
            "sossi_experiment_selection_invalid",
            "selected runs must be a non-empty subset of the supplied experiments",
        )
    engine_binding_identity: dict[str, str] | None = None
    if engine == "openimcc":
        try:
            import openimcc
            from openimcc import load_gas_datapack
            from openimcc.kernel import ImccNonconvergenceError
            from simulator.melt_backend import openimcc_bridge
        except ImportError as exc:
            raise ResidueInventoryRefusal("openimcc_not_importable", str(exc)) from exc
        gas_pack = load_gas_datapack()
        engine_binding_identity = openimcc_bridge.engine_binding_identity(
            openimcc_bridge._load_pack(
                openimcc_bridge.DEFAULT_BACKEND_PACK_NAME
            ),
            gas_pack,
        )
        raw_gas_channels, omissions = _openimcc_gas_channels_and_omission_notices(
            tuple(_SOSSI_TRACE_PARENTS.values()), gas_pack
        )
        gas_channels = [
            (str(species), str(reaction[0]), float(reaction[1]), float(reaction[2]))
            for species, reaction in raw_gas_channels
            if str(reaction[0]) in set(_SOSSI_TRACE_PARENTS.values())
        ]
    else:
        gas_pack = None
        omissions = ()
        gas_channels = []

    prediction_rows: list[_SossiResiduePrediction] = []
    for experiment in experiments:
        experiment_id = str(experiment.get("experiment_id") or "")
        if not experiment_id:
            raise ResidueInventoryRefusal("sossi_experiment_id_missing")
        if (
            selected_experiment_ids is not None
            and experiment_id not in selected_experiment_ids
        ):
            continue
        schedule = experiment.get("thermal_schedule", {})
        setpoints = schedule.get("setpoints_and_holds", ())
        if not setpoints or not isinstance(setpoints[0], Mapping):
            raise ResidueInventoryRefusal("sossi_temperature_missing")
        temperature_K = _hashimoto_point_value(
            setpoints[0].get("temperature_K", {})
        )
        duration_s = _hashimoto_point_value(
            schedule.get("total_duration_s", {})
        )
        sample = experiment.get("sample") or {}
        total_pressure_Pa = _hashimoto_point_value(
            experiment.get("pressure_environment", {}).get("total_pressure_Pa", {})
        )
        inventory, sample_mass_kg, initial_melt_mass_kg = _sossi_initial_inventory(
            experiment, starting_trace_ppm_by_experiment.get(experiment_id, {})
        )
        try:
            buffered_logp = float(buffered_fO2_log_by_experiment[experiment_id])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ResidueInventoryRefusal("oxygen_condition_missing", experiment_id) from exc
        if not math.isfinite(buffered_logp) or buffered_logp > 0.0:
            raise ResidueInventoryRefusal("oxygen_condition_invalid", experiment_id)

        selected_channels: list[tuple[str, str, float, str]] = []
        missing_elements: list[str] = []
        for element, parent in _SOSSI_TRACE_PARENTS.items():
            available = [
                item for item in gas_channels if item[1] == parent
            ] if engine == "openimcc" else [
                (str(row.get("formula")), parent, 1.0, 0.0)
                for _name, row in catalog_rows
                if row.get("formula")
                and row.get("parent_oxide") == parent
                and row.get("flux_dormant") is not True
            ]
            added = 0
            for species, channel_parent, _reaction_n, _reaction_o2 in available:
                matches = [
                    (name, row)
                    for name, row in catalog_rows
                    if str(row.get("formula")) == species
                    and row.get("parent_oxide") == channel_parent
                    and row.get("flux_dormant") is not True
                ]
                if not matches:
                    continue
                catalog_name, catalog_row = matches[0]
                alpha_spec = alpha_specs.get(catalog_name)
                if alpha_spec is None:
                    continue
                alpha_context: dict[str, Any] = {"coefficient_spec": alpha_spec}
                try:
                    alpha = float(alpha_s(catalog_name, temperature_K, alpha_context))
                except (TypeError, ValueError, ArithmeticError):
                    continue
                raw_alpha = catalog_row.get("evaporation_alpha")
                source = (
                    str(raw_alpha.get("source") or raw_alpha.get("cite") or catalog_name)
                    if isinstance(raw_alpha, Mapping)
                    else catalog_name
                )
                selected_channels.append((species, parent, alpha, f"{catalog_name}: {source}"))
                added += 1
            if added == 0:
                missing_elements.append(element)
        if len(missing_elements) == len(_SOSSI_TRACE_PARENTS):
            raise ResidueInventoryRefusal(
                "channel_missing", ",".join(missing_elements)
            )
        # A missing channel is a refusal for that element, never a retained
        # trace prediction. Remove its unsupported oxide from this engine's
        # starting inventory before evaluating the supported channel.
        for element in missing_elements:
            inventory.pop(_SOSSI_TRACE_PARENTS[element], None)
        channels = tuple(
            ResidueChannel(species, species, parent, alpha, source)
            for species, parent, alpha, source in selected_channels
        )
        pressure_cache: dict[tuple[tuple[str, float], ...], Mapping[str, float]] = {}
        melt_pack_identity: dict[str, str] = {}
        melt_activity_input_policy: dict[str, Any] = {}
        melt_activity_extensions: dict[str, dict[str, Any]] = {}
        if engine == "openimcc":
            from simulator.diagnostic_helpers.binary_pot_battery import (
                _ENGINE_OUTER_TIMEOUT_S,
            )

            pO2_exponents = {
                species: (1.0 if species == "O2" else -reaction_o2 / reaction_parent)
                for species, _parent, reaction_parent, reaction_o2 in gas_channels
            }
            mno_channel_active = any(
                parent == "MnO"
                for _species, parent, _reaction_parent, _reaction_o2 in gas_channels
            )

            def pressure_model(
                composition: Mapping[str, float], log10_pO2_bar: float
            ) -> Mapping[str, float]:
                key = tuple(sorted((str(k), float(v)) for k, v in composition.items()))
                base = pressure_cache.get(key)
                if base is None:
                    try:
                        activity_composition, activity_input_policy = (
                            openimcc_bridge._cleaned_melt_policy(composition)
                        )
                        melt_activity_input_policy.update(activity_input_policy)
                        timeout_s = _ENGINE_OUTER_TIMEOUT_S["openimcc"]
                        from simulator.engine_cache import cached_engine_call
                        from simulator.melt_backend.openimcc_bridge import (
                            OpenImccBridgeResult,
                        )

                        state_payload = cached_engine_call(
                            "openimcc",
                            openimcc_bridge._evaluate_request_vector(
                                composition_mol=activity_composition,
                                temperature_K=temperature_K,
                                allow_extrapolation=True,
                                allow_out_of_envelope=True,
                            ),
                            lambda: _evaluate_openimcc_isolated(
                                openimcc_bridge._evaluate_uncached,
                                timeout_s=timeout_s,
                                composition_mol=activity_composition,
                                temperature_K=temperature_K,
                                allow_extrapolation=True,
                                allow_out_of_envelope=True,
                            ).to_hot(),
                        )
                        state = OpenImccBridgeResult.from_hot(state_payload)
                    except ImccNonconvergenceError as exc:
                        raise ResidueEngineNonconvergence("openimcc", str(exc)) from exc
                    except TimeoutError as exc:
                        raise ResidueEngineNonconvergence(
                            "openimcc",
                            f"Sossi residue cohort OpenIMCC timeout: {exc}",
                            reason="engine_timeout:openimcc:sossi_residue_cohort",
                        ) from exc
                    gas_parent_oxide_activities = dict(
                        state.parent_oxide_activities
                    )
                    gas_parent_oxides = tuple(state.parent_oxides)
                    if mno_channel_active:
                        from simulator.chemistry.melt_activity import (
                            melt_oxide_activity,
                        )

                        mno_activity = melt_oxide_activity(
                            "MnO", composition, temperature_K=temperature_K
                        )
                        if mno_activity is None:
                            raise ResidueInventoryRefusal(
                                "melt_activity_unavailable",
                                "OpenIMCC Mn gas channel needs a caller-supplied MnO activity",
                            )
                        mno_parent_activity = (
                            mno_activity.thermodynamic_parent_activity()
                        )
                        if (
                            not math.isfinite(mno_parent_activity)
                            or mno_parent_activity < 0.0
                        ):
                            raise ResidueInventoryRefusal(
                                "melt_activity_invalid",
                                f"MnO activity extension returned {mno_parent_activity!r}",
                            )
                        gas_parent_oxide_activities["MnO"] = mno_parent_activity
                        gas_parent_oxides += ("MnO",)
                        melt_activity_extensions.setdefault(
                            "MnO",
                            {
                                "reason": (
                                    "MnO is outside the IMCC-SF04 melt-activity domain; "
                                    "the simulator constant-gamma extension supplies the "
                                    "activity required by the new Mn gas channel"
                                ),
                                "authority_level": mno_activity.evidence_tier,
                                "certified_band_K": mno_activity.valid_range_K,
                                "activity_provenance": mno_activity.provenance(),
                            },
                        )
                    gas_result = openimcc_bridge.evaluate_gas(
                        gas_parent_oxide_activities,
                        temperature_K,
                        1.0,
                        gas_pack,
                        parent_oxides=gas_parent_oxides,
                        allow_extrapolation=True,
                        gas_pack_selection=(
                            openimcc_bridge.DEFAULT_GAS_PACK_SELECTION
                        ),
                    )
                    melt_pack_identity.update(
                        {
                            "model_id": str(state.pack_model_id),
                            "datapack_version": str(state.pack_version),
                            "pack_digest": str(state.pack_digest),
                            "openimcc_version": str(state.openimcc_version),
                        }
                    )
                    base = dict(gas_result)
                    pressure_cache[key] = base
                pO2_bar = 10.0**log10_pO2_bar
                return {
                    species: float(base.get(species, 0.0))
                    * pO2_bar ** pO2_exponents[species]
                    * PA_PER_BAR
                    for species, _parent, _n, _o2 in gas_channels
                    if species in pO2_exponents
                }
        else:
            from simulator.diagnostic_helpers.binary_pot_battery import (
                PO2_COMMANDED,
                Po2Request,
                internal_analytical_residue_vapor_pressure,
                _new_internal_analytical_core,
            )

            analytical_core = _new_internal_analytical_core()

            def pressure_model(
                composition: Mapping[str, float], log10_pO2_bar: float
            ) -> Mapping[str, float]:
                response = internal_analytical_residue_vapor_pressure(
                    core=analytical_core,
                    temperature_C=temperature_K - 273.15,
                    pressure_bar=total_pressure_Pa / PA_PER_BAR,
                    composition_kg=None,
                    composition_mol=composition,
                    fO2_log=log10_pO2_bar,
                    po2_request=Po2Request(
                        mode=PO2_COMMANDED,
                        po2_bar=10.0**log10_pO2_bar,
                    ),
                    include_diagnostic_shadows=False,
                )
                return response.vapor_pressures_Pa

        try:
            initial_pressures = pressure_model(inventory, buffered_logp)
        except ResidueEngineNonconvergence as exc:
            prediction_rows.append(
                _SossiResiduePrediction(
                    experiment_id=experiment_id,
                    primary_element_ppm={},
                    geometry_element_ppm={},
                    sensitivity_band_ppm={},
                    channel_missing_elements=tuple(missing_elements),
                    provenance={
                        "source_id": _SOSSI_SOURCE_ID,
                        "experiment_id": experiment_id,
                        "engine": engine,
                        "run_refusal": {
                            **exc.as_record(),
                            "cohort": "sossi_residue_cohort",
                        },
                    },
                )
            )
            continue
        pressure_species = {
            species
            for species, pressure in initial_pressures.items()
            if math.isfinite(float(pressure)) and float(pressure) >= 0.0
        }
        channels = tuple(
            channel for channel in channels if channel.species in pressure_species
        )
        for element, parent in _SOSSI_TRACE_PARENTS.items():
            if not any(channel.parent_oxide == parent for channel in channels):
                if element not in missing_elements:
                    missing_elements.append(element)
                inventory.pop(parent, None)
        if not channels:
            raise ResidueInventoryRefusal(
                "channel_missing", ",".join(missing_elements)
            )
        supported_elements = tuple(
            element for element in _SOSSI_TRACE_PARENTS if element not in missing_elements
        )
        if not supported_elements:
            raise ResidueInventoryRefusal(
                "channel_missing", ",".join(missing_elements)
            )
        initial_melt_mass_kg = math.fsum(
            amount * _formula_terms(oxide)[1]
            for oxide, amount in inventory.items()
        )
        liquid_rows_used = (
            _hashimoto_liquid_row_provenance(
                gas_pack,
                tuple(
                    (channel.species, (str(channel.parent_oxide), 1.0, 0.0))
                    for channel in channels
                ),
                temperature_K,
            )
            if engine == "openimcc"
            else ()
        )

        areas = _sossi_geometry_areas(sample_mass_kg)
        steps = min(_HASHIMOTO_N_CAP, max(8, math.ceil(duration_s / 60.0)))
        geometry_predictions: dict[str, dict[str, float]] = {}
        geometry_inventory_mol: dict[str, dict[str, float]] = {}
        geometry_buffer_exchange: dict[str, float] = {}
        geometry_refinement: dict[str, dict[str, Any]] = {}
        run_refusal: ResidueEngineNonconvergence | None = None
        for geometry in _SOSSI_GEOMETRIES:
            steps = min(_HASHIMOTO_N_CAP, max(8, math.ceil(duration_s / 60.0)))
            previous: dict[str, float] | None = None
            accepted: dict[str, float] = {}
            tried: list[int] = []
            differences: list[float] = []
            buffer_exchange = 0.0
            while True:
                current_inventory = dict(inventory)
                buffer_exchange = 0.0
                initial_total_mass = initial_melt_mass_kg
                dt = duration_s / steps
                for _step in range(steps):
                    current_mass = math.fsum(
                        amount * _formula_terms(oxide)[1]
                        for oxide, amount in current_inventory.items()
                    ) + min(0.0, buffer_exchange) * _formula_terms("O")[1]
                    if geometry == "pt_loop_bead_sphere_shrinking":
                        area = _self_similar_area_m2(
                            areas[geometry], current_mass, initial_total_mass
                        )
                    else:
                        area = areas[geometry]
                    try:
                        step_result = integrate_residue_inventory(
                            current_inventory,
                            channels,
                            pressure_model,
                            temperature_K=temperature_K,
                            duration_s=dt,
                            area_evolution_m2=(area,),
                            buffered_fO2_log=buffered_logp,
                        )
                    except ResidueEngineNonconvergence as exc:
                        run_refusal = exc
                        break
                    current_inventory = dict(step_result.residue_mol)
                    buffer_exchange += step_result.buffer_oxygen_exchange_mol
                if run_refusal is not None:
                    break
                accepted = _sossi_project_element_ppm(
                    current_inventory, buffer_exchange, supported_elements
                )
                tried.append(steps)
                if previous is not None:
                    difference = max(
                        abs(math.log10(accepted[element] / previous[element]))
                        for element in supported_elements
                        if accepted[element] > 0.0 and previous[element] > 0.0
                    )
                    differences.append(difference)
                    if difference < 0.05:
                        break
                if steps >= _HASHIMOTO_N_CAP:
                    break
                previous = accepted
                steps = min(_HASHIMOTO_N_CAP, steps * 2)
            if run_refusal is not None:
                break
            geometry_predictions[geometry] = accepted
            geometry_inventory_mol[geometry] = dict(current_inventory)
            geometry_buffer_exchange[geometry] = buffer_exchange
            geometry_refinement[geometry] = {
                "steps_tried": tried,
                "max_difference_dex": differences,
                "status": (
                    "converged"
                    if differences and differences[-1] < 0.05
                    else "unconverged_at_cap"
                ),
            }

        if run_refusal is not None:
            prediction_rows.append(
                _SossiResiduePrediction(
                    experiment_id=experiment_id,
                    primary_element_ppm={},
                    geometry_element_ppm=geometry_predictions,
                    sensitivity_band_ppm={},
                    channel_missing_elements=tuple(missing_elements),
                    provenance={
                        "source_id": _SOSSI_SOURCE_ID,
                        "experiment_id": experiment_id,
                        "engine": engine,
                        "run_refusal": {
                            **run_refusal.as_record(),
                            "cohort": "sossi_residue_cohort",
                        },
                    },
                )
            )
            continue

        bands = {
            element: (
                min(row[element] for row in geometry_predictions.values()),
                max(row[element] for row in geometry_predictions.values()),
            )
            for element in supported_elements
        }
        primary_inventory = geometry_inventory_mol[_SOSSI_PRIMARY_GEOMETRY]
        primary_buffer_exchange = geometry_buffer_exchange[_SOSSI_PRIMARY_GEOMETRY]
        printed_composition = sample.get("printed_composition", {})
        prediction_rows.append(
            _SossiResiduePrediction(
                experiment_id=experiment_id,
                primary_element_ppm=dict(geometry_predictions[_SOSSI_PRIMARY_GEOMETRY]),
                geometry_element_ppm=geometry_predictions,
                sensitivity_band_ppm=bands,
                channel_missing_elements=tuple(missing_elements),
                provenance={
                    "source_id": _SOSSI_SOURCE_ID,
                    "experiment_id": experiment_id,
                    "engine": engine,
                    "sample_mass_kg": sample_mass_kg,
                    "total_pressure_Pa": total_pressure_Pa,
                    "temperature_K": temperature_K,
                    "duration_s": duration_s,
                    "buffered_fO2_log": buffered_logp,
                    "buffered_boundary": "printed log10 fO2; reservoir oxygen exchange included",
                    "buffer_oxygen_exchange_mol_by_geometry": geometry_buffer_exchange,
                    "host_composition_wt_pct": dict(
                        printed_composition.get("state", {}).get("value", {})
                    ),
                    "host_composition_source": printed_composition.get("locator"),
                    "apparatus_source": (experiment.get("apparatus") or {}).get(
                        "cell_material_and_liner", {}
                    ).get("locator"),
                    "geometry_model_note": (
                        "Pt wire-loop bead from the printed apparatus; spherical bead, "
                        "shrinking sphere, and equal-volume disk are declared sensitivity assumptions"
                    ),
                    "starting_trace_ppm": dict(
                        starting_trace_ppm_by_experiment.get(experiment_id, {})
                    ),
                    "starting_trace_basis": "element ppm on the printed approximately 25 mg chemical basis",
                    "geometry_policy_id": _SOSSI_PRIMARY_GEOMETRY,
                    "geometry_areas_m2": areas,
                    "integrated_inventory_mol": primary_inventory,
                    "initial_inventory_mol": dict(inventory),
                    "area_m2_initial_by_geometry": areas,
                    "area_evolution_by_geometry": {
                        _SOSSI_GEOMETRIES[0]: "constant sphere area",
                        _SOSSI_GEOMETRIES[1]: "sphere area scales with remaining mass to 2/3",
                        _SOSSI_GEOMETRIES[2]: "constant equal-volume disk area",
                    },
                    "density_kg_m3": _HASHIMOTO_DENSITY_KG_M3,
                    "density_source": _SOSSI_DENSITY_SOURCE,
                    "alpha_arm": "alpha_runtime_catalog",
                    "alpha_by_channel": [
                        {
                            "species": channel.species,
                            "parent_oxide": channel.parent_oxide,
                            "alpha_value": channel.alpha,
                            "alpha_assumed": True,
                            "alpha_source": channel.alpha_source,
                        }
                        for channel in channels
                    ],
                    "geometry_sensitivity_band_ppm": bands,
                    "integration": geometry_refinement,
                    "gas_channel_omissions": omissions,
                    "channel_missing_elements": tuple(missing_elements),
                    **(
                        {"engine_binding_identity": engine_binding_identity}
                        if engine_binding_identity is not None
                        else {}
                    ),
                    "liquid_rows_used": liquid_rows_used,
                    "melt_pack_identity": dict(melt_pack_identity),
                    **(
                        {"melt_activity_input_policy": dict(melt_activity_input_policy)}
                        if melt_activity_input_policy
                        else {}
                    ),
                    **(
                        {"melt_activity_extensions": dict(melt_activity_extensions)}
                        if melt_activity_extensions
                        else {}
                    ),
                    "assumption_flags": {
                        "pt_wire_loop_bead_geometry_assumed": True,
                        "density_fallback_2700_kg_m3": True,
                        "bc_open_furnace_langmuir_limit_diagnostic": True,
                    },
                },
            )
        )
    return tuple(prediction_rows)
