"""Import-isolated Phase A fouling lifecycle harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from simulator.coating_lifespan import (
    CampaignsToResinterTotal,
    FoulingProjectionError,
    FoulingTerminalSnapshot,
    LifecycleProjection,
    NestedDeposit,
    campaigns_to_resinter_total,
    merge_run_snapshot,
    project_lifecycle,
)


MASS_BALANCE_CLOSURE_LIMIT_PCT = 5.0e-12


@dataclass(frozen=True)
class FoulingRunArtifact:
    """Completed run surfaces consumed by the lifecycle overlay."""

    trace: Any
    result_document: Mapping[str, Any] | None = None
    simulator: Any | None = None
    snapshots: Sequence[Any] = ()
    c4b_binding_substrate_state: Mapping[str, Any] | None = None
    campaigns_to_resinter_total: CampaignsToResinterTotal | Mapping[str, Any] | None = None
    mass_balance_error_pct: float | None = None


@dataclass(frozen=True)
class FoulingLifecycleRunRecord:
    campaign_index: int
    snapshot: FoulingTerminalSnapshot
    cumulative_snapshot: FoulingTerminalSnapshot
    per_run_net_deposit_by_segment_species_kg: NestedDeposit
    campaigns_to_resinter_total: CampaignsToResinterTotal
    mass_balance_error_pct: float | None
    atom_ledger: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_index": self.campaign_index,
            "snapshot": self.snapshot.deposit_plain(),
            "cumulative_snapshot": self.cumulative_snapshot.deposit_plain(),
            "per_run_net_deposit_by_segment_species_kg": {
                segment: dict(species_kg)
                for segment, species_kg in (
                    self.per_run_net_deposit_by_segment_species_kg.items()
                )
            },
            "campaigns_to_resinter_total": self.campaigns_to_resinter_total.to_dict(),
            "mass_balance_error_pct": self.mass_balance_error_pct,
        }


@dataclass(frozen=True)
class FoulingLifecycleHarnessResult:
    campaigns_to_resinter_total: CampaignsToResinterTotal
    lifecycle_projection: LifecycleProjection
    fouling_state_trajectory: tuple[NestedDeposit, ...]
    run_records: tuple[FoulingLifecycleRunRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaigns_to_resinter_total": self.campaigns_to_resinter_total.to_dict(),
            "lifecycle_projection": self.lifecycle_projection.to_dict(),
            "fouling_state_trajectory": [
                {segment: dict(species_kg) for segment, species_kg in item.items()}
                for item in self.fouling_state_trajectory
            ],
            "run_records": [record.to_dict() for record in self.run_records],
        }


class FoulingLifecycleHarness:
    """Run-N to N+1 projection loop over completed campaign artifacts."""

    def __init__(
        self,
        run_campaign: Callable[[Any], Any],
        *,
        segment_area_m2: Mapping[str, float],
        rho_deposit_kg_m3: float | Mapping[str, float] | None = None,
        thickness_limit_m: float | None = None,
        resinter_threshold_kg: float | None = None,
        export_includes_carried: bool = True,
        closure_limit_pct: float = MASS_BALANCE_CLOSURE_LIMIT_PCT,
    ) -> None:
        self._run_campaign = run_campaign
        self._segment_area_m2 = dict(segment_area_m2)
        self._rho_deposit_kg_m3 = rho_deposit_kg_m3
        self._thickness_limit_m = thickness_limit_m
        self._resinter_threshold_kg = resinter_threshold_kg
        self._export_includes_carried = bool(export_includes_carried)
        self._closure_limit_pct = float(closure_limit_pct)

    def run(self, campaign_inputs: Sequence[Any]) -> FoulingLifecycleHarnessResult:
        carried: FoulingTerminalSnapshot | None = None
        records: list[FoulingLifecycleRunRecord] = []
        trajectory: list[NestedDeposit] = []
        ledgers: list[Any] = []

        for campaign_index, campaign_input in enumerate(campaign_inputs, start=1):
            artifact = _coerce_run_artifact(self._run_campaign(campaign_input))
            mass_balance_error_pct = _mass_balance_error_pct(artifact)
            if (
                mass_balance_error_pct is not None
                and abs(float(mass_balance_error_pct)) > self._closure_limit_pct
            ):
                raise FoulingProjectionError(
                    "constituent run mass balance closure exceeded "
                    f"{self._closure_limit_pct:.12g}%"
                )

            ledger = _atom_ledger(artifact)
            if ledger is not None:
                if any(ledger is previous for previous in ledgers):
                    raise FoulingProjectionError(
                        "warm-worker ledger isolation violated: reused atom_ledger"
                    )
                ledgers.append(ledger)

            snapshot = FoulingTerminalSnapshot.from_trace(
                artifact.trace,
                grounding_status=_grounding_status(
                    self._thickness_limit_m,
                    self._rho_deposit_kg_m3,
                    self._resinter_threshold_kg,
                ),
                threshold_params={
                    "resinter_threshold_kg": self._resinter_threshold_kg,
                    "thickness_limit_m": self._thickness_limit_m,
                    "rho_deposit_kg_m3": self._rho_deposit_kg_m3,
                    "segment_area_m2": self._segment_area_m2,
                },
                c4b_binding_substrate_state=artifact.c4b_binding_substrate_state,
            )
            cumulative, per_run_net = merge_run_snapshot(
                carried,
                snapshot,
                export_includes_carried=self._export_includes_carried,
            )
            total = _campaigns_total_from_artifact(
                artifact,
                snapshot=snapshot,
                resinter_threshold_kg=self._resinter_threshold_kg,
            )
            records.append(
                FoulingLifecycleRunRecord(
                    campaign_index=campaign_index,
                    snapshot=snapshot,
                    cumulative_snapshot=cumulative,
                    per_run_net_deposit_by_segment_species_kg=per_run_net,
                    campaigns_to_resinter_total=total,
                    mass_balance_error_pct=mass_balance_error_pct,
                    atom_ledger=ledger,
                )
            )
            trajectory.append(cumulative.wall_deposit_by_segment_species_kg)
            carried = cumulative

        cumulative_snapshots = tuple(record.cumulative_snapshot for record in records)
        return FoulingLifecycleHarnessResult(
            campaigns_to_resinter_total=(
                records[-1].campaigns_to_resinter_total
                if records
                else CampaignsToResinterTotal(
                    value="infinite",
                    authoritative_for_resinter=False,
                )
            ),
            lifecycle_projection=project_lifecycle(
                cumulative_snapshots,
                segment_area_m2=self._segment_area_m2,
                rho_deposit_kg_m3=self._rho_deposit_kg_m3,
                thickness_limit_m=self._thickness_limit_m,
                resinter_threshold_kg=self._resinter_threshold_kg,
            ),
            fouling_state_trajectory=tuple(trajectory),
            run_records=tuple(records),
        )


def _coerce_run_artifact(raw: Any) -> FoulingRunArtifact:
    if isinstance(raw, FoulingRunArtifact):
        return raw
    trace = getattr(raw, "trace", None)
    if trace is None:
        raise FoulingProjectionError("run artifact must expose a trace")
    return FoulingRunArtifact(
        trace=trace,
        simulator=getattr(raw, "simulator", None),
        snapshots=tuple(getattr(raw, "snapshots", ()) or ()),
        result_document=getattr(raw, "result_document", None),
        c4b_binding_substrate_state=getattr(
            raw,
            "c4b_binding_substrate_state",
            None,
        ),
    )


def _campaigns_total_from_artifact(
    artifact: FoulingRunArtifact,
    *,
    snapshot: FoulingTerminalSnapshot,
    resinter_threshold_kg: float | None,
) -> CampaignsToResinterTotal:
    explicit = artifact.campaigns_to_resinter_total
    if isinstance(explicit, CampaignsToResinterTotal):
        return explicit
    if isinstance(explicit, Mapping):
        return CampaignsToResinterTotal(
            value=explicit.get("value", explicit.get("campaigns_to_resinter", "infinite")),
            authoritative_for_resinter=bool(
                explicit.get("authoritative_for_resinter", False)
            ),
        )
    report = _find_fouling_report(artifact.result_document)
    if report is not None:
        return CampaignsToResinterTotal(
            value=report.get("campaigns_to_resinter", "infinite"),
            authoritative_for_resinter=bool(
                report.get("authoritative_for_resinter", False)
            ),
        )
    return campaigns_to_resinter_total(
        snapshot.wall_deposit_by_segment_species_kg,
        resinter_threshold_kg=resinter_threshold_kg,
        authoritative_for_resinter=_authoritative_for_resinter(snapshot),
    )


def _find_fouling_report(document: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(document, Mapping):
        return None
    candidates = (
        document.get("fouling_rate"),
        document.get("wall_fouling"),
        document.get("campaigns_to_resinter_total"),
    )
    for candidate in candidates:
        if isinstance(candidate, Mapping) and "campaigns_to_resinter" in candidate:
            return candidate
    diagnostics = document.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        candidate = diagnostics.get("fouling_rate")
        if isinstance(candidate, Mapping) and "campaigns_to_resinter" in candidate:
            return candidate
    return None


def _authoritative_for_resinter(snapshot: FoulingTerminalSnapshot) -> bool:
    authority = snapshot.wall_deposit_sticking_authority
    if not isinstance(authority, Mapping):
        return False
    for key in ("authoritative_for_resinter", "authoritative", "authoritative_for_coating"):
        if key in authority:
            return bool(authority[key])
    return False


def _mass_balance_error_pct(artifact: FoulingRunArtifact) -> float | None:
    if artifact.mass_balance_error_pct is not None:
        return float(artifact.mass_balance_error_pct)
    snapshots = tuple(artifact.snapshots or getattr(artifact.trace, "snapshots", ()) or ())
    values = [
        abs(float(value))
        for value in (
            getattr(snapshot, "mass_balance_error_pct", None)
            for snapshot in snapshots
        )
        if value is not None
    ]
    if values:
        return max(values)
    document = artifact.result_document
    if isinstance(document, Mapping):
        per_hour = document.get("per_hour_summary")
        if isinstance(per_hour, Sequence):
            values = [
                abs(float(row["mass_balance_pct"]))
                for row in per_hour
                if isinstance(row, Mapping) and row.get("mass_balance_pct") is not None
            ]
            if values:
                return max(values)
    return None


def _atom_ledger(artifact: FoulingRunArtifact) -> Any | None:
    simulator = artifact.simulator
    if simulator is None:
        return None
    return getattr(simulator, "atom_ledger", None)


def _grounding_status(
    thickness_limit_m: float | None,
    rho_deposit_kg_m3: float | Mapping[str, float] | None,
    resinter_threshold_kg: float | None,
) -> str:
    if (
        thickness_limit_m is None
        and rho_deposit_kg_m3 is None
        and resinter_threshold_kg is None
    ):
        return "ungrounded_threshold"
    return "PROVISIONAL"
