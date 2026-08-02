"""VR-8 oxide + trace acquisition transcription (Group-A/B + O(g)).

Loads ``data/vapour_rail_trace_acquisition.yaml``, joins against the U0
manifest membership sets, and exposes the single pending-validation query
required by the VR-8 acceptance gate.

Rows remain dormant to flux: this module never feeds the evaporation path.
Numeric R-family promotion is deferred to VR-17 (Group-A) and VR-18 (Group-B).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from simulator.vapour_rail.u0_manifest import (
    GROUP_A_ELEMENT_IDS,
    GROUP_A_GAS_IDS,
    GROUP_B_ELEMENT_IDS,
    GROUP_B_GAS_IDS,
    load_u0_manifest,
)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_ACQUISITION_PATH = _DATA_DIR / "vapour_rail_trace_acquisition.yaml"
_VAPOR_PRESSURES_PATH = _DATA_DIR / "vapor_pressures.yaml"
_SPECIES_CATALOG_PATH = _DATA_DIR / "species_catalog.yaml"

GROUP_A_IDS: frozenset[str] = frozenset(GROUP_A_GAS_IDS) | frozenset(
    GROUP_A_ELEMENT_IDS
)
GROUP_B_IDS: frozenset[str] = frozenset(GROUP_B_GAS_IDS) | frozenset(
    GROUP_B_ELEMENT_IDS
)
MONATOMIC_OXYGEN_ID = "O"
PO2_EXPONENT_ATOMIC_O = 0.5  # +1/2 from ½ O2 ⇌ O
_GROUP_A_TYPED_OUTCOMES = frozenset({"evolve"})


class TraceAcquisitionError(ValueError):
    """Raised when the VR-8 acquisition corpus fails its coverage contract."""


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


@lru_cache(maxsize=1)
def load_trace_acquisition(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and validate the VR-8 acquisition YAML."""

    payload_path = Path(path) if path is not None else _ACQUISITION_PATH
    payload = _load_yaml(payload_path)
    if not isinstance(payload, Mapping):
        raise TraceAcquisitionError("trace acquisition root must be a mapping")
    if payload.get("schema_version") != 1:
        raise TraceAcquisitionError("trace acquisition schema_version must be 1")
    if payload.get("kind") != "vapour_rail_trace_acquisition":
        raise TraceAcquisitionError(
            "trace acquisition kind must be vapour_rail_trace_acquisition"
        )
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise TraceAcquisitionError("trace acquisition must declare a non-empty rows list")
    errors = validate_trace_acquisition(payload)
    if errors:
        raise TraceAcquisitionError("; ".join(errors[:12]))
    return dict(payload)


