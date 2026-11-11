"""Shared scalar vapor-source report fields."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping


def vapor_pressure_source_report_fields(
    source_by_species: Mapping[object, object] | None,
    activity_sources: Mapping[object, object] | None = None,
) -> dict[str, object]:
    species_sources = {
        str(species): str(source)
        for species, source in sorted((source_by_species or {}).items())
    }
    total = len(species_sources)
    counts = Counter(species_sources.values())
    activity_source_map = (
        activity_sources if isinstance(activity_sources, Mapping) else {}
    )
    activity_source_entries = {
        str(species): dict(activity_source)
        for species, activity_source in sorted(activity_source_map.items())
        if str(species) in species_sources
        and isinstance(activity_source, Mapping)
    }
    return {
        "species": species_sources,
        "summary": {
            source: {
                "count": count,
                "percentage": round(count / total * 100.0, 6) if total else 0.0,
            }
            for source, count in sorted(counts.items())
        },
        "total_species": total,
        "activity_sources": activity_source_entries,
    }
