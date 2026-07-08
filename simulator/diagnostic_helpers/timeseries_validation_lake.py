"""Validation harness for artifact-backed KEMS/evaporation time-series data.

The public datasets in ``validation-data/timeseries`` rarely include the
geometry, melt inventory, activity model, or per-species gas back-pressure
needed to claim an absolute kg/s reproduction. This harness therefore reports
what is artifact-backed:

* direct dex error for measured evaporation coefficients;
* scale-free dex error for measured depletion/mass-loss ordering, using the
  runtime Hertz-Knudsen/alpha flux model as the model ordering signal.

No coefficient fitting or per-dataset scale calibration is performed.
"""

from __future__ import annotations

import csv
import math
import warnings
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    VaporPressureFallbackWarning,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.chemistry.langmuir_knudsen import (
    grounded_alpha,
    series_flux,
    species_molar_mass_kg_mol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "validation-data" / "timeseries"
CATALOG = "catalog.yaml"
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
TIME_SERIES_SIGNALS = {"mass_loss_pct", "residue_wt_pct"}
ALPHA_SIGNALS = {"evaporation_coefficient"}
DIRECT_FLUX_SIGNALS = {"evaporation_flux_molecules_cm2_s"}
UNMODELED_SPECIES = {"", "bulk"}
VALIDATION_PO2_BAR = 1.0e-9
AVOGADRO_PER_MOL = 6.022_140_76e23


@dataclass(frozen=True)
class SpeciesReproductionError:
    dataset_id: str
    species: str
    model_species: str
    signal_type: str
    T_K: float
    observed_rate_proxy: float | None
    model_flux_kg_s_m2: float | None
    modeled_value: float
    observed_value: float
    dex_error_model_minus_observed: float
    error_factor: float
    p_eq_pa: float | None
    alpha: float | None
    observed_floor_applied: bool = False
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "species": self.species,
            "model_species": self.model_species,
            "signal_type": self.signal_type,
            "T_K": self.T_K,
            "observed_rate_proxy": self.observed_rate_proxy,
            "model_flux_kg_s_m2": self.model_flux_kg_s_m2,
            "modeled_value": self.modeled_value,
            "observed_value": self.observed_value,
            "dex_error_model_minus_observed": self.dex_error_model_minus_observed,
            "error_factor": self.error_factor,
            "p_eq_pa": self.p_eq_pa,
            "alpha": self.alpha,
            "observed_floor_applied": self.observed_floor_applied,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class DatasetReproductionReport:
    dataset_id: str
    status: str
    rows_evaluated: int
    median_abs_dex_error: float | None
    max_abs_dex_error: float | None
    endpoint_rank_disagreement_fraction: float | None
    species: tuple[SpeciesReproductionError, ...]
    skipped_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "status": self.status,
            "rows_evaluated": self.rows_evaluated,
            "median_abs_dex_error": self.median_abs_dex_error,
            "max_abs_dex_error": self.max_abs_dex_error,
            "endpoint_rank_disagreement_fraction": (
                self.endpoint_rank_disagreement_fraction
            ),
            "skipped_reasons": list(self.skipped_reasons),
            "species": [item.as_dict() for item in self.species],
        }


@dataclass(frozen=True)
class _ModelFlux:
    flux_kg_s_m2: float
    p_eq_pa: float
    alpha: float


@dataclass(frozen=True)
class _ObservedProxy:
    dataset_id: str
    species: str
    model_species: str
    signal_type: str
    T_K: float
    observed_rate: float
    model_flux: _ModelFlux
    notes: str = ""


