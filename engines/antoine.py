"""Shared arithmetic for Antoine vapour-pressure correlations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _antoine_skip_reason(coefficients: Mapping[str, object]) -> str:
    return (
        "missing_antoine_A"
        if coefficients.get("A") is None
        else "nonpositive_antoine_A"
    )


def _antoine_skip_notice(
    coefficients: Mapping[str, object],
    *,
    species: str,
    site: str,
    temperature_K: float,
) -> str:
    """Describe a skipped Antoine row without changing its evaluation."""
    reason = _antoine_skip_reason(coefficients)
    return (
        "antoine_species_skipped: "
        f"species={species} site={site} reason={reason} "
        f"temperature_K={temperature_K:.3f}"
    )


def _append_antoine_skip_notice(
    notices: list[str],
    seen: set[tuple[str, str, str]],
    coefficients: Mapping[str, object],
    *,
    species: str,
    site: str,
    temperature_K: float,
) -> None:
    """Append at most one notice per skipped species/site/reason in a result."""
    key = (species, site, _antoine_skip_reason(coefficients))
    if key in seen:
        return
    seen.add(key)
    notices.append(
        _antoine_skip_notice(
            coefficients,
            species=species,
            site=site,
            temperature_K=temperature_K,
        )
    )


def _antoine_skip_notices_for_omitted_species(
    notices: Sequence[str],
    resolved_vapor_pressures: Mapping[str, object],
) -> list[str]:
    """Keep skip notices only for species absent from the completed result."""
    retained: list[str] = []
    for notice in notices:
        if notice.startswith("antoine_species_skipped:"):
            species_field = next(
                (field for field in notice.split() if field.startswith("species=")),
                None,
            )
            if species_field is not None:
                species = species_field[len("species="):]
                if species in resolved_vapor_pressures:
                    continue
        retained.append(notice)
    return retained


def _antoine_log10_pressure(
    A: float,
    B: float,
    C: float,
    temperature: float,
) -> float:
    """Return ``log10(P) = A - B / (T + C)`` in the row's pressure unit.

    Callers supply coefficients and temperature in their declared units and
    retain responsibility for unit conversion, coefficient selection, and
    validity policy.
    """
    return A - B / (temperature + C)
