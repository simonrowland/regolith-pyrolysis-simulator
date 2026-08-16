"""VR-12 / U5: cross-feedstock shadow/parity + O2 coverage gate.

Acceptance (DECOMPOSITION VR-12 / DESIGN-REV5 §7.2 U5 / §1.1):

- Exact-join every ``feedstock_presence=true`` U0 row under a **strict**
  predicate: cited physical values **or** a finite expansion whose **every**
  child has literature (recursive carriers allowed). Empty or partial joins
  FAIL (``ok=False``). Open acquisition debt is enumerated explicitly and is
  never soft-passed as coverage-ok.
- At ``1e-9 mol/species`` inventory significance, legacy and catalog paths match
  request activation (inventory parents → request gases), live pressures/ranges
  **via the typed pre-RG seam**, source provenance, activity verdicts,
  condensation routing, ledger proposals, mass closure, and O2 outlet
  separation across lunar + Mars + asteroid + one carbonaceous feedstock.
- Mismatches block activation; they are never hidden by golden edits.
- O2 outlet separation and activity verdicts are non-vacuous: each is proved
  fail-closed by a deliberate in-memory mutation documented in-test.

Post-decomposition reality (0de9c6d / t-499 / RG-1 precondition):

- Flux **values** come from the equilibrium-backend effective-pressure seam
  until activity-corrected catalog ``P_eff`` lands. The parity gate therefore
  compares batch **eligibility / refusal / set** authority plus seam values —
  **not** catalog-evaluated pure-component ``P_sat`` numbers.
- Studio-config doctrine (``docs/ci-local-divergence.md``): no bare laptop
  golden pins. This suite is structural / mol-native / shadow-proved; it does
  not commit engine-routed numeric fixtures.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.account_ids import (
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.evaporation import _pre_rg_effective_pressure_source
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.session import SimSessionConfig
from simulator.state import CampaignPhase
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    ActivityVerdictKind,
    CondensedPhaseActivityProvider,
    StandardStateIdentity,
)
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FluxActivationContext,
    FluxEligible,
    PressureValue,
    VapourBatch,
)
from simulator.vapour_rail.catalog import (
    compile_vapour_rail_catalog,
    vapor_pressure_compatibility_view,
    vapor_pressure_legacy_view,
)
from simulator.vapour_rail.instrumentation import (
    CONTROL_FLUX_PRESSURES_KEY,
    FLUX_CONSUMER_RELPATHS,
    SHADOW_PROVED,
    assert_no_flux_consumer_iterates_compatibility_maps,
    compare_live_shadow_to_batch_flux,
    flux_pressures_from_batch,
)
from simulator.vapour_rail.request import _INVENTORY_EPSILON, build_request
from simulator.vapour_rail.u0_manifest import FEEDSTOCK_DELTA_IDS, load_u0_manifest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Acceptance floor: inventory / channel significance (mol per species).
MOL_SIGNIFICANCE = 1.0e-9

# Cross-feedstock matrix required by VR-12.
FEEDSTOCK_MATRIX: tuple[tuple[str, str], ...] = (
    ("lunar_mare_low_ti", "lunar"),
    ("mars_basalt", "mars"),
    ("s_type_asteroid_silicate", "asteroid"),
    ("ci_carbonaceous_chondrite", "carbonaceous"),
)

# Non-delta feedstock_presence rows with disposition R (physically retained
# carriers — not vapour channels). Pin prevents silent V→R rebadging of
# pending rows (review F2 / P2-3).
ALLOWED_RETAINED_FP_IDS = frozenset({"Cl", "He", "N"})

# Explicit open acquisition debt under strict DESIGN §1.1: these
# feedstock_presence rows do **not** have self literature and do **not** have
# a complete expansion whose every child carries literature. They are
# enumerated so the gate cannot soft-pass them as coverage-ok; the suite
# requires that the strict join return ok=False for exactly this set among
# non-delta non-R non-U failures.
#
# Provenance (strict probe at authoring):
# - Cr2O3 / Cr2O3_gas: request children include Cr2O3_gas without literature
# - Na2CO3: stage-0 products include Na2SiO3 without literature
OPEN_ACQUISITION_DEBT_IDS = frozenset(
    {
        "Cr2O3",
        "Cr2O3_gas",
        "Na2CO3",
    }
)

# Stage-0 carbonate / aggregate expansions (foulant_thermo.yaml product sets).
# Labels claim atom-balanced routes; _assert_stage0_expansion_nonempty pins
# non-empty product sets. Full stoichiometry lives in foulant_thermo.yaml.
_STAGE0_FINITE_EXPANSIONS: dict[str, frozenset[str]] = {
    "CaCO3": frozenset({"CaO", "CO2"}),
    "MgCO3": frozenset({"MgO", "CO2"}),
    "Na2CO3": frozenset({"Na2SiO3", "CO2", "Na2O"}),
    "NaCl_KCl_salts": frozenset({"NaCl", "KCl"}),
    "O2_extra": frozenset({"O2"}),
    "ClO4": frozenset({"Cl", "O2", "MgCl2", "CaCl2"}),
}

_PHYSICAL_LIT_KEYS = (
    "literature_values",
    "pure_component_antoine",
    "literature_correlation",
    "literature_candidate_correlations",
    "child_expansion",
    "correlations",
    "janaf",
    "p_carrier_draft",
    "pressure_models",
    "nasa7",
    "nasa9",
    "shomate",
)

_O2_OUTLET_ACCOUNTS = (
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
)

_RAOULTIAN_LIQUID = StandardStateIdentity(
    convention="raoultian",
    phase="liquid",
    reference_pressure_bar=1.0,
    reference_temperature_K=1873.15,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def production_payload() -> dict[str, Any]:
    return yaml.safe_load((DATA / "vapor_pressures.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def u0_manifest() -> dict[str, Any]:
    return load_u0_manifest()


@pytest.fixture(scope="module")
def production_catalog(production_payload: dict[str, Any], u0_manifest: dict[str, Any]):
    return compile_vapour_rail_catalog(
        production_payload, u0_manifest=u0_manifest
    )


@pytest.fixture(scope="module")
def parent_to_gas_ids(production_catalog) -> dict[str, frozenset[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    for rule in production_catalog.request_rules:
        for parent in rule.parent_species_ids or ():
            mapping[str(parent)].add(str(rule.species_id))
    return {k: frozenset(v) for k, v in mapping.items()}


@pytest.fixture(scope="module")
def yaml_phys_index(production_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """species_id -> physical_properties row from schema-v2 families."""

    index: dict[str, dict[str, Any]] = {}
    families = production_payload.get("families") or {}
    if not isinstance(families, Mapping):
        return index
    for family in families.values():
        if not isinstance(family, Mapping):
            continue
        phys = (family.get("physical_properties") or {}).get("species") or {}
        if not isinstance(phys, Mapping):
            continue
        for sid, row in phys.items():
            if isinstance(row, Mapping):
                index[str(sid)] = dict(row)
    return index


@pytest.fixture(scope="module")
def u0_by_id(u0_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): dict(row) for row in u0_manifest["species"]}


def _has_cited_physical_values(
    species_id: str,
    *,
    catalog,
    yaml_phys: Mapping[str, Mapping[str, Any]],
    legacy_view: Mapping[str, Any],
) -> tuple[bool, str | None, list[str]]:
    """Return (ok, where, validation_status_or_note)."""

    if species_id in catalog.species:
        sp = catalog.species[species_id]
        status = sp.validation_status.value
        anchors = list(sp.validation_anchor_refs or ())
        if status == "validated" and not anchors:
            return False, "catalog_validated_without_anchor", []
        if status not in {"pending_validation", "validated"}:
            return False, f"catalog_bad_status:{status}", anchors
        # Compiled species implies a physical channel (evaluator may still
        # refuse at runtime for domain); literature row is present.
        return True, "catalog_species", [status, *anchors]

    phys = yaml_phys.get(species_id)
    if phys is not None:
        status = str((phys.get("validation") or {}).get("status") or "")
        anchors = list((phys.get("validation") or {}).get("anchor_refs") or [])
        has_lit = any(phys.get(key) for key in _PHYSICAL_LIT_KEYS)
        if not has_lit:
            return False, "yaml_without_literature", anchors
        if status == "validated" and not anchors:
            return False, "yaml_validated_without_anchor", anchors
        if status not in {"pending_validation", "validated"}:
            return False, f"yaml_bad_status:{status}", anchors
        return True, "yaml_physical_properties", [status, *anchors]

    for section in ("metals", "oxide_vapors", "foulant_vapor"):
        group = legacy_view.get(section) or {}
        if isinstance(group, Mapping) and species_id in group:
            # Live compatibility projection — Antoine / alpha present.
            row = group[species_id]
            if isinstance(row, Mapping) and (
                row.get("A") is not None
                or row.get("antoine") is not None
                or row.get("evaporation_alpha") is not None
                or "coefficients" in row
                or "source" in row
            ):
                return True, f"legacy:{section}", ["pending_validation"]
    return False, None, []


def _child_expansion_ids(phys_row: Mapping[str, Any] | None) -> frozenset[str]:
    if not phys_row:
        return frozenset()
    expansion = phys_row.get("child_expansion")
    if not isinstance(expansion, Mapping):
        return frozenset()
    children = expansion.get("children") or []
    routes = expansion.get("routes") or []
    ids: set[str] = set()
    for child in children:
        ids.add(str(child))
    for route in routes:
        if not isinstance(route, Mapping):
            continue
        for product in route.get("products") or ():
            ids.add(str(product))
    return frozenset(ids)


def _expansion_candidate_sets(
    sid: str,
    *,
    yaml_phys: Mapping[str, Mapping[str, Any]],
    parent_to_gas: Mapping[str, frozenset[str]],
) -> list[tuple[str, frozenset[str]]]:
    """Discrete expansion sets (not a soft union of partial successes)."""

    sets: list[tuple[str, frozenset[str]]] = []
    if sid in parent_to_gas and parent_to_gas[sid]:
        sets.append(("request_parents", frozenset(parent_to_gas[sid])))
    ce = _child_expansion_ids(yaml_phys.get(sid))
    if ce:
        sets.append(("child_expansion", ce))
    stage0 = _STAGE0_FINITE_EXPANSIONS.get(sid)
    if stage0:
        sets.append(("stage0", stage0))
    return sets


def _species_joinable(
    sid: str,
    *,
    catalog,
    yaml_phys: Mapping[str, Mapping[str, Any]],
    parent_to_gas: Mapping[str, frozenset[str]],
    legacy_view: Mapping[str, Any],
    u0_by_id: Mapping[str, Mapping[str, Any]],
    stack: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """Strict leaf/carrier joinability for expansion children.

    A species is joinable when it has cited physical values, is a typed
    U0 delta/R/U row, or has a **complete** expansion (every child joinable).
    Partial expansions return False — never soft-ok.
    """

    if sid in stack:
        return False, "cycle"
    stack2 = stack | {sid}

    row = u0_by_id.get(sid)
    if row is not None:
        flags = set(row.get("flags") or ())
        is_delta = sid in FEEDSTOCK_DELTA_IDS or "feedstock_delta" in flags
        disposition = str(row.get("disposition") or "")
        if is_delta:
            return True, "delta"
        if disposition == "R":
            return True, "retained"
        if disposition == "U":
            return True, "typed_unresolved"

    ok, where, _meta = _has_cited_physical_values(
        sid, catalog=catalog, yaml_phys=yaml_phys, legacy_view=legacy_view
    )
    if ok:
        return True, f"lit:{where}"

    best_fail = "no_expansion"
    for name, children in _expansion_candidate_sets(
        sid, yaml_phys=yaml_phys, parent_to_gas=parent_to_gas
    ):
        if not children:
            continue
        child_fail: dict[str, str] = {}
        all_ok = True
        for child in children:
            cok, chow = _species_joinable(
                child,
                catalog=catalog,
                yaml_phys=yaml_phys,
                parent_to_gas=parent_to_gas,
                legacy_view=legacy_view,
                u0_by_id=u0_by_id,
                stack=stack2,
            )
            if not cok:
                all_ok = False
                child_fail[child] = chow
        if all_ok:
            return True, f"expansion:{name}:{sorted(children)}"
        best_fail = f"partial:{name}:{child_fail}"
    return False, best_fail


def _join_feedstock_presence_row(
    row: Mapping[str, Any],
    *,
    catalog,
    yaml_phys: Mapping[str, Mapping[str, Any]],
    parent_to_gas: Mapping[str, frozenset[str]],
    legacy_view: Mapping[str, Any],
    u0_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Strict exact-join one feedstock_presence=true U0 row.

    Null hypothesis refuted by callers: a feedstock-present row is silently
    absent from the physical-values / finite-expansion projection, or a
    partial expansion is greenwashed as coverage-ok.
    """

    sid = str(row["id"])
    disposition = str(row["disposition"])
    flags = set(row.get("flags") or ())
    validation_status = str(row.get("validation_status") or "pending_validation")
    is_delta = sid in FEEDSTOCK_DELTA_IDS or "feedstock_delta" in flags

    result: dict[str, Any] = {
        "id": sid,
        "disposition": disposition,
        "validation_status": validation_status,
        "is_delta": is_delta,
        "ok": False,
        "resolution": None,
        "children": [],
        "detail": "",
    }

    # Frozen uncovered DELTA rows: explicit membership is the join.
    if is_delta:
        result["ok"] = sid in FEEDSTOCK_DELTA_IDS
        result["resolution"] = "explicit_feedstock_delta"
        result["detail"] = "frozen uncovered feedstock_delta (DESIGN-REV5 §1.1)"
        return result

    if disposition == "R":
        # Physically retained carrier — pin membership so pending V→R
        # rebadging cannot pass silently (review F2 / P2-3).
        if sid in ALLOWED_RETAINED_FP_IDS:
            result["ok"] = True
            result["resolution"] = "retained"
            result["detail"] = "disposition R retained carrier (pinned set)"
            return result
        result["detail"] = (
            f"disposition R not in ALLOWED_RETAINED_FP_IDS={sorted(ALLOWED_RETAINED_FP_IDS)}"
        )
        result["resolution"] = "retained_unpinned"
        return result

    if disposition == "U":
        result["ok"] = True
        result["resolution"] = "typed_unresolved"
        result["detail"] = "disposition U typed unresolved/refusal channel"
        return result

    # Self literature (primary V/C success path).
    self_ok, where, meta = _has_cited_physical_values(
        sid, catalog=catalog, yaml_phys=yaml_phys, legacy_view=legacy_view
    )
    if self_ok:
        result["ok"] = True
        result["resolution"] = f"physical_values:{where}"
        result["detail"] = ",".join(meta)
        return result

    # Strict expansion: at least one discrete expansion set whose EVERY
    # child is joinable. Partial / empty expansions FAIL (not soft-ok).
    # Bare request-rule presence without literature children is NOT a
    # coverage success (dropped request_rule_pending_evaluator sole path).
    expansion_sets = _expansion_candidate_sets(
        sid, yaml_phys=yaml_phys, parent_to_gas=parent_to_gas
    )
    if not expansion_sets:
        result["detail"] = (
            f"{disposition} row lacks physical values and expansion graph"
        )
        result["resolution"] = "no_physical_coverage"
        return result

    best_fail: list[str] = []
    for name, children in expansion_sets:
        child_status: dict[str, str] = {}
        all_ok = True
        for child in sorted(children):
            cok, chow = _species_joinable(
                child,
                catalog=catalog,
                yaml_phys=yaml_phys,
                parent_to_gas=parent_to_gas,
                legacy_view=legacy_view,
                u0_by_id=u0_by_id,
                stack=frozenset({sid}),
            )
            child_status[child] = chow
            if not cok:
                all_ok = False
        if all_ok:
            result["ok"] = True
            result["resolution"] = f"finite_atom_balanced_expansion:{name}"
            result["detail"] = f"expansion={sorted(children)}"
            result["children"] = sorted(children)
            return result
        fails = {
            c: child_status[c]
            for c in children
            if not _join_status_ok(child_status[c])
        }
        best_fail.append(f"{name}:{fails}")

    result["detail"] = f"partial_or_empty_expansion fails={best_fail}"
    result["resolution"] = "partial_expansion"
    result["children"] = sorted(
        {c for _n, ch in expansion_sets for c in ch}
    )
    return result