def acquisition_rows_by_id(
    payload: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    doc = payload if payload is not None else load_trace_acquisition()
    out: dict[str, dict[str, Any]] = {}
    for row in doc["rows"]:
        sid = row["id"]
        if sid in out:
            raise TraceAcquisitionError(f"duplicate acquisition row {sid!r}")
        out[sid] = dict(row)
    return out


def validate_trace_acquisition(payload: Mapping[str, Any]) -> list[str]:
    """Return validation errors (empty means OK)."""

    errors: list[str] = []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return ["rows must be a list"]

    by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            errors.append(f"rows[{index}] must be a mapping")
            continue
        sid = raw.get("id")
        if not isinstance(sid, str) or not sid:
            errors.append(f"rows[{index}]: missing id")
            continue
        if sid in by_id:
            errors.append(f"duplicate id {sid!r}")
            continue
        by_id[sid] = raw
        for field in (
            "group",
            "formula",
            "atoms",
            "candidate_vapor_form",
            "phase_system",
            "volatility",
            "validation",
            "source_account",
            "route",
            "regime",
        ):
            if field not in raw:
                errors.append(f"{sid}: missing {field}")

        validation = raw.get("validation")
        if isinstance(validation, Mapping):
            status = validation.get("status")
            if status not in ("pending_validation", "validated"):
                errors.append(f"{sid}: invalid validation.status {status!r}")
        else:
            errors.append(f"{sid}: validation must be a mapping")

        if not raw.get("source_account"):
            errors.append(f"{sid}: source_account required")

        route = raw.get("route")
        if isinstance(route, Mapping):
            if route.get("dormant_to_flux") is not True:
                errors.append(f"{sid}: rows must remain dormant_to_flux")
        else:
            errors.append(f"{sid}: route must be a mapping")

        regime = raw.get("regime")
        if not isinstance(regime, Mapping):
            errors.append(f"{sid}: regime must be a mapping")
            continue
        for band in ("millibar", "hard_vacuum"):
            band_row = regime.get(band)
            if not isinstance(band_row, Mapping):
                errors.append(f"{sid}: regime.{band} missing")
                continue
            if "applicable" not in band_row or "dominance" not in band_row:
                errors.append(f"{sid}: regime.{band} incomplete")
            if "outcome" not in band_row:
                errors.append(f"{sid}: regime.{band}.outcome missing")

        group = raw.get("group")
        if group == "B":
            millibar = regime.get("millibar") if isinstance(regime, Mapping) else None
            hard_vacuum = (
                regime.get("hard_vacuum") if isinstance(regime, Mapping) else None
            )
            if isinstance(millibar, Mapping) and isinstance(hard_vacuum, Mapping):
                if millibar.get("dominance") == "negligible" and hard_vacuum.get(
                    "dominance"
                ) in (None, "negligible", "zero"):
                    errors.append(
                        f"{sid}: Group-B hard_vacuum must not inherit millibar negligible"
                    )
                if hard_vacuum.get("applicable") is not True:
                    errors.append(f"{sid}: Group-B hard_vacuum must remain applicable")
                if hard_vacuum.get("outcome") in ("zero", "drop", "hard_vacuum_zero"):
                    errors.append(
                        f"{sid}: Group-B hard_vacuum outcome may not declare zero"
                    )
                if hard_vacuum.get("dominance") == "negligible" and millibar.get(
                    "dominance"
                ) == "negligible":
                    # Explicit twin-negligible is still forbidden as a silent drop.
                    if hard_vacuum.get("outcome") == "retain_rump":
                        errors.append(
                            f"{sid}: Group-B may not declare hard-vacuum retain "
                            "solely from millibar-negligible pairing"
                        )
            route = raw.get("route") if isinstance(raw.get("route"), Mapping) else {}
            if (
                route.get(
                    "never_declare_hard_vacuum_zero_from_millibar_negligible"
                )
                is not True
            ):
                errors.append(
                    f"{sid}: Group-B route must pin never_declare_hard_vacuum_zero_..."
                )
            if route.get("never_drop") is not True:
                errors.append(f"{sid}: Group-B route.never_drop must be true")

        # Group-A: literature phase/volatility payload must be non-empty
        # (phase/window/note already required as keys via volatility mapping
        # consumers; values content is the residual gap for criterion 1).
        if group == "A":
            vol = (
                raw.get("volatility")
                if isinstance(raw.get("volatility"), Mapping)
                else {}
            )
            values = vol.get("values") if isinstance(vol, Mapping) else None
            if not isinstance(values, Mapping) or not values:
                errors.append(f"{sid}: Group-A volatility.values required (non-empty)")
            lit = raw.get("literature_sources")
            if not isinstance(lit, list) or not lit:
                errors.append(f"{sid}: Group-A literature_sources required (non-empty)")
            route = raw.get("route") if isinstance(raw.get("route"), Mapping) else {}
            typed_outcome = route.get("typed_outcome")
            if (
                not isinstance(typed_outcome, str)
                or typed_outcome not in _GROUP_A_TYPED_OUTCOMES
            ):
                errors.append(
                    f"{sid}: Group-A route.typed_outcome must be one of "
                    f"{sorted(_GROUP_A_TYPED_OUTCOMES)}"
                )

        if group == "B":
            lit = raw.get("literature_sources")
            if not isinstance(lit, list) or not lit:
                errors.append(f"{sid}: Group-B literature_sources required (non-empty)")
            route = raw.get("route") if isinstance(raw.get("route"), Mapping) else {}
            if not isinstance(route.get("typed_outcome_by_regime"), Mapping):
                errors.append(
                    f"{sid}: Group-B route.typed_outcome_by_regime required"
                )

        if group == "monatomic_oxygen" or sid == MONATOMIC_OXYGEN_ID:
            vol = raw.get("volatility") if isinstance(raw.get("volatility"), Mapping) else {}
            values = vol.get("values") if isinstance(vol, Mapping) else {}
            if not isinstance(values, Mapping):
                errors.append(f"{sid}: O(g) volatility.values required")
            else:
                exp = values.get("pO2_exponent")
                if exp is None or abs(float(exp) - PO2_EXPONENT_ATOMIC_O) > 1e-12:
                    errors.append(
                        f"{sid}: O(g) requires pO2_exponent +1/2 (got {exp!r})"
                    )
                reaction = values.get("source_reaction")
                if not isinstance(reaction, Mapping):
                    errors.append(f"{sid}: O(g) requires atom-explicit source_reaction")
                else:
                    products = reaction.get("products") or []
                    reactants = reaction.get("reactants") or []
                    o_products = [
                        p
                        for p in products
                        if isinstance(p, Mapping) and p.get("formula") == "O"
                    ]
                    o2_reactants = [
                        r
                        for r in reactants
                        if isinstance(r, Mapping) and r.get("formula") == "O2"
                    ]
                    if not o_products:
                        errors.append(f"{sid}: source_reaction must product O")
                    else:
                        try:
                            p_stoich = float(o_products[0].get("stoichiometry"))
                        except (TypeError, ValueError):
                            p_stoich = None
                        if p_stoich is None or abs(p_stoich - 1.0) > 1e-12:
                            errors.append(
                                f"{sid}: source_reaction product O stoichiometry "
                                f"must be 1 (got {o_products[0].get('stoichiometry')!r})"
                            )
                    if not o2_reactants:
                        errors.append(f"{sid}: source_reaction must debit O2")
                    else:
                        try:
                            r_stoich = float(o2_reactants[0].get("stoichiometry"))
                        except (TypeError, ValueError):
                            r_stoich = None
                        if r_stoich is None or abs(r_stoich - 0.5) > 1e-12:
                            errors.append(
                                f"{sid}: source_reaction reactant O2 stoichiometry "
                                f"must be 0.5 (got {o2_reactants[0].get('stoichiometry')!r})"
                            )

    # Exact U0 Group-A/B coverage.
    missing_a = sorted(GROUP_A_IDS - set(by_id))
    missing_b = sorted(GROUP_B_IDS - set(by_id))
    if missing_a:
        errors.append(f"missing Group-A U0 rows: {missing_a[:8]}")
    if missing_b:
        errors.append(f"missing Group-B U0 rows: {missing_b[:8]}")
    if MONATOMIC_OXYGEN_ID not in by_id:
        errors.append("missing monatomic O row")

    extra = sorted(
        set(by_id)
        - GROUP_A_IDS
        - GROUP_B_IDS
        - {MONATOMIC_OXYGEN_ID}
    )
    if extra:
        errors.append(f"unexpected acquisition ids: {extra[:8]}")

    return errors


def list_pending_validation(
    *,
    include_catalog_collision_gases: bool = True,
    include_live_vapor_pressures: bool = True,
    acquisition_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the complete remaining pending-validation set.

    One query. Includes:
    - every VR-8 acquisition row still ``pending_validation``;
    - optional schema-v2 vapour-pressure family species still pending;
    - optional species-catalog collision-gas rows still pending.

    Validated rows are omitted. Order is stable by (surface, id).
    """

    pending: list[dict[str, Any]] = []

    acq = acquisition_payload if acquisition_payload is not None else load_trace_acquisition()
    for row in acq["rows"]:
        status = (row.get("validation") or {}).get("status")
        if status == "pending_validation":
            pending.append(
                {
                    "surface": "trace_acquisition",
                    "id": row["id"],
                    "group": row.get("group"),
                    "validation_status": status,
                    "acquisition_status": (row.get("validation") or {}).get(
                        "acquisition_status"
                    ),
                    "candidate_vapor_form": row.get("candidate_vapor_form"),
                    "source_account": row.get("source_account"),
                    "dormant_to_flux": True,
                }
            )

    if include_live_vapor_pressures and _VAPOR_PRESSURES_PATH.is_file():
        vp = _load_yaml(_VAPOR_PRESSURES_PATH)
        families = (vp or {}).get("families") or {}
        if isinstance(families, Mapping):
            for family_id, family in families.items():
                if not isinstance(family, Mapping):
                    continue
                physical = family.get("physical_properties") or {}
                species_map = physical.get("species") or {}
                if not isinstance(species_map, Mapping):
                    continue
                for sid, srow in species_map.items():
                    if not isinstance(srow, Mapping):
                        continue
                    status = (srow.get("validation") or {}).get("status")
                    if status == "pending_validation":
                        pending.append(
                            {
                                "surface": "vapor_pressures",
                                "id": str(sid),
                                "family_id": str(family_id),
                                "validation_status": status,
                                "dormant_to_flux": (
                                    ((family.get("code_metadata") or {}).get(
                                        "hot_train_applicability"
                                    )
                                    == "not_applicable")
                                    or (
                                        (family.get("code_metadata") or {}).get(
                                            "request_rule"
                                        )
                                        or ""
                                    ).startswith("dormant")
                                ),
                            }
                        )

    if include_catalog_collision_gases and _SPECIES_CATALOG_PATH.is_file():
        catalog = _load_yaml(_SPECIES_CATALOG_PATH)
        for srow in (catalog or {}).get("species") or []:
            if not isinstance(srow, Mapping):
                continue
            status = (srow.get("validation") or {}).get("status")
            if status == "pending_validation":
                pending.append(
                    {
                        "surface": "species_catalog",
                        "id": srow.get("id"),
                        "validation_status": status,
                        "catalog_role": srow.get("catalog_role"),
                        "direct_vapour_flux": srow.get("direct_vapour_flux"),
                    }
                )

    pending.sort(key=lambda r: (str(r.get("surface")), str(r.get("id"))))
    return pending


def group_a_coverage() -> dict[str, Any]:
    rows = acquisition_rows_by_id()
    covered = sorted(sid for sid in GROUP_A_IDS if sid in rows)
    return {
        "expected": sorted(GROUP_A_IDS),
        "covered": covered,
        "complete": set(covered) == GROUP_A_IDS,
    }


def group_b_coverage() -> dict[str, Any]:
    rows = acquisition_rows_by_id()
    covered = sorted(sid for sid in GROUP_B_IDS if sid in rows)
    return {
        "expected": sorted(GROUP_B_IDS),
        "covered": covered,
        "complete": set(covered) == GROUP_B_IDS,
    }


def monatomic_oxygen_record() -> dict[str, Any]:
    rows = acquisition_rows_by_id()
    if MONATOMIC_OXYGEN_ID not in rows:
        raise TraceAcquisitionError("O(g) acquisition row missing")
    return rows[MONATOMIC_OXYGEN_ID]


def assert_u0_join_closed(u0_payload: Mapping[str, Any] | None = None) -> None:
    """Fail if any Group-A/B U0 row lacks an acquisition record."""

    manifest = u0_payload if u0_payload is not None else load_u0_manifest()
    acq = acquisition_rows_by_id()
    missing: list[str] = []
    for row in manifest.get("species") or []:
        flags = set(row.get("flags") or [])
        if flags & {"group_a", "group_b"} or row.get("id") == MONATOMIC_OXYGEN_ID:
            if "group_a" in flags or "group_b" in flags or row.get("id") == MONATOMIC_OXYGEN_ID:
                if row["id"] not in acq:
                    missing.append(row["id"])
    # O is always required even if flags differ
    if MONATOMIC_OXYGEN_ID not in acq:
        missing.append(MONATOMIC_OXYGEN_ID)
    if missing:
        raise TraceAcquisitionError(
            f"U0 join incomplete; missing acquisition for {sorted(set(missing))[:12]}"
        )


def group_b_regime_pairs(
    payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return Group-B rows with separate millibar and hard-vacuum records."""

    doc = payload if payload is not None else load_trace_acquisition()
    out: list[dict[str, Any]] = []
    for row in doc["rows"]:
        if row.get("group") != "B":
            continue
        out.append(
            {
                "id": row["id"],
                "millibar": dict(row["regime"]["millibar"]),
                "hard_vacuum": dict(row["regime"]["hard_vacuum"]),
                "candidate_vapor_form": row["candidate_vapor_form"],
                "route": dict(row["route"]),
            }
        )
    return out


def clear_trace_acquisition_cache() -> None:
    load_trace_acquisition.cache_clear()


__all__ = [
    "GROUP_A_IDS",
    "GROUP_B_IDS",
    "MONATOMIC_OXYGEN_ID",
    "PO2_EXPONENT_ATOMIC_O",
    "TraceAcquisitionError",
    "acquisition_rows_by_id",
    "assert_u0_join_closed",
    "clear_trace_acquisition_cache",
    "group_a_coverage",
    "group_b_coverage",
    "group_b_regime_pairs",
    "list_pending_validation",
    "load_trace_acquisition",
    "monatomic_oxygen_record",
    "validate_trace_acquisition",
]
