"""C0 subsolidus bakeout: exact feed-declared hosts only.

Element totals are not mineral phases. ``FeS`` and elemental ``S`` do not
dispatch a troilite reaction, and ``FeSO4`` stays on the carbon-cleanup path.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

import yaml

SALT_FEED_ACCOUNT = "process.stage0_salt_feed"
FOULANT_ACCOUNT = "process.stage0_foulant"
METAL_ACCOUNT = "process.metal_phase"
OVERHEAD_GAS_ACCOUNT = "process.overhead_gas"
CLEANED_MELT_ACCOUNT = "process.cleaned_melt"

# Catalog ids. Formula and phase match openimcc subsolidus.json at 680574d.
BAKEOUT_HOST_IDENTITY = {
    "FeS_troilite": {"formula": "FeS", "phase": "troilite"},
    "CaSO4": {"formula": "CaSO4", "phase": "anhydrite"},
    "MgSO4": {
        "formula": "MgSO4",
        "phase": "crystalline anhydrous magnesium sulfate",
    },
}

BAKEOUT_SULFIDE_HOSTS = frozenset({"FeS_troilite"})
BAKEOUT_SULFATE_HOSTS = frozenset({"CaSO4", "MgSO4"})

# Reviewed C0 envelope from the bakeout design. A catalog domain admits a
# driver only when it contains the step temperature and this whole interval.
C0_BAKE_TEMPERATURE_K = (348.0, 1223.0)

HOST_DRIVER = {
    "FeS_troilite": "S2",
    "CaSO4": "SO2",
    "MgSO4": "SO2",
}
HANDOFF_OXIDES = frozenset({"CaO", "MgO"})
VOLATILE_THROTTLE_SPECIES = frozenset({"Na", "K", "S2", "SO2", "H2O"})
REDOX_BACK_OXIDATION_NOTICE = "redox_back_oxidation_not_modelled"
REDOX_FEEDSTOCK_KEYS = frozenset({
    "ci_carbonaceous_chondrite",
    "cm_carbonaceous_chondrite",
})
OPENIMCC_UNAVAILABLE_REASON = "openimcc_subsolidus_unavailable"

_SPECIES_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "species_catalog.yaml"
_CATALOG_DOMAIN_CACHE: dict[str, tuple[float, float] | None] | None = None


def redox_notice_for_feedstock(feedstock_key: str) -> str | None:
    """CI and CM only. Ordinary-chondrite and CV keys are not inferred."""
    if str(feedstock_key or "") in REDOX_FEEDSTOCK_KEYS:
        return REDOX_BACK_OXIDATION_NOTICE
    return None


def throttle_mass_rate_kg_hr(
    evap_species_kg_hr: Mapping[str, float] | None,
    bakeout_species_kg_hr: Mapping[str, float] | None,
) -> float:
    """Committed step-average mass rate for the C0/C0B volatile throttle."""
    evap = evap_species_kg_hr or {}
    bakeout = bakeout_species_kg_hr or {}
    total = 0.0
    for species in VOLATILE_THROTTLE_SPECIES:
        total += float(evap.get(species, 0.0) or 0.0)
        total += float(bakeout.get(species, 0.0) or 0.0)
    return total


def catalog_temperature_domain(
    species_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[float, float] | None:
    for row in rows:
        if str(row.get("id") or "") != species_id:
            continue
        domain = row.get("valid_domain")
        if not isinstance(domain, Mapping):
            return None
        bounds = domain.get("temperature_K")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            return None
        try:
            low = float(bounds[0])
            high = float(bounds[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(low) or not math.isfinite(high) or high < low:
            return None
        return low, high
    return None


def _domain_admits_c0(
    domain: tuple[float, float] | None,
    temperature_K: float,
) -> bool:
    if domain is None or not math.isfinite(temperature_K):
        return False
    low, high = domain
    envelope_low, envelope_high = C0_BAKE_TEMPERATURE_K
    covers_step = low <= float(temperature_K) <= high
    covers_envelope = low <= envelope_low and high >= envelope_high
    return covers_step and covers_envelope


def seam_refusal(
    species_id: str,
    temperature_K: float,
    *,
    runtime_owner: str | None,
    domain: tuple[float, float] | None,
) -> str | None:
    """None when the driver may enter the flux provider. Otherwise a reason."""
    if species_id == "S2" and not runtime_owner:
        return "missing_s2_channel_owner"
    if not _domain_admits_c0(domain, temperature_K):
        if species_id == "SO2":
            return "so2_c0_domain_unowned"
        if species_id == "S2":
            return "s2_c0_domain_unowned"
        return "c0_domain_unowned"
    if not runtime_owner:
        if species_id == "SO2":
            return "so2_c0_domain_unowned"
        return "missing_s2_channel_owner"
    return None


def _catalog_domains() -> dict[str, tuple[float, float] | None]:
    global _CATALOG_DOMAIN_CACHE
    if _CATALOG_DOMAIN_CACHE is None:
        payload = yaml.safe_load(_SPECIES_CATALOG_PATH.read_text())
        rows = payload.get("species") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            rows = []
        _CATALOG_DOMAIN_CACHE = {
            "S2": catalog_temperature_domain("S2", rows),
            "SO2": catalog_temperature_domain("SO2", rows),
        }
    return _CATALOG_DOMAIN_CACHE


def production_seam_refusal(species_id: str, temperature_K: float) -> str | None:
    """Read the channel registry and the species catalog. Do not hardcode them."""
    from simulator.vapour_rail.channels import CHANNEL_REGISTRY, CHANNEL_S2

    domains = _catalog_domains()
    if species_id == "S2":
        owner = CHANNEL_REGISTRY[CHANNEL_S2].runtime_owner
    else:
        owner = None
    return seam_refusal(
        species_id,
        temperature_K,
        runtime_owner=owner,
        domain=domains.get(species_id),
    )


def load_subsolidus_evaluator():
    """Return openimcc's evaluator, or None when that API is not installed."""
    try:
        import openimcc.subsolidus as module
    except ImportError:
        return None
    evaluator = getattr(module, "evaluate_subsolidus_reactions", None)
    if not callable(evaluator):
        return None
    return evaluator