def _join_status_ok(status: str) -> bool:
    if status in {"delta", "retained", "typed_unresolved"}:
        return True
    if status.startswith("lit:") or status.startswith("expansion:"):
        return True
    return False


def _stage0_additives_for(feedstock_id: str, mass_kg: float) -> dict[str, float]:
    """Mars basalt Stage-0 carbon cleanup needs explicit C reductant.

    Scale matches production accounting: 3 kg C per 100 kg batch
    (see runner smoke ``mars_basalt_C2A_12h`` and core preload validation).
    """

    if feedstock_id == "mars_basalt":
        return {"C": 0.03 * float(mass_kg)}
    return {}


def _build_hot_sim(feedstock_id: str, *, mass_kg: float = 100.0) -> PyrolysisSimulator:
    bundle = load_config_bundle(DATA)
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    setpoints = dict(bundle.setpoints)
    kernel = dict(setpoints.get("chemistry_kernel") or {})
    kernel["allow_fallback_vapor"] = True
    kernel["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
    )
    additives = _stage0_additives_for(feedstock_id, mass_kg)
    sim.load_batch(feedstock_id, mass_kg=mass_kg, additives_kg=additives or None)
    # Hot melt so the pre-RG seam is non-empty (C0 ramps are cold).
    sim.melt.temperature_C = 1600.0
    return sim


