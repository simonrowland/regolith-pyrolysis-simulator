"""Markdown for an already-prepared battery score report.

Formatting only. Engine dispatch, residual math, and admission, eligibility,
and headline-membership decisions stay in ``score.py``. Callers pass the
rows and counts those decisions already produced.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Iterable, Mapping, Protocol, Sequence

from simulator.battery.enums import Engine
from simulator.battery.records import Observation
from simulator.battery.score_report_data import (
    ElementRetentionReportData,
    Nasa9FitComparisonData,
)


REPORT_HEADING = "# Battery score report (schema v2.1)"


class _ConditionHullView(Protocol):
    """Prepared hull cells. The accumulator that fills them stays in score.py."""

    n: int

    def cells(self) -> tuple[str, str, str]: ...


UNKNOWN_STORE_PROVENANCE_LINE = (
    "The measuring store for this ledger is unknown."
)


def store_stamp_has_known_revision(stamp: Mapping[str, object] | None) -> bool:
    if not isinstance(stamp, Mapping):
        return False
    revision = stamp.get("revision")
    return bool(revision) and str(revision) not in {"", "unknown"}


def format_store_stamp_report_lines(
    stamp: Mapping[str, object] | None,
    *,
    mismatch_warning: str | None = None,
) -> list[str]:
    if not store_stamp_has_known_revision(stamp):
        lines = [UNKNOWN_STORE_PROVENANCE_LINE]
    else:
        assert stamp is not None
        lines = [
            (
                f"This report measured store `{stamp['revision']}`: "
                f"{stamp['rows_in']} rows in, {stamp['observations']} observations, "
                f"{stamp['works']} works, {stamp['experiments']} experiments, "
                f"queue {stamp['queue']}, {stamp['hard_issues']} hard issues."
            ),
        ]
    if mismatch_warning:
        lines.extend(["", f"Warning: {mismatch_warning}"])
    return lines


def _e15_number(value: Decimal | None, *, places: int = 3, signed: bool = False) -> str:
    if value is None:
        return "—"
    rendered = f"{value:.{places}f}"
    return f"+{rendered}" if signed and value >= 0 else rendered


def _e15_markdown_lines(
    shape_rows: Sequence[Mapping[str, object]],
    precheck_rows: Sequence[Mapping[str, object]],
) -> list[str]:
    if not shape_rows and not precheck_rows:
        return []
    lines = [
        "",
        "## E15 level, shape, and activity pre-check",
        "",
        "Each group uses numeric single-liquid residuals. Flags label rows and",
        "contested rows are a separate population; the OLS slope is diagnostic only.",
        "",
        "| population | source | table | series | T (K) | engine | flags | line | level basis | level (dex) | shape RMS (dex) | OLS slope (dex per unit x) | axis | n |",
        "|---|---|---|---|---:|---|---|---|---|---:|---:|---:|---|---:|",
    ]
    for row in shape_rows:
        lines.append(
            f"| {row['population']} | {row['source']} | {row['table']} | "
            f"{row['series']} | {row['temperature_K']} | {row['engine']} | "
            f"{', '.join(row['flags']) or '—'} | "
            f"{row['statistic']} | {row['level_basis'] or '—'} | "
            f"{_e15_number(row['level_dex'])} | "
            f"{_e15_number(row['shape_rms_dex'])} | "
            f"{_e15_number(row['slope_dex_per_x'], places=2, signed=True)} | "
            f"{row['x_axis']} | {row['n']} |"
        )
    if precheck_rows:
        lines.extend([
            "",
            "Activity self-consistency uses log10(a_printed/a_abs); pressures are",
            "dimensionless p/1 bar and K° comes from the bound OpenIMCC JANAF data.",
            "",
            "| source | table | T (K) | activity series | method | reaction | log10 ratio mean ± sd (dex) | axis | n |",
            "|---|---|---:|---|---|---|---:|---|---:|",
        ])
        for row in precheck_rows:
            result = str(row["result"])
            if result == "computed":
                mean = _e15_number(row.get("mean_dex"))
                sd = _e15_number(row.get("sd_dex"))
                result = f"{mean} ± {sd}"
            lines.append(
                f"| {row['source']} | {row['table']} | {row['temperature_K']} | "
                f"{row['activity_series']} | {row['method']} | {row['reaction']} | "
                f"{result} | {row['x_axis']} | {row['n']} |"
            )
    return lines


def _all_numeric_markdown_lines(
    records: Sequence[Mapping[str, object]],
    *,
    regenerated: bool = True,
) -> list[str]:
    """The A section for both Markdown writers; every grid row is printed."""

    lines = [
        "## All-numeric tier",
        "",
        "Co-equal descriptive headline (A) over every priced residual, certified and "
        "flagged. Every rail × engine cell is printed, n = 0 included. Dex statistics "
        "use DEX residuals only; every other metric stays in its own quantity / "
        "operation / unit stratum below and is never labelled dex. n certified is the "
        "measured (B) membership decision, consumed rather than recomputed. Does not "
        "change the measured/certified table.",
        "",
    ]
    if not regenerated:
        lines.append("Not regenerated.")
        return lines
    lines.extend(
        [
            "| rail | engine | n | n certified | n flagged | n dex | median dex | "
            "IQR dex | flag classes | sources |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    strata_lines: list[str] = []
    for row in records:
        flags = row.get("flag_class_counts") or {}
        assert isinstance(flags, Mapping)
        flag_s = ", ".join(f"{k}={v}" for k, v in flags.items()) if flags else "—"
        sources = row.get("sources") or []
        assert isinstance(sources, Sequence)
        source_s = (
            ", ".join(
                f"{s.get('source_id')}(n={s.get('n_numeric')},"
                f"cert={s.get('n_certified')},flag={s.get('n_flagged')})"
                for s in sources
                if isinstance(s, Mapping)
            )
            or "—"
        )
        lines.append(
            f"| {row['rail']} | {row['engine']} | {row['n']} | {row['n_certified']} | "
            f"{row['n_flagged']} | {row['n_dex']} | {row.get('median_dex') or '—'} | "
            f"{row.get('iqr_dex') or '—'} | {flag_s} | {source_s} |"
        )
        for stratum in row.get("metric_strata") or ():
            assert isinstance(stratum, Mapping)
            strata_lines.append(
                f"| {row['rail']} | {row['engine']} | {stratum['quantity']} | "
                f"{stratum['label']} | {stratum['n']} | {stratum.get('median') or '—'} | "
                f"{stratum.get('iqr') or '—'} |"
            )
    lines.extend(
        [
            "",
            "### All-numeric metric strata",
            "",
            "| rail | engine | quantity | metric | n | median | IQR |",
            "|---|---|---|---|---:|---:|---:|",
            *(strata_lines or ["| (none) | — | — | — | 0 | — | — |"]),
        ]
    )
    return lines


def _census_count_line(row: Mapping[str, object], label: str) -> str:
    return (
        f"| {label} | {row['candidates']} | {row['admitted']} | "
        f"{row['pending']} | {row['points']} |"
    )


def _append_quench_pair_diagnostics(
    report: str, diagnostics: Sequence[Mapping[str, object]]
) -> str:
    if not diagnostics:
        return report
    lines = [
        "## Paired in-situ / quenched valence diagnostics",
        "",
        "Δlog10 is the dimensionless glass-to-melt ratio difference in dex.",
        "",
        "| experiment | in-situ observation | glass observation | in-situ ratio | glass ratio | quench_state | Δlog10 (dex) |",
        "|---|---|---|---:|---:|---|---:|",
    ]
    for row in diagnostics:
        lines.append(
            f"| {row['experiment_id']} | {row['in_situ_observation']} | "
            f"{row['glass_observation']} | {row['in_situ_ratio']} | "
            f"{row['glass_ratio']} | {row['quench_state']} | "
            f"{row['delta_log10']} |"
        )
    section = "\n".join(lines)
    return f"{report.rstrip()}\n\n{section}\n"


def format_element_retention_diagnostics(
    report: str,
    data: ElementRetentionReportData,
) -> str:
    cells, loss_bin_counts = data
    if not cells:
        return report

    def markdown(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    lines = [
        "## Element retention diagnostics",
        "",
        "Observed loss is 1 − FM; printed uncertainty is shown without rescaling.",
        "Loss bins are reporting diagnostics and do not affect residual weights.",
        "",
        "| observation | engine | status | FM observed | observed loss | printed uncertainty | residual (dex) |",
        "|---|---|---|---:|---:|---|---:|",
    ]
    for observation_id, engine, status, fm, loss, uncertainty, numeric in cells:
        raw_uncertainty = uncertainty.verbatim
        if raw_uncertainty is None and uncertainty.value is not None:
            raw_uncertainty = f"{uncertainty.value} {uncertainty.basis or ''}".strip()
        if raw_uncertainty is None:
            uncertainty_text = "not printed"
        elif isinstance(raw_uncertainty, Mapping):
            uncertainty_text = json.dumps(
                raw_uncertainty, sort_keys=True, ensure_ascii=False
            )
        else:
            uncertainty_text = str(raw_uncertainty)
        lines.append(
            f"| {markdown(observation_id)} | {markdown(engine)} | {markdown(status)} | "
            f"{'—' if fm is None else fm} | "
            f"{'—' if loss is None else loss} | {markdown(uncertainty_text)} | "
            f"{'—' if numeric is None else numeric} |"
        )
    lines.extend(
        [
            "",
            "### Observed loss bins",
            "",
            "| observed loss | n rows |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {label} | {count} |" for label, count in loss_bin_counts)
    section = "\n".join(lines)
    return f"{report.rstrip()}\n\n{section}\n"


def format_nasa9_fit_comparison_stratum(
    report: str, data: Nasa9FitComparisonData
) -> str:
    if not data.counts:
        return report

    lines = [
        "## NASA-9 fit comparison stratum",
        "",
        "Fit comparisons are separate from printed-cell comparisons and measured-tier counts.",
        "",
        "| engine | quantity | n comparisons | n numeric | n refused |",
        "|---|---|---:|---:|---:|",
    ]
    for engine, quantity, total, numeric, refused in data.counts:
        lines.append(
            f"| {engine} | {quantity} | {total} | {numeric} | {refused} |"
        )
    lines.extend(["", "### Typed refusal diagnostics", ""])
    if not data.refusals:
        lines.append("None.")
    else:
        lines.extend(
            [
                "| engine | quantity | reason | count | detail |",
                "|---|---|---|---:|---|",
            ]
        )
        for engine, quantity, reason, detail, count in data.refusals:
            safe_detail = detail.replace("|", "\\|").replace("`", "\\`")
            lines.append(
                f"| {engine} | {quantity} | `{reason}` | {count} | `{safe_detail}` |"
            )
    section = "\n".join(lines)
    return f"{report.rstrip()}\n\n{section}\n"


def _condition_hull_lines(
    hulls: Mapping[tuple[str, str, str], _ConditionHullView],
) -> list[str]:
    """Markdown table of the per (tier, rail, engine) condition hulls."""

    lines = [
        "## Condition hull per rail × engine",
        "",
        "Min–max of the stored (T, P, composition) of the numeric rows behind "
        "each per rail × engine statistic: the measured and compilation "
        "headlines (the n scored column) and each flagged stratum (its n). "
        "T and total pressure are the reference row's own (a compilation "
        "cell's printed T); composition is the row's stated composition per "
        "amount basis. Nothing is filled in: bracketed counts are rows "
        "carrying a value. Total P is the stored total pressure only; a "
        "table's standard-state pressure (standard_pressure_Pa) is not a "
        "total pressure and is not shown. A compilation row pools every "
        "compilation table on that rail × engine, so its T span is not one "
        "table's window. Six significant figures. Presentation only; no "
        "residual or statistic reads this table.",
        "",
        "| tier / stratum | rail | engine | n | T K (n) | total P Pa (n) | composition (n) |",
        "|---|---|---|---:|---|---|---|",
    ]
    order = {"measured": 0, "compilation": 1}
    rows = sorted(
        ((key, hull) for key, hull in hulls.items() if hull.n),
        key=lambda item: (order.get(item[0][0], 2), item[0]),
    )
    if not rows:
        lines.append("| (none) | — | — | 0 | — | — | — |")
    for (tier, rail, engine), hull in rows:
        t_cell, p_cell, composition_cell = hull.cells()
        lines.append(
            f"| {tier} | {rail} | {engine} | {hull.n} | {t_cell} | {p_cell} | "
            f"{composition_cell} |"
        )
    return lines


def render_legacy_score_report_markdown(
    *,
    hostname: str,
    studio_hostname: str | None,
    stamp: Mapping[str, object] | None,
    engines: Sequence[Engine],
    has_residuals: bool,
    rail_census: Sequence[Mapping[str, object]],
    unassigned_census: Sequence[Mapping[str, object]],
    all_numeric_records: Sequence[Mapping[str, object]],
    headline_rows: Sequence[Mapping[str, object]],
    e15_lines: Sequence[str],
    flagged_rows: Sequence[Mapping[str, object]],
    condition_lines: Sequence[str],
    compilation_lines: Sequence[str],
    refusal_counts: Mapping[str, int],
    pending: int,
    admitted: int,
    admission_with: int,
    admission_alone: int,
    admission_alone_unique: int,
    notice_counts: Mapping[str, int],
    diagnostic_count: int,
    pin_failures: Sequence[Mapping[str, object]],
    status_diff: Sequence[Mapping[str, object]],
    unmapped_legacy_keys: Sequence[str],
    circularity_warning: str,
) -> str:
    lines: list[str] = [
        REPORT_HEADING,
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "The measured tier keeps numeric rows visible; its headline reports",
        "n, n inside band, RMS dex, signed and absolute median dex, band width,",
        "RMS/band, and score_eligible separately. The compilation tier is beside",
        "it and is never added to it. Match rate is banded rows only.",
        "",
        f"Hostname: `{hostname}`.",
    ]
    if studio_hostname:
        lines.append(f"Studio hostname: `{studio_hostname}`.")
    lines.extend(["", *format_store_stamp_report_lines(stamp)])
    lines.extend(["", f"Engines: {', '.join(e.value for e in engines)}."])
    if not has_residuals:
        lines.extend(
            [
                "",
                "No engine residuals were regenerated for this store revision.",
                "Match rate is blank. score_eligible is 0.",
                "The live candidate census is the published candidate count.",
            ]
        )
    lines.extend(
        [
            "",
            "## Live candidate census",
            "",
            "Comparison candidates are measured rows admitted or pending.",
            "Admitted is split from pending. Counts are observations, not",
            "residuals, so they are not multiplied by the engine set.",
            "A vapour candidate is only `p_sat`, `p_partial`, or `p_reference`.",
            "",
            "| rail | candidates | admitted | pending | points |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in rail_census:
        lines.append(_census_count_line(row, str(row["rail"])))
    lines.extend(
        [
            "",
            "### No headline rail",
            "",
            "These quantities are not vapour candidates and not SiO_evolution",
            "candidates. The reason is the refusal, not a borrowed rail.",
            "",
            "| reason | candidates | admitted | pending | points |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    if not unassigned_census:
        lines.append("| (none) | 0 | 0 | 0 | 0 |")
    else:
        for row in unassigned_census:
            lines.append(_census_count_line(row, f"`{row['reason']}`"))
    # A has its own complete engine × rail grid (the run's engines plus any
    # engine with an A row); B keeps its measured-only membership below.
    lines.extend(
        [
            "",
            *_all_numeric_markdown_lines(
                all_numeric_records,
                regenerated=has_residuals,
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Measured tier",
            "",
            "Numeric measured rows stay visible. score_eligible and inside-band "
            "counts are reported separately. Compilation rows are not in this table.",
            "",
        ]
    )
    if not has_residuals:
        lines.append("Not regenerated. Match rate is blank. score_eligible is 0.")
    else:
        lines.extend(
            [
                "| rail | engine | population / source relation | n candidates | "
                "n refused | n scored | n score eligible | "
                "n inside band | RMS dex | median dex | median abs dex | band width dex | "
                "RMS/band | n no band | match rate |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in headline_rows:
            if row.get("tier") != "measured":
                continue
            rate = row["match_rate"]
            rate_s = "—" if rate is None else f"{rate:.3f}"
            lines.append(
                f"| {row['rail']} | {row['engine']} | "
                f"{row.get('population', row.get('source_relation', '—'))} | "
                f"{row['n_candidates']} | "
                f"{row['n_refused']} | {row['n']} | {row['n_score_eligible']} | "
                f"{row['n_inside_band']} | {row['rms_dex'] or '—'} | "
                f"{row['median_dex'] or '—'} | {row['median_abs_dex'] or '—'} | "
                f"{row['band_width_dex'] or '—'} | {row['rms_over_band'] or '—'} | "
                f"{row['n_no_band']} | {rate_s} |"
            )
    lines.extend(e15_lines)
    lines.extend(
        [
            "",
            "## Flagged strata",
            "",
            "These numeric diagnostics carry an explicit source or apparatus flag. "
            "Flagged rows do not derive measurement bands or enter unflagged headlines. "
            "Cell-material-inferred residuals are still judged against the band; other "
            "flag kinds retain their existing scoring rules. A row carrying both flags "
            "appears in both strata.",
            "",
            "| stratum | rail | engine | n | median dex | RMS dex |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    if not flagged_rows:
        lines.append("| (none) | — | — | 0 | — | — |")
    else:
        for row in flagged_rows:
            lines.append(
                f"| {row['stratum']} | {row['rail']} | {row['engine']} | "
                f"{row['n']} | {row['median_dex'] or '—'} | {row['rms_dex'] or '—'} |"
            )
    lines.extend(["", *condition_lines])
    lines.extend(
        [
            "",
            *compilation_lines,
        ]
    )
    lines.extend(["", "## Refusal census", ""])
    if not has_residuals:
        lines.append(
            "Engine refusals were not regenerated with this census. "
            "Rows with no headline rail are counted above and are not hidden."
        )
    else:
        lines.extend(
            [
                "Measured tier only. Compilation refusals are in the compilation table.",
                "",
                "| reason | n |",
                "|---|---:|",
            ]
        )
        census = dict(sorted(refusal_counts.items()))
        if not census:
            lines.append("| (none) | 0 |")
        else:
            for reason, n in census.items():
                lines.append(f"| `{reason}` | {n} |")
    admit = {
        "residuals_with_admission_exclusion": admission_with,
        "residuals_admission_alone": admission_alone,
        "unique_obs_admission_alone": admission_alone_unique,
    }
    lines.extend(
        [
            "",
            "## Admission",
            "",
            "score_eligible requires canonical admission admitted. Pending rows",
            "stay in the comparison set as diagnostics (flagged and priced when",
            "numeric, never the empirical headline). The admission rule is unchanged.",
            "",
            "| count | n |",
            "|---|---:|",
            f"| comparison candidates pending | {pending} |",
            f"| comparison candidates admitted | {admitted} |",
            f"| residuals with admission_admitted exclusion | {admit['residuals_with_admission_exclusion']} |",
            f"| residuals that die on admission alone | {admit['residuals_admission_alone']} |",
            f"| unique observations that die on admission alone | {admit['unique_obs_admission_alone']} |",
        ]
    )
    lines.extend(
        [
            "",
            "## Notice backlog (certification projects)",
            "",
            "| notice kind | n |",
            "|---|---:|",
        ]
    )
    backlog = dict(sorted(notice_counts.items()))
    if not backlog:
        lines.append("| (none) | 0 |")
    else:
        for kind, n in backlog.items():
            lines.append(f"| `{kind}` | {n} |")
    n_diag = diagnostic_count
    lines.extend(
        [
            "",
            "## Internal-consistency diagnostics",
            "",
            "species_rail_differential_ledger and gibbs_battery_residual_ledger",
            "are internal-consistency instruments, not scoring ledgers. They do",
            "not enter either headline tier. Compilation rows are scored in the",
            "compilation tier above, still COMPILATION_ASSESSED, and never added",
            "to the measured tier. " + circularity_warning,
            "",
            f"Diagnostic residuals in this file: {n_diag}.",
            "",
            "## Pin failures",
            "",
        ]
    )
    if not has_residuals and not pin_failures:
        lines.append(
            "Pins were not compared; no residuals ledger was regenerated for this store revision."
        )
    elif not pin_failures:
        lines.append("None.")
    else:
        lines.append(
            f"{len(pin_failures)} pin failures (coverage or outside pin_band). A live residual outside its pin_band is a failure, never a re-centre."
        )
        lines.extend(["", "| key | reason | live | centre | pin_band |", "|---|---|---:|---:|---:|"])
        for failure in list(pin_failures)[:50]:
            lines.append(
                f"| `{failure.get('key')}` | {failure.get('reason')} | {failure.get('live')} | "
                f"{failure.get('centre')} | {failure.get('pin_band')} |"
            )
        if len(pin_failures) > 50:
            lines.append(f"| … | {len(pin_failures) - 50} more | | | |")
    lines.extend(["", "## status_diff vs old scorers", ""])
    if not has_residuals and not status_diff:
        lines.append(
            "status_diff was not run; no residuals ledger was regenerated for this store revision."
        )
    elif not status_diff:
        lines.append("No mapped outcome changes.")
    else:
        lines.extend(["| old key | old | new | axis |", "|---|---|---|---|"])
        for row in status_diff:
            lines.append(
                f"| `{row.get('old_key')}` | {row.get('old')} | {row.get('new')} | "
                f"{row.get('axis')} |"
            )
    if unmapped_legacy_keys:
        lines.extend(
            ["", f"Unmapped legacy keys: {len(unmapped_legacy_keys)}. Old ledgers retained."]
        )
    elif has_residuals:
        lines.extend(["", "All mapped legacy keys have a v2.1 comparison slot."])
    lines.append("")
    return "\n".join(lines)


def render_payload_score_report_markdown(
    *,
    hostname: str,
    studio_hostname: str | None,
    store_stamp: Mapping[str, object] | None,
    mismatch_warning: str | None,
    engines: Sequence[Engine],
    observations_present: bool,
    all_numeric_records: Sequence[Mapping[str, object]],
    measured_headline_rows: Sequence[Mapping[str, object]],
    e15_lines: Sequence[str],
    flagged_rows: Sequence[Mapping[str, object]],
    condition_lines: Sequence[str] | None,
    compilation_lines: Sequence[str],
    refusal_counts: Mapping[str, int],
    admit: Mapping[str, int],
    notice_counts: Mapping[str, int],
    diagnostic_count: int,
    pin_failures: Sequence[Mapping[str, object]],
    status_diff: Sequence[Mapping[str, object]],
    unmapped_legacy_keys: Sequence[str],
    circularity_warning: str,
) -> str:
    lines: list[str] = [
        REPORT_HEADING,
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "Headline accuracy per rail reports numeric n, inside-band n, RMS dex,",
        "median dex, band width, and RMS/band. Measured and compilation tiers",
        "are separate and never summed; match rate is secondary.",
        "",
        f"Hostname: `{hostname}`.",
    ]
    if studio_hostname:
        lines.append(f"Studio hostname: `{studio_hostname}`.")
    lines.extend(
        ["", *format_store_stamp_report_lines(store_stamp, mismatch_warning=mismatch_warning)]
    )
    lines.extend(
        [
            "",
            f"Engines: {', '.join(e.value for e in engines)}.",
            "",
            *_all_numeric_markdown_lines(
                all_numeric_records
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Measured tier" if observations_present else "## Per rail × engine headline",
            "",
            *(
                [
                    "Numeric measured rows stay visible. score_eligible and inside-band "
                    "counts are reported separately.",
                    "",
                ]
                if observations_present
                else []
            ),
            "| rail | engine | population / source relation | n candidates | "
            "n refused | n scored | n score eligible | "
            "n inside band | RMS dex | median dex | median abs dex | band width dex | "
            "RMS/band | n no band | match rate |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in measured_headline_rows:
        rate = row["match_rate"]
        rate_s = "—" if rate is None else f"{rate:.3f}"
        rms = row["rms_dex"] or "—"
        med = row["median_dex"] or "—"
        med_abs = row["median_abs_dex"] or "—"
        band = row["band_width_dex"] or "—"
        ratio = row["rms_over_band"] or "—"
        lines.append(
            f"| {row['rail']} | {row['engine']} | "
            f"{row.get('population', row.get('source_relation', '—'))} | "
            f"{row['n_candidates']} | "
            f"{row['n_refused']} | {row['n']} | {row['n_score_eligible']} | "
            f"{row['n_inside_band']} | {rms} | {med} | {med_abs} | {band} | {ratio} | "
            f"{row['n_no_band']} | {rate_s} |"
        )
    if observations_present:
        lines.extend(e15_lines)
    lines.extend(
        [
            "",
            "## Flagged strata",
            "",
            "Flagged numeric diagnostics are excluded from the measured headline, "
            "bands, and band statistics.",
            "",
            "| stratum | rail | engine | n | median dex | RMS dex |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    if not flagged_rows:
        lines.append("| (none) | — | — | 0 | — | — |")
    else:
        for row in flagged_rows:
            lines.append(
                f"| {row['stratum']} | {row['rail']} | {row['engine']} | "
                f"{row['n']} | {row['median_dex'] or '—'} | {row['rms_dex'] or '—'} |"
            )
    if condition_lines is not None:
        # Compilation headline hull (t-1110 ROR P1). Only the hull is kept: the
        # compilation statistics are printed by compilation_lines below.
        lines.extend(["", *condition_lines])
    if compilation_lines:
        lines.extend(["", *compilation_lines])
    lines.extend(
        [
            "",
            "## Refusal census",
            "",
            *(
                ["Measured tier only. Compilation refusals are in the compilation table.", ""]
                if observations_present
                else []
            ),
            "| reason | n |",
            "|---|---:|",
        ]
    )
    if not refusal_counts:
        lines.append("| (none) | 0 |")
    else:
        for reason, n in refusal_counts.items():
            lines.append(f"| `{reason}` | {n} |")
    lines.extend(
        [
            "",
            "## Admission",
            "",
            "score_eligible requires canonical admission admitted. Pending rows",
            "stay in the comparison set as diagnostics (flagged and priced when",
            "numeric, never the empirical headline). The admission rule is unchanged.",
            "",
            "| count | n |",
            "|---|---:|",
            f"| residuals with admission_admitted exclusion | {admit['residuals_with_admission_exclusion']} |",
            f"| residuals that die on admission alone | {admit['residuals_admission_alone']} |",
            f"| unique observations that die on admission alone | {admit['unique_obs_admission_alone']} |",
        ]
    )
    lines.extend(
        [
            "",
            "## Notice backlog (certification projects)",
            "",
            "| notice kind | n |",
            "|---|---:|",
        ]
    )
    if not notice_counts:
        lines.append("| (none) | 0 |")
    else:
        for kind, n in notice_counts.items():
            lines.append(f"| `{kind}` | {n} |")
    n_diag = diagnostic_count
    lines.extend(
        [
            "",
            "## Internal-consistency / compilation diagnostics",
            "",
            "species_rail_differential_ledger and gibbs_battery_residual_ledger",
            "are internal-consistency instruments, not scoring ledgers. Compilation",
            "observations are engine reference inputs. Neither population enters",
            "the empirical headline. " + circularity_warning,
            "",
            f"Diagnostic residuals in this file: {n_diag}.",
            "",
            "## Pin failures",
            "",
        ]
    )
    if not pin_failures:
        lines.append("None.")
    else:
        lines.append(
            f"{len(pin_failures)} pin failures (coverage or outside pin_band). "
            "A live residual outside its pin_band is a failure, never a re-centre."
        )
        lines.append("")
        lines.append("| key | reason | live | centre | pin_band |")
        lines.append("|---|---|---:|---:|---:|")
        for failure in list(pin_failures)[:50]:
            lines.append(
                f"| `{failure.get('key')}` | {failure.get('reason')} | {failure.get('live')} | "
                f"{failure.get('centre')} | {failure.get('pin_band')} |"
            )
        if len(pin_failures) > 50:
            lines.append(f"| … | {len(pin_failures) - 50} more | | | |")
    lines.extend(["", "## status_diff vs old scorers", ""])
    if not status_diff:
        lines.append("No mapped outcome changes.")
    else:
        lines.append("| old key | old | new | axis |")
        lines.append("|---|---|---|---|")
        for row in status_diff[:200]:
            lines.append(
                f"| `{row.get('old_key')}` | {row.get('old')} | {row.get('new')} | "
                f"{row.get('axis')} |"
            )
    if unmapped_legacy_keys:
        lines.extend(
            [
                "",
                f"Unmapped legacy keys: {len(unmapped_legacy_keys)}. Old ledgers retained.",
            ]
        )
    else:
        lines.extend(["", "All mapped legacy keys have a v2.1 comparison slot."])
    lines.append("")
    return "\n".join(lines)

