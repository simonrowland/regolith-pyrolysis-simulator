"""KEMS vs Langmuir kinetics anchors and alpha provenance guards (VR-9).

Owner ruling (FEEDBACK-ROUND-1 §7): bulk-check standalone-species kinetics
against **KEMS** (Knudsen effusion mass spectrometry) **and** the second
(lower-backpressure / **Langmuir free-evaporation**) mass-spec database as
**distinct experimental regimes**. Alpha carries cited kinetics provenance and
is **never** fit from VapoRock.

These loaders are diagnostic validation anchors. They do not write flux, do
not promote rows, and never certify.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

import yaml

from simulator.scalar_boundary import is_declared_real_scalar

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEMS_SIDECAR: Final[Path] = (
    _REPO_ROOT / "data" / "literature" / "vapour_rail_kems_anchors.yaml"
)
DEFAULT_LANGMUIR_SIDECAR: Final[Path] = (
    _REPO_ROOT / "data" / "literature" / "vapour_rail_langmuir_anchors.yaml"
)

# Tokens that must never appear as an evaporation-alpha provenance source.
# Pressure-path VapoRock calibration is a separate evidence class; alpha is
# kinetic and must be grounded in KEMS/Langmuir (or other measured kinetics).
FORBIDDEN_ALPHA_SOURCE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "vaporock",
        "vaporock_fit",
        "vaporock-fit",
        "fit_from_vaporock",
        "fit-from-vaporock",
        "backsolved_from_vaporock",
        "pseudo_psat_backsolved_from_vaporock",
        "analytical:vaporock_calibrated",  # pressure class — not an alpha source
    }
)

# Honest narrative rejections of VapoRock as an alpha source. Longer phrases
# first so stripping does not leave residual "vaporock" fragments that re-trip
# the denylist. Matched on lowercased (not compact) text.
_ALPHA_VAPOROCK_REJECTION_PHRASES: Final[tuple[str, ...]] = (
    "never fit from vaporock",
    "not a vaporock fit",
    "forbidden vaporock",
    "forbids vaporock",
    "not vaporock",
    "no vaporock",
)

# Compact (lowercased, punctuation/whitespace stripped) fit / derivation
# markers. Scanned on the residual AFTER rejection-phrase stripping so that
# honest "not a vaporock fit" does not itself match "vaporockfit", while
# whitewash + smuggled fit tokens still fail. Denylist wins over allowlist.
_ALPHA_VAPOROCK_FIT_MARKERS: Final[tuple[str, ...]] = (
    "vaporockfit",
    "fitfromvaporock",
    "backsolvedfromvaporock",
    "pseudopsatbacksolvedfromvaporock",
    "fromvaporock",
    "vaporockcalibrated",
    "analyticalvaporockcalibrated",
    "againstvaporock",
    "vaporockderived",
    "withvaporock",
    "usingvaporock",
)


def _compact_alnum_lower(text: str) -> str:
    """Lowercase and strip to alphanumeric only (punctuation/whitespace gone)."""

    return "".join(ch for ch in text.lower() if ch.isalnum())


class KineticsExperimentalRegime(str, Enum):
    """Distinct experimental regimes for kinetics grounding.

    KEMS (effusion / equilibrium cell) measures the thermodynamic driving
    force under orifice-limited transport. Langmuir free-evaporation measures
    the kinetic coefficient alpha at low back-pressure. The two must not be
    collapsed into one "mass-spec" bucket when anchoring alpha (owner R1 §7;
    henrian-correlations.md / vp-acquire-5 grounding for regime vocabulary).
    """

    KEMS_EFFUSION = "kems_effusion"
    LANGMUIR_FREE_EVAPORATION = "langmuir_free_evaporation"


@dataclass(frozen=True)
class KineticsAnchorRecord:
    """One literature kinetics anchor with a single declared regime."""

    record_id: str
    species: str
    regime: KineticsExperimentalRegime
    temperature_range_K: tuple[float, float]
    citation: str
    doi_or_url: str | None
    alpha_value: float | None
    alpha_range: tuple[float, float] | None
    material: str | None
    extraction_note: str | None
    source_sidecar: str
    certifies: bool = False

    def __post_init__(self) -> None:
        if self.certifies:
            raise ValueError(
                f"kinetics anchor {self.record_id!r} must not certify "
                "(diagnostic-only ceiling)"
            )


@dataclass(frozen=True)
class AlphaProvenance:
    """Cited evaporation-alpha provenance; never a VapoRock fit."""

    species: str
    value: float | None
    source: str
    tier: int | None
    regime: KineticsExperimentalRegime | None
    temperature_range_K: tuple[float, float] | None
    envelope: tuple[float, float] | None
    status: str  # measured | policy_no_data | refused

    @property
    def authority(self) -> bool:
        return False

    def may_certify(self) -> bool:
        return False


class KineticsAnchorError(ValueError):
    """Invalid kinetics anchor payload or provenance violation."""


def _anchor_float(value: Any, *, species: str, field: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise KineticsAnchorError(f"{species}: {field} must be numeric")
    return float(value)


def load_kems_anchors(path: Path | None = None) -> tuple[KineticsAnchorRecord, ...]:
    """Load the KEMS-only rail sidecar; every row is regime=KEMS_EFFUSION."""

    return _load_regime_sidecar(
        path or DEFAULT_KEMS_SIDECAR,
        expected_regime=KineticsExperimentalRegime.KEMS_EFFUSION,
    )


def load_langmuir_anchors(
    path: Path | None = None,
) -> tuple[KineticsAnchorRecord, ...]:
    """Load the Langmuir-only rail sidecar; free-evaporation regime only."""

    return _load_regime_sidecar(
        path or DEFAULT_LANGMUIR_SIDECAR,
        expected_regime=KineticsExperimentalRegime.LANGMUIR_FREE_EVAPORATION,
    )


def regimes_remain_distinct(
    kems: Sequence[KineticsAnchorRecord],
    langmuir: Sequence[KineticsAnchorRecord],
) -> bool:
    """True iff the two collections never share a regime token."""

    kems_regimes = {record.regime for record in kems}
    langmuir_regimes = {record.regime for record in langmuir}
    if not kems_regimes and not langmuir_regimes:
        return True
    if KineticsExperimentalRegime.LANGMUIR_FREE_EVAPORATION in kems_regimes:
        return False
    if KineticsExperimentalRegime.KEMS_EFFUSION in langmuir_regimes:
        return False
    return kems_regimes.isdisjoint(langmuir_regimes) or (
        kems_regimes == {KineticsExperimentalRegime.KEMS_EFFUSION}
        and langmuir_regimes
        == {KineticsExperimentalRegime.LANGMUIR_FREE_EVAPORATION}
    )


def assert_alpha_source_not_vaporock(source: str | None) -> None:
    """Refuse any alpha provenance that is a VapoRock fit or synonym.

    Order of evaluation (denylist wins):

    1. Strip known narrative-rejection phrases from the lowercased source.
    2. Scan the **residual** (alnum-compact) for fit/derivation markers and
       any remaining ``vaporock`` occurrence — residual hits always refuse,
       even when a whitewash phrase was also present.
    3. Narrative allowlist therefore applies only when the residual has
       **zero** fit markers and **zero** residual ``vaporock`` tokens
       (honest rejection of VapoRock as a source, nothing smuggled).

    Matching is case-insensitive and punctuation/whitespace-insensitive for
    markers (``against vaporock``, ``vaporock-derived``, ``VapoRock`` all
    collapse to compact alnum forms).
    """

    if source is None:
        raise KineticsAnchorError("alpha provenance source is required")
    text = str(source).strip()
    if not text:
        raise KineticsAnchorError("alpha provenance source is required")
    lower = text.lower()

    # Exact forbidden identity / synonym on the whole source string.
    if lower.strip() in FORBIDDEN_ALPHA_SOURCE_TOKENS:
        raise KineticsAnchorError(
            f"evaporation alpha must not be fit from VapoRock; got {source!r}"
        )

    # Strip narrative rejections first so residual denylist sees only the
    # non-rejection content. Longer phrases already ordered first.
    residual_lower = lower
    for phrase in _ALPHA_VAPOROCK_REJECTION_PHRASES:
        residual_lower = residual_lower.replace(phrase, " ")
    residual_compact = _compact_alnum_lower(residual_lower)

    # Fit-marker denylist FIRST on residual — denylist wins over whitewash.
    if any(marker in residual_compact for marker in _ALPHA_VAPOROCK_FIT_MARKERS):
        raise KineticsAnchorError(
            f"evaporation alpha must not be fit from VapoRock; got {source!r}"
        )

    # Any residual "vaporock" (bare identity, "against vaporock", prose, …)
    # requires a rejection phrase that fully accounts for it. Residual hit
    # means the allowlist did not cover this occurrence.
    if "vaporock" in residual_compact:
        raise KineticsAnchorError(
            f"evaporation alpha must not be fit from VapoRock; got {source!r}"
        )

    # Residual clean: either no VapoRock mention, or only honest rejection
    # phrasing with zero smuggled fit markers — allow.


def alpha_provenance_from_mapping(
    species: str,
    payload: Mapping[str, Any],
    *,
    regime: KineticsExperimentalRegime | None = None,
) -> AlphaProvenance:
    """Build :class:`AlphaProvenance` from a vaporisation_coefficients mapping.

    Accepts the schema-v2 shape (``value`` / ``source`` / domain) and the
    legacy numeric-plus-source shape. Status ``no_data`` is preserved as
    policy, never invented into a numeric alpha.
    """

    if not isinstance(payload, Mapping):
        raise KineticsAnchorError(f"{species}: alpha payload must be a mapping")

    status = str(payload.get("status") or "measured").strip().lower()
    if status in {"no_data", "absent", "unmeasured"}:
        source = str(
            payload.get("source")
            or payload.get("provenance")
            or "policy_no_data"
        )
        assert_alpha_source_not_vaporock(source)
        return AlphaProvenance(
            species=species,
            value=None,
            source=source,
            tier=None,
            regime=regime,
            temperature_range_K=None,
            envelope=None,
            status="policy_no_data",
        )

    source = payload.get("source") or payload.get("citation") or payload.get(
        "provenance"
    )
    if source is None and "value" in payload:
        # Nested forms still require an explicit source string.
        source = payload.get("source_note")
    if source is None:
        raise KineticsAnchorError(
            f"{species}: alpha requires a cited source (never implicit VapoRock)"
        )
    source_text = str(source)
    assert_alpha_source_not_vaporock(source_text)

    value = payload.get("value")
    legacy_alpha = payload.get("alpha")
    if (
        value is None
        and is_declared_real_scalar(legacy_alpha)
        and isinstance(legacy_alpha, (int, float))
    ):
        value = legacy_alpha
    # Temperature-dependent correlation form (e.g. SiO alpha_s(T) = A exp(-B/T)).
    # Provenance still requires a cited source; the numeric point is left None
    # and status records the correlation rather than inventing a scalar.
    correlation = None
    if isinstance(value, Mapping):
        correlation = dict(value)
        nested_source = correlation.get("cite") or correlation.get("source")
        if nested_source is not None:
            source_text = str(nested_source)
            assert_alpha_source_not_vaporock(source_text)
        value = None
        status_token = "correlation"
    else:
        status_token = "measured"
        if value is not None:
            value = _anchor_float(value, species=species, field="alpha value")
            if not (value >= 0.0):
                raise KineticsAnchorError(f"{species}: alpha value must be >= 0")

    envelope = None
    raw_env = payload.get("envelope") or payload.get("uncertainty_envelope")
    if isinstance(raw_env, (list, tuple)) and len(raw_env) == 2:
        envelope = tuple(
            _anchor_float(item, species=species, field="uncertainty envelope")
            for item in raw_env
        )
    elif correlation is not None:
        raw_env = correlation.get("uncertainty_envelope")
        if isinstance(raw_env, (list, tuple)) and len(raw_env) == 2:
            envelope = tuple(
                _anchor_float(
                    item,
                    species=species,
                    field="uncertainty envelope",
                )
                for item in raw_env
            )

    t_range = None
    raw_t = (
        payload.get("temperature_range_K")
        or payload.get("valid_range_K")
        or payload.get("T_band_K")
    )
    if isinstance(raw_t, (list, tuple)) and len(raw_t) == 2:
        t_range = tuple(
            _anchor_float(item, species=species, field="temperature range")
            for item in raw_t
        )
    elif correlation is not None:
        raw_t = correlation.get("valid_range_K")
        if isinstance(raw_t, (list, tuple)) and len(raw_t) == 2:
            t_range = tuple(
                _anchor_float(item, species=species, field="temperature range")
                for item in raw_t
            )

    tier = payload.get("tier")
    if tier is not None:
        if not is_declared_real_scalar(tier, allow_numeric_str=True):
            raise KineticsAnchorError(f"{species}: tier must be numeric")
        tier = int(tier)

    if value is None and status_token != "correlation":
        status_token = "policy_no_data"

    return AlphaProvenance(
        species=species,
        value=value,
        source=source_text,
        tier=tier,
        regime=regime,
        temperature_range_K=t_range,
        envelope=envelope,
        status=status_token,
    )


def _load_regime_sidecar(
    path: Path,
    *,
    expected_regime: KineticsExperimentalRegime,
) -> tuple[KineticsAnchorRecord, ...]:
    if not path.is_file():
        raise KineticsAnchorError(f"kinetics anchor sidecar missing: {path}")
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, Mapping):
        raise KineticsAnchorError(f"{path}: root must be a mapping")
    declared = str(payload.get("experimental_regime") or "").strip()
    if declared != expected_regime.value:
        raise KineticsAnchorError(
            f"{path}: experimental_regime must be {expected_regime.value!r}, "
            f"got {declared!r}"
        )
    records_raw = payload.get("records")
    if not isinstance(records_raw, list) or not records_raw:
        raise KineticsAnchorError(f"{path}: records must be a non-empty list")

    out: list[KineticsAnchorRecord] = []
    for item in records_raw:
        if not isinstance(item, Mapping):
            raise KineticsAnchorError(f"{path}: each record must be a mapping")
        record_regime = str(item.get("regime") or declared).strip()
        if record_regime != expected_regime.value:
            raise KineticsAnchorError(
                f"{path}: record {item.get('record_id')!r} regime "
                f"{record_regime!r} collides with sidecar regime "
                f"{expected_regime.value!r}"
            )
        t_range = item.get("temperature_range_K")
        if not isinstance(t_range, (list, tuple)) or len(t_range) != 2:
            raise KineticsAnchorError(
                f"{path}: record {item.get('record_id')!r} needs "
                "temperature_range_K: [low, high]"
            )
        alpha_value = item.get("alpha_value")
        if alpha_value is not None:
            alpha_value = float(alpha_value)
        alpha_range = item.get("alpha_range")
        if alpha_range is not None:
            if not isinstance(alpha_range, (list, tuple)) or len(alpha_range) != 2:
                raise KineticsAnchorError(
                    f"{path}: alpha_range must be a length-2 sequence"
                )
            alpha_range = (float(alpha_range[0]), float(alpha_range[1]))

        source = item.get("citation") or item.get("source")
        if not source:
            raise KineticsAnchorError(
                f"{path}: record {item.get('record_id')!r} needs a citation"
            )
        if alpha_value is not None or alpha_range is not None:
            assert_alpha_source_not_vaporock(str(source))

        out.append(
            KineticsAnchorRecord(
                record_id=str(item["record_id"]),
                species=str(item["species"]),
                regime=expected_regime,
                temperature_range_K=(float(t_range[0]), float(t_range[1])),
                citation=str(source),
                doi_or_url=(
                    None
                    if item.get("doi_or_url") is None
                    else str(item.get("doi_or_url"))
                ),
                alpha_value=alpha_value,
                alpha_range=alpha_range,
                material=(
                    None if item.get("material") is None else str(item.get("material"))
                ),
                extraction_note=(
                    None
                    if item.get("extraction_note") is None
                    else str(item.get("extraction_note"))
                ),
                source_sidecar=str(path.relative_to(_REPO_ROOT)),
                certifies=bool(item.get("certifies", False)),
            )
        )
    return tuple(out)


__all__ = [
    "DEFAULT_KEMS_SIDECAR",
    "DEFAULT_LANGMUIR_SIDECAR",
    "FORBIDDEN_ALPHA_SOURCE_TOKENS",
    "AlphaProvenance",
    "KineticsAnchorError",
    "KineticsAnchorRecord",
    "KineticsExperimentalRegime",
    "alpha_provenance_from_mapping",
    "assert_alpha_source_not_vaporock",
    "load_kems_anchors",
    "load_langmuir_anchors",
    "regimes_remain_distinct",
]
