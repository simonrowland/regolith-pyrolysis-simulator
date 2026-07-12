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
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.cost_parameters import (
    cost_parameter_values,
    default_cost_parameters_block,
)
from simulator.optimize.canonical import (
    FLOAT_QUANTUM,
    CanonicalizationError,
    canonical_json_dumps,
    normalize_canonical_value,
)
from simulator.chemistry.kernel.config import normalize_chemistry_kernel_config
from simulator.optimize.recipe import (
    allowlist_version as DEFAULT_ALLOWLIST_VERSION,
    default_bounds_digest,
)


_VERSION_PATH = Path(__file__).resolve().parents[2] / "VERSION"
REQUIRED_DATA_DIGEST_KEYS = frozenset(
    (
        "feedstocks",
        "foulant_thermo",
        "materials",
        "profile",
        "setpoints",
        "species_catalog",
        "vapor_pressures",
    )
)
_LEGACY_DATA_DIGEST_KEYS = frozenset(
    ("feedstocks", "profile", "setpoints", "vapor_pressures")
)
_LEGACY_DATA_DIGEST_SENTINELS = MappingProxyType(
    {
        "foulant_thermo": "legacy-missing-foulant-thermo-digest",
        "materials": "legacy-missing-materials-digest",
        "species_catalog": "legacy-missing-species-catalog-digest",
    }
)
DEFAULT_VAPOR_PRESSURE_PROVIDER_ID = "builtin-vapor-pressure"
DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID = "builtin-vapor-pressure"
VAPOROCK_DIAGNOSTIC_PROVIDER_ID = "vaporock"


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
    backend_name: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    c5_enabled: bool = False
    mre_max_voltage_V: float = 0.0
    mre_target_species: str = ""
    stage0_redox_oxidant_kg: float = 0.0
    stage0_carbon_reductant_kg: float = 0.0
    o2_bubbler_settings: Mapping[str, Any] = field(default_factory=dict)
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    lab_schedule: Mapping[str, Any] = field(default_factory=dict)
    chemistry_kernel: Mapping[str, Any] = field(default_factory=dict)
    lab_alpha_digest: str = ""
    geometry_digest: str = ""
    effective_exposed_area_m2: float | None = None
    area_basis: str = ""
    oxide_vapor_ceiling_digest: str = ""
    sink_channel_evidence_digests: Mapping[str, str] = field(default_factory=dict)
    target_spec_id: str = ""
    target_spec_digest: str = ""
    target_maturity: Mapping[str, Any] = field(default_factory=dict)
    target_provenance: Mapping[str, Any] = field(default_factory=dict)
    vapor_pressure_provider_id: str = DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    vapor_pressure_fallback_provider_id: str = DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    allow_fallback_vapor: bool = False
    force_builtin_vapor_pressure: bool = False
    vapor_pressure_provider_code_fingerprint: str = ""
    allowlist_version: str = field(default=DEFAULT_ALLOWLIST_VERSION, kw_only=True)
    bounds_digest: str = field(default_factory=default_bounds_digest, kw_only=True)
    stop_at_stage0_exit: bool = field(default=False, kw_only=True)
    cost_parameters: Mapping[str, Any] = field(
        default_factory=default_cost_parameters_block,
        kw_only=True,
    )

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
            "lab_alpha_digest",
            "geometry_digest",
            "area_basis",
            "oxide_vapor_ceiling_digest",
            "vapor_pressure_provider_id",
            "vapor_pressure_fallback_provider_id",
            "vapor_pressure_provider_code_fingerprint",
            "allowlist_version",
            "bounds_digest",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        # Normalize legacy input before cache-key serialization and authority
        # checks; the 0.6 corpus emits only the canonical analytical token.
        object.__setattr__(
            self, "backend_name", canonical_backend_name(self.backend_name)
        )
        if not isinstance(self.hours, int):
            raise TypeError("hours must be an int")
        if not isinstance(self.mass_kg, (int, float, Decimal)):
            raise TypeError("mass_kg must be numeric")
        if not isinstance(self.c5_enabled, bool):
            raise TypeError("c5_enabled must be a bool")
        if not isinstance(self.stop_at_stage0_exit, bool):
            raise TypeError("stop_at_stage0_exit must be a bool")
        if not isinstance(self.allow_fallback_vapor, bool):
            raise TypeError("allow_fallback_vapor must be a bool")
        if not isinstance(self.force_builtin_vapor_pressure, bool):
            raise TypeError("force_builtin_vapor_pressure must be a bool")
        if isinstance(self.mre_max_voltage_V, bool) or not isinstance(
            self.mre_max_voltage_V, (int, float, Decimal)
        ):
            raise TypeError("mre_max_voltage_V must be numeric")
        if not isinstance(self.mre_target_species, str):
            raise TypeError("mre_target_species must be a string")
        for field_name in (
            "stage0_redox_oxidant_kg",
            "stage0_carbon_reductant_kg",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise TypeError(f"{field_name} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise CanonicalizationError(f"{field_name} must be finite and non-negative")
        if not isinstance(self.target_spec_id, str):
            raise TypeError("target_spec_id must be a string")
        if not isinstance(self.target_spec_digest, str):
            raise TypeError("target_spec_digest must be a string")
        if self.effective_exposed_area_m2 is not None:
            if isinstance(self.effective_exposed_area_m2, bool) or not isinstance(
                self.effective_exposed_area_m2,
                (int, float, Decimal),
            ):
                raise TypeError("effective_exposed_area_m2 must be numeric or None")
            if not math.isfinite(float(self.effective_exposed_area_m2)):
                raise CanonicalizationError("effective_exposed_area_m2 must be finite")
        object.__setattr__(self, "data_digests", _freeze_digest_map(self.data_digests))
        object.__setattr__(self, "additives_kg", _freeze_value(self.additives_kg, "additives_kg"))
        object.__setattr__(
            self,
            "o2_bubbler_settings",
            _freeze_value(self.o2_bubbler_settings, "o2_bubbler_settings"),
        )
        object.__setattr__(
            self,
            "runtime_campaign_overrides",
            _freeze_value(self.runtime_campaign_overrides, "runtime_campaign_overrides"),
        )
        object.__setattr__(
            self,
            "lab_schedule",
            _freeze_value(self.lab_schedule, "lab_schedule"),
        )
        object.__setattr__(
            self,
            "chemistry_kernel",
            _freeze_value(
                normalize_chemistry_kernel_config(self.chemistry_kernel),
                "chemistry_kernel",
            ),
        )
        object.__setattr__(
            self,
            "sink_channel_evidence_digests",
            _freeze_optional_digest_map(
                self.sink_channel_evidence_digests,
                "sink_channel_evidence_digests",
            ),
        )
        object.__setattr__(
            self,
            "target_maturity",
            _freeze_value(self.target_maturity, "target_maturity"),
        )
        object.__setattr__(
            self,
            "target_provenance",
            _freeze_value(self.target_provenance, "target_provenance"),
        )
        object.__setattr__(
            self,
            "cost_parameters",
            _freeze_value(
                self.cost_parameters,
                "cost_parameters",
            ),
        )
        cost_parameter_values(self.cost_parameters)

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (
            _rebuild_eval_spec,
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
                self.c5_enabled,
                self.mre_max_voltage_V,
                self.mre_target_species,
                self.stage0_redox_oxidant_kg,
                self.stage0_carbon_reductant_kg,
                _thaw_value(self.o2_bubbler_settings),
                _thaw_value(self.runtime_campaign_overrides),
                _thaw_value(self.lab_schedule),
                _thaw_value(self.chemistry_kernel),
                self.lab_alpha_digest,
                self.geometry_digest,
                self.effective_exposed_area_m2,
                self.area_basis,
                self.oxide_vapor_ceiling_digest,
                _thaw_value(self.sink_channel_evidence_digests),
                self.target_spec_id,
                self.target_spec_digest,
                _thaw_value(self.target_maturity),
                _thaw_value(self.target_provenance),
                self.vapor_pressure_provider_id,
                self.vapor_pressure_fallback_provider_id,
                self.allow_fallback_vapor,
                self.force_builtin_vapor_pressure,
                self.vapor_pressure_provider_code_fingerprint,
                self.allowlist_version,
                self.bounds_digest,
                self.stop_at_stage0_exit,
                _thaw_value(self.cost_parameters),
            ),
        )


@dataclass(frozen=True)
class PrefixEvalSpec(EvalSpec):
    prefix_stage_ids: tuple[str, ...] = field(default_factory=tuple)
    prefix_recipe_ids: tuple[str, ...] = field(default_factory=tuple)
    topology_id: str = "PATH_AB"
    eval_spec_type: str = "prefix"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "prefix_stage_ids", _freeze_string_tuple(self.prefix_stage_ids, "prefix_stage_ids"))
        object.__setattr__(self, "prefix_recipe_ids", _freeze_string_tuple(self.prefix_recipe_ids, "prefix_recipe_ids"))
        if len(self.prefix_recipe_ids) != len(self.prefix_stage_ids):
            raise CanonicalizationError("prefix_recipe_ids must match prefix_stage_ids length")
        if not isinstance(self.topology_id, str) or not self.topology_id:
            raise CanonicalizationError("topology_id must be a non-empty string")
        if self.eval_spec_type != "prefix":
            raise CanonicalizationError("PrefixEvalSpec.eval_spec_type must be 'prefix'")

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (
            _rebuild_prefix_eval_spec,
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
                self.c5_enabled,
                self.mre_max_voltage_V,
                self.mre_target_species,
                self.stage0_redox_oxidant_kg,
                self.stage0_carbon_reductant_kg,
                _thaw_value(self.o2_bubbler_settings),
                _thaw_value(self.runtime_campaign_overrides),
                _thaw_value(self.lab_schedule),
                _thaw_value(self.chemistry_kernel),
                self.lab_alpha_digest,
                self.geometry_digest,
                self.effective_exposed_area_m2,
                self.area_basis,
                self.oxide_vapor_ceiling_digest,
                _thaw_value(self.sink_channel_evidence_digests),
                self.target_spec_id,
                self.target_spec_digest,
                _thaw_value(self.target_maturity),
                _thaw_value(self.target_provenance),
                self.vapor_pressure_provider_id,
                self.vapor_pressure_fallback_provider_id,
                self.allow_fallback_vapor,
                self.force_builtin_vapor_pressure,
                self.vapor_pressure_provider_code_fingerprint,
                self.prefix_stage_ids,
                self.prefix_recipe_ids,
                self.topology_id,
                self.eval_spec_type,
                self.allowlist_version,
                self.bounds_digest,
                self.stop_at_stage0_exit,
                _thaw_value(self.cost_parameters),
            ),
        )