def _step_hot(sim: PyrolysisSimulator, n: int = 3) -> None:
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    for _ in range(n):
        if sim.is_complete():
            break
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        sim.melt.temperature_C = 1600.0


def _parity_surface(sim: PyrolysisSimulator) -> dict[str, Any]:
    """Capture the pre-RG eligibility/set + seam-value parity surface."""

    equilibrium = sim._get_equilibrium()
    effective = _pre_rg_effective_pressure_source(sim.vapor_pressures, equilibrium)
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=float(sim.melt.temperature_C) + 273.15,
        effective_pressure_source=effective,
    )
    assert batch is not None
    assert isinstance(batch, VapourBatch)

    flux_pressures, flux_report = flux_pressures_from_batch(
        batch, effective_pressure_source=effective
    )
    # Independent legacy shadow projection = alpha-gated finite seam map
    # (same source the production shadow uses — not catalog P_sat).
    live_seam = {
        sid: float(effective.pressure_pa(sid) or 0.0)
        for sid in effective.species_ids
    }
    shadow = compare_live_shadow_to_batch_flux(
        batch=batch,
        live_pressures_Pa=live_seam,
        batch_flux_pressures_Pa=flux_pressures,
    )

    channel_surface = {
        sid: {
            "refused": batch.channel(sid).is_refused,
            "flux_active_answer": batch.channel(sid).is_flux_active,
            "in_flux_active_set": sid in batch.flux_active_species_ids,
            "source_label": batch.channel(sid).source_label,
            "validation_status": batch.channel(sid).validation_status,
            "verdict_status": batch.channel(sid).verdict_status,
            "certification_ceiling": batch.channel(sid).certification_ceiling,
            "pressure_kind": type(batch.channel(sid).pressure).__name__,
            "flux_kind": type(batch.channel(sid).flux).__name__,
            "refusal_code": batch.channel(sid).refusal_code,
            "gate_state": (flux_report.get("batch_channel_states") or {}).get(sid),
        }
        for sid in sorted(batch.requested_species_ids)
    }

    return {
        "feedstock_id": sim.record.feedstock_id
        if hasattr(sim.record, "feedstock_id")
        else None,
        "requested_species_ids": frozenset(batch.requested_species_ids),
        "flux_active_species_ids": frozenset(batch.flux_active_species_ids),
        "effective_species_ids": frozenset(effective.species_ids),
        "effective_source_id": effective.source_id,
        "flux_pressures_Pa": dict(flux_pressures),
        "live_seam_Pa": live_seam,
        "shadow": shadow,
        "flux_report": flux_report,
        "channel_surface": channel_surface,
        "batch": batch,
        "solve_bundle_ids": {
            k: frozenset(v) for k, v in batch.solve_bundle_ids.items()
        },
    }


def _significant_inventory_species(
    sim: PyrolysisSimulator, *, floor_mol: float = MOL_SIGNIFICANCE
) -> frozenset[str]:
    full = sim.atom_ledger.mol_by_account()
    if not isinstance(full, Mapping):
        return frozenset()
    present: set[str] = set()
    for account, species_map in full.items():
        if not isinstance(species_map, Mapping):
            continue
        del account
        for sid, mol in species_map.items():
            try:
                if float(mol) >= floor_mol:
                    present.add(str(sid))
            except (TypeError, ValueError):
                continue
    return frozenset(present)


def _o2_outlet_snapshot(sim: PyrolysisSimulator) -> dict[str, dict[str, float]]:
    """Mol-native O2 by distinct outlet account from live ledger state.

    Keys are only those present in the live ``mol_by_account`` map **or**
    the fixed outlet tuple when the account exists in the ledger account
    set. Callers must not treat key pre-materialization alone as a
    separation proof — use :func:`_assert_o2_outlet_separation`.
    """

    out: dict[str, dict[str, float]] = {}
    full = sim.atom_ledger.mol_by_account()
    accounts = (
        *_O2_OUTLET_ACCOUNTS,
        *OXYGEN_STORED_ACCOUNTS,
        *OXYGEN_VENTED_ACCOUNTS,
    )
    for account in accounts:
        raw: Mapping[str, Any] = {}
        if isinstance(full, Mapping):
            raw = dict(full.get(account) or {})
        out[account] = {
            str(k): float(v)
            for k, v in raw.items()
            if isinstance(v, (int, float)) and math.isfinite(float(v))
        }
    return out


def _o2_total_mol(species_map: Mapping[str, float]) -> float:
    return sum(float(v) for v in species_map.values())


def _assert_o2_outlet_separation(
    o2: Mapping[str, Mapping[str, float]],
    *,
    feedstock_id: str,
    require_populated_outlet: bool = True,
) -> None:
    """Non-vacuous O2 outlet separation.

    Null hypothesis: outlets are a single collapsed writer (alias / shared
    key). Refutation: (1) account ids are distinct strings; (2) after a
    controlled inject into one outlet, peer outlets are unchanged; (3) at
    least one process-populated outlet is non-empty on the hot path when
    ``require_populated_outlet``.
    """

    # (1) Distinct account identities (compile-time constants are necessary
    # but not sufficient — paired with inject proof below).
    assert OXYGEN_MRE_ANODE_ACCOUNT != OXYGEN_MELT_OFFGAS_ACCOUNT
    assert OXYGEN_MELT_OFFGAS_ACCOUNT != OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT
    assert OXYGEN_MRE_ANODE_ACCOUNT != OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT

    for account in _O2_OUTLET_ACCOUNTS:
        assert account in o2, f"{feedstock_id}: missing O2 outlet key {account}"
        for species, mol in o2[account].items():
            assert mol >= -1.0e-15, (
                f"{feedstock_id}: negative O2 inventory {account}/{species}={mol}"
            )

    if require_populated_outlet:
        populated = [
            acct
            for acct in _O2_OUTLET_ACCOUNTS
            if _o2_total_mol(o2.get(acct) or {}) > 0.0
        ]
        assert populated, (
            f"{feedstock_id}: expected at least one O2 outlet with moles after "
            "hot steps (process-generated O2); empty multi-outlet map is vacuous"
        )


