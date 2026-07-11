"""Synchronous command core for one simulator session."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Callable, Iterable, Mapping

from simulator.backend_names import canonical_backend_name
from simulator.backends import (
    BackendSelectionPolicy,
    CACHED_REAL_BACKEND_NAME,
    SimulatorBuildConfig,
    assert_real_backend_feedstock_supported,
    assert_stage0_subprocess_backend_safe,
    build_simulator,
    build_cached_real_store,
    normalize_cached_real_config,
    requires_stage0_subprocess,
    resolve_backend,
)
from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.feedstock_guard import BlockedFeedstockError, assert_feedstock_loadable
from simulator.lab_schedule import LAB_SCHEDULE_OVERRIDE_KEY, normalize_lab_schedule
from simulator.state import (
    DecisionPoint,
    DecisionType,
    HourSnapshot,
    clamp_stir_factor,
    clamp_stir_state,
)


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _canonical_runtime_campaign_overrides(
    *,
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] | None,
    setpoints_overrides: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if (
        runtime_campaign_overrides is not None
        and setpoints_overrides is not None
        and dict(runtime_campaign_overrides) != dict(setpoints_overrides)
    ):
        raise ValueError(
            "runtime_campaign_overrides conflicts with deprecated "
            "setpoints_overrides alias"
        )
    source = (
        runtime_campaign_overrides
        if runtime_campaign_overrides is not None
        else setpoints_overrides
    )
    if source is None:
        return {}
    return {str(campaign): dict(fields) for campaign, fields in source.items()}


def _has_runtime_surface_temperature_schedule(
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]],
) -> bool:
    for fields in runtime_campaign_overrides.values():
        if not isinstance(fields, Mapping):
            continue
        raw_schedule = fields.get(LAB_SCHEDULE_OVERRIDE_KEY)
        if not isinstance(raw_schedule, Mapping):
            continue
        surface_temperatures = raw_schedule.get("surface_temperature_C")
        if isinstance(surface_temperatures, Mapping) and bool(surface_temperatures):
            return True
    return False


def normalize_mre_policy(
    c5_enabled: bool,
    mre_target_species: str,
    mre_max_voltage_V: float,
) -> tuple[bool, str, float]:
    enabled = bool(c5_enabled)
    if not enabled:
        return False, "", 0.0
    return (
        True,
        str(mre_target_species or "").strip(),
        float(mre_max_voltage_V or 0.0),
    )


class InvalidDecisionChoiceError(ValueError):
    """Raised when a session refuses a choice outside the pending options."""

    def __init__(
        self,
        decision_type: DecisionType,
        choice: str,
        valid_choices: tuple[str, ...],
    ) -> None:
        self.decision_type = decision_type
        self.choice = choice
        self.valid_choices = valid_choices
        super().__init__(
            f"invalid {decision_type.name} choice {choice!r}; "
            f"expected one of {list(valid_choices)!r}"
        )


class DecisionPolicy(Enum):
    """Driver-loop decision routing mode.

    ``advance()`` does not consult this enum. It is policy-free; drivers decide
    whether a pending decision should be applied or surfaced to an operator.
    """

    AUTO_APPLY = "auto-apply"
    OPERATOR = "operator"


@dataclass(frozen=True)
class SimSessionConfig:
    """Inputs required to start a simulator session."""

    feedstock_id: str
    feedstocks: Mapping[str, Any]
    setpoints: Mapping[str, Any]
    vapor_pressures: Mapping[str, Any]
    materials: Mapping[str, Any] | None = None
    campaign: str = "C0"
    backend_name: str = "stub"
    backend_policy: BackendSelectionPolicy = BackendSelectionPolicy.RUNNER_STRICT
    hours: int = 0
    mass_kg: float = 1000.0
    additives_kg: Mapping[str, float] = field(default_factory=dict)
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] | None = None
    setpoints_overrides: Mapping[str, Mapping[str, Any]] | None = None
    track: str = "pyrolysis"
    c4_max_temp: float | None = None
    c5_enabled: bool = False
    stop_at_stage0_exit: bool = False
    mre_target_species: str = ""
    mre_max_voltage_V: float = 0.0
    unavailable_error_cls: type[Exception] = RuntimeError
    force_builtin_vapor_pressure: Callable[[PyrolysisSimulator], None] | None = None
    result_document_factory: Callable[["SimSession"], Mapping[str, Any]] | None = None
    reduced_real_cache: Mapping[str, Any] | None = None
    backend_config: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        # Fold the `internal-analytical` display alias onto the stable `stub`
        # token so session config, serialized metadata, and the fidelity-
        # vocabulary backend-token translator all see the legacy token.
        object.__setattr__(
            self, "backend_name", canonical_backend_name(self.backend_name)
        )
        overrides = _canonical_runtime_campaign_overrides(
            runtime_campaign_overrides=self.runtime_campaign_overrides,
            setpoints_overrides=self.setpoints_overrides,
        )
        object.__setattr__(self, "runtime_campaign_overrides", overrides)
        object.__setattr__(self, "setpoints_overrides", overrides)


@dataclass(frozen=True)
class StepResult:
    """Read-only projections captured immediately after one simulator step."""

    snapshot: HourSnapshot
    per_hour_summary: dict[str, Any]
    campaign_summary: dict[str, Any] | None = None
    decision_event: dict[str, Any] | None = None
    backend_error: str = ""


class SimSession:
    """Lock-free, synchronous wrapper around ``PyrolysisSimulator``.

    The web adapter owns locking and pacing. This core only performs command
    verbs against a single simulator instance.
    """

    def __init__(self) -> None:
        self._sim: PyrolysisSimulator | None = None
        self._config: SimSessionConfig | None = None
        self._paused = False
        self._step_results: list[StepResult] = []
        self._operator_decisions: list[dict[str, Any]] = []
        self._result_document: Mapping[str, Any] | None = None

    @property
    def simulator(self) -> PyrolysisSimulator:
        return self._require_sim()

    def start(
        self,
        config: SimSessionConfig,
        *,
        backend: Any | None = None,
    ) -> "SimSession":
        """Build, load, and start a simulator from explicit config."""

        cached_real_config = None
        if config.backend_name == CACHED_REAL_BACKEND_NAME:
            cached_real_config = normalize_cached_real_config(
                config.reduced_real_cache,
                unavailable_error_cls=config.unavailable_error_cls,
            )
        elif config.reduced_real_cache is not None:
            raise config.unavailable_error_cls(
                "reduced_real_cache is only valid with backend_name='cached-real'"
            )
        stage0_subprocess_required = requires_stage0_subprocess(
            config.feedstock_id,
            config.feedstocks,
        )
        if config.feedstock_id in config.feedstocks:
            try:
                assert_feedstock_loadable(
                    config.feedstock_id,
                    config.feedstocks[config.feedstock_id],
                )
            except BlockedFeedstockError as exc:
                raise config.unavailable_error_cls(
                    f"BlockedFeedstockError: {exc}"
                ) from exc
            assert_real_backend_feedstock_supported(
                config.backend_name,
                config.feedstock_id,
                config.feedstocks,
                unavailable_error_cls=config.unavailable_error_cls,
            )
        if backend is None:
            backend = resolve_backend(
                config.backend_name,
                config.backend_policy,
                unavailable_error_cls=config.unavailable_error_cls,
                cached_real_config=cached_real_config,
                backend_config=config.backend_config,
                feedstock_id=config.feedstock_id,
                feedstocks=config.feedstocks,
                stage0_subprocess_required=stage0_subprocess_required,
            )
        else:
            assert_stage0_subprocess_backend_safe(
                backend,
                subprocess_required=stage0_subprocess_required,
                unavailable_error_cls=config.unavailable_error_cls,
            )
        if config.feedstock_id not in config.feedstocks:
            expected = sorted(config.feedstocks)[:5]
            raise config.unavailable_error_cls(
                f"unknown feedstock {config.feedstock_id!r}; expected one of "
                f"{expected}..."
            )

        sim = build_simulator(
            SimulatorBuildConfig(
                backend=backend,
                setpoints=config.setpoints,
                feedstocks=config.feedstocks,
                vapor_pressures=config.vapor_pressures,
                materials=config.materials,
                allow_lab_geometry_temperature_profiles=(
                    _has_runtime_surface_temperature_schedule(
                        config.runtime_campaign_overrides
                    )
                ),
            )
        )
        if cached_real_config is not None:
            sim.configure_pt0_determinism_store(
                build_cached_real_store(cached_real_config)
            )

        try:
            sim.load_batch(
                config.feedstock_id,
                config.mass_kg,
                additives_kg=dict(config.additives_kg),
            )
        except ValueError as exc:
            raise config.unavailable_error_cls(
                f"load_batch failed: {exc}"
            ) from exc

        if config.force_builtin_vapor_pressure is not None:
            config.force_builtin_vapor_pressure(sim)

        if config.c4_max_temp is not None:
            sim.c4_max_temp_C = float(config.c4_max_temp)
            sim.campaign_mgr.c4_max_temp_C = float(config.c4_max_temp)
        c5_enabled, mre_target_species, mre_max_voltage_V = normalize_mre_policy(
            config.c5_enabled,
            config.mre_target_species,
            config.mre_max_voltage_V,
        )
        sim.melt.c5_enabled = c5_enabled
        sim.melt.mre_target_species = mre_target_species
        sim.melt.mre_max_voltage_V = mre_max_voltage_V
        sim.campaign_mgr.c5_enabled = sim.melt.c5_enabled

        campaign_phase = self._campaign_phase(config.campaign, config)
        if config.track == "mre_baseline":
            sim.record.track = "mre_baseline"

        for campaign, overrides in config.runtime_campaign_overrides.items():
            if not isinstance(overrides, Mapping):
                raise config.unavailable_error_cls(
                    f"runtime_campaign_overrides[{campaign!r}] must be a mapping"
                )
            target = sim.campaign_mgr.overrides.setdefault(str(campaign), {})
            for field_name, value in overrides.items():
                if str(field_name) == LAB_SCHEDULE_OVERRIDE_KEY:
                    target[str(field_name)] = normalize_lab_schedule(value)
                else:
                    target[str(field_name)] = _finite_float(
                        value,
                        f"runtime_campaign_overrides[{campaign!r}].{field_name}",
                    )

        # Retain the loaded simulator before campaign configuration so a typed
        # startup refusal can still be projected through the runner envelope.
        self._sim = sim
        self._config = config
        self._paused = False
        self._step_results = []
        self._operator_decisions = []
        self._result_document = None

        sim.start_campaign(campaign_phase)
        sim.validate_lab_surface_temperature_resolver()
        return self

    def advance(self) -> StepResult:
        """Run exactly one policy-free simulator step."""

        sim = self._require_sim()
        snapshot = sim.step()
        campaign_summary = getattr(sim, "_last_campaign_summary", None)
        backend_error = str(getattr(sim, "_last_backend_error", "") or "")
        sim._last_campaign_summary = None
        decision = (
            sim.pending_decision
            if getattr(sim, "paused_for_decision", False)
            and sim.pending_decision is not None
            else None
        )
        result = StepResult(
            snapshot=snapshot,
            per_hour_summary=self._build_per_hour_summary(sim, snapshot),
            campaign_summary=campaign_summary,
            decision_event=_decision_event(decision) if decision else None,
            backend_error=backend_error,
        )
        self._step_results.append(result)
        return result

    def decide(self, choice: str) -> None:
        sim = self._require_sim()
        decision = sim.pending_decision
        if decision is None:
            raise RuntimeError("no pending decision")
        valid_choices = tuple(str(option) for option in decision.options)
        if choice not in valid_choices:
            raise InvalidDecisionChoiceError(
                decision.decision_type,
                choice,
                valid_choices,
            )
        sim.apply_decision(decision.decision_type, choice)

    def pending_decision(self) -> DecisionPoint | None:
        return self._require_sim().pending_decision

    def adjust(self, param: str, value: Any, **kw: Any) -> None:
        sim = self._require_sim()
        if param == "stir_factor":
            # 0.5.2 Phase B P1: clamp at the operator boundary so both
            # consumer subsystems (evaporation linear multiplier +
            # condensation series-resistance Sherwood) see the same
            # bounded value.
            #
            # 0.5.3 Phase B (2-axis stirring): the legacy scalar
            # ``stir_factor`` writes ONLY the axial axis (operator
            # signalled a single-axis intent). The radial axis is
            # untouched and stays at its current value. Use
            # ``adjust("stir_state", {axial, radial})`` to drive both
            # axes; this scalar path is preserved for backward-compat
            # with pre-0.5.3 web UIs and campaign auto-tuners.
            sim.melt.stir_state.axial = clamp_stir_factor(value)
        elif param == "stir_state":
            # 0.5.3 Phase B: canonical 2-axis writer. Accepts a dict
            # ({axial, radial}), an existing ``StirState``, or — for
            # convenience — a scalar that maps to axial-only (same
            # semantics as the legacy ``stir_factor`` path). Both axes
            # go through ``clamp_stir_state`` so the operator-boundary
            # contract carries component-wise (per-axis clamp,
            # non-finite/bool fail-closed, partial dict defaults to
            # 1.0 on the missing axis). Replaces the whole
            # ``melt.stir_state`` instance — operator-facing intent is
            # "set the stirring state to this", not "merge".
            sim.melt.stir_state = clamp_stir_state(value)
        elif param == "pO2_mbar":
            # 0.5.3 Phase C milestone review P2 (codex 2026-05-28):
            # commanding a positive pO2 under PN2_SWEEP or HARD_VACUUM
            # is a no-op for SiO suppression because the commanded-pO2
            # floor in equilibrium.py / overhead.py only fires in the
            # _O2_CONTROLLED_ATMOSPHERES family. Mirroring the wall-sweep
            # CLI's Phase A P2 fix: when the operator commands a
            # positive pO2 via session.adjust("pO2_mbar", x>0), also
            # switch melt.atmosphere to CONTROLLED_O2 so the
            # 1/sqrt(pO2) Ellingham SiO suppression becomes live. A
            # value of 0 leaves the atmosphere alone (operator clearing
            # the setpoint, NOT requesting controlled-O2).
            new_pO2 = _finite_float(value, "pO2_mbar")
            sim.melt.pO2_mbar = new_pO2
            sim.melt.p_total_mbar = max(
                sim.melt.p_total_mbar,
                sim.melt.pO2_mbar,
            )
            if new_pO2 > 0.0:
                # Only import locally to avoid a top-level cycle.
                from simulator.state import Atmosphere
                sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        elif param == "c4_max_temp":
            finite_value = _finite_float(value, "c4_max_temp")
            sim.c4_max_temp_C = finite_value
            sim.campaign_mgr.c4_max_temp_C = finite_value
        elif param == "campaign_override":
            campaign_name = str(kw.get("campaign", ""))
            field_name = str(kw.get("field", ""))
            if not campaign_name or not field_name:
                raise ValueError(
                    "campaign_override requires campaign and field keywords"
                )
            target = sim.campaign_mgr.overrides.setdefault(campaign_name, {})
            if field_name == "stir_factor":
                # 0.5.2 Phase B codex autoreview-r2 P3: route the
                # campaign_override write through ``clamp_stir_factor``
                # BEFORE the float() coercion below, otherwise
                # ``True``/``False`` silently become ``1.0``/``0.0``
                # (lying bool→float) and ``"bad"`` raises ValueError
                # here instead of taking the fail-closed defensive
                # path. The overrides dict carries the canonical
                # clamped value so any re-entry path applies the
                # operator-bounded contract.
                target[field_name] = clamp_stir_factor(value)
                if sim.melt.campaign.name == campaign_name:
                    # Live-update the melt field too if this override
                    # targets the currently-active campaign. The
                    # legacy ``stir_factor`` write touches AXIAL only
                    # (via the backward-compat property setter on
                    # MeltState).
                    sim.melt.stir_factor = target[field_name]
            elif field_name == "stir_state":
                # 0.5.3 Phase B: canonical 2-axis campaign override.
                # Stored as a StirState dataclass in the overrides
                # dict so any re-entry path (e.g.
                # ``CampaignManager._apply_overrides``) sees the
                # clamped 2-axis value rather than a scalar that the
                # legacy path would silently mis-route. Live-update
                # the melt too if the override targets the active
                # campaign.
                clamped = clamp_stir_state(value)
                target[field_name] = clamped
                if sim.melt.campaign.name == campaign_name:
                    sim.melt.stir_state = clamped
            else:
                target[field_name] = _finite_float(
                    value,
                    f"campaign_override {campaign_name}.{field_name}",
                )
            if field_name == "pO2_mbar" and sim.melt.campaign.name == campaign_name:
                # 0.5.4 W5 (post-push P2 convergent finding, codex
                # review + codex challenge 2026-05-28): mirror the
                # direct-adjust ``"pO2_mbar"`` atmosphere-switch fix
                # on this campaign-override write path. Pre-W5 a
                # ``session.adjust("campaign_override",
                # campaign="C2A", field="pO2_mbar", value=1.0)``
                # call wrote the melt setpoint but left
                # ``melt.atmosphere`` in (e.g.) ``PN2_SWEEP``, so the
                # commanded-pO2 floor never fired under
                # finite-headspace ON (only triggers in
                # ``_O2_CONTROLLED_ATMOSPHERES``). Now: when the
                # operator commands a positive pO2 via the campaign-
                # override write path, also switch atmosphere to
                # CONTROLLED_O2 so the 1/sqrt(pO2) Ellingham SiO
                # suppression becomes live. ``value == 0`` leaves
                # atmosphere alone (clearing the setpoint, NOT
                # requesting controlled-O2).
                new_pO2 = target[field_name]
                sim.melt.pO2_mbar = new_pO2
                sim.melt.p_total_mbar = max(
                    sim.melt.p_total_mbar,
                    sim.melt.pO2_mbar,
                )
                if new_pO2 > 0.0:
                    from simulator.state import Atmosphere
                    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        else:
            raise ValueError(f"unsupported session adjustment {param!r}")
        sim.melt.validate_melt_pressures()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def is_complete(self) -> bool:
        return self._require_sim().is_complete()

    def snapshot(self) -> HourSnapshot:
        return self._require_sim()._make_snapshot()

    def result_document(self) -> Mapping[str, Any]:
        if self._result_document is not None:
            return self._result_document
        if self._config and self._config.result_document_factory is not None:
            return self._config.result_document_factory(self)
        raise RuntimeError("no result document has been recorded")

    def per_hour_summaries(self) -> list[dict[str, Any]]:
        return [result.per_hour_summary for result in self._step_results]

    def operator_decisions(self) -> list[dict[str, Any]]:
        return list(self._operator_decisions)

    def _record_operator_decision(self, event: dict[str, Any]) -> None:
        self._operator_decisions.append(event)

    def _set_result_document(self, document: Mapping[str, Any]) -> None:
        self._result_document = document

    def _require_sim(self) -> PyrolysisSimulator:
        if self._sim is None:
            raise RuntimeError("session has not been started")
        return self._sim

    def _campaign_phase(
        self,
        campaign_name: str,
        config: SimSessionConfig,
    ) -> CampaignPhase:
        aliases = {
            "C0b_p_cleanup": "C0B",
            "C2A_continuous": "C2A",
            "C2A_staged": "C2A_STAGED",
        }
        campaign_name = aliases.get(campaign_name, campaign_name)
        try:
            return CampaignPhase[campaign_name]
        except KeyError as exc:
            valid = ", ".join(member.name for member in CampaignPhase)
            raise config.unavailable_error_cls(
                f"unknown campaign {campaign_name!r}; valid options: {valid}"
            ) from exc

    @staticmethod
    def _build_per_hour_summary(
        sim: PyrolysisSimulator,
        snapshot: HourSnapshot,
    ) -> dict[str, Any]:
        from simulator.runner import build_per_hour_summary

        return build_per_hour_summary(sim, snapshot)


def drive_auto_apply(
    session: SimSession,
    hours: int,
    *,
    operator_decisions: list[dict[str, Any]] | None = None,
    stop_at_stage0_exit: bool = False,
) -> Iterable[StepResult]:
    """AUTO_APPLY driver loop for batch-runner surfaces."""

    return drive_session(
        session,
        hours,
        DecisionPolicy.AUTO_APPLY,
        operator_decisions=operator_decisions,
        stop_at_stage0_exit=stop_at_stage0_exit,
    )


def drive_session(
    session: SimSession,
    hours: int,
    policy: DecisionPolicy,
    *,
    operator_decisions: list[dict[str, Any]] | None = None,
    stop_at_stage0_exit: bool = False,
) -> Iterable[StepResult]:
    """Drive a session under a policy outside ``advance()``."""

    for _ in range(int(hours)):
        if stop_at_stage0_exit and _at_stage0_exit(session):
            return
        if session.is_complete():
            return
        decision = session.pending_decision()
        if stop_at_stage0_exit and _stage0_exit_decision(session):
            return
        if decision is not None and policy is DecisionPolicy.OPERATOR:
            return
        if decision is not None and policy is DecisionPolicy.AUTO_APPLY:
            choice = decision.recommendation or (
                decision.options[0] if decision.options else ""
            )
            event = _operator_decision_event(session.simulator, decision, choice)
            session._record_operator_decision(event)
            if operator_decisions is not None:
                operator_decisions.append(event)
            session.decide(choice)
            if stop_at_stage0_exit and _at_stage0_exit(session):
                return
            if session.is_complete():
                return
        elif decision is not None:
            raise ValueError(f"unsupported decision policy {policy!r}")
        result = session.advance()
        yield result
        if stop_at_stage0_exit and _at_stage0_exit(session):
            return


def _at_stage0_exit(session: SimSession) -> bool:
    if _stage0_exit_decision(session):
        return True
    return session.simulator.melt.campaign not in (
        CampaignPhase.C0,
        CampaignPhase.C0B,
    )


def _stage0_exit_decision(session: SimSession) -> bool:
    decision = session.pending_decision()
    return (
        decision is not None
        and decision.decision_type is DecisionType.PATH_AB
        and session.simulator.melt.campaign is CampaignPhase.C0B
    )


def _decision_event(decision: DecisionPoint) -> dict[str, Any]:
    return {
        "type": decision.decision_type.name,
        "options": list(decision.options),
        "recommendation": decision.recommendation,
        "context": decision.context,
    }


def _operator_decision_event(
    sim: PyrolysisSimulator,
    decision: DecisionPoint,
    choice: str,
) -> dict[str, Any]:
    return {
        "event": "operator_decision",
        "hour": sim.melt.hour,
        "decision_type": decision.decision_type.name,
        "choice": choice,
        "recommendation": decision.recommendation,
        "options": list(decision.options),
        "context": decision.context,
    }