_OLD_EVALSPEC_REDUCE_ARG_COUNT = 34
_PRE_BUBBLER_EVALSPEC_REDUCE_ARG_COUNT = 36
_OLD_PREFIX_EVALSPEC_REDUCE_ARG_COUNT = 38
_PRE_BUBBLER_PREFIX_EVALSPEC_REDUCE_ARG_COUNT = 40
_EVALSPEC_REDUCE_ARG_COUNT = 37
_PREFIX_EVALSPEC_REDUCE_ARG_COUNT = 41
# Position of (stage0_redox_oxidant_kg, stage0_carbon_reductant_kg) in the
# __reduce__ tuple: immediately after mre_target_species (index 15) and before
# runtime_campaign_overrides (index 18). Must track that field order — if the
# reduce tuple is reordered, update this index or old-arity pickles rebuild
# with the redox defaults in the wrong slots (silent cache corruption).
_REDOX_REDUCE_INSERT_INDEX = 16
_BUBBLER_REDUCE_INSERT_INDEX = 18
_DATA_DIGESTS_REDUCE_INDEX = 6


def _with_default_redox_reduce_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    return (
        args[:_REDOX_REDUCE_INSERT_INDEX]
        + (0.0, 0.0)
        + args[_REDOX_REDUCE_INSERT_INDEX:]
    )