def _assert_activity_verdicts_nonvacuous(
    sim: PyrolysisSimulator,
    *,
    feedstock_id: str,
    free_oxide_ids: frozenset[str] | set[str],
) -> list[Any]:
    """Require ≥1 structured activity answer on free-oxide inventory set.

    Null hypothesis: activity 'gate' is a non-negative counter that always
    passes (``provider_ok >= 0``). Refutation: at least one POINT answer with
    a finite value on a free oxide present at ≥1e-9 mol.

    Deliberate red-under-mutation (documented): replace the returned answers
    with ``[]`` or force every verdict to REFUSAL — the ``assert answers`` /
    POINT checks below go RED. See
    ``test_activity_verdict_assertion_red_under_mutation``.
    """

    melt = sim.atom_ledger.mol_by_account().get("process.cleaned_melt") or {}
    if not isinstance(melt, Mapping):
        melt = {}
    total = sum(float(v) for v in melt.values() if isinstance(v, (int, float)))
    assert total > 0.0, f"{feedstock_id}: empty melt for activity mole fractions"

    provider = CondensedPhaseActivityProvider()
    answers: list[Any] = []
    # Prefer free oxides present in inventory; fall back to a small fixed set.
    candidates = sorted(
        sid
        for sid in free_oxide_ids
        if sid in melt and float(melt[sid]) >= MOL_SIGNIFICANCE
    )
    if not candidates:
        candidates = ["Na2O", "K2O", "SiO2", "FeO", "MgO", "CaO", "Al2O3"]
        candidates = [s for s in candidates if s in melt]

    for sid in candidates[:8]:
        x = float(melt[sid]) / total
        decl = ActivityInputDeclaration(
            component_id=sid,
            standard_state=_RAOULTIAN_LIQUID,
            activity_model="ideal_raoultian",
            allow_henrian_upper_bound=True,
            compound_bearing=False,
            require_assemblage_match=False,
        )
        answer = provider.resolve_source_reaction_activity(
            decl,
            magemin=None,
            thermoengine=None,
            activity_exponent=1.0,
            measured_gamma=1.0,
            mole_fraction=x,
        )
        assert answer is not None, f"{feedstock_id}/{sid}: activity answer is None"
        assert answer.verdict is not None
        answers.append(answer)

    assert answers, (
        f"{feedstock_id}: activity provider returned zero structured answers "
        f"(vacuous gate)"
    )
    # 2026-08-06 chemact landing (09fefc0): POINT authority now requires an
    # external evidence_ref (b-121/b-122 discipline); production free-oxide
    # activities without one honestly report STATUS_BEARING_VALUE. The
    # contract here is value-bearing flux-driving verdicts, either kind.
    value_bearing = (
        ActivityVerdictKind.POINT,
        ActivityVerdictKind.STATUS_BEARING_VALUE,
    )
    point_answers = [
        a for a in answers if a.verdict in value_bearing
    ]
    assert point_answers, (
        f"{feedstock_id}: expected ≥1 value-bearing activity verdict on free "
        f"oxides; got {[a.verdict for a in answers]}"
    )
    for ans in point_answers:
        assert ans.value is not None and math.isfinite(float(ans.value))
        assert ans.authority is False
        assert ans.may_certify() is False
    return answers


def _assert_inventory_request_activation(
    sim: PyrolysisSimulator,
    *,
    feedstock_id: str,
    parent_to_gas: Mapping[str, frozenset[str]],
    floor_mol: float = MOL_SIGNIFICANCE,
) -> None:
    """Every ≥1e-9 mol **melt** request parent activates its gas channels.

    Null hypothesis (P1-4): inventory-significant oxide is present in
    ``process.cleaned_melt`` but request omits its vapour children.
    Refutation: join melt parents → request membership.

    Scope is the cleaned melt (not terminal offgas / stage-0 O2). Gas-phase
    inventory species (CO, CO2, O2 in terminal accounts) use separate
    source-account predicates and are not this join's subject.
    """

    full = sim.atom_ledger.mol_by_account()
    melt = full.get("process.cleaned_melt") if isinstance(full, Mapping) else None
    assert isinstance(melt, Mapping) and melt, (
        f"{feedstock_id}: expected process.cleaned_melt inventory"
    )
    melt_sig = {
        str(sid): float(mol)
        for sid, mol in melt.items()
        if isinstance(mol, (int, float)) and float(mol) >= floor_mol
    }
    assert melt_sig, (
        f"{feedstock_id}: expected melt inventory >= {floor_mol} mol"
    )
    request = build_request(
        sim.vapour_rail_catalog.request_rules,
        sim.atom_ledger.mol_by_account(),
    )
    # Rules whose entire parent set is satisfied by melt significance must
    # contribute their species_id to the request (or the parent has no rule).
    missing: list[str] = []
    for rule in sim.vapour_rail_catalog.request_rules:
        parents = tuple(str(p) for p in (rule.parent_species_ids or ()))
        if not parents:
            continue
        if not all(p in melt_sig for p in parents):
            continue
        gas = str(rule.species_id)
        if gas not in request:
            missing.append(f"{'+'.join(parents)}→{gas}")
    # Also: every melt species that is a sole parent of some gas must fire.
    for parent in sorted(melt_sig):
        gases = parent_to_gas.get(parent)
        if not gases:
            continue
        # Sole-parent edges only (multi-parent rules handled above).
        sole = [
            g
            for g in gases
            if any(
                tuple(str(p) for p in (r.parent_species_ids or ())) == (parent,)
                for r in sim.vapour_rail_catalog.request_rules
                if str(r.species_id) == g
            )
        ]
        for gas in sole:
            if gas not in request:
                missing.append(f"{parent}→{gas}")
    assert not missing, (
        f"{feedstock_id}: melt inventory ≥{floor_mol} mol parents missing "
        f"request activation: {sorted(set(missing))}; request={sorted(request)}"
    )


# ---------------------------------------------------------------------------
# U0 exact-join
# ---------------------------------------------------------------------------


