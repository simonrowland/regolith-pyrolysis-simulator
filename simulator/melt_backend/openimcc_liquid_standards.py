"""Domain owner for OpenIMCC pack liquid-reference checks and dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any


_REFUSAL_KINDS = frozenset({"mismatch", "undeclared", "redox-mismatch"})
_FLAG_KINDS = frozenset({"reference-unknown", "non-source-basis"})


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

    melt_records = tuple(openimcc.check_pack_standards(melt_pack, profile))
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
        all_records = tuple(
            openimcc.check_pack_standards(
                melt_pack, profile, redox_pack=redox_pack
            )
        )
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
    if refusals:
        return LiquidStandardAssessment(refusals=refusals, flags=())

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
                if record.kind == "reference-unknown":
                    if effective.get(record.oxide) != record.declared:
                        continue
                    standard_id = record.declared
                    basis = effective.standard_basis.get(record.oxide) or "unknown"
                    citation = (
                        effective.standard_citations.get(record.oxide)
                        or record.citation
                        or "unknown"
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
    return LiquidStandardAssessment(refusals=(), flags=tuple(flags))


__all__ = [
    "LiquidStandardAssessment",
    "LiquidStandardFlag",
    "LiquidStandardRefusal",
    "assess_liquid_standards",
]