def _with_default_bubbler_reduce_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    return args[:_BUBBLER_REDUCE_INSERT_INDEX] + ({},) + args[_BUBBLER_REDUCE_INSERT_INDEX:]


def _with_default_redox_and_bubbler_reduce_args(
    args: tuple[Any, ...],
) -> tuple[Any, ...]:
    return _with_default_bubbler_reduce_args(_with_default_redox_reduce_args(args))


def _with_legacy_data_digest_scope(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    if _LEGACY_DATA_DIGEST_KEYS.difference(value):
        return value
    missing = REQUIRED_DATA_DIGEST_KEYS.difference(value)
    tolerated = missing.intersection(_LEGACY_DATA_DIGEST_SENTINELS)
    if missing.difference(tolerated) or not tolerated:
        return value
    patched = dict(value)
    for key in tolerated:
        patched[key] = _LEGACY_DATA_DIGEST_SENTINELS[key]
    return patched


def _with_legacy_data_digest_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    if len(args) <= _DATA_DIGESTS_REDUCE_INDEX:
        return args
    patched = list(args)
    patched[_DATA_DIGESTS_REDUCE_INDEX] = _with_legacy_data_digest_scope(
        patched[_DATA_DIGESTS_REDUCE_INDEX]
    )
    return tuple(patched)


def _rebuild_eval_spec(*args: Any) -> EvalSpec:
    if len(args) == _OLD_EVALSPEC_REDUCE_ARG_COUNT:
        return EvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_redox_and_bubbler_reduce_args(args)
            )
        )
    if len(args) == _PRE_BUBBLER_EVALSPEC_REDUCE_ARG_COUNT:
        return EvalSpec(
            *_with_legacy_data_digest_args(_with_default_bubbler_reduce_args(args))
        )
    if len(args) == _PRE_BUBBLER_EVALSPEC_REDUCE_ARG_COUNT + 1 and isinstance(args[-1], bool):
        return EvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_bubbler_reduce_args(args[:-1])
            ),
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _EVALSPEC_REDUCE_ARG_COUNT:
        return EvalSpec(*_with_legacy_data_digest_args(args))
    if len(args) == _PRE_BUBBLER_EVALSPEC_REDUCE_ARG_COUNT + 2:
        return EvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_bubbler_reduce_args(args[:-2])
            ),
            allowlist_version=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _EVALSPEC_REDUCE_ARG_COUNT + 3:
        return EvalSpec(
            *_with_legacy_data_digest_args(args[:-3]),
            allowlist_version=args[-3],
            bounds_digest=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _EVALSPEC_REDUCE_ARG_COUNT + 4:
        return EvalSpec(
            *_with_legacy_data_digest_args(args[:-4]),
            allowlist_version=args[-4],
            bounds_digest=args[-3],
            stop_at_stage0_exit=args[-2],
            cost_parameters=args[-1],
        )
    if len(args) == _EVALSPEC_REDUCE_ARG_COUNT + 2:
        return EvalSpec(
            *_with_legacy_data_digest_args(args[:-2]),
            allowlist_version=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _OLD_EVALSPEC_REDUCE_ARG_COUNT + 1:
        return EvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_redox_and_bubbler_reduce_args(args[:-1])
            ),
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _EVALSPEC_REDUCE_ARG_COUNT + 1:
        return EvalSpec(
            *_with_legacy_data_digest_args(args[:-1]),
            stop_at_stage0_exit=args[-1],
        )
    raise TypeError(f"unexpected EvalSpec reduce arity {len(args)}")


def _rebuild_prefix_eval_spec(*args: Any) -> PrefixEvalSpec:
    if len(args) == _OLD_PREFIX_EVALSPEC_REDUCE_ARG_COUNT:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_redox_and_bubbler_reduce_args(args)
            )
        )
    if len(args) == _PRE_BUBBLER_PREFIX_EVALSPEC_REDUCE_ARG_COUNT:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(_with_default_bubbler_reduce_args(args))
        )
    if (
        len(args) == _PRE_BUBBLER_PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 1
        and isinstance(args[-1], bool)
    ):
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_bubbler_reduce_args(args[:-1])
            ),
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _PREFIX_EVALSPEC_REDUCE_ARG_COUNT:
        return PrefixEvalSpec(*_with_legacy_data_digest_args(args))
    if len(args) == _PRE_BUBBLER_PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 2:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_bubbler_reduce_args(args[:-2])
            ),
            allowlist_version=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 3:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(args[:-3]),
            allowlist_version=args[-3],
            bounds_digest=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 4:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(args[:-4]),
            allowlist_version=args[-4],
            bounds_digest=args[-3],
            stop_at_stage0_exit=args[-2],
            cost_parameters=args[-1],
        )
    if len(args) == _PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 2:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(args[:-2]),
            allowlist_version=args[-2],
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _OLD_PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 1:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(
                _with_default_redox_and_bubbler_reduce_args(args[:-1])
            ),
            stop_at_stage0_exit=args[-1],
        )
    if len(args) == _PREFIX_EVALSPEC_REDUCE_ARG_COUNT + 1:
        return PrefixEvalSpec(
            *_with_legacy_data_digest_args(args[:-1]),
            stop_at_stage0_exit=args[-1],
        )
    raise TypeError(f"unexpected PrefixEvalSpec reduce arity {len(args)}")