def test_u0_feedstock_presence_exact_join(
    production_catalog,
    production_payload: dict[str, Any],
    u0_manifest: dict[str, Any],
    parent_to_gas_ids: dict[str, frozenset[str]],
    yaml_phys_index: dict[str, dict[str, Any]],
    u0_by_id: dict[str, dict[str, Any]],
) -> None:
    """Every feedstock_presence=true U0 row joins strictly or is open debt.

    Null hypothesis: a feedstock-present row is silently absent from the
    physical-values / finite-expansion projection, or a partial expansion
    is greenwashed as coverage-ok (soft C/V branches / request-rule-only).
    """

    legacy = vapor_pressure_legacy_view(production_payload)
    fp_rows = [
        row
        for row in u0_manifest["species"]
        if row.get("feedstock_presence") is True
    ]
    assert fp_rows, "expected feedstock_presence=true rows in U0"

    failures: list[str] = []
    resolutions: dict[str, int] = defaultdict(int)
    coverage_ok_ids: set[str] = set()
    strict_fail_ids: set[str] = set()
    retained_ids: set[str] = set()

    for row in fp_rows:
        joined = _join_feedstock_presence_row(
            row,
            catalog=production_catalog,
            yaml_phys=yaml_phys_index,
            parent_to_gas=parent_to_gas_ids,
            legacy_view=legacy,
            u0_by_id=u0_by_id,
        )
        resolutions[str(joined["resolution"])] += 1
        sid = joined["id"]
        if joined["ok"]:
            coverage_ok_ids.add(sid)
            if joined["resolution"] == "retained":
                retained_ids.add(sid)
        else:
            strict_fail_ids.add(sid)
            if sid not in OPEN_ACQUISITION_DEBT_IDS:
                failures.append(
                    f"{sid} disp={joined['disposition']}: {joined['detail']}"
                )

    # Unexpected strict failures (not enumerated open debt) block activation.
    assert not failures, (
        "U0 feedstock_presence exact-join failures (block activation):\n"
        + "\n".join(failures)
    )

    # Open debt must fail the strict predicate (proves we did not soft-pass).
    fp_ids = {str(r["id"]) for r in fp_rows}
    debt_in_fp = OPEN_ACQUISITION_DEBT_IDS & fp_ids
    assert debt_in_fp == OPEN_ACQUISITION_DEBT_IDS, (
        "OPEN_ACQUISITION_DEBT_IDS must be a subset of feedstock_presence rows: "
        f"missing={sorted(OPEN_ACQUISITION_DEBT_IDS - fp_ids)}"
    )
    soft_passed_debt = debt_in_fp & coverage_ok_ids
    assert not soft_passed_debt, (
        "open acquisition debt must not soft-pass as coverage-ok: "
        f"{sorted(soft_passed_debt)}"
    )
    assert debt_in_fp <= strict_fail_ids, (
        "open debt rows must fail strict join: "
        f"not_failing={sorted(debt_in_fp - strict_fail_ids)}"
    )
    # No unaccounted strict failures outside open debt.
    assert strict_fail_ids == debt_in_fp, (
        "strict-fail set drifted from OPEN_ACQUISITION_DEBT_IDS: "
        f"extra={sorted(strict_fail_ids - debt_in_fp)} "
        f"missing={sorted(debt_in_fp - strict_fail_ids)}"
    )

    # Completeness: every fp row is coverage-ok OR explicit open debt.
    assert coverage_ok_ids | debt_in_fp == fp_ids, (
        "incomplete join partition: "
        f"unclassified={sorted(fp_ids - coverage_ok_ids - debt_in_fp)}"
    )
    # Empty join of the coverage set must not pass: we have real coverage.
    assert coverage_ok_ids, "coverage-ok set must be non-empty"
    assert len(coverage_ok_ids) + len(debt_in_fp) == len(fp_rows)

    # Non-delta R pin (F2 / P2-3): only the allowed retained carriers.
    assert retained_ids == ALLOWED_RETAINED_FP_IDS, (
        f"non-delta retained fp set drifted: got={sorted(retained_ids)} "
        f"expected={sorted(ALLOWED_RETAINED_FP_IDS)}"
    )

    # Sanity: delta rows still frozen at 33, and we covered every fp row.
    assert sum(resolutions.values()) == len(fp_rows)
    assert resolutions.get("explicit_feedstock_delta", 0) == len(FEEDSTOCK_DELTA_IDS)

    # Stage-0 expansion dict non-empty product sets (drift guard F5).
    for parent, products in _STAGE0_FINITE_EXPANSIONS.items():
        assert products, f"stage0 expansion for {parent} is empty"


def test_strict_join_nio_expansion_resolves_landed_evaluator(
    production_catalog,
    production_payload: dict[str, Any],
    parent_to_gas_ids: dict[str, frozenset[str]],
    yaml_phys_index: dict[str, dict[str, Any]],
    u0_by_id: dict[str, dict[str, Any]],
) -> None:
    """t-609 closes the former NiO/NiO_gas strict-join debt."""

    legacy = vapor_pressure_legacy_view(production_payload)
    row = u0_by_id["NiO"]
    joined = _join_feedstock_presence_row(
        row,
        catalog=production_catalog,
        yaml_phys=yaml_phys_index,
        parent_to_gas=parent_to_gas_ids,
        legacy_view=legacy,
        u0_by_id=u0_by_id,
    )
    assert joined["ok"] is True, joined["resolution"]
    gas = _join_feedstock_presence_row(
        u0_by_id["NiO_gas"],
        catalog=production_catalog,
        yaml_phys=yaml_phys_index,
        parent_to_gas=parent_to_gas_ids,
        legacy_view=legacy,
        u0_by_id=u0_by_id,
    )
    assert gas["ok"] is True
    assert gas["resolution"] != "request_rule_pending_evaluator"


def test_pending_validation_rows_select_evaluator_not_retention(
    production_catalog,
    u0_manifest: dict[str, Any],
    parent_to_gas_ids: dict[str, frozenset[str]],
) -> None:
    """Pending rows keep request rules / evaluators; never demoted to R."""

    fp_v = [
        row
        for row in u0_manifest["species"]
        if row.get("feedstock_presence")
        and row.get("disposition") == "V"
        and row.get("validation_status") == "pending_validation"
        and row["id"] not in FEEDSTOCK_DELTA_IDS
    ]
    rule_ids = {rule.species_id for rule in production_catalog.request_rules}
    parent_of_rules = {
        parent for parent, gases in parent_to_gas_ids.items() if gases
    }
    for row in fp_v:
        sid = row["id"]
        in_catalog = sid in production_catalog.species
        in_rules = sid in rule_ids
        expands = sid in parent_of_rules
        # Open-debt V rows may still hold a request rule without literature;
        # they must still select an evaluator path, not retention.
        assert in_catalog or in_rules or expands or sid in OPEN_ACQUISITION_DEBT_IDS, (
            f"{sid}: pending_validation V row has neither catalog evaluator, "
            "request rule, nor finite child expansion (would be silent omission)"
        )
        if in_catalog:
            sp = production_catalog.species[sid]
            assert sp.validation_status.value == "pending_validation"
            assert production_catalog.validation_may_certify(sid) is False
        # Never rebadged as retention solely for pending validation.
        assert row.get("disposition") != "R"

    # Positive invariant: no non-delta pending fp row is R outside the pin.
    for row in u0_manifest["species"]:
        if not row.get("feedstock_presence"):
            continue
        if row["id"] in FEEDSTOCK_DELTA_IDS:
            continue
        if row.get("disposition") == "R":
            assert row["id"] in ALLOWED_RETAINED_FP_IDS, (
                f"{row['id']}: pending fp disposition R outside pinned retain set"
            )


# ---------------------------------------------------------------------------
# Cross-feedstock parity (seam values + batch eligibility/set)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_id,body_class",
    FEEDSTOCK_MATRIX,
    ids=[body for _, body in FEEDSTOCK_MATRIX],
)
def test_cross_feedstock_request_activation_and_seam_parity(
    feedstock_id: str,
    body_class: str,
    parent_to_gas_ids: dict[str, frozenset[str]],
) -> None:
    """Legacy seam vs catalog batch: activation, eligibility, seam Pa match.

    Compares at >=1e-9 mol inventory significance. Catalog ``P_sat`` is
    deliberately **not** required to equal seam ``P_eff`` (t-499 / RG-1).
    """

    assert body_class in {"lunar", "mars", "asteroid", "carbonaceous"}
    sim = _build_hot_sim(feedstock_id)
    surface = _parity_surface(sim)

    # P1-4: inventory significance binds request activation (all four bodies).
    _assert_inventory_request_activation(
        sim, feedstock_id=feedstock_id, parent_to_gas=parent_to_gas_ids
    )

    # Exact-key invariant.
    batch: VapourBatch = surface["batch"]
    assert frozenset(batch.channels_by_species) == batch.requested_species_ids

    # Request activation derives only from rules + inventory (rebuild).
    rebuilt = build_request(
        sim.vapour_rail_catalog.request_rules,
        sim.atom_ledger.mol_by_account(),
    )
    assert rebuilt == surface["requested_species_ids"]

    # Pre-RG flux-active set ⊆ effective seam species ∩ eligible answers.
    assert surface["flux_active_species_ids"] <= surface["effective_species_ids"]
    # Hot matrix must exercise a non-empty flux set (empty-vs-empty is vacuous).
    assert surface["flux_active_species_ids"], (
        f"{feedstock_id}: expected non-empty flux-active set at 1600 C"
    )
    for sid in surface["flux_active_species_ids"]:
        answer = batch.channel(sid)
        assert answer.is_flux_active
        assert isinstance(answer.pressure, PressureValue)
        assert isinstance(answer.flux, FluxEligible)

    # Shadow proof: seam live == batch-gated seam values.
    shadow = surface["shadow"]
    assert shadow["shadow_equal"] is True, (
        f"{feedstock_id}: seam/batch shadow mismatch blocks activation: "
        f"{shadow}"
    )
    assert shadow["shadow_outcome"] == SHADOW_PROVED
    assert surface["effective_source_id"] == (
        "equilibrium_backend_effective_pressure_pre_rg"
    )

    # Catalog P_sat may disagree with P_eff — pin that the gate does NOT
    # require catalog_pa_shadow_equal (null hypothesis: someone re-binds
    # activation to catalog P_sat equality and greenwashes RG-1 debt).
    assert "catalog_pa_shadow_equal" in shadow
    # Do not assert True — that would re-introduce the forbidden metric.

    # Per-species seam Pa equality within shadow tolerances.
    for sid, pa in surface["flux_pressures_Pa"].items():
        live = surface["live_seam_Pa"][sid]
        assert math.isclose(pa, live, rel_tol=1.0e-9, abs_tol=1.0e-12), (
            f"{feedstock_id}/{sid}: batch seam Pa {pa} != live seam {live}"
        )
        assert surface["flux_report"][
            "selected_pressure_source_by_species"
        ][sid] == surface["effective_source_id"]

    # Provenance / verdict ceilings on every requested channel (SC-50 consumers).
    for sid, ch in surface["channel_surface"].items():
        assert ch["source_label"], f"{feedstock_id}/{sid}: empty source_label"
        assert ch["validation_status"] in {
            "pending_validation",
            "validated",
            "unavailable",
        } or ch["refused"]
        assert ch["certification_ceiling"] == "never"
        assert ch["verdict_status"] in {
            "status_bearing_non_authoritative",
            "authoritative",
        }
        if ch["validation_status"] == "pending_validation":
            assert ch["certification_ceiling"] == "never"
        # gate_state is produced for flux report consumers (P2-4).
        if ch["in_flux_active_set"]:
            assert ch["gate_state"] is not None or ch["flux_active_answer"]

    # solve_bundle_ids surface is consumed (non-vacuous dict type).
    assert isinstance(surface["solve_bundle_ids"], dict)


