"""Content-addressed evaluation spec keys for recipe optimizer runs.

EvalSpec cache keys are VERSION-scoped: within one VERSION value, behaviour-
changing code edits require a VERSION bump. The key intentionally avoids a
git-dirty fingerprint until the persistent store exists, so byte-stable inputs
on the same VERSION produce byte-stable keys.

Recipe optimizer setpoints changes are covered by ``recipe_id``; raw
``PyrolysisRun.setpoints_patch`` inputs outside a RecipePatch need their own
EvalSpec determinant before they can use this cache safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from simulator.optimize.canonical import (
    FLOAT_QUANTUM,
    CanonicalizationError,
    canonical_json_dumps,
    normalize_canonical_value,
)


_VERSION_PATH = Path(__file__).resolve().parents[2] / "VERSION"
REQUIRED_DATA_DIGEST_KEYS = frozenset(
    ("feedstocks", "profile", "setpoints", "vapor_pressures")
)
# TODO(O-P2b store): optional code-tree fingerprint.


@dataclass(frozen=True)
class EvalSpec:
    recipe_id: str
    feedstock_recipe_digest: str
    feedstock_id: str
    profile_id: str
    fidelity: str
    code_version: str
    data_digests: Mapping[str, str]
    campaign: str = "C0"
    hours: int = 24
    mass_kg: float = 1000.0
    additives_kg: Mapping[str, Any] = field(default_factory=dict)
    track: str = "pyrolysis"
    backend_name: str = "stub"
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    chemistry_kernel: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "recipe_id",
            "feedstock_recipe_digest",
            "feedstock_id",
            "profile_id",
            "fidelity",
            "code_version",
            "campaign",
            "track",
            "backend_name",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if not isinstance(self.hours, int):
            raise TypeError("hours must be an int")
        if not isinstance(self.mass_kg, (int, float, Decimal)):
            raise TypeError("mass_kg must be numeric")
        object.__setattr__(self, "data_digests", _freeze_digest_map(self.data_digests))
        object.__setattr__(self, "additives_kg", _freeze_value(self.additives_kg, "additives_kg"))
        object.__setattr__(
            self,
            "runtime_campaign_overrides",
            _freeze_value(self.runtime_campaign_overrides, "runtime_campaign_overrides"),
        )
        object.__setattr__(
            self,
            "chemistry_kernel",
            _freeze_value(self.chemistry_kernel, "chemistry_kernel"),
        )

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (
            type(self),
            (
                self.recipe_id,
                self.feedstock_recipe_digest,
                self.feedstock_id,
                self.profile_id,
                self.fidelity,
                self.code_version,
                _thaw_value(self.data_digests),
                self.campaign,
                self.hours,
                self.mass_kg,
                _thaw_value(self.additives_kg),
                self.track,
                self.backend_name,
                _thaw_value(self.runtime_campaign_overrides),
                _thaw_value(self.chemistry_kernel),
            ),
        )


def current_code_version() -> str:
    return _VERSION_PATH.read_text(encoding="utf-8").strip()


def canonical_evalspec_json(spec: EvalSpec) -> bytes:
    payload = {
        "additives_kg": spec.additives_kg,
        "backend_name": spec.backend_name,
        "campaign": spec.campaign,
        "chemistry_kernel": spec.chemistry_kernel,
        "code_version": spec.code_version,
        "data_digests": spec.data_digests,
        "feedstock_id": spec.feedstock_id,
        "feedstock_recipe_digest": spec.feedstock_recipe_digest,
        "fidelity": spec.fidelity,
        "hours": spec.hours,
        "mass_kg": spec.mass_kg,
        "profile_id": spec.profile_id,
        "recipe_id": spec.recipe_id,
        "runtime_campaign_overrides": spec.runtime_campaign_overrides,
        "track": spec.track,
    }
    normalized = normalize_canonical_value(payload)
    return canonical_json_dumps(normalized).encode("utf-8")


def cache_key(spec: EvalSpec) -> str:
    return hashlib.sha256(canonical_evalspec_json(spec)).hexdigest()


def feedstock_recipe_digest(composition: Mapping[str, Any]) -> str:
    composition_wt_pct = _composition_wt_pct(composition)
    entries: list[list[str]] = []
    species_labels = list(composition_wt_pct)
    if not all(isinstance(species_label, str) for species_label in species_labels):
        raise CanonicalizationError("feedstock species labels must be strings")
    for species_label in sorted(species_labels):
        wt_pct = composition_wt_pct[species_label]
        entries.append([species_label, _normalize_wt_pct(wt_pct)])
    canonical = canonical_json_dumps(entries).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_feedstock_recipe_json(composition: Mapping[str, Any]) -> bytes:
    composition_wt_pct = _composition_wt_pct(composition)
    entries: list[list[str]] = []
    species_labels = list(composition_wt_pct)
    if not all(isinstance(species_label, str) for species_label in species_labels):
        raise CanonicalizationError("feedstock species labels must be strings")
    for species_label in sorted(species_labels):
        wt_pct = composition_wt_pct[species_label]
        entries.append([species_label, _normalize_wt_pct(wt_pct)])
    return canonical_json_dumps(entries).encode("utf-8")


def _composition_wt_pct(composition: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(composition, Mapping):
        raise TypeError("feedstock composition must be a mapping")
    nested = composition.get("composition_wt_pct")
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise TypeError("composition_wt_pct must be a mapping")
        return nested
    return composition


def _normalize_wt_pct(value: Any) -> str:
    if isinstance(value, bool):
        raise CanonicalizationError("feedstock wt% values must be numeric")
    if isinstance(value, int):
        return normalize_canonical_value(Decimal(value), float_quantum=FLOAT_QUANTUM)
    if isinstance(value, float) or isinstance(value, Decimal):
        return normalize_canonical_value(value, float_quantum=FLOAT_QUANTUM)
    raise CanonicalizationError("feedstock wt% values must be numeric")


def _freeze_digest_map(value: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("data_digests must be a mapping")
    frozen: dict[str, str] = {}
    keys = list(value)
    if not all(isinstance(key, str) for key in keys):
        raise CanonicalizationError("data_digests keys must be strings")
    missing = REQUIRED_DATA_DIGEST_KEYS.difference(keys)
    if missing:
        joined = ", ".join(sorted(missing))
        raise CanonicalizationError(f"data_digests missing required keys: {joined}")
    for key in sorted(keys):
        digest = value[key]
        if not isinstance(digest, str):
            raise TypeError("data_digests values must be strings")
        if not digest:
            raise CanonicalizationError(f"data_digests[{key!r}] must be non-empty")
        frozen[key] = digest
    return MappingProxyType(frozen)


def _freeze_value(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise CanonicalizationError(f"{field_name} keys must be strings")
        for key in sorted(keys):
            frozen[key] = _freeze_value(value[key], field_name)
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze_value(item, field_name) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item, field_name) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw_value(item) for item in value)
    if isinstance(value, list):
        return [_thaw_value(item) for item in value]
    return value
