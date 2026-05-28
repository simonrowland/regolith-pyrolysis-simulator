"""E6b: markdown formatter for the E6a three-product classifier output.

Wraps the JSON-shaped dict returned by
``simulator.three_product_report.classify_products`` into a human-
readable markdown report suitable for:

- CLI runner output (``runners/three_product_sweep.py`` future work)
- Web UI download surface
- log scrapers + post-run summaries

This module is PURE PRESENTATION — it doesn't touch the simulator,
doesn't run the classifier, and doesn't depend on any sim instance.
Inputs are the 5-bucket dict + an optional feedstock/campaign label.

E6a closed the four-product class semantic surface; E6b closes the
operator-facing presentation. E6c (deferred to a future session)
would be the runner CLI + JSON-or-MD output mode.
"""

from __future__ import annotations

from typing import Any, Mapping

# Display order matches CLAUDE.md § 5 product class enumeration.
_CLASS_DISPLAY_ORDER: tuple[tuple[str, str], ...] = (
    ('metals_plus_O2',
     '1. Metals + O₂ (alkali / Fe / Mg / Si / Ti / Al / Ca / Cr / Mn / Ni / Co + terminal O₂)'),
    ('pure_silica_glass',
     '2. Pure silica glass (Stage 3 fused-silica baffle capture)'),
    ('industrial_mixed_glass',
     '3. Industrial mixed glass (early-tap residual melt option)'),
    ('refractory_ceramic_rump',
     '4. Refractory ceramic rump (Ca / REE / refractory oxides — by physics)'),
)


def _format_kg(value: float) -> str:
    """Format a kg value with appropriate precision.

    Values < 1e-9 read as "—" (effective zero); values < 1.0 use
    scientific notation; values >= 1.0 use 3 decimal places.
    """
    if value < 1.0e-9:
        return "—"
    if value < 1.0:
        return f"{value:.3e}"
    return f"{value:.3f}"


def _kg_by_species_block(species_kg: Mapping[str, float]) -> str:
    """Format a per-species kg breakdown as a bullet list."""
    if not species_kg:
        return "  (no species in this class)"
    lines = []
    for species in sorted(species_kg.keys()):
        kg = float(species_kg[species])
        if kg > 0.0:
            lines.append(f"  - **{species}**: {_format_kg(kg)} kg")
    if not lines:
        return "  (no species above the noise floor)"
    return "\n".join(lines)


def format_three_product_markdown(
    classification: Mapping[str, Any],
    *,
    feedstock_id: str | None = None,
    campaign: str | None = None,
    title: str = "Three-Product-Class Report",
) -> str:
    """Project a ``classify_products()`` output into a markdown
    operator report.

    Args:
        classification: The dict returned by
            ``simulator.three_product_report.classify_products``.
        feedstock_id: Optional human-readable feedstock label
            (e.g. ``"lunar_mare_low_ti"``).
        campaign: Optional campaign / recipe label
            (e.g. ``"C2A_continuous"``).
        title: Header title; defaults to a generic report name.

    Returns:
        A markdown string ready to write to disk or pass to a Slack
        / web UI surface. Headers / bullets follow Keep-a-Changelog
        style for diff-friendliness.

    The report opens with a 1-line totals snapshot, then expands
    each of the four product classes with per-species kg + the
    class total, then ends with an unclassified bin if non-empty.
    """
    lines: list[str] = []
    lines.append(f"# {title}")
    if feedstock_id or campaign:
        meta_parts = []
        if feedstock_id:
            meta_parts.append(f"**Feedstock**: `{feedstock_id}`")
        if campaign:
            meta_parts.append(f"**Campaign**: `{campaign}`")
        lines.append(" • ".join(meta_parts))
    lines.append("")

    # ----- One-line totals snapshot -----
    totals = [
        ('Metals + O₂',
         classification.get('metals_plus_O2', {}).get('class_total_kg', 0.0)),
        ('Silica glass',
         classification.get('pure_silica_glass', {}).get('class_total_kg', 0.0)),
        ('Mixed glass',
         classification.get('industrial_mixed_glass', {}).get('class_total_kg', 0.0)),
        ('Rump',
         classification.get('refractory_ceramic_rump', {}).get('class_total_kg', 0.0)),
    ]
    snapshot = " | ".join(
        f"{label}: {_format_kg(kg)} kg" for label, kg in totals
    )
    lines.append(f"**Class totals**: {snapshot}")
    lines.append("")

    # ----- Per-class expansion -----
    for bucket_key, header in _CLASS_DISPLAY_ORDER:
        bucket = dict(classification.get(bucket_key, {}) or {})
        class_total_kg = float(bucket.get('class_total_kg', 0.0))
        lines.append(f"## {header}")
        lines.append(f"**Class total**: {_format_kg(class_total_kg)} kg")
        lines.append("")

        if bucket_key == 'metals_plus_O2':
            metals_kg = bucket.get('metals_kg', {}) or {}
            o2_kg = float(bucket.get('O2_kg', 0.0))
            lines.append(
                f"- Metals subtotal: "
                f"{_format_kg(float(bucket.get('metals_total_kg', 0.0)))} kg"
            )
            lines.append(f"- O₂ subtotal: {_format_kg(o2_kg)} kg")
            lines.append("")
            lines.append("Per-species:")
            lines.append(_kg_by_species_block(metals_kg))
        elif bucket_key == 'pure_silica_glass':
            stage_3 = bucket.get('stage_3_kg_by_species', {}) or {}
            lines.append(
                f"- Stage 3 capture: "
                f"{_format_kg(float(bucket.get('stage_3_capture_kg', 0.0)))} kg"
            )
            lines.append("")
            lines.append("Per-species on Stage 3 baffles:")
            lines.append(_kg_by_species_block(stage_3))
        elif bucket_key == 'industrial_mixed_glass':
            residual = float(bucket.get('mixed_melt_residual_kg', 0.0))
            note = bucket.get('note', '')
            lines.append(
                f"- Mixed melt residual: {_format_kg(residual)} kg"
            )
            if note:
                lines.append(f"  - *{note}*")
        elif bucket_key == 'refractory_ceramic_rump':
            rump_species = bucket.get('rump_kg_by_species', {}) or {}
            lines.append(
                f"- Rump total: "
                f"{_format_kg(float(bucket.get('rump_total_kg', 0.0)))} kg"
            )
            lines.append("")
            lines.append("Per-species:")
            lines.append(_kg_by_species_block(rump_species))
        lines.append("")

    # ----- Unclassified bin (only when non-empty) -----
    unclassified = classification.get('unclassified', {}) or {}
    unclassified_total = float(unclassified.get('total_kg', 0.0))
    if unclassified_total > 0.0:
        lines.append("## ⚠️ Unclassified species (mapping gap)")
        lines.append(
            f"**Total**: {_format_kg(unclassified_total)} kg — "
            "these species were in `product_ledger()` but did not "
            "map to any of the four north-star product classes. "
            "The mapping in `simulator/three_product_report.py` "
            "may need to be extended."
        )
        lines.append("")
        lines.append(_kg_by_species_block(
            unclassified.get('kg_by_species', {})
        ))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