def host_inventory_row(species_id: str, remaining_mol: float) -> dict[str, Any]:
    identity = BAKEOUT_HOST_IDENTITY[species_id]
    return {
        "species_id": species_id,
        "formula": identity["formula"],
        "phase": identity["phase"],
        "remaining_mol": float(remaining_mol),
        "activity": None,
    }


def gas_partials_Pa_from_headspace_mbar(
    headspace_mbar: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Keep only finite positive partials. mbar × 100 = Pa. No invented fill."""
    pressures: dict[str, float] = {}
    for species, mbar in dict(headspace_mbar or {}).items():
        if isinstance(mbar, bool) or not isinstance(mbar, (int, float)):
            continue
        pa = float(mbar) * 100.0
        if math.isfinite(pa) and pa > 0.0:
            pressures[str(species)] = pa
    return pressures


def first_melt_backstop(liquid_fraction: Any) -> bool:
    """True only for a declared liquid fraction that is not frozen.

    ``None`` is not a melt. No solidus temperature is assumed.
    """
    from simulator.melt_regime import MeltRegime, melt_regime
    from simulator.scalar_boundary import is_declared_real_scalar

    if not is_declared_real_scalar(liquid_fraction):
        return False
    return melt_regime(liquid_fraction=float(liquid_fraction)) is not MeltRegime.FROZEN


def _add_mol(target: dict[str, dict[str, float]], account: str, species: str, mol: float) -> None:
    if mol <= 0.0:
        return
    bucket = target.setdefault(account, {})
    bucket[species] = bucket.get(species, 0.0) + mol


def _account_for_product(phase: str) -> str | None:
    if phase == "metal":
        return METAL_ACCOUNT
    if phase == "crystal":
        return FOULANT_ACCOUNT
    if phase == "gas":
        return OVERHEAD_GAS_ACCOUNT
    return None


def mol_maps_for_extents(
    reactions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Route one accepted extent through the reaction's signed stoichiometry."""
    debits: dict[str, dict[str, float]] = {}
    credits: dict[str, dict[str, float]] = {}
    for reaction in reactions:
        extent = float(reaction.get("extent_mol") or 0.0)
        if not math.isfinite(extent) or extent <= 0.0:
            continue
        for term in reaction.get("stoichiometry") or ():
            if not isinstance(term, Mapping):
                raise ValueError("bakeout stoichiometry term must be a mapping")
            species = str(term.get("species_id") or "").strip()
            phase = str(term.get("phase") or "").strip()
            nu = float(term["nu_mol_per_mol_rxn"])
            if not species or not math.isfinite(nu) or nu == 0.0:
                raise ValueError(f"bakeout term {species!r} has no signed coefficient")
            amount = abs(nu) * extent
            if nu < 0.0:
                _add_mol(debits, SALT_FEED_ACCOUNT, species, amount)
                continue
            account = _account_for_product(phase)
            if account is None:
                raise ValueError(
                    f"bakeout product {species!r} phase {phase!r} has no account"
                )
            _add_mol(credits, account, species, amount)
    return debits, credits


def handoff_mol_maps(
    salt_mol: Mapping[str, float],
    foulant_mol: Mapping[str, float],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Move remaining hosts and CaO/MgO to the melt once. Leave metal iron."""
    debits: dict[str, dict[str, float]] = {}
    credits: dict[str, dict[str, float]] = {}
    for species, mol in dict(salt_mol or {}).items():
        amount = float(mol or 0.0)
        if species not in BAKEOUT_HOST_IDENTITY or amount <= 1.0e-15:
            continue
        _add_mol(debits, SALT_FEED_ACCOUNT, species, amount)
        _add_mol(credits, CLEANED_MELT_ACCOUNT, species, amount)
    for species in sorted(HANDOFF_OXIDES):
        amount = float((foulant_mol or {}).get(species, 0.0) or 0.0)
        if amount <= 1.0e-15:
            continue
        _add_mol(debits, FOULANT_ACCOUNT, species, amount)
        _add_mol(credits, CLEANED_MELT_ACCOUNT, species, amount)
    return debits, credits


# The simulator advances one hour at a time. This is that step length.
SUBSOLIDUS_STEP_DURATION_HR = 1.0


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def partition_openimcc_results(
    hosts: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    host_remaining_mol: Mapping[str, float],
    temperature_K: float,
    seam_refusal_for: Callable[[str, float], str | None],
) -> dict[str, Any]:
    """Split evaluator rows into a provider batch and typed refusals.

    A supported row whose driver the seam refuses does not enter the
    provider batch, and its equilibrium pressure is not copied. Two
    admitted reactions that name different pressures for one driver are
    withheld rather than collapsed onto one number.
    """
    if len(hosts) != len(results):
        raise ValueError(
            "openimcc subsolidus returned "
            f"{len(results)} rows for {len(hosts)} hosts"
        )
    provider_reactions: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for host, result in zip(hosts, results):
        host_id = str(host.get("species_id") or "")
        status = str(result.get("status") or "")
        driver = str(result.get("driving_gas_species_id") or HOST_DRIVER.get(host_id, ""))
        reaction_id = str(result.get("reaction_id") or "")
        record = {
            "host_species_id": host_id,
            "reaction_id": reaction_id,
            "openimcc_status": status,
            "driving_gas_species_id": driver,
        }
        if status != "supported":
            record["status"] = "unavailable"
            record["reason"] = status or "unsupported_reaction"
            rows.append(record)
            continue
        refusal = seam_refusal_for(driver, temperature_K) if driver else "c0_domain_unowned"
        if refusal:
            record["status"] = "unavailable"
            record["reason"] = refusal
            rows.append(record)
            continue
        pressures = result.get("equilibrium_gas_partial_pressures_Pa") or {}
        equilibrium_pressure_Pa = None
        if isinstance(pressures, Mapping) and driver in pressures:
            equilibrium_pressure_Pa = _finite_nonnegative(pressures.get(driver))
        provider_reactions.append({
            "reaction_id": reaction_id,
            "status": "supported",
            "driving_gas_species_id": driver,
            "host_species_id": host_id,
            "host_remaining_mol": float(host_remaining_mol.get(host_id, 0.0) or 0.0),
            "extent_bound_mol_rxn": result.get("extent_bound_mol_rxn"),
            "stoichiometry": [
                {
                    "species_id": str(term.get("species_id") or ""),
                    "phase": str(term.get("phase") or ""),
                    "nu_mol_per_mol_rxn": float(term["nu_mol_per_mol_rxn"]),
                }
                for term in result.get("stoichiometry") or ()
                if isinstance(term, Mapping)
            ],
            "equilibrium_pressure_Pa": equilibrium_pressure_Pa,
        })
        record["status"] = "supported"
        rows.append(record)

    pressures_Pa, conflicts = _joint_driver_pressures(provider_reactions)
    if conflicts:
        conflict_set = set(conflicts)
        kept: list[dict[str, Any]] = []
        for reaction in provider_reactions:
            driver = str(reaction.get("driving_gas_species_id") or "")
            if driver not in conflict_set:
                kept.append(reaction)
                continue
            for record in rows:
                if record.get("reaction_id") == reaction.get("reaction_id") and record.get(
                    "host_species_id"
                ) == reaction.get("host_species_id"):
                    record["status"] = "unavailable"
                    record["reason"] = "conflicting_driver_equilibrium_pressure"
        provider_reactions = kept
    for reaction in provider_reactions:
        reaction.pop("equilibrium_pressure_Pa", None)
    return {
        "provider_reactions": provider_reactions,
        "rows": rows,
        "vapour_batch_flux_pressures_Pa": pressures_Pa,
    }


def _joint_driver_pressures(
    reactions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    grouped: dict[str, list[float | None]] = {}
    for reaction in reactions:
        driver = str(reaction.get("driving_gas_species_id") or "")
        grouped.setdefault(driver, []).append(reaction.get("equilibrium_pressure_Pa"))
    pressures: dict[str, float] = {}
    conflicts: list[str] = []
    for driver, values in grouped.items():
        if not driver or any(value is None for value in values):
            continue
        numbers = [float(value) for value in values if value is not None]
        if any(number != numbers[0] for number in numbers):
            conflicts.append(driver)
            continue
        pressures[driver] = numbers[0]
    return pressures, conflicts


def reactions_at_accepted_extents(
    reactions: Sequence[Mapping[str, Any]],
    accepted_extent_mol_by_reaction: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Attach the provider's one accepted extent to each reaction's terms."""
    selected: list[dict[str, Any]] = []
    for reaction in reactions:
        reaction_id = str(reaction.get("reaction_id") or "")
        extent = _finite_nonnegative(accepted_extent_mol_by_reaction.get(reaction_id))
        if extent is None or extent <= 0.0:
            continue
        selected.append({
            "extent_mol": extent,
            "stoichiometry": reaction.get("stoichiometry") or (),
        })
    return selected