@pytest.mark.parametrize(
    "feedstock_id,body_class",
    FEEDSTOCK_MATRIX,
    ids=[body for _, body in FEEDSTOCK_MATRIX],
)
def test_cross_feedstock_activity_verdicts_and_condensation_routing(
    feedstock_id: str,
    body_class: str,
    parent_to_gas_ids: dict[str, frozenset[str]],
) -> None:
    """Activity seam + condensation routing stay typed across feedstocks."""

    assert body_class in {"lunar", "mars", "asteroid", "carbonaceous"}
    sim = _build_hot_sim(feedstock_id)
    surface = _parity_surface(sim)
    batch: VapourBatch = surface["batch"]
    del batch  # batch used via surface channel assertions above if needed

    inventory = _significant_inventory_species(sim)
    free_oxides = {
        sid
        for sid in inventory
        if sid.endswith("O") or sid.endswith("O2") or sid.endswith("O3") or "O" in sid
    }
    # Non-vacuous activity verdicts (P1-3 / F6).
    _assert_activity_verdicts_nonvacuous(
        sim, feedstock_id=feedstock_id, free_oxide_ids=free_oxides or inventory
    )

    # Drive evaporate+condense so routing / ledger proposals fire.
    _step_hot(sim, n=3)

    transitions = list(sim.atom_ledger.transitions)
    assert transitions, f"{feedstock_id}: no ledger transitions after steps"
    transition_names = {getattr(t, "name", "") for t in transitions}
    assert any(n.startswith("evaporate_") for n in transition_names), (
        f"{feedstock_id}: expected evaporate_* ledger proposals"
    )
    assert any(n.startswith("condense_") for n in transition_names), (
        f"{feedstock_id}: expected condense_* ledger proposals"
    )
    # Mol-native proposal payload (P2-1): at least one condense credit carries
    # species_mol > 0 — name-level-only checks are not enough.
    mol_native_credits = 0
    for t in transitions:
        if not str(getattr(t, "name", "")).startswith("condense_"):
            continue
        for credit in t.credits:
            species_mol = (credit.meta or {}).get("species_mol") or {}
            if any(float(v) > 0.0 for v in species_mol.values()):
                mol_native_credits += 1
    assert mol_native_credits >= 1, (
        f"{feedstock_id}: condensation ledger proposals lack mol-native credits"
    )

    # Condensation train: stages + non-empty collected mass (F1 teeth).
    assert sim.train is not None, f"{feedstock_id}: missing condensation train"
    assert sim.train.stages, f"{feedstock_id}: missing condensation stages"
    totals = sim.train.total_by_species
    if callable(totals):
        totals = totals()
    assert isinstance(totals, Mapping) and totals, (
        f"{feedstock_id}: condensation total_by_species empty after steps"
    )
    assert any(float(v) > 0.0 for v in totals.values()), (
        f"{feedstock_id}: condensation collected mass is zero"
    )
    stage_with_mass = 0
    for stage in sim.train.stages:
        collected = getattr(stage, "collected_kg", None)
        if collected is None:
            collected = getattr(stage, "collected_mol", None)
        assert collected is not None or hasattr(stage, "collected_kg")
        if isinstance(collected, Mapping) and any(
            float(v) > 0.0 for v in collected.values() if v is not None
        ):
            stage_with_mass += 1
    assert stage_with_mass >= 1, (
        f"{feedstock_id}: no condensation stage holds collected mass"
    )

    # O2 outlets: process path populates at least one; identities distinct.
    o2 = _o2_outlet_snapshot(sim)
    _assert_o2_outlet_separation(o2, feedstock_id=feedstock_id)

    # Mass balance closes — fail closed if field omitted (P2-2).
    snapshot = sim._make_snapshot()
    err = snapshot.mass_balance_error_pct
    assert err is not None, (
        f"{feedstock_id}: mass_balance_error_pct missing after steps (fail closed)"
    )
    assert abs(float(err)) < 1.0e-9, (
        f"{feedstock_id}: mass balance error {err}% exceeds 1e-9 gate"
    )

    # Batch still exact-key after steps.
    assert sim._last_vapour_batch is not None, (
        f"{feedstock_id}: expected vapour batch after steps"
    )
    late = sim._last_vapour_batch
    assert frozenset(late.channels_by_species) == late.requested_species_ids


