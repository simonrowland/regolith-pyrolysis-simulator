"""FeO(l) activity standard declarations and conversions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib import resources
from typing import Mapping, Sequence

from simulator.battery.decimal_tokens import decimal_token
from simulator.battery.enums import (
    Authority,
    Engine,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import Notice, ResidualRefusal
from simulator.physical_constants import GAS_CONSTANT


@dataclass(frozen=True)
class _FeOActivityStandard:
    name: str
    offset_j_mol: Decimal
    melt_pack_identity: Mapping[str, str] | None = None
    redox_certified_band_K: tuple[Decimal, Decimal] | None = None
    redox_range_flags: tuple[str, ...] = ()


_INTERNAL_ANALYTICAL_FEO_STANDARD = _FeOActivityStandard(
    name="Fe-019",
    offset_j_mol=Decimal(0),
)
_OPENIMCC_PUBLISHED_CORE_PACK_DIGEST = (
    "ae979b68295f6e653fb1c37d6b381f06cb0be8d4c5b421e673420a6e4596357c"
)
# Full condensate table served by OpenIMCC bae51f7 load_gas_datapack()
# (condensate-default-v1.csv; openimcc PACKAGED_FULL_CONDENSATE_TABLE_DIGEST).
# The 979adb2..bae51f7 diff does not touch that CSV, so this digest is unchanged.
# Green's 66ec4a3 table was b9c49790.... FeO(l) in the pinned table is the
# DG46-MASSON65 row, not the JANAF row that digest covered.
_OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST = (
    "493c67d5ba23b75767cc3ce4bd6e571bd2400162b3affdbbc581a7b0ae75362e"
)
_FEO_ACTIVITY_CANONICAL_STANDARD = "Fe-019"
_OPENIMCC_DEFAULT_PACK_VERSION = "1.0.6-d066-v4"
_OPENIMCC_DEFAULT_PACK_DIGEST = (
    "0879f5a8e94dfd844ed5cfcd900677b496a89e717a85f320b3e0b374ebcdc3b0"
)
_OPENIMCC_REDOX_DG46_FEO_COEFFICIENTS = {
    "dH298_R": Decimal("-26.668958359186785"),
    "dG_A": Decimal("8.03837194242113"),
    "dG_B": Decimal("6.588"),
    "dG_C": Decimal("-1.248"),
    "dG_D": Decimal("0.15"),
    "dG_E": Decimal("-0.007697"),
}


def _source_value(sources: Sequence[str], prefix: str) -> str | None:
    return next(
        (source[len(prefix):] for source in sources if source.startswith(prefix)),
        None,
    )


def _openimcc_redox_pack_feo_entry(redox_pack_digest: str) -> Mapping[str, object]:
    import openimcc
    from openimcc.redox_pack import RedoxInputError, load_redox_pack

    try:
        pack = load_redox_pack()
    except openimcc.ImccMalformedDatapackError as exc:
        raise ValueError(f"OpenIMCC redox datapack is malformed: {exc}") from exc
    except RedoxInputError:
        pack = None
    if pack is None or pack.canonical_digest != redox_pack_digest:
        pack_directory = resources.files("openimcc.data").joinpath("packs")
        for resource in sorted(pack_directory.iterdir(), key=lambda item: item.name):
            if not (
                resource.name.startswith("openimcc-redox-")
                and resource.name.endswith(".json")
            ):
                continue
            try:
                with resources.as_file(resource) as pack_path:
                    candidate = load_redox_pack(pack_path)
            except openimcc.ImccMalformedDatapackError as exc:
                raise ValueError(
                    f"OpenIMCC redox datapack is malformed: {exc}"
                ) from exc
            except RedoxInputError:
                continue
            if candidate.canonical_digest == redox_pack_digest:
                pack = candidate
                break
        else:
            raise ValueError(
                f"no installed OpenIMCC redox pack matches digest {redox_pack_digest}"
            )

    species = pack.content.get("thermo_species")
    species_entry = species.get("FeO(l)") if isinstance(species, Mapping) else None
    row = species_entry.get("row") if isinstance(species_entry, Mapping) else None
    if not isinstance(row, Mapping):
        raise ValueError("OpenIMCC redox FeO(l) row is missing")
    return species_entry


def _redox_pack_feo_row_refusal(
    row: Mapping[str, object],
) -> ResidualRefusal | None:
    for key, expected in _OPENIMCC_REDOX_DG46_FEO_COEFFICIENTS.items():
        try:
            actual = Decimal(str(row[key]))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            actual = None
        if actual != expected:
            return ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_redox_standard_row_mismatch",
                    "field": key,
                    "expected": str(expected),
                    "actual": None if actual is None else str(actual),
                },
            )
    return None


def _redox_feo_extrapolation_evidence(
    standard: _FeOActivityStandard,
    temperature_K: Decimal,
) -> Mapping[str, object] | None:
    band = standard.redox_certified_band_K
    if band is None or band[0] <= temperature_K <= band[1]:
        return None
    return {
        "authority": Authority.EXTRAPOLATED.value,
        "certified_band": {
            "temperature_K": [float(band[0]), float(band[1])]
        },
        "range_flags": list(standard.redox_range_flags),
    }


def _redox_melt_standard_refusal(
    standard: _FeOActivityStandard,
) -> ResidualRefusal | None:
    if standard.name == "DG46-MASSON65":
        return None
    return ResidualRefusal(
        RefusalReason.METRIC_DOMAIN,
        {
            "reason": "openimcc_feo_redox_melt_standard_mismatch",
            "melt_standard": standard.name,
            "redox_standard": "DG46-MASSON65",
        },
    )


def _resolve_feo_activity_standard(
    engine: Engine,
    coefficient_sources: Sequence[str],
    *,
    melt_pack_identity: Mapping[str, object] | None = None,
    temperature_K: Decimal | None = None,
) -> tuple[_FeOActivityStandard | None, ResidualRefusal | None]:
    if engine is Engine.INTERNAL_ANALYTICAL:
        return _INTERNAL_ANALYTICAL_FEO_STANDARD, None
    if engine is not Engine.OPENIMCC:
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {
                "reason": "feo_activity_standard_not_declared",
                "engine": engine.value,
            },
        )

    condensate_digest = _source_value(
        coefficient_sources,
        "openimcc-condensate-table:sha256:",
    )
    if condensate_digest is not None and condensate_digest != (
        _OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
    ):
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {
                "reason": "openimcc_feo_condensate_table_digest_mismatch",
                "expected_condensate_table_digest": (
                    _OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
                ),
                "actual_condensate_table_digest": condensate_digest,
            },
        )

    redox_pack_digest = _source_value(
        coefficient_sources,
        "openimcc-redox-pack:sha256:",
    )
    redox_certified_band_K = None
    redox_range_flags: tuple[str, ...] = ()
    if redox_pack_digest is not None:
        try:
            redox_entry = _openimcc_redox_pack_feo_entry(redox_pack_digest)
        except (ImportError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_redox_pack_unresolved",
                    "redox_pack_digest": redox_pack_digest,
                    "detail": str(exc),
                },
            )
        redox_row_refusal = _redox_pack_feo_row_refusal(redox_entry["row"])
        if redox_row_refusal is not None:
            return None, redox_row_refusal
        fit_residual = redox_entry.get("fit_residual")
        raw_band = (
            fit_residual.get("temperature_range")
            if isinstance(fit_residual, Mapping)
            else None
        )
        raw_flags = redox_entry.get("range_flags")
        try:
            if not isinstance(raw_band, (list, tuple)) or len(raw_band) != 2:
                raise ValueError("fit_residual.temperature_range must have two edges")
            band = (Decimal(str(raw_band[0])), Decimal(str(raw_band[1])))
            if (
                not all(edge.is_finite() for edge in band)
                or band[0] >= band[1]
                or not isinstance(raw_flags, (list, tuple))
                or not raw_flags
                or not all(isinstance(flag, str) for flag in raw_flags)
            ):
                raise ValueError("FeO(l) redox range metadata is invalid")
        except (InvalidOperation, TypeError, ValueError) as exc:
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_redox_standard_range_unavailable",
                    "redox_pack_digest": redox_pack_digest,
                    "detail": str(exc),
                },
            )
        redox_certified_band_K = band
        redox_range_flags = tuple(raw_flags)

    if temperature_K is None or temperature_K <= 0:
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "feo_activity_standard_temperature_missing"},
        )

    melt_binding_digest = _source_value(
        coefficient_sources,
        "openimcc-melt-binding:sha256:",
    )
    if melt_binding_digest is None:
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_melt_pack_identity_missing"},
        )
    if melt_pack_identity is not None:
        identity, identity_refusal = _validate_openimcc_melt_pack_identity(
            melt_pack_identity
        )
        if identity_refusal is not None:
            return None, identity_refusal
        assert identity is not None
        if melt_binding_digest != identity["binding_digest"]:
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_standard_melt_pack_identity_mismatch",
                    "identity_digest": identity["binding_digest"],
                    "coefficient_source_digest": melt_binding_digest,
                },
            )
        try:
            pack_name, pack_payload = _openimcc_melt_pack_payload(identity)
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            reason = (
                "openimcc_feo_standard_melt_pack_identity_unknown"
                if str(exc) == "melt pack identity is not a pinned OpenIMCC pack"
                else "openimcc_feo_standard_pack_data_unavailable"
            )
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {"reason": reason, "melt_pack_identity": identity, "detail": str(exc)},
            )
    else:
        try:
            pack_name, identity, pack_payload = (
                _openimcc_melt_pack_payload_for_digest(melt_binding_digest)
            )
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            reason = (
                "openimcc_feo_standard_melt_pack_identity_unknown"
                if str(exc) == "melt pack identity is not a pinned OpenIMCC pack"
                else "openimcc_feo_standard_pack_data_unavailable"
            )
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {"reason": reason, "melt_pack_binding_digest": melt_binding_digest, "detail": str(exc)},
            )

    candidate = pack_payload.get("feo_liquid_candidate")
    if pack_name == "published-core" and candidate is None:
        standard = _FeOActivityStandard(
            name=_FEO_ACTIVITY_CANONICAL_STANDARD,
            offset_j_mol=Decimal(0),
            melt_pack_identity=identity,
            redox_certified_band_K=redox_certified_band_K,
            redox_range_flags=redox_range_flags,
        )
    else:
        if not isinstance(candidate, Mapping):
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_standard_pack_candidate_missing",
                    "melt_pack_identity": identity,
                },
            )
        offset, detail = _feo_liquid_candidate_delta_g(candidate, temperature_K)
        if offset is None:
            return None, ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "openimcc_feo_standard_delta_g_unreadable",
                    "melt_pack_identity": identity,
                    "detail": detail,
                },
            )
        standard = _FeOActivityStandard(
            name="DG46-MASSON65",
            offset_j_mol=offset,
            melt_pack_identity=identity,
            redox_certified_band_K=redox_certified_band_K,
            redox_range_flags=redox_range_flags,
        )

    if redox_pack_digest is not None:
        refusal = _redox_melt_standard_refusal(standard)
        if refusal is not None:
            return None, refusal
    return standard, None


def _validated_openimcc_melt_pack_identity(
    identity: Mapping[str, object],
) -> Mapping[str, str]:
    return {
        "model_id": str(identity["model_id"]),
        "pack_version": str(identity["pack_version"]),
        "binding_digest": str(identity["binding_digest"]),
    }


def _validate_openimcc_melt_pack_identity(
    identity: Mapping[str, object] | None,
) -> tuple[Mapping[str, str] | None, ResidualRefusal | None]:
    if not isinstance(identity, Mapping) or any(
        not isinstance(identity.get(key), str) or not identity.get(key)
        for key in ("model_id", "pack_version", "binding_digest")
    ):
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_melt_pack_identity_missing"},
        )
    try:
        return _validated_openimcc_melt_pack_identity(identity), None
    except KeyError:
        return None, ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_melt_pack_identity_missing"},
        )


@lru_cache(maxsize=2)
def _openimcc_pack_identity_and_payload(
    pack_name: str,
) -> tuple[dict[str, str], dict[str, object]]:
    import openimcc
    from simulator.melt_backend import openimcc_bridge

    try:
        pack = openimcc_bridge._load_pack(pack_name)
    except openimcc.ImccMalformedDatapackError as exc:
        raise ValueError(f"OpenIMCC melt datapack is malformed: {exc}") from exc
    identity = {
        "model_id": str(pack.model_id),
        "pack_version": str(pack.version),
        "binding_digest": openimcc_bridge._pack_digest(pack),
    }
    resource_name = openimcc_bridge._PACK_RESOURCE_NAMES[pack_name]
    if resource_name is None:
        resource_name = "imcc-sf04-d066-v4.json"
    payload = json.loads(
        resources.files(openimcc)
        .joinpath("data", "packs", resource_name)
        .read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise ValueError("OpenIMCC melt pack JSON root is not an object")
    return identity, payload


def _openimcc_melt_pack_payload_for_digest(
    binding_digest: str,
) -> tuple[str, Mapping[str, str], dict[str, object]]:
    for pack_name in ("default", "published-core"):
        identity, payload = _openimcc_pack_identity_and_payload(pack_name)
        if identity["binding_digest"] != binding_digest:
            continue
        if pack_name == "default" and (
            identity["pack_version"] != _OPENIMCC_DEFAULT_PACK_VERSION
            or binding_digest != _OPENIMCC_DEFAULT_PACK_DIGEST
        ):
            continue
        if (
            pack_name == "published-core"
            and binding_digest != _OPENIMCC_PUBLISHED_CORE_PACK_DIGEST
        ):
            continue
        return pack_name, identity, payload
    raise ValueError("melt pack identity is not a pinned OpenIMCC pack")


def _openimcc_melt_pack_payload(
    identity: Mapping[str, str],
) -> tuple[str, dict[str, object]]:
    pack_name, pinned_identity, payload = _openimcc_melt_pack_payload_for_digest(
        identity["binding_digest"]
    )
    if dict(identity) != dict(pinned_identity):
        raise ValueError("melt pack identity is not a pinned OpenIMCC pack")
    return pack_name, payload


def _feo_liquid_candidate_delta_g(
    candidate: Mapping[str, object], temperature_K: Decimal
) -> tuple[Decimal | None, str | None]:
    raw = candidate.get("delta_g_j_mol")
    number = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    match = (
        re.fullmatch(rf"\s*({number})\s*-\s*({number})\s*\*\s*T\s*", raw)
        if isinstance(raw, str)
        else None
    )
    if match is None:
        return None, "delta_g_j_mol must match the pinned c0 - c1*T expression"
    intercept_j_mol = Decimal(match.group(1))
    temperature_coefficient_j_mol_K = Decimal(match.group(2))
    if not intercept_j_mol.is_finite() or not temperature_coefficient_j_mol_K.is_finite():
        return None, "delta_g_j_mol coefficients must be finite"
    return (
        intercept_j_mol - temperature_coefficient_j_mol_K * temperature_K,
        None,
    )


def _canonical_log10_feo_activity(
    log10_engine_activity: Decimal,
    temperature_K: Decimal,
    offset_j_mol: Decimal,
) -> Decimal:
    """Convert FeO(l) activity from its engine standard to Fe-019."""
    if offset_j_mol == 0:
        return log10_engine_activity
    # For one FeO chemical potential, equating μ = μ° + RT ln(a) gives
    # log10(a_Fe-019) = log10(a_engine) + delta_G°/(RT ln(10)). The MELT
    # pack's candidate supplies delta_G° in J/mol; the denominator has the
    # same units, leaving a dimensionless activity shift.
    denominator = (
        Decimal(str(GAS_CONSTANT))
        * temperature_K
        * Decimal(10).ln()
    )
    return log10_engine_activity + offset_j_mol / denominator


def normalize_openimcc_feo_activity_value(
    value: Decimal,
    coefficient_sources: Sequence[str],
    melt_pack_identity: Mapping[str, object] | None,
    temperature_K: Decimal,
) -> tuple[
    Decimal | None,
    Mapping[str, object] | None,
    Notice | None,
    ResidualRefusal | None,
]:
    standard, refusal = _resolve_feo_activity_standard(
        Engine.OPENIMCC,
        coefficient_sources,
        melt_pack_identity=melt_pack_identity,
        temperature_K=temperature_K,
    )
    if refusal is not None:
        return None, None, None, refusal
    assert standard is not None and standard.melt_pack_identity is not None
    shift = _canonical_log10_feo_activity(
        Decimal(0), temperature_K, standard.offset_j_mol
    )
    normalized_value = value
    if value.is_finite() and value > 0 and standard.offset_j_mol != 0:
        normalized_value = Decimal(10) ** _canonical_log10_feo_activity(
            value.log10(), temperature_K, standard.offset_j_mol
        )
    record: dict[str, object] = {
        "from_standard": standard.name,
        "to_standard": _FEO_ACTIVITY_CANONICAL_STANDARD,
        "melt_pack_identity": dict(standard.melt_pack_identity),
        "delta_g_j_mol": decimal_token(standard.offset_j_mol),
        "log10_shift": decimal_token(shift),
        "temperature_K": decimal_token(temperature_K),
    }
    extrapolation_evidence = _redox_feo_extrapolation_evidence(
        standard, temperature_K
    )
    if extrapolation_evidence is not None:
        record.update(extrapolation_evidence)
    notice = None
    if shift != 0:
        notice = Notice(
            kind=NoticeKind.ENGINE_MODEL_CAVEAT,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=(
                f"OpenIMCC FeO(l) activity normalized from {standard.name} to "
                f"{_FEO_ACTIVITY_CANONICAL_STANDARD}"
            ),
            origin="openimcc_feo_standard_normalisation",
            band=f"delta_g={record['delta_g_j_mol']} J/mol at {record['temperature_K']} K",
            authority=(
                Authority.EXTRAPOLATED
                if extrapolation_evidence is not None
                else None
            ),
        )
    return normalized_value, record, notice, None


def openimcc_feo_activity_normalisation_refusal(
    normalisation: object,
    melt_pack_identity: object,
    coefficient_sources: Sequence[str],
    temperature_K: Decimal | None,
) -> ResidualRefusal | None:
    if (
        not isinstance(normalisation, Mapping)
        or normalisation.get("to_standard") != _FEO_ACTIVITY_CANONICAL_STANDARD
        or not isinstance(normalisation.get("melt_pack_identity"), Mapping)
        or not isinstance(melt_pack_identity, Mapping)
        or dict(normalisation["melt_pack_identity"]) != dict(melt_pack_identity)
        or temperature_K is None
        or not isinstance(normalisation.get("temperature_K"), str)
        or not isinstance(normalisation.get("log10_shift"), str)
    ):
        return ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_not_normalised"},
        )
    try:
        recorded_temperature = Decimal(normalisation["temperature_K"])
        recorded_shift = Decimal(normalisation["log10_shift"])
        recorded_offset = Decimal(str(normalisation["delta_g_j_mol"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_not_normalised"},
        )
    standard, refusal = _resolve_feo_activity_standard(
        Engine.OPENIMCC,
        coefficient_sources,
        melt_pack_identity=melt_pack_identity,
        temperature_K=temperature_K,
    )
    if (
        refusal is not None
        or standard is None
        or normalisation.get("from_standard") != standard.name
        or recorded_temperature != temperature_K
        or recorded_offset != standard.offset_j_mol
        or recorded_shift
        != _canonical_log10_feo_activity(
            Decimal(0), temperature_K, standard.offset_j_mol
        )
    ):
        return ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_not_normalised"},
        )
    expected_extrapolation_evidence = _redox_feo_extrapolation_evidence(
        standard, temperature_K
    )
    extrapolation_keys = ("authority", "certified_band", "range_flags")
    if expected_extrapolation_evidence is None:
        if any(key in normalisation for key in extrapolation_keys):
            return ResidualRefusal(
                RefusalReason.METRIC_DOMAIN,
                {"reason": "openimcc_feo_standard_not_normalised"},
            )
    elif any(
        normalisation.get(key) != value
        for key, value in expected_extrapolation_evidence.items()
    ):
        return ResidualRefusal(
            RefusalReason.METRIC_DOMAIN,
            {"reason": "openimcc_feo_standard_not_normalised"},
        )
    return None
