"""Domain owner for OpenIMCC pack liquid-reference checks and dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
import importlib
import json
import math
import threading
from typing import Any

from importlib import resources


_REFUSAL_KINDS = frozenset({"undeclared"})
_FLAG_KINDS = frozenset(
    {"mismatch", "reference-unknown", "non-source-basis"}
)
_SOURCE_LOOKUP_LOCK = threading.RLock()
_UNRECOGNIZED_SOURCE = object()
_UNRECOGNIZED_SOURCE_REASON = (
    "active source not recognized by the openimcc 9f25dfe registry"
)


def _check_pack_standards(
    openimcc: Any,
    pack: object,
    profile: str | None,
    *,
    redox_pack: object | None = None,
) -> tuple[tuple[Any, ...], dict[str, str]]:
    """Run OpenIMCC's checker while preserving unknown active-source oxides.

    The registry helper walks the whole active table and aborts on its first
    unrecognized source. During that classification call only, capture the
    offending oxide and printed Ref at OpenIMCC's resolver primitive, let its
    helper finish, then omit that oxide from the active map. The checker can
    then classify its declaration as reference-unknown. The temporary hooks
    are serialized and thread-scoped; solve calls run after they are restored.

    INTERIM: remove this hook when t-1647 re-pins to the OpenIMCC release that
    handles per-oxide unknown references in active_liquid_standards. The lock
    serializes our patch windows; the thread check delegates unrelated callers
    to the original functions while the module attributes are patched. Both
    attributes are restored in finally before any solve call.
    """
    gas = importlib.import_module("openimcc.gas")
    standards = importlib.import_module("openimcc.standards")
    unrecognized_sources: dict[str, str] = {}
    lookup_thread = threading.get_ident()

    with _SOURCE_LOOKUP_LOCK:
        original_active_lookup = standards.active_liquid_standards
        original_source_resolver = gas._condensate_source_table_id

        def active_liquid_standards(profile_value: str | None = None):
            if threading.get_ident() != lookup_thread:
                return original_active_lookup(profile_value)

            def resolve_source(
                species: str,
                phase: str,
                source_ref: str,
                oxide_df: Any,
            ) -> str | object | None:
                if threading.get_ident() != lookup_thread:
                    return original_source_resolver(
                        species, phase, source_ref, oxide_df
                    )
                try:
                    return original_source_resolver(
                        species, phase, source_ref, oxide_df
                    )
                except gas.ImccGasUnrecognizedSourceError:
                    unrecognized_sources[str(species)] = str(source_ref)
                    return _UNRECOGNIZED_SOURCE

            gas._condensate_source_table_id = resolve_source
            try:
                active = dict(original_active_lookup(profile_value))
            finally:
                gas._condensate_source_table_id = original_source_resolver
            for oxide in unrecognized_sources:
                active.pop(oxide, None)
            return active

        standards.active_liquid_standards = active_liquid_standards
        try:
            records = openimcc.check_pack_standards(
                pack,
                profile,
                redox_pack=redox_pack,
            )
        finally:
            standards.active_liquid_standards = original_active_lookup
            gas._condensate_source_table_id = original_source_resolver

    normalized = tuple(
        replace(record, kind="reference-unknown", active=None)
        if record.oxide in unrecognized_sources
        and record.kind in {"mismatch", "redox-mismatch"}
        else record
        for record in records
    )
    return normalized, unrecognized_sources


@dataclass(frozen=True)
class LiquidStandardRefusal:
    kind: str
    oxide: str
    declared_standard_id: str
    active_standard_id: str | None
    row_key: str | None
    pack: str

    @property
    def reason(self) -> str:
        return (
            f"kind={self.kind}; oxide={self.oxide}; "
            f"declared={self.declared_standard_id}; "
            f"active={self.active_standard_id or '<missing>'}; pack={self.pack}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "oxide": self.oxide,
            "declared_standard_id": self.declared_standard_id,
            "active_standard_id": self.active_standard_id,
            "row_key": self.row_key,
            "pack": self.pack,
        }


@dataclass(frozen=True)
class LiquidStandardFlag:
    kind: str
    oxide: str
    standard_id: str
    active_standard_id: str | None
    basis: str
    citation: str
    row_key: str
    pack: str

    @property
    def reason(self) -> str:
        return (
            f"openimcc_liquid_standard; kind={self.kind}; pack={self.pack}; "
            f"row={self.row_key}; oxide={self.oxide}; "
            f"standard_id={self.standard_id}; "
            f"active_standard_id={self.active_standard_id or 'unknown'}; "
            f"basis={self.basis}; citation={self.citation}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "oxide": self.oxide,
            "standard_id": self.standard_id,
            "active_standard_id": self.active_standard_id,
            "basis": self.basis,
            "citation": self.citation,
            "row_key": self.row_key,
            "pack": self.pack,
        }


@dataclass(frozen=True)
class LiquidStandardAssessment:
    refusals: tuple[LiquidStandardRefusal, ...]
    flags: tuple[LiquidStandardFlag, ...]


class LiquidStandardMissingError(ValueError):
    """A parent oxide has no declared or usable standard Gibbs function."""

    code = "openimcc_liquid_standard_missing"
    reason_code = code

    def __init__(self, oxide: str, standard_id: str | None, detail: str) -> None:
        self.oxide = str(oxide)
        self.standard_id = None if standard_id is None else str(standard_id)
        self.detail = str(detail)
        failure = self.as_dict()
        self.diagnostics = {
            "invalid_input": True,
            "category": 1,
            "standard_failures": [failure],
        }
        super().__init__(
            f"missing liquid standard for {self.oxide} "
            f"({self.standard_id or 'undeclared'}): {self.detail}"
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": "missing-standard",
            "oxide": self.oxide,
            "standard_id": self.standard_id,
            "detail": self.detail,
        }


def _gas_module() -> Any:
    return importlib.import_module("openimcc.gas")


@lru_cache(maxsize=1)
def _packaged_gas_datapack() -> Any:
    """Load canonical packaged condensate rows, independent of table overrides."""
    gas = _gas_module()
    return gas.load_gas_datapack(
        gas_path=gas._packaged_resource(gas._PACKAGED_GAS_NAME),
        oxide_path=gas._packaged_resource(gas._PACKAGED_CONDENSATE_NAME),
    )


@lru_cache(maxsize=1)
def _d066_k_translations() -> tuple[dict[str, Any], ...]:
    resource = resources.files("openimcc.data").joinpath(
        "packs", "imcc-sf04-d066-v1.json"
    )
    data = json.loads(resource.read_text(encoding="utf-8"))
    return tuple(
        row
        for row in data.get("rows", ())
        if isinstance(row, dict) and isinstance(row.get("k_translation"), dict)
    )


def _source_standard_id(gas: Any, oxide: str, row: Any, gas_pack: Any) -> str:
    source_ref = str(row.get("Ref", "")).strip()
    if not source_ref:
        return ""
    try:
        mapped = gas._condensate_source_table_id(
            oxide, "l", source_ref, gas_pack.oxide_df
        )
    except gas.ImccGasUnrecognizedSourceError:
        # Unknown but present table rows still carry G0(T); preserve their
        # printed Ref as the active standard and disclose it to the caller.
        mapped = source_ref
    return str(mapped or source_ref)


def _gibbs_for_standard(
    gas: Any,
    gas_pack: Any,
    oxide: str,
    standard_id: str,
    temperature_K: float,
) -> tuple[float, bool] | None:
    row_name = f"{oxide}(l)"
    if row_name not in gas_pack.oxide_df.index:
        return None
    candidates = gas_pack.oxide_df.loc[gas_pack.oxide_df.index == row_name]
    matching_indices = []
    for index, row in candidates.iterrows():
        if _source_standard_id(gas, oxide, row, gas_pack) == standard_id:
            matching_indices.append(index)
    if not matching_indices:
        return None
    selected_rows = candidates.loc[matching_indices]
    row = gas._nearest_interval_row(
        selected_rows, row_name, temperature_K, allow_extrapolation=True
    )
    g0 = float(gas._lamor_gibbs(temperature_K, row))
    if not math.isfinite(g0):
        return None
    extrapolated = not (
        float(row["T_min"]) <= temperature_K <= float(row["T_max"])
    )
    return g0, extrapolated


def _d066_translation_to_packaged_standard(
    gas: Any,
    oxide: str,
    source_standard_id: str,
    temperature_K: float,
) -> tuple[str, float, bool] | None:
    """Return source-to-D066 log10 activity offset from its recorded K fit."""
    packaged = _packaged_gas_datapack()
    source_marker = source_standard_id.split("-", 1)[0]
    candidates: list[tuple[str, float, bool]] = []
    for row in _d066_k_translations():
        translation = row["k_translation"]
        basis = str(translation.get("basis", ""))
        stoichiometry = row.get("nu", {})
        nu = (
            float(stoichiometry.get(oxide, 0.0))
            if isinstance(stoichiometry, dict)
            else 0.0
        )
        if (
            not source_marker
            or source_marker not in basis
            or oxide not in basis
            or nu == 0.0
        ):
            continue
        delta_A = float(translation["delta_A"])
        delta_B_K = float(translation["delta_B_K"])
        # The fit stores log10(K_D066/K_pack). For K reactions with this
        # positive parent stoichiometry, the activity translation is its
        # negative divided by nu, source pack -> D066 standard.
        log10_offset = -(delta_A + delta_B_K / temperature_K) / nu
        reference = str(translation.get("new_reference", ""))
        target_standards = {
            _source_standard_id(gas, oxide, table_row, packaged)
            for index, table_row in packaged.oxide_df.iterrows()
            if index == f"{oxide}(l)"
            and _source_standard_id(gas, oxide, table_row, packaged) in reference
        }
        if len(target_standards) != 1:
            continue
        temperature_domain = translation.get("T_domain_K")
        extrapolated = bool(
            isinstance(temperature_domain, list)
            and len(temperature_domain) == 2
            and not (
                float(temperature_domain[0])
                <= temperature_K
                <= float(temperature_domain[1])
            )
        )
        target_standard = next(iter(target_standards))
        target_gibbs = _gibbs_for_standard(
            gas, packaged, oxide, target_standard, temperature_K
        )
        if target_gibbs is None:
            continue
        candidates.append(
            (
                next(iter(target_standards)),
                log10_offset,
                extrapolated or target_gibbs[1],
            )
        )
    if not candidates:
        return None
    first = candidates[0]
    if any(
        target != first[0]
        or not math.isclose(offset, first[1], abs_tol=1e-10)
        for target, offset, _ in candidates[1:]
    ):
        raise LiquidStandardMissingError(
            oxide,
            source_standard_id,
            "the D066 standard translation rows disagree at this temperature",
        )
    return first[0], first[1], any(
        extrapolated for _, _, extrapolated in candidates
    )


def convert_parent_activities_for_gas(
    parent_activities: Mapping[str, float],
    temperature_K: float,
    melt_pack: object,
    gas_pack: object | None,
) -> tuple[dict[str, float], tuple[dict[str, Any], ...]]:
    """Convert pack-native parent activities to the active gas-table standards.

    For one species at T, both standards describe the same chemical potential:
    μ = G0_pack + RT ln(a_pack) = G0_active + RT ln(a_active). Solving gives
    a_active/a_pack = exp((G0_pack − G0_active)/RT), so the sign is pack G0
    minus active G0. Equal standards give factor 1; positive (G0_pack −
    G0_active) raises a(active), as the exponential requires.
    """
    if not math.isfinite(float(temperature_K)) or float(temperature_K) <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    import openimcc

    gas = _gas_module()
    if gas_pack is None:
        gas_pack = gas.load_gas_datapack()
    temperature = float(temperature_K)
    declared = openimcc.pack_liquid_standards(melt_pack).standards
    activities = {str(oxide): float(value) for oxide, value in parent_activities.items()}
    notices: list[dict[str, Any]] = []
    packaged = None
    gas_channels, _omissions = gas._default_reactions(
        tuple(activities), gas_pack
    )
    coupled_oxides = {
        str(reaction[1][0])
        for reaction in gas_channels
        if reaction[1] and reaction[1][0]
    }

    for oxide, activity in tuple(activities.items()):
        if oxide not in coupled_oxides:
            continue
        pack_standard = declared.get(oxide)
        if not pack_standard:
            raise LiquidStandardMissingError(
                oxide, None, "the melt pack does not declare a liquid standard"
            )
        row_name = f"{oxide}(l)"
        if row_name not in gas_pack.oxide_df.index:
            raise LiquidStandardMissingError(
                oxide, str(pack_standard), "the active gas table has no G0(T) row"
            )
        active_row = gas._oxide_row_for_T(
            gas_pack.oxide_df, row_name, temperature, allow_extrapolation=True
        )
        active_standard = _source_standard_id(gas, oxide, active_row, gas_pack)
        if not active_standard:
            raise LiquidStandardMissingError(
                oxide, str(pack_standard), "the active gas row has no standard id"
            )
        if str(pack_standard) == active_standard:
            continue

        active_gibbs = float(gas._lamor_gibbs(temperature, active_row))
        extrapolated = not (
            float(active_row["T_min"]) <= temperature <= float(active_row["T_max"])
        )
        pack_gibbs_result = _gibbs_for_standard(
            gas, gas_pack, oxide, str(pack_standard), temperature
        )
        if pack_gibbs_result is None:
            packaged = packaged or _packaged_gas_datapack()
            pack_gibbs_result = _gibbs_for_standard(
                gas, packaged, oxide, str(pack_standard), temperature
            )
        if pack_gibbs_result is None:
            translation = _d066_translation_to_packaged_standard(
                gas, oxide, str(pack_standard), temperature
            )
            if translation is None:
                raise LiquidStandardMissingError(
                    oxide,
                    str(pack_standard),
                    "no G0(T) row or recorded D066 translation is available",
                )
            target_standard, source_to_d066_offset, translation_extrapolated = (
                translation
            )
            assert packaged is not None
            target_gibbs_result = _gibbs_for_standard(
                gas, packaged, oxide, target_standard, temperature
            )
            assert target_gibbs_result is not None
            pack_gibbs_result = (
                target_gibbs_result[0]
                + gas.R_J_MOL_K * temperature * math.log(10.0) * source_to_d066_offset,
                target_gibbs_result[1] or translation_extrapolated,
            )

        pack_gibbs, pack_extrapolated = pack_gibbs_result
        gibbs_difference = float(pack_gibbs) - active_gibbs
        log10_offset = gibbs_difference / (
            gas.R_J_MOL_K * temperature * math.log(10.0)
        )
        activity_factor = math.exp(
            gibbs_difference / (gas.R_J_MOL_K * temperature)
        )
        activities[oxide] = activity * activity_factor
        notices.append(
            {
                "kind": "openimcc_liquid_standard_conversion",
                "code": "openimcc_liquid_standard_conversion",
                "oxide": oxide,
                "standard_id": str(pack_standard),
                "active_standard_id": active_standard,
                "temperature_K": temperature,
                "log10_offset": log10_offset,
                "extrapolated": extrapolated or pack_extrapolated,
            }
        )
    return activities, tuple(notices)


@dataclass(frozen=True)
class _PackRecords:
    role: str
    pack: object
    records: tuple[Any, ...]
    rows: Mapping[str, frozenset[str]]
    row_standards: Mapping[str, Any]


def _oxide_name(value: object) -> str:
    return str(value).removesuffix("(l)")


def _melt_rows(pack: object) -> dict[str, frozenset[str]]:
    kernel = getattr(pack, "kernel_datapack", None)
    reactions = getattr(kernel, "reactions", ())
    parents = getattr(kernel, "parent_oxides", ())
    stoichiometry = getattr(kernel, "nu", None)
    if stoichiometry is None:
        return {}
    return {
        str(row_key): frozenset(
            _oxide_name(parent)
            for parent_index, parent in enumerate(parents)
            if float(stoichiometry[parent_index, row_index]) != 0.0
        )
        for row_index, row_key in enumerate(reactions)
    }


def _redox_rows(pack: object) -> dict[str, frozenset[str]]:
    content = getattr(pack, "content", {})
    if not isinstance(content, Mapping):
        return {}
    parent_oxides = {
        _oxide_name(parent.get("parent_oxide"))
        for parent in content.get("parent_registry", ())
        if isinstance(parent, Mapping)
        and isinstance(parent.get("parent_oxide"), str)
        and "O" in str(parent["parent_oxide"])
    }
    rows: dict[str, frozenset[str]] = {}
    for row in content.get("rows", ()):
        if not isinstance(row, Mapping):
            continue
        species: set[str] = set()
        for side in ("reactants", "products"):
            terms = row.get(side, {})
            if isinstance(terms, Mapping):
                species.update(str(name) for name in terms)
        reactants = frozenset(
            oxide
            for name in species
            if (oxide := _oxide_name(name)) in parent_oxides
        )
        row_key = row.get("complex") or row.get("row")
        if row_key is not None:
            rows[str(row_key)] = reactants
    return rows


def _pack_label(pack: object, role: str) -> str:
    if role == "redox":
        content = getattr(pack, "content", {})
        version = (
            content.get("version", "unknown")
            if isinstance(content, Mapping)
            else "unknown"
        )
        return f"openimcc-redox-pack {version}"
    model_id = str(getattr(pack, "model_id", "openimcc melt pack"))
    version = str(getattr(pack, "version", "unknown"))
    return f"{model_id} {version}"


def _row_standards(
    pack: object,
    rows: Mapping[str, frozenset[str]],
    records: Sequence[Any],
) -> dict[str, Any]:
    import openimcc

    row_keys: set[str] = set()
    for record in records:
        if record.kind not in _FLAG_KINDS:
            continue
        candidates = (record.row_key,) if record.row_key is not None else rows.keys()
        row_keys.update(
            row_key
            for row_key in candidates
            if row_key in rows and record.oxide in rows[row_key]
        )
    return {
        row_key: openimcc.pack_row_liquid_standards(pack, row_key)
        for row_key in row_keys
    }


def _positive_composition_oxides(
    composition: Mapping[str, object],
) -> frozenset[str]:
    oxides: set[str] = set()
    for name, raw_amount in composition.items():
        if isinstance(raw_amount, bool):
            continue
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0.0:
            continue
        oxide = _oxide_name(name)
        oxides.add("FeO" if oxide == "FeO_total" else oxide)
        if oxide == "Fe2O3":
            # The simulator folds ferric oxide onto OpenIMCC's FeO basis.
            oxides.add("FeO")
    return frozenset(oxides)


def assess_liquid_standards(
    melt_pack: object,
    redox_pack: object | None,
    profile: str | None,
    composition: Mapping[str, object],
) -> LiquidStandardAssessment:
    """Return typed refusal and composition-dependent flag records.

    Standards are compared only by OpenIMCC's registry checker. A flagged row
    is a dependency when every oxide in its reaction is present above zero in
    the evaluated composition. This intentionally over-flags uncertain active
    rows, which is the safe disclosure direction until OpenIMCC reports rows
    that entered the solve.
    """
    import openimcc

    melt_records, unrecognized_sources = _check_pack_standards(
        openimcc, melt_pack, profile
    )
    if redox_pack is None:
        melt_rows = _melt_rows(melt_pack)
        records_by_pack = (
            _PackRecords(
                "melt",
                melt_pack,
                melt_records,
                melt_rows,
                _row_standards(melt_pack, melt_rows, melt_records),
            ),
        )
    else:
        all_records, redox_unrecognized_sources = _check_pack_standards(
            openimcc,
            melt_pack,
            profile,
            redox_pack=redox_pack,
        )
        unrecognized_sources.update(redox_unrecognized_sources)
        redox_records = all_records[len(melt_records):]
        melt_rows = _melt_rows(melt_pack)
        redox_rows = _redox_rows(redox_pack)
        records_by_pack = (
            _PackRecords(
                "melt",
                melt_pack,
                melt_records,
                melt_rows,
                _row_standards(melt_pack, melt_rows, melt_records),
            ),
            _PackRecords(
                "redox",
                redox_pack,
                redox_records,
                redox_rows,
                _row_standards(redox_pack, redox_rows, redox_records),
            ),
        )

    refusals = tuple(
        LiquidStandardRefusal(
            kind=str(record.kind),
            oxide=str(record.oxide),
            declared_standard_id=str(record.declared),
            active_standard_id=(
                None if record.active is None else str(record.active)
            ),
            row_key=None if record.row_key is None else str(record.row_key),
            pack=_pack_label(pack_records.pack, pack_records.role),
        )
        for pack_records in records_by_pack
        for record in pack_records.records
        if record.kind in _REFUSAL_KINDS
    )
    # Preserve dependent disclosures even when a separate row causes refusal;
    # flag-only findings do not become additional refusal causes.
    present_oxides = _positive_composition_oxides(composition)
    flags: list[LiquidStandardFlag] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for pack_records in records_by_pack:
        for record in pack_records.records:
            if record.kind not in _FLAG_KINDS:
                continue
            candidates = (
                (record.row_key,)
                if record.row_key is not None
                else pack_records.rows.keys()
            )
            for row_key in candidates:
                reaction_oxides = pack_records.rows.get(row_key)
                effective = pack_records.row_standards.get(row_key)
                if (
                    reaction_oxides is None
                    or effective is None
                    or record.oxide not in reaction_oxides
                    or not reaction_oxides.issubset(present_oxides)
                ):
                    continue
                if record.kind in {
                    "reference-unknown",
                    "mismatch",
                }:
                    if effective.get(record.oxide) != record.declared:
                        continue
                    standard_id = record.declared
                    basis = effective.standard_basis.get(record.oxide) or "unknown"
                    citation = (
                        effective.standard_citations.get(record.oxide)
                        or record.citation
                        or "unknown"
                    )
                    source_ref = unrecognized_sources.get(record.oxide)
                    if record.kind == "reference-unknown" and source_ref is not None:
                        citation = (
                            f"{citation}; {_UNRECOGNIZED_SOURCE_REASON}; "
                            f"printed Ref: {source_ref}"
                        )
                else:
                    if (
                        effective.get(record.oxide) != record.standard_id
                        or effective.standard_basis.get(record.oxide) != record.basis
                    ):
                        continue
                    standard_id = record.standard_id or record.declared
                    basis = record.basis or "unknown"
                    citation = record.citation or "unknown"
                key = (
                    pack_records.role,
                    str(row_key),
                    str(record.oxide),
                    str(record.kind),
                    str(standard_id),
                )
                if key in seen:
                    continue
                seen.add(key)
                flags.append(
                    LiquidStandardFlag(
                        kind=str(record.kind),
                        oxide=str(record.oxide),
                        standard_id=str(standard_id),
                        active_standard_id=(
                            None if record.active is None else str(record.active)
                        ),
                        basis=str(basis),
                        citation=str(citation),
                        row_key=str(row_key),
                        pack=_pack_label(pack_records.pack, pack_records.role),
                    )
                )
    return LiquidStandardAssessment(refusals=refusals, flags=tuple(flags))


__all__ = [
    "LiquidStandardAssessment",
    "LiquidStandardFlag",
    "LiquidStandardMissingError",
    "LiquidStandardRefusal",
    "assess_liquid_standards",
    "convert_parent_activities_for_gas",
]