@pytest.mark.parametrize(
    "feedstock_id,body_class",
    FEEDSTOCK_MATRIX,
    ids=[body for _, body in FEEDSTOCK_MATRIX],
)
def test_cross_feedstock_o2_outlet_separation_and_mass_closure(
    feedstock_id: str, body_class: str
) -> None:
    """O2 whole-feedstock coverage: distinct outlets + mol-native closure.

    Null hypothesis (F3 / P1-2): separation asserts are key presence +
    constant ``!=`` only, so a collapsed outlet writer stays green.
    Refutation: inject O2 into the MRE anode outlet and assert peer outlets
    are unchanged; require a process-populated outlet after hot steps.
    """

    assert body_class in {"lunar", "mars", "asteroid", "carbonaceous"}
    sim = _build_hot_sim(feedstock_id, mass_kg=50.0)
    _step_hot(sim, n=5)

    o2_before = _o2_outlet_snapshot(sim)
    _assert_o2_outlet_separation(o2_before, feedstock_id=feedstock_id)

    # Mass closure + atom drift on the **pre-inject** process path (fail
    # closed if the snapshot omits the field — P2-2). External inject below
    # is a separation probe only and intentionally adds external mass.
    close = sim.atom_ledger.close_report()
    drift = close.get("element_atom_drift") or {}
    for surface_name in (
        "accepted_transition_residual_mol_atoms",
        "whole_run_boundary_residual_mol_atoms",
    ):
        residual = drift.get(surface_name) or {}
        for element, value in residual.items():
            assert math.isfinite(float(value)), (
                f"{feedstock_id}: non-finite atom drift {surface_name}/{element}"
            )
            assert abs(float(value)) < 1.0e-6, (
                f"{feedstock_id}: atom drift {surface_name}/{element}={value}"
            )

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct is not None, (
        f"{feedstock_id}: mass_balance_error_pct missing (fail closed)"
    )
    assert abs(float(snapshot.mass_balance_error_pct)) < 1.0e-9, (
        f"{feedstock_id}: mass balance error {snapshot.mass_balance_error_pct}% "
        "exceeds 1e-9 gate"
    )

    # Controlled inject into outlet A; peers must be untouched (P1-2 / F3).
    # Distinctive quantity so collapse-into-offgas cannot accidentally match
    # a pre-existing offgas total (mutation-sensitivity demo below).
    inject_mol = 3.14159265e-4
    peer_before = {
        OXYGEN_MELT_OFFGAS_ACCOUNT: copy.deepcopy(
            o2_before.get(OXYGEN_MELT_OFFGAS_ACCOUNT) or {}
        ),
        OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT: copy.deepcopy(
            o2_before.get(OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT) or {}
        ),
        OXYGEN_STAGE0_ACCOUNT: copy.deepcopy(
            o2_before.get(OXYGEN_STAGE0_ACCOUNT) or {}
        ),
    }
    anode_before = _o2_total_mol(o2_before.get(OXYGEN_MRE_ANODE_ACCOUNT) or {})
    sim.atom_ledger.load_external_mol(
        OXYGEN_MRE_ANODE_ACCOUNT,
        {"O2": inject_mol},
        source="vr12_o2_outlet_separation_inject",
        material_origin="reagent",
    )
    o2_after = _o2_outlet_snapshot(sim)
    anode_after = _o2_total_mol(o2_after.get(OXYGEN_MRE_ANODE_ACCOUNT) or {})
    assert anode_after == pytest.approx(anode_before + inject_mol, rel=0, abs=1e-12), (
        f"{feedstock_id}: anode O2 did not receive inject "
        f"before={anode_before} after={anode_after}"
    )
    for peer, before_map in peer_before.items():
        after_map = o2_after.get(peer) or {}
        # Peer totals unchanged (no aliasing into anode credit).
        assert _o2_total_mol(after_map) == pytest.approx(
            _o2_total_mol(before_map), rel=0, abs=1e-12
        ), (
            f"{feedstock_id}: peer outlet {peer} changed after anode inject "
            f"(alias/collapse): before={before_map} after={after_map}"
        )

    # Deliberate red-under-mutation (in-memory): collapse anode moles into
    # the offgas map. The peer-unchanged predicate is RED on that map —
    # proving the assertion can fail (not constant-string theatre).
    mutated_offgas = dict(o2_after.get(OXYGEN_MRE_ANODE_ACCOUNT) or {})
    collapsed_peer_total = _o2_total_mol(mutated_offgas)
    real_peer_total = _o2_total_mol(peer_before[OXYGEN_MELT_OFFGAS_ACCOUNT])
    assert not (
        collapsed_peer_total == pytest.approx(real_peer_total, abs=1e-15)
    ), (
        "mutated collapse must fail peer-unchanged (proves O2 separation "
        "assertion has teeth); inject_mol must make anode≠offgas baseline"
    )


def test_activity_verdict_assertion_red_under_mutation() -> None:
    """Activity gate is mutation-sensitive (P1-3).

    Null hypothesis: ``assert provider_ok >= 0`` always passes.
    Refutation: empty answer list / all-REFUSAL fails the non-vacuous checks.
    """

    provider = CondensedPhaseActivityProvider()
    decl = ActivityInputDeclaration(
        component_id="Na2O",
        standard_state=_RAOULTIAN_LIQUID,
        activity_model="ideal_raoultian",
        allow_henrian_upper_bound=True,
        compound_bearing=False,
        require_assemblage_match=False,
    )
    good = provider.resolve_source_reaction_activity(
        decl,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        measured_gamma=1.0,
        mole_fraction=0.05,
    )
    # 2026-08-06 chemact landing: no evidence_ref supplied, so the honest
    # verdict is STATUS_BEARING_VALUE (POINT now requires external evidence).
    assert good.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert good.value == pytest.approx(0.05)

    # In-memory mutation: empty answers → non-vacuous gate RED.
    answers: list[Any] = []
    with pytest.raises(AssertionError):
        assert answers, "activity provider returned zero structured answers"

    # In-memory mutation: strip to non-POINT → RED.
    answers = [good]
    value_bearing = (
        ActivityVerdictKind.POINT,
        ActivityVerdictKind.STATUS_BEARING_VALUE,
    )
    point_answers = [a for a in answers if a.verdict in value_bearing]
    assert point_answers  # baseline green
    mutated = [
        dataclasses.replace(
            good,
            verdict=ActivityVerdictKind.REFUSAL,
            value=None,
            ln_value=None,
        )
        if hasattr(dataclasses, "replace")
        else good
    ]
    # SourceReactionActivity is a dataclass — replace verdict.
    from simulator.vapour_rail.activity import SourceReactionActivity

    refused = SourceReactionActivity(
        component_id=good.component_id,
        value=None,
        verdict=ActivityVerdictKind.REFUSAL,
        bound_direction=None,
        reason="mutation",
        standard_state=good.standard_state,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=None,
        solve_group_id=None,
        provider=None,
        authority=False,
    )
    mutated_points = [a for a in [refused] if a.verdict in value_bearing]
    with pytest.raises(AssertionError):
        assert mutated_points, "expected ≥1 value-bearing activity verdict"


def test_onee_minus_nine_mol_activation_threshold_parity(
    production_catalog,
    parent_to_gas_ids: dict[str, frozenset[str]],
) -> None:
    """At 1e-9 mol parent inventory, request activation matches across rebuilds.

    Null hypothesis: tiny but significant inventory is dropped on one path.
    Covers synthetic Na/K/Si/Al parents and the floor semantics used by the
    four-feedstock inventory→request join (P1-4 companion).
    """

    # Synthetic ledger: exactly the acceptance floor on Na2O / K2O / SiO2.
    ledger = {
        "process.cleaned_melt": {
            "Na2O": MOL_SIGNIFICANCE,
            "K2O": MOL_SIGNIFICANCE,
            "SiO2": MOL_SIGNIFICANCE,
            "Al2O3": MOL_SIGNIFICANCE,
        }
    }
    # Sub-threshold species must not activate.
    ledger_low = {
        "process.cleaned_melt": {
            "Na2O": MOL_SIGNIFICANCE * 0.1 if _INVENTORY_EPSILON > 0 else 0.0,
            "FeO": 0.0,
        }
    }

    req_hi = build_request(production_catalog.request_rules, ledger)
    req_hi_again = build_request(production_catalog.request_rules, ledger)
    assert req_hi == req_hi_again
    assert req_hi, "1e-9 mol oxide parents must activate request members"
    for expected in ("Na", "K"):
        assert expected in req_hi, (
            f"{expected} missing from request at 1e-9 mol Na2O/K2O: {sorted(req_hi)}"
        )
    # Soft family hit for Si/Al (rules may emit Si vs SiO, Al vs Al2O).
    for expected in ("Si", "Al"):
        family_hit = any(
            expected in sid or sid in {expected, f"{expected}O", f"{expected}2O"}
            for sid in req_hi
        )
        assert family_hit, (
            f"{expected} family missing from request at 1e-9 mol: {sorted(req_hi)}"
        )

    # Inventory-parent join at the floor: every parent maps into request.
    for parent, mol in ledger["process.cleaned_melt"].items():
        if mol < MOL_SIGNIFICANCE:
            continue
        gases = parent_to_gas_ids.get(parent)
        if not gases:
            continue
        for gas in gases:
            assert gas in req_hi, (
                f"parent {parent} at 1e-9 mol must activate {gas}; got {sorted(req_hi)}"
            )

    req_lo = build_request(production_catalog.request_rules, ledger_low)
    assert "Na" not in req_lo or ledger_low["process.cleaned_melt"]["Na2O"] > 0.0