def current_code_version() -> str:
    return _VERSION_PATH.read_text(encoding="utf-8").strip()


def canonical_evalspec_json(spec: EvalSpec) -> bytes:
    payload = {
        "additives_kg": spec.additives_kg,
        "allowlist_version": spec.allowlist_version,
        "backend_name": spec.backend_name,
        "bounds_digest": spec.bounds_digest,
        "c5_enabled": spec.c5_enabled,
        "campaign": spec.campaign,
        "chemistry_kernel": _chemistry_kernel_key_payload(spec.chemistry_kernel),
        "code_version": spec.code_version,
        "cost_parameters": cost_parameter_values(spec.cost_parameters),
        "data_digests": spec.data_digests,
        "feedstock_id": spec.feedstock_id,
        "feedstock_recipe_digest": spec.feedstock_recipe_digest,
        "fidelity": spec.fidelity,
        "hours": spec.hours,
        "mass_kg": spec.mass_kg,
        "mre_max_voltage_V": spec.mre_max_voltage_V,
        "mre_target_species": spec.mre_target_species,
        "profile_id": spec.profile_id,
        "recipe_id": spec.recipe_id,
        "runtime_campaign_overrides": spec.runtime_campaign_overrides,
        "track": spec.track,
        "vapor_pressure_provider_id": spec.vapor_pressure_provider_id,
        "allow_fallback_vapor": spec.allow_fallback_vapor,
        "force_builtin_vapor_pressure": spec.force_builtin_vapor_pressure,
    }
    if spec.stop_at_stage0_exit:
        payload["stop_at_stage0_exit"] = spec.stop_at_stage0_exit
    if spec.stage0_redox_oxidant_kg:
        payload["stage0_redox_oxidant_kg"] = spec.stage0_redox_oxidant_kg
    if spec.stage0_carbon_reductant_kg:
        payload["stage0_carbon_reductant_kg"] = spec.stage0_carbon_reductant_kg
    if spec.o2_bubbler_settings:
        payload["o2_bubbler_settings"] = spec.o2_bubbler_settings
    if spec.allow_fallback_vapor:
        payload["vapor_pressure_fallback_provider_id"] = (
            spec.vapor_pressure_fallback_provider_id
        )
    if spec.vapor_pressure_provider_code_fingerprint:
        payload["vapor_pressure_provider_code_fingerprint"] = (
            spec.vapor_pressure_provider_code_fingerprint
        )
    if spec.lab_schedule:
        payload["lab_schedule"] = spec.lab_schedule
    payload.update(lab_overlay_scope_payload(spec))
    if spec.target_spec_digest:
        payload.update(
            {
                "target_maturity": spec.target_maturity,
                "target_spec_digest": spec.target_spec_digest,
                "target_spec_id": spec.target_spec_id,
            }
        )
    if isinstance(spec, PrefixEvalSpec):
        payload.update(
            {
                "eval_spec_type": spec.eval_spec_type,
                "prefix_recipe_ids": spec.prefix_recipe_ids,
                "prefix_stage_ids": spec.prefix_stage_ids,
                "topology_id": spec.topology_id,
            }
        )
    normalized = normalize_canonical_value(payload)
    return canonical_json_dumps(normalized).encode("utf-8")