def load_catalog(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    return yaml.safe_load((data_root / CATALOG).read_text()) or {}


def load_dataset_rows(dataset_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> list[dict[str, str]]:
    path = data_root / f"{dataset_id}.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def validate_lake(data_root: Path = DEFAULT_DATA_ROOT) -> list[DatasetReproductionReport]:
    catalog = load_catalog(data_root)
    reports: list[DatasetReproductionReport] = []
    for entry in catalog.get("datasets", []):
        dataset_id = str(entry.get("id") or "")
        rows = load_dataset_rows(dataset_id, data_root)
        reports.append(validate_dataset(dataset_id, rows, catalog_entry=entry))
    return reports


def validate_dataset(
    dataset_id: str,
    rows: Sequence[Mapping[str, str]],
    *,
    catalog_entry: Mapping[str, Any] | None = None,
) -> DatasetReproductionReport:
    if not rows:
        reason = str((catalog_entry or {}).get("condition_gap") or "no normalized rows")
        return _skipped_report(dataset_id, reason)

    alpha_rows = [row for row in rows if row.get("signal_type") in ALPHA_SIGNALS]
    series_rows = [row for row in rows if row.get("signal_type") in TIME_SERIES_SIGNALS]
    direct_flux_rows = [
        row for row in rows if row.get("signal_type") in DIRECT_FLUX_SIGNALS
    ]

    species_errors: list[SpeciesReproductionError] = []
    skipped_reasons: list[str] = []
    if alpha_rows:
        alpha_errors, alpha_skips = _validate_alpha_rows(dataset_id, alpha_rows)
        species_errors.extend(alpha_errors)
        skipped_reasons.extend(alpha_skips)
    if direct_flux_rows:
        flux_errors, flux_skips = _validate_direct_flux_rows(
            dataset_id,
            direct_flux_rows,
        )
        species_errors.extend(flux_errors)
        skipped_reasons.extend(flux_skips)
    if series_rows:
        series_errors, series_skips = _validate_scale_free_series(dataset_id, series_rows)
        species_errors.extend(series_errors)
        skipped_reasons.extend(series_skips)

    if not species_errors:
        if not skipped_reasons:
            skipped_reasons.append("no rows with time-series or alpha validation signals")
        return DatasetReproductionReport(
            dataset_id=dataset_id,
            status="skipped",
            rows_evaluated=0,
            median_abs_dex_error=None,
            max_abs_dex_error=None,
            endpoint_rank_disagreement_fraction=None,
            species=(),
            skipped_reasons=tuple(_unique(skipped_reasons)),
        )

    abs_errors = [abs(item.dex_error_model_minus_observed) for item in species_errors]
    return DatasetReproductionReport(
        dataset_id=dataset_id,
        status="validated",
        rows_evaluated=len(species_errors),
        median_abs_dex_error=median(abs_errors),
        max_abs_dex_error=max(abs_errors),
        endpoint_rank_disagreement_fraction=_endpoint_rank_disagreement_fraction(
            species_errors
        ),
        species=tuple(species_errors),
        skipped_reasons=tuple(_unique(skipped_reasons)),
    )


def render_markdown_report(
    reports: Sequence[DatasetReproductionReport],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        "# Time-Series KEMS/Evaporation Validation Report",
        "",
        "No coefficients were tuned. Direct flux rows compare measured molecular fluxes to the runtime HKL/alpha flux after unit conversion. Scale-free rows compare endpoint rank agreement only; they are not sequential kinetic-ordering validation. Alpha rows compare measured evaporation coefficients directly.",
        "",
        "Rows with zero, negative, or enrichment-only observed depletion are marked below-detection / not-comparable and excluded from dex-error statistics. Si-bearing oxide/residue rows use the runtime SiO volatile proxy instead of inactive elemental Si. K uses the builtin vapor-pressure provider with melt activity and pO2 context, not raw pseudo-Antoine.",
        "",
        "## Dataset Summary",
        "",
        "| Dataset | status | rows evaluated | median abs dex | max abs dex | endpoint rank disagreement | skipped / gap |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for report in reports:
        lines.append(
            "| {dataset} | {status} | {rows} | {median} | {maxerr} | {inv} | {gap} |".format(
                dataset=report.dataset_id,
                status=report.status,
                rows=report.rows_evaluated,
                median=_fmt_optional(report.median_abs_dex_error),
                maxerr=_fmt_optional(report.max_abs_dex_error),
                inv=_fmt_optional(report.endpoint_rank_disagreement_fraction),
                gap=_summarize_reasons(report.skipped_reasons),
            )
        )

    lines.extend(
        [
            "",
            "## Per-Species Reproduction Error",
            "",
            "| Dataset | species | model species | signal | T K | observed proxy/rate | model flux kg/m2/s | modeled value | observed value | dex error | factor | notes |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for report in reports:
        for item in report.species:
            lines.append(
                "| {dataset} | {species} | {model_species} | {signal} | {T} | {observed_rate} | {flux} | {modeled} | {observed} | {dex} | {factor} | {notes} |".format(
                    dataset=item.dataset_id,
                    species=item.species,
                    model_species=item.model_species,
                    signal=item.signal_type,
                    T=_fmt_float(item.T_K),
                    observed_rate=_fmt_optional(item.observed_rate_proxy),
                    flux=_fmt_optional(item.model_flux_kg_s_m2),
                    modeled=_fmt_float(item.modeled_value),
                    observed=_fmt_float(item.observed_value),
                    dex=_fmt_float(item.dex_error_model_minus_observed),
                    factor=_fmt_float(item.error_factor),
                    notes=item.notes.replace("|", "/") or "-",
                )
            )

    if catalog:
        lines.extend(["", "## Catalog Gaps", ""])
        for entry in catalog.get("datasets", []):
            if entry.get("rows"):
                continue
            lines.append(f"- {entry.get('id')}: {entry.get('status')} - {entry.get('condition_gap')}")

    return "\n".join(lines) + "\n"


def _validate_alpha_rows(
    dataset_id: str,
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[SpeciesReproductionError], list[str]]:
    errors: list[SpeciesReproductionError] = []
    skipped: list[str] = []
    grouped: dict[tuple[str, str, float], list[float]] = defaultdict(list)
    original_species: dict[tuple[str, str, float], str] = {}
    for row in rows:
        species = str(row.get("species") or "")
        model_species = str(row.get("model_species") or species)
        T_K = _float_or_none(row.get("T_K"))
        observed = _float_or_none(row.get("value"))
        if T_K is None or observed is None or observed <= 0.0:
            continue
        key = (species, model_species, T_K)
        grouped[key].append(observed)
        original_species[key] = species

    for (species, model_species, T_K), values in grouped.items():
        if model_species in UNMODELED_SPECIES:
            skipped.append(f"{dataset_id}:{species} has no model species")
            continue
        try:
            modeled_alpha, _ = grounded_alpha(model_species, T_K)
            p_eq = _maybe_p_eq(model_species, T_K)
        except Exception as exc:  # noqa: BLE001 - validation reports model gaps.
            skipped.append(f"{dataset_id}:{species} alpha model unavailable: {exc}")
            continue
        observed_alpha = median(values)
        dex_error = math.log10(_positive(modeled_alpha) / _positive(observed_alpha))
        errors.append(
            SpeciesReproductionError(
                dataset_id=dataset_id,
                species=original_species[(species, model_species, T_K)],
                model_species=model_species,
                signal_type="evaporation_coefficient",
                T_K=T_K,
                observed_rate_proxy=None,
                model_flux_kg_s_m2=None,
                modeled_value=modeled_alpha,
                observed_value=observed_alpha,
                dex_error_model_minus_observed=dex_error,
                error_factor=10.0 ** abs(dex_error),
                p_eq_pa=p_eq,
                alpha=modeled_alpha,
                notes="direct alpha comparison",
            )
        )
    return errors, skipped


def _validate_direct_flux_rows(
    dataset_id: str,
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[SpeciesReproductionError], list[str]]:
    errors: list[SpeciesReproductionError] = []
    skipped: list[str] = []
    for row in rows:
        species = str(row.get("species") or "")
        model_species = str(row.get("model_species") or species)
        T_K = _float_or_none(row.get("T_K"))
        observed_flux = _float_or_none(row.get("value"))
        if T_K is None or observed_flux is None or observed_flux <= 0.0:
            skipped.append(
                f"{dataset_id}:{species} direct flux below-detection / not-comparable"
            )
            continue
        if model_species in UNMODELED_SPECIES:
            skipped.append(f"{dataset_id}:{species} has no model species")
            continue
        try:
            model_flux = _model_flux(model_species, T_K)
        except Exception as exc:  # noqa: BLE001 - validation reports model gaps.
            skipped.append(f"{dataset_id}:{species} flux model unavailable: {exc}")
            continue
        modeled_flux = _flux_kg_m2_s_to_molecules_cm2_s(
            model_flux.flux_kg_s_m2,
            model_species,
        )
        dex_error = math.log10(_positive(modeled_flux) / _positive(observed_flux))
        notes = str(row.get("notes") or "").strip()
        if notes:
            notes = f"direct dimensional flux comparison; {notes}"
        else:
            notes = "direct dimensional flux comparison"
        errors.append(
            SpeciesReproductionError(
                dataset_id=dataset_id,
                species=species,
                model_species=model_species,
                signal_type=str(row.get("signal_type") or ""),
                T_K=T_K,
                observed_rate_proxy=observed_flux,
                model_flux_kg_s_m2=model_flux.flux_kg_s_m2,
                modeled_value=modeled_flux,
                observed_value=observed_flux,
                dex_error_model_minus_observed=dex_error,
                error_factor=10.0 ** abs(dex_error),
                p_eq_pa=model_flux.p_eq_pa,
                alpha=model_flux.alpha,
                notes=notes,
            )
        )
    return errors, skipped


def _validate_scale_free_series(
    dataset_id: str,
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[SpeciesReproductionError], list[str]]:
    grouped: dict[tuple[str, str, str, float], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        signal = str(row.get("signal_type") or "")
        if signal not in TIME_SERIES_SIGNALS:
            continue
        time_s = _float_or_none(row.get("time_s"))
        T_K = _float_or_none(row.get("T_K"))
        value = _float_or_none(row.get("value"))
        species = str(row.get("species") or "")
        model_species = str(row.get("model_species") or species)
        if time_s is None or T_K is None or value is None:
            continue
        grouped[(species, model_species, signal, round(T_K, 6))].append(row)

    skipped: list[str] = []
    proxies: list[_ObservedProxy] = []
    for (species, model_species, signal, T_K), group_rows in grouped.items():
        if len(group_rows) < 2:
            continue
        if model_species in UNMODELED_SPECIES:
            skipped.append(f"{dataset_id}:{species} has no model species")
            continue
        points = sorted(
            (
                (_float_or_none(row.get("time_s")), _float_or_none(row.get("value")))
                for row in group_rows
            ),
            key=lambda item: item[0] if item[0] is not None else math.inf,
        )
        numeric_points = [(t, v) for t, v in points if t is not None and v is not None]
        if len(numeric_points) < 2:
            continue
        duration_s = numeric_points[-1][0] - numeric_points[0][0]
        if duration_s <= 0.0:
            continue
        observed_change = _observed_change(signal, numeric_points[0][1], numeric_points[-1][1])
        try:
            model_flux = _model_flux(model_species, T_K)
        except Exception as exc:  # noqa: BLE001 - validation reports model gaps.
            skipped.append(f"{dataset_id}:{species} flux model unavailable: {exc}")
            continue
        observed_rate = max(0.0, observed_change) / duration_s
        if observed_rate <= 0.0:
            skipped.append(
                f"{dataset_id}:{species} {signal} below-detection / not-comparable"
            )
            continue
        proxies.append(
            _ObservedProxy(
                dataset_id=dataset_id,
                species=species,
                model_species=model_species,
                signal_type=signal,
                T_K=T_K,
                observed_rate=observed_rate,
                model_flux=model_flux,
                notes="scale-free endpoint depletion proxy",
            )
        )

    errors: list[SpeciesReproductionError] = []
    for signal in sorted({proxy.signal_type for proxy in proxies}):
        signal_proxies = [proxy for proxy in proxies if proxy.signal_type == signal]
        positive_rates = [proxy.observed_rate for proxy in signal_proxies if proxy.observed_rate > 0.0]
        if not positive_rates:
            skipped.append(f"{dataset_id}:{signal} has no positive observed depletion")
            continue
        effective_observed = [proxy.observed_rate for proxy in signal_proxies]
        model_values = [proxy.model_flux.flux_kg_s_m2 for proxy in signal_proxies]
        observed_norm = _positive(median(effective_observed))
        model_norm = _positive(median(model_values))
        for proxy, observed_effective in zip(signal_proxies, effective_observed, strict=True):
            model_scaled = _positive(proxy.model_flux.flux_kg_s_m2) / model_norm
            observed_scaled = _positive(observed_effective) / observed_norm
            dex_error = math.log10(model_scaled / observed_scaled)
            errors.append(
                SpeciesReproductionError(
                    dataset_id=dataset_id,
                    species=proxy.species,
                    model_species=proxy.model_species,
                    signal_type=signal,
                    T_K=proxy.T_K,
                    observed_rate_proxy=proxy.observed_rate,
                    model_flux_kg_s_m2=proxy.model_flux.flux_kg_s_m2,
                    modeled_value=model_scaled,
                    observed_value=observed_scaled,
                    dex_error_model_minus_observed=dex_error,
                    error_factor=10.0 ** abs(dex_error),
                    p_eq_pa=proxy.model_flux.p_eq_pa,
                    alpha=proxy.model_flux.alpha,
                    notes=proxy.notes,
                )
            )
    return errors, skipped


def _model_flux(model_species: str, T_K: float) -> _ModelFlux:
    p_eq = _builtin_provider_p_eq_pa(model_species, T_K)
    alpha, _ = grounded_alpha(model_species, T_K)
    molar_mass = species_molar_mass_kg_mol(model_species)
    diagnostic = series_flux(
        model_species,
        p_eq,
        0.0,
        T_K,
        molar_mass,
        alpha,
        knudsen_number=1.0e6,
        overhead_pressure_pa=0.0,
        axial_stir_factor=0.0,
        radial_stir_factor=1.0,
        melt_resistance_enabled=False,
        gas_resistance_enabled=False,
    )
    return _ModelFlux(
        flux_kg_s_m2=diagnostic.flux_kg_s_m2,
        p_eq_pa=p_eq,
        alpha=alpha,
    )


def _maybe_p_eq(model_species: str, T_K: float) -> float | None:
    try:
        return _builtin_provider_p_eq_pa(model_species, T_K)
    except Exception:  # noqa: BLE001 - optional decomposition field.
        return None


def _builtin_provider_p_eq_pa(model_species: str, T_K: float) -> float:
    row = _vapor_pressure_row(model_species)
    parent_oxide = str(row.get("parent_oxide") or "")
    if not parent_oxide:
        raise ValueError(f"{model_species} vapor-pressure row lacks parent_oxide")
    provider = BuiltinVaporPressureProvider(_vapor_pressure_data())
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {parent_oxide: 1.0}},
            species_formula_registry={},
        ),
        temperature_C=T_K - 273.15,
        pressure_bar=1.0e-6,
        control_inputs={"pO2_bar": VALIDATION_PO2_BAR},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VaporPressureFallbackWarning)
        result = provider.dispatch(request)
    if result.status != "ok":
        raise ValueError(
            f"builtin vapor-pressure provider returned {result.status!r} "
            f"for {model_species}"
        )
    vapor_pressures = dict((result.diagnostic or {}).get("vapor_pressures_Pa") or {})
    p_eq = _float_or_none(vapor_pressures.get(model_species))
    if p_eq is None or p_eq <= 0.0:
        raise ValueError(
            f"builtin vapor-pressure provider emitted no comparable P_eq for "
            f"{model_species} at {T_K:g} K"
        )
    return p_eq


def _vapor_pressure_row(model_species: str) -> Mapping[str, Any]:
    data = _vapor_pressure_data()
    for group_name in ("metals", "oxide_vapors"):
        row = (data.get(group_name) or {}).get(model_species)
        if row:
            return row
    raise KeyError(f"no vapor_pressures.yaml row for species {model_species!r}")


@lru_cache(maxsize=1)
def _vapor_pressure_data() -> dict[str, Any]:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text()) or {}


def _flux_kg_m2_s_to_molecules_cm2_s(
    flux_kg_s_m2: float,
    model_species: str,
) -> float:
    molar_mass = species_molar_mass_kg_mol(model_species)
    return flux_kg_s_m2 / molar_mass * AVOGADRO_PER_MOL / 1.0e4


def _observed_change(signal: str, first: float, last: float) -> float:
    if signal == "mass_loss_pct":
        return last - first
    if signal == "residue_wt_pct":
        return (first - last) / max(abs(first), 1.0e-30) * 100.0
    raise ValueError(f"unsupported time-series signal {signal!r}")


def _endpoint_rank_disagreement_fraction(
    species_errors: Sequence[SpeciesReproductionError],
) -> float | None:
    comparable = [
        item
        for item in species_errors
        if item.model_flux_kg_s_m2 is not None and item.observed_rate_proxy is not None
    ]
    inverted = 0
    total = 0
    for i, left in enumerate(comparable):
        for right in comparable[i + 1 :]:
            if left.signal_type != right.signal_type:
                continue
            model_delta = left.modeled_value - right.modeled_value
            observed_delta = left.observed_value - right.observed_value
            if model_delta == 0.0 or observed_delta == 0.0:
                continue
            total += 1
            if model_delta * observed_delta < 0.0:
                inverted += 1
    if total == 0:
        return None
    return inverted / total


def _skipped_report(dataset_id: str, reason: str) -> DatasetReproductionReport:
    return DatasetReproductionReport(
        dataset_id=dataset_id,
        status="skipped",
        rows_evaluated=0,
        median_abs_dex_error=None,
        max_abs_dex_error=None,
        endpoint_rank_disagreement_fraction=None,
        species=(),
        skipped_reasons=(reason,),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    if not math.isfinite(out):
        return None
    return out


def _positive(value: float, floor: float = 1.0e-300) -> float:
    if not math.isfinite(value):
        raise ValueError(f"non-finite validation value {value!r}")
    return max(abs(value), floor)


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "-"
    return _fmt_float(value)


def _summarize_reasons(reasons: Sequence[str], limit: int = 3) -> str:
    if not reasons:
        return "-"
    shown = [reason.replace("|", "/") for reason in reasons[:limit]]
    omitted = len(reasons) - len(shown)
    if omitted > 0:
        shown.append(f"{omitted} more in JSON")
    return "; ".join(shown)


def _fmt_float(value: float) -> str:
    if value == 0.0:
        return "0"
    if abs(value) >= 1000.0 or abs(value) < 0.001:
        return f"{value:.3e}"
    return f"{value:.3f}"