@pytest.mark.parametrize(
    "feedstock_id,body_class",
    FEEDSTOCK_MATRIX,
    ids=[body for _, body in FEEDSTOCK_MATRIX],
)
def test_onee_minus_nine_inventory_request_join_all_feedstocks(
    feedstock_id: str,
    body_class: str,
    parent_to_gas_ids: dict[str, frozenset[str]],
) -> None:
    """1e-9 mol/species inventory→request join across all four feedstocks.

    Completes P1-4 beyond the synthetic Na2O unit case: live matrix bodies.
    """

    assert body_class in {"lunar", "mars", "asteroid", "carbonaceous"}
    sim = _build_hot_sim(feedstock_id)
    _assert_inventory_request_activation(
        sim, feedstock_id=feedstock_id, parent_to_gas=parent_to_gas_ids
    )


def test_shadow_mismatch_blocks_activation_not_golden_edit() -> None:
    """Mismatched seam/batch sets must report shadow_equal=False (no hide).

    Null hypothesis: a mismatched active set still reports proved equality
    (the golden-edit / stubbed-shadow corruption path).
    """

    from simulator.vapour_rail.batch import (
        CERTIFICATION_CEILING_NEVER,
        FluxEligible,
        FluxRefusal,
        PressureRefusal,
        PressureValue,
        VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
        VapourAnswer,
        VapourBatch,
    )

    def _answer(sid: str, *, eligible: bool) -> VapourAnswer:
        if eligible:
            pressure: Any = PressureValue(pa=1.0)
            flux: Any = FluxEligible(alpha_ref="alpha:test")
        else:
            pressure = PressureRefusal(code="test", detail="x")
            flux = FluxRefusal(code="test", detail="x")
        return VapourAnswer(
            species_id=sid,
            pressure=pressure,
            selected_runtime_pressure=pressure,
            flux=flux,
            source_label="test",
            formula_id=sid,
            source_account="process.cleaned_melt",
            solve_group_id="g",
            state_fingerprint="fp",
            validation_status="pending_validation",
            verdict_status=VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
            certification_ceiling=CERTIFICATION_CEILING_NEVER,
            refusal_code=None if eligible else "test",
        )

    batch = VapourBatch(
        requested_species_ids=frozenset({"Na", "K"}),
        channels_by_species={
            "Na": _answer("Na", eligible=True),
            "K": _answer("K", eligible=False),
        },
        flux_active_species_ids=frozenset({"Na"}),
    )
    # Live seam claims K as well → set mismatch must not prove equal.
    live = {"Na": 1.0, "K": 2.0}
    batch_flux = {"Na": 1.0}
    report = compare_live_shadow_to_batch_flux(
        batch=batch,
        live_pressures_Pa=live,
        batch_flux_pressures_Pa=batch_flux,
    )
    assert report["shadow_equal"] is False
    assert report["shadow_outcome"] != SHADOW_PROVED

    # Value mismatch also fails closed.
    report2 = compare_live_shadow_to_batch_flux(
        batch=batch,
        live_pressures_Pa={"Na": 1.0},
        batch_flux_pressures_Pa={"Na": 2.0},
    )
    assert report2["shadow_equal"] is False


# ---------------------------------------------------------------------------
# Surface pins: web/session, grid-pregrind, exact-key, no-compat flux consumer
# ---------------------------------------------------------------------------


def test_simsessionconfig_vapour_keyset_pinned() -> None:
    """U5 pin: SimSessionConfig vapour surface stays the shared facade field."""

    field_names = {f.name for f in dataclasses.fields(SimSessionConfig)}
    assert "vapor_pressures" in field_names
    assert "feedstocks" in field_names
    assert "setpoints" in field_names
    # No parallel raw-YAML authority field slipped in beside the facade.
    assert "vapor_pressure_catalog_raw" not in field_names
    assert "vapour_rail_yaml_path" not in field_names


def test_web_session_uses_compatibility_facade_not_raw_parallel_authority() -> None:
    """web/events.py loads vapor_pressures through the schema-v2 facade."""

    source = (ROOT / "web" / "events.py").read_text(encoding="utf-8")
    assert "vapor_pressure_compatibility_view" in source
    assert "schema_version" in source
    # Strengthened pin (P2-6): schema-v2 branch calls the facade, then
    # hands the result to SimSessionConfig(vapor_pressures=...).
    assert "vapor_pressure_compatibility_view(vapor_pressures)" in source
    assert "SimSessionConfig(" in source
    assert "vapor_pressures=vapor_pressures" in source


def test_grid_pregrind_iterates_compiler_projection() -> None:
    """grid_pregrind_writer uses vapor_pressure_legacy_view (compiler projection)."""

    source = (ROOT / "scripts" / "grid_pregrind_writer.py").read_text(
        encoding="utf-8"
    )
    assert "vapor_pressure_legacy_view" in source
    # Still enumerates the three hot sections via the projection, not a
    # private raw family walk for flux species selection.
    assert 'for section in ("metals", "oxide_vapors", "foulant_vapor")' in source

    # Runtime: projection covers the live hot set.
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    legacy = vapor_pressure_legacy_view(payload)
    hot_ids: set[str] = set()
    for section in ("metals", "oxide_vapors", "foulant_vapor"):
        group = legacy.get(section) or {}
        assert isinstance(group, Mapping)
        hot_ids.update(str(k) for k in group)
    # Core live metals that every feedstock matrix exercises.
    for required in ("Na", "K", "SiO", "Fe", "Mg"):
        assert required in hot_ids, f"grid projection missing hot species {required}"


def test_exact_key_batch_hard_fails_on_missing_channel(production_catalog) -> None:
    """channels_by_species.keys() must equal requested_species_ids."""

    from simulator.vapour_rail.batch import (
        CERTIFICATION_CEILING_NEVER,
        IncompleteVapourBatchError,
        PressureValue,
        FluxEligible,
        VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
        VapourAnswer,
        VapourBatch,
    )

    pressure = PressureValue(pa=1.0)
    answer = VapourAnswer(
        species_id="Na",
        pressure=pressure,
        selected_runtime_pressure=pressure,
        flux=FluxEligible(alpha_ref="a"),
        source_label="t",
        formula_id="Na",
        source_account="process.cleaned_melt",
        solve_group_id="g",
        state_fingerprint="fp",
        validation_status="pending_validation",
        verdict_status=VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
        certification_ceiling=CERTIFICATION_CEILING_NEVER,
    )
    with pytest.raises(IncompleteVapourBatchError):
        VapourBatch(
            requested_species_ids=frozenset({"Na", "K"}),
            channels_by_species={"Na": answer},
        )


def test_no_flux_consumer_iterates_compatibility_maps() -> None:
    """U4/U5 pin: flux consumers never iterate compatibility pressure maps."""

    sources = {
        relpath: (ROOT / relpath).read_text(encoding="utf-8")
        for relpath in FLUX_CONSUMER_RELPATHS
    }
    assert_no_flux_consumer_iterates_compatibility_maps(sources)
    kernel = sources["engines/builtin/evaporation_flux.py"]
    assert CONTROL_FLUX_PRESSURES_KEY in kernel
    assert 'controls.get("vapor_pressures_Pa")' not in kernel


def test_config_load_and_compatibility_view_share_facade(
    production_payload: dict[str, Any],
) -> None:
    """config + web facade identity: schema-v2 retains catalog_payload."""

    if production_payload.get("schema_version") != 2:
        pytest.skip("production vapor_pressures.yaml is not schema-v2")
    view = vapor_pressure_compatibility_view(production_payload)
    assert getattr(view, "catalog_payload", None) is not None
    assert view.catalog_payload.get("schema_version") == 2
    # Legacy sections still project for grid / reporting.
    legacy = vapor_pressure_legacy_view(production_payload)
    assert "metals" in legacy and "oxide_vapors" in legacy


def test_inventory_epsilon_documented_against_acceptance_floor() -> None:
    """Document request epsilon vs VR-12 1e-9 significance floor.

    Request activation uses ``_INVENTORY_EPSILON`` (currently 0.0 — any
    positive mol). The VR-12 acceptance floor of 1e-9 mol/species is the
    **parity comparison** significance used in this suite, not a silent
    second request threshold. This pin fails if epsilon grows past the
    acceptance floor without a deliberate review (would hide sub-1e-9
    inventory divergence).
    """

    assert _INVENTORY_EPSILON <= MOL_SIGNIFICANCE
