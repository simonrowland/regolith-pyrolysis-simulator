"""Below-band saturation pressure: JANAF pairs, then anchored CC."""

from __future__ import annotations

import math
from bisect import bisect_right
from functools import lru_cache

from engines.antoine import _antoine_log10_pressure


def _antoine_reciprocal_temperature_log10(A, B, C, temperature, boundary):
    """Constant-enthalpy CC continuation tangent to Antoine at boundary.

    d ln P/dT = deltaH/(R T²) integrates to
    P = P0 exp[-deltaH/R (1/T - 1/T0)]. Antoine gives
    deltaH/R = B ln(10) T0²/(T0+C)². B, C, T0 have units K, so
    deltaH/R is K and the exponent is dimensionless. T -> T0 recovers
    P0; for every T > 0 there is no shifted-temperature pole.
    """
    # The catalog owns this arithmetic and its source bytes are pinned by
    # historical additivity evidence. Reuse its evaluator without changing
    # that pin or keeping a second derivative/integration implementation.
    from simulator.vapour_rail.catalog import (
        CompiledPressureEvaluator, DEFAULT_EXTRAPOLATION_POLICY,
        PressureObservable, ValidationStatus, _ReferencePressureModel,
    )

    evaluator = CompiledPressureEvaluator(
        species_id="wall_saturation_anchor", evaluator_family="antoine",
        pressure_observable=PressureObservable.PURE_COMPONENT_SATURATION_PRESSURE,
        species_basis="pure_component", valid_temperature_K=(boundary, boundary),
        validation_status=ValidationStatus.PENDING,
        reference_model=_ReferencePressureModel("antoine", {"A": A, "B": B, "C": C}, ()),
        extrapolation_policy=DEFAULT_EXTRAPOLATION_POLICY,
        out_of_range_status="extrapolated", acquisition_flag="",
    )
    return evaluator._reciprocal_T_tangent_log10(temperature)


@lru_cache(maxsize=None)
def _janaf_saturation_curve(species):
    # Lazy: importing simulator during raw Antoine module initialization would
    # re-enter core -> builtin providers -> this module.
    from simulator.physical_constants import GAS_CONSTANT
    from simulator.reference_data.janaf import ROOT, load_manifest, load_table_document

    entries = [entry for entry in load_manifest()["entries"]
               if entry["formula"] == species and not entry.get("charge")]
    gas = next((entry for entry in entries if entry["phase"] == "g"), None)
    # cr,l is the stable condensed phase sequence, not the gas-bearing ref
    # sequence. A gas formation table alone is not a compound P_sat curve.
    condensed = next((entry for entry in entries if entry["phase"] == "cr,l"), None)
    if gas is None or condensed is None:
        return None

    def gibbs(entry):
        document = load_table_document(ROOT / entry["path"])
        return {
            float(row["temperature"]["value"]): float(row["formation_gibbs_energy"]["value"])
            for row in document["table"]["values"]
            if row["temperature"]["value"] is not None
            and row["temperature"]["value"] > 0.0
            and row["formation_gibbs_energy"]["value"] is not None
        }

    gas_g, condensed_g = gibbs(gas), gibbs(condensed)
    # mu_g° + RT ln(P/p°) = mu_cond°. JANAF p°=0.1 MPa=1e5 Pa;
    # (G_g-G_cond) kJ/mol * 1000 J/kJ / (R J/mol/K * T K) is
    # dimensionless. For an element the stable condensed G_f is zero;
    # for a salt it must be subtracted, never treated as zero.
    points = tuple((temperature, math.log(1e5) -
                    1000.0 * (gas_g[temperature] - condensed_g[temperature]) /
                    (GAS_CONSTANT * temperature))
                   for temperature in sorted(gas_g.keys() & condensed_g.keys()))
    return points, (gas["table_id"], condensed["table_id"])


def _below_band_saturation_log10(species, A, B, C, temperature, boundary):
    """One below-band P_sat owner: JANAF pair, else slope-anchored CC."""
    notice = {"anchor_temperature_K": boundary}
    curve = _janaf_saturation_curve(species)
    if curve is None:
        notice["method"] = "clausius_clapeyron"
        return _antoine_reciprocal_temperature_log10(A, B, C, temperature, boundary), notice
    points, table_ids = curve

    def ln_pressure(T):
        index = min(max(1, bisect_right([p[0] for p in points], T)), len(points) - 1)
        (t0, ln0), (t1, ln1) = points[index-1:index+1]
        # Constant local enthalpy: ln P is linear in 1/T. Same chord at
        # table edges extends to T>0 without an Antoine pole; T=T_i recovers
        # the published point. The reciprocal-temperature ratio is unitless.
        fraction = (1.0 / T - 1.0 / t0) / (1.0 / t1 - 1.0 / t0)
        return ln0 + fraction * (ln1 - ln0)

    janaf_boundary_ln = ln_pressure(boundary)
    antoine_boundary_log = _antoine_log10_pressure(A, B, C, boundary)
    ratio = math.exp(janaf_boundary_ln - math.log(10.0) * antoine_boundary_log)
    notice.update({
        "method": "janaf",
        "source_id": "nist-janaf-4th",
        "table_ids": table_ids,
        "interpolation": "ln_pressure_vs_reciprocal_temperature",
        "janaf_table_range_K": (points[0][0], points[-1][0]),
        "janaf_out_of_table_range": not points[0][0] <= temperature <= points[-1][0],
        "below_band_janaf_join": {
            "temperature_K": boundary,
            "janaf_pressure_Pa": math.exp(janaf_boundary_ln),
            "antoine_pressure_Pa": 10.0 ** antoine_boundary_log,
            "janaf_to_antoine_ratio": ratio,
            "exceeds_factor_two": ratio > 2.0 or ratio < 0.5,
        },
    })
    return ln_pressure(temperature) / math.log(10.0), notice