def cache_key(spec: EvalSpec) -> str:
    return hashlib.sha256(canonical_evalspec_json(spec)).hexdigest()


def _chemistry_kernel_key_payload(kernel: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = dict(kernel)
    payload.pop("allow_fallback_vapor", None)
    payload.pop("force_builtin_vapor_pressure", None)
    return payload


def lab_overlay_scope_payload(spec: EvalSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if spec.lab_alpha_digest:
        payload["lab_alpha_digest"] = spec.lab_alpha_digest
    if spec.geometry_digest:
        payload["geometry_digest"] = spec.geometry_digest
    if spec.effective_exposed_area_m2 is not None:
        payload["effective_exposed_area_m2"] = spec.effective_exposed_area_m2
    if spec.area_basis:
        payload["area_basis"] = spec.area_basis
    if spec.oxide_vapor_ceiling_digest:
        payload["oxide_vapor_ceiling_digest"] = spec.oxide_vapor_ceiling_digest
    if spec.sink_channel_evidence_digests:
        payload["sink_channel_evidence_digests"] = spec.sink_channel_evidence_digests
    return payload


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


def _freeze_optional_digest_map(value: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    frozen: dict[str, str] = {}
    keys = list(value)
    if not all(isinstance(key, str) for key in keys):
        raise CanonicalizationError(f"{field_name} keys must be strings")
    for key in sorted(keys):
        digest = value[key]
        if not isinstance(digest, str):
            raise TypeError(f"{field_name} values must be strings")
        if not digest:
            raise CanonicalizationError(f"{field_name}[{key!r}] must be non-empty")
        frozen[key] = digest
    return MappingProxyType(frozen)


def _freeze_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    frozen = tuple(value)
    if not all(isinstance(item, str) for item in frozen):
        raise CanonicalizationError(f"{field_name} must contain only strings")
    return frozen


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
