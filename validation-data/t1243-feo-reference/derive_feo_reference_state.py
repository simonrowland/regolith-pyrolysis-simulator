"""Fe(1-x)O(l, Fe-saturated) -> stoichiometric FeO(l) reference conversion.

Phase-1 derivation. Reads the seat's Darken & Gurry 1946 extract and the
JANAF compilation tables. Does not score and does not modify the store.

Algebra (partial molar FeO component, Fe + 1/2 O2 = FeO)
-------------------------------------------------------
At iron saturation the measured gas ratio fixes the oxygen potential, and
equilibrium with the iron phase fixes mu_Fe. With the pure-iron convention
a_Fe = 1:

    pO2 / p° = (pCO2 / pCO)^2 / K(T)^2
    K(T) = exp(-ΔG°(CO + 1/2 O2 = CO2) / RT)
    ΔG° = ΔfG(CO2) - ΔfG(CO)          # ΔfG(O2) = 0; JANAF p° = 0.1 MPa = 1 bar
    ΔG_w,vs_solid = RT ln(pCO2/pCO) + ΔG°
                  = 1/2 RT ln(pO2 / p°)

That ΔG_w is mu_FeO(at the Fe-saturated liquid) - mu_Fe(s) - 1/2 mu°_O2.
JANAF Fe-019 ΔfG(FeO,l) is the same difference for pure stoichiometric FeO(l)
against the stable element phase. Below the iron melting point the stable
phase is solid, so

    ln(a_FeO,stoich / a_Fe(1-x)O) = (ΔG_w,vs_solid - ΔfG_FeO(l)) / RT

Above the iron melting point JANAF's element reference is liquid iron, and
the coexisting metal is liquid iron. The solid-iron buffer extrapolated past
the melting point is shifted by the iron fusion energy:

    ΔG_w,vs_stable = ΔG_w,vs_solid - ΔG_fus(Fe)
    ΔG_fus(Tm) = 0
    ΔG_fus(T) = ΔH_fus - T ΔS_fus     # ΔH, ΔS taken from the JANAF transition lines

The composition y = O/Fe does not multiply this partial. It would multiply
the integral formation reaction Fe + (y/2) O2 = FeO_y, which is a different
reaction. Those two expressions agree only at y = 1 (x = 0).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.generators.janaf import (
    JANAF_R_J_PER_MOL_K,
    _fusion_table_points,
    _interpolate_formation_gibbs,
    _interpolate_table_points,
    load_table_document,
)

ROOT = Path(__file__).resolve().parents[2]
DG_PATH = ROOT / "data/literature/extracts/darken-gurry-1946-iron-oxygen-ii.yaml"
HOLZHEID_PATH = (
    ROOT / "data/literature/extracts/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml"
)
DISTIN_PATH = ROOT / "data/literature/extracts/distin-1971-ferrous-silicate-melts.yaml"
TABLES = ROOT / "data/literature/compilations/janaf/tables"

R = JANAF_R_J_PER_MOL_K
LN10 = Decimal(10).ln()
KELVIN_OFFSET = Decimal("273.15")
LAST_DIGIT = Decimal("0.001")  # printed gas ratios are given to 0.001; no ± is printed

# Holzheid 1997 p. 30 eq. (9), J/mol, T in K. Comparison only, not the answer.
# AG("FeO" l) = -244118 + 115.559 T - 8.474 T ln T
HOLZHEID_EQ9 = (
    Decimal("-244118"),
    Decimal("115.559"),
    Decimal("8.474"),
)
# Printed activity-coefficient ratios γ_stoich / γ_wüstite, Holzheid p. 30-31.
HOLZHEID_FACTORS = (
    (Decimal("1300"), Decimal("2.02")),
    (Decimal("1400"), Decimal("1.94")),
    (Decimal("1600"), Decimal("1.66")),
)


def load_yaml(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.CSafeLoader)


def kelvin(t_c: Decimal) -> Decimal:
    return t_c + KELVIN_OFFSET


def o_over_fe(r: Decimal) -> Decimal:
    """Darken & Gurry p. 801: N_O/N_Fe = (3r - 1) / (2r)."""

    return (Decimal(3) * r - 1) / (Decimal(2) * r)


def formation_points(table_id: str):
    points, _digest = _fusion_table_points(table_id)
    return points


def transition_markers(table_id: str) -> tuple[Decimal, ...]:
    document = load_table_document(TABLES / f"{table_id}.yaml")
    table = document["table"]
    marked: list[Decimal] = []
    for item in table.get("parse_ambiguities") or ():
        raw = str(item.get("raw_line") or "")
        head = raw.split("\t", 1)[0]
        try:
            marked.append(Decimal(head))
        except Exception:
            continue
    return tuple(sorted(set(marked)))


def formation_gibbs_kJ(table_id: str, temperature_K: Decimal) -> tuple[Decimal, str]:
    """Return ΔfG in kJ/mol and how the node was read.

    The production interpolator joins the formation-Gibbs nodes on either side
    of the requested temperature. It refuses the span only when a 100 K grid
    node between those neighbours has no formation value. Fe-019's 1650 K
    CRYSTAL <-> LIQUID line is an unparsed marker, not a 100 K grid node, so
    a temperature between the 1600 K and 1700 K formation values is read by
    the production interpolator. The bridge below fires only when a real
    missing grid node is also one of those unparsed markers.
    """

    points = formation_points(table_id)
    try:
        return _interpolate_formation_gibbs(points, temperature_K), "production_interpolator"
    except ValueError as exc:
        text = str(exc)
        if "missing grid node" not in text:
            raise
        markers = set(transition_markers(table_id))
        left = max(t for t, _ in points if t < temperature_K)
        right = min(t for t, _ in points if t > temperature_K)
        missing = [
            node
            for node in markers
            if left < node < right
        ]
        if not missing:
            raise
        value = _interpolate_table_points(points, temperature_K)
        names = ",".join(str(node) for node in missing)
        return value, f"bridged_unparsed_transition:{names}"


def delta_g_co_reaction_J(temperature_K: Decimal) -> tuple[Decimal, str]:
    """ΔG° of CO + 1/2 O2 = CO2, J/mol. ΔfG(O2) = 0 by the element reference."""

    g_co2, how_co2 = formation_gibbs_kJ("C-095", temperature_K)
    g_co, how_co = formation_gibbs_kJ("C-093", temperature_K)
    how = how_co2 if how_co2 == how_co else f"CO2={how_co2};CO={how_co}"
    return (g_co2 - g_co) * Decimal(1000), how


def delta_g_w_vs_solid_J(ratio: Decimal, temperature_K: Decimal) -> tuple[Decimal, str]:
    """1/2 RT ln(pO2/p°) from a printed pCO2/pCO, in J/mol."""

    delta_g_rxn, how = delta_g_co_reaction_J(temperature_K)
    # ΔG_w = RT ln(ratio) + ΔG°(CO + 1/2 O2 = CO2)
    return R * temperature_K * ratio.ln() + delta_g_rxn, how


def fit_line(xs: list[Decimal], ys: list[Decimal]) -> tuple[Decimal, Decimal]:
    """Least squares y = A + B x. Exact for the printed Decimal inputs."""

    n = Decimal(len(xs))
    sum_x = sum(xs, Decimal(0))
    sum_y = sum(ys, Decimal(0))
    sum_xx = sum((x * x for x in xs), Decimal(0))
    sum_xy = sum((x * y for x, y in zip(xs, ys)), Decimal(0))
    denominator = n * sum_xx - sum_x * sum_x
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    return intercept, slope


def iron_fusion_from_janaf() -> dict[str, Decimal]:
    """ΔH_fus and ΔS_fus of Fe at the JANAF DELTA <-> LIQUID lines on Fe-003.

    The two lines fail the 8-column parser, so the enthalpy and entropy are
    read from the stored raw_line, not from a formation-Gibbs cell.
    Columns, as on every parsed neighbour: T, Cp, S, -[G-H]/T, H-H(Tr).
    """

    document = load_table_document(TABLES / "Fe-003.yaml")
    solid = None
    liquid = None
    for item in document["table"].get("parse_ambiguities") or ():
        raw = str(item.get("raw_line") or "")
        parts = raw.split("\t")
        if len(parts) < 6:
            continue
        temperature = Decimal(parts[0])
        if temperature != Decimal("1809.000") and temperature != Decimal("1809"):
            continue
        entropy = Decimal(parts[2])
        enthalpy = Decimal(parts[4])
        label = parts[5]
        if "DELTA" in label and "LIQUID" in label:
            solid = (entropy, enthalpy, label)
        elif label.startswith("TRANSITION"):
            liquid = (entropy, enthalpy, label)
    if solid is None or liquid is None:
        raise SystemExit("Fe-003 is missing the 1809 K DELTA <-> LIQUID pair")
    delta_s = liquid[0] - solid[0]  # J/mol/K
    delta_h_kJ = liquid[1] - solid[1]  # kJ/mol
    t_m = Decimal("1809")
    return {
        "Tm_K": t_m,
        "delta_H_J_per_mol": delta_h_kJ * Decimal(1000),
        "delta_S_J_per_mol_K": delta_s,
        "solid_S": solid[0],
        "liquid_S": liquid[0],
        "solid_H_kJ": solid[1],
        "liquid_H_kJ": liquid[1],
        "solid_label": solid[2],
        "liquid_label": liquid[2],
    }


def delta_g_fus_J(temperature_K: Decimal, fusion: dict[str, Decimal]) -> Decimal:
    """G(liquid) - G(solid) with ΔH and ΔS held at the 1809 K values."""

    return fusion["delta_H_J_per_mol"] - temperature_K * fusion["delta_S_J_per_mol_K"]


def holzheid_eq9_J(temperature_K: Decimal) -> Decimal:
    a, b, c = HOLZHEID_EQ9
    return a + b * temperature_K - c * temperature_K * temperature_K.ln()


def collect_boundary_rows(document: dict) -> tuple[list[dict], list[dict]]:
    gas: list[dict] = []
    compositions: list[dict] = []
    for species, block in (document.get("species") or {}).items():
        for obs in block.get("observations") or ():
            values = obs.get("values") or {}
            if "pCO2_over_pCO_as_printed" in values:
                gas.append(
                    {
                        "id": obs["observation_id"],
                        "species": species,
                        "T_C": Decimal(str(values["T_C"])),
                        "ratio": Decimal(str(values["pCO2_over_pCO_as_printed"])),
                    }
                )
            if "r_as_printed" in values and "equilibrium with iron" in str(obs.get("phase")):
                compositions.append(
                    {
                        "id": obs["observation_id"],
                        "T_C": Decimal(str(values["T_C"])),
                        "r": Decimal(str(values["r_as_printed"])),
                    }
                )
    gas.sort(key=lambda row: row["T_C"])
    compositions.sort(key=lambda row: (row["T_C"], row["r"]))
    return gas, compositions


def _standard_formula(obs: dict) -> str | None:
    standard = obs.get("standard_state")
    if not isinstance(standard, dict):
        return None
    state = standard.get("state") or {}
    if not isinstance(state, dict):
        return None
    value = state.get("value") or {}
    if not isinstance(value, dict):
        return None
    endmember = value.get("endmember") or {}
    if not isinstance(endmember, dict):
        return None
    formula = endmember.get("formula")
    return str(formula) if formula else None


def _temperatures_in(obs: dict) -> list[Decimal]:
    found: list[Decimal] = []
    values = obs.get("values") or {}
    if "T_K" in values:
        found.append(Decimal(str(values["T_K"])))
    elif "T_C" in values:
        found.append(kelvin(Decimal(str(values["T_C"]))))
    series = values.get("series")
    if isinstance(series, list):
        for item in series:
            if not isinstance(item, dict):
                continue
            if "T_K" in item:
                found.append(Decimal(str(item["T_K"])))
            elif "T_C" in item:
                found.append(kelvin(Decimal(str(item["T_C"]))))
    return found


def feo_reference_rows(document: dict) -> list[dict]:
    rows: list[dict] = []
    for block in (document.get("species") or {}).values():
        for obs in block.get("observations") or ():
            if _standard_formula(obs) != "Fe(1-x)O":
                continue
            values = obs.get("values") or {}
            rows.append(
                {
                    "id": obs.get("observation_id"),
                    "type": obs.get("type"),
                    "quantity": values.get("quantity"),
                    "temperatures": _temperatures_in(obs),
                }
            )
    return rows


def heat_capacity_J(table_id: str, temperature_K: Decimal) -> Decimal | None:
    document = load_table_document(TABLES / f"{table_id}.yaml")
    for row in document["table"].get("values") or ():
        temperature = (row.get("temperature") or {}).get("value")
        if temperature is None or Decimal(str(temperature)) != temperature_K:
            continue
        heat = row.get("heat_capacity") or {}
        if heat.get("value") is None:
            return None
        return Decimal(str(heat["value"]))
    return None


def span(temperatures: list[Decimal]) -> tuple[int, Decimal, Decimal]:
    return len(temperatures), min(temperatures), max(temperatures)


def evaluate_factor(
    temperature_K: Decimal,
    intercept_J: Decimal,
    slope_J_per_K: Decimal,
    fusion: dict[str, Decimal],
) -> dict[str, Decimal | str]:
    delta_g_solid = intercept_J + slope_J_per_K * temperature_K
    fus = delta_g_fus_J(temperature_K, fusion)
    above_iron_melt = temperature_K > fusion["Tm_K"]
    delta_g_stable = delta_g_solid - fus if above_iron_melt else delta_g_solid
    g_feo, how = formation_gibbs_kJ("Fe-019", temperature_K)
    delta_g_feo = g_feo * Decimal(1000)
    delta = delta_g_stable - delta_g_feo
    ln_factor = delta / (R * temperature_K)
    factor = ln_factor.exp()
    log10_factor = ln_factor / LN10
    return {
        "T_K": temperature_K,
        "delta_g_w_vs_solid_J": delta_g_solid,
        "delta_g_fus_J": fus,
        "iron_phase": "liquid" if above_iron_melt else "solid",
        "delta_g_w_vs_stable_J": delta_g_stable,
        "delta_fG_FeO_l_J": delta_g_feo,
        "janaf_read": how,
        "ln_factor": ln_factor,
        "factor": factor,
        "log10_factor": log10_factor,
    }


def fmt(value: Decimal, places: str = "0.000") -> str:
    quantum = Decimal(places)
    return str(value.quantize(quantum))


def main() -> None:
    dg = load_yaml(DG_PATH)
    gas, compositions = collect_boundary_rows(dg)
    if len(gas) != 3:
        raise SystemExit(f"expected 3 iron-boundary gas rows, found {len(gas)}")

    print("IRON-BOUNDARY GAS RATIOS (Table III B)")
    anchors = []
    for row in gas:
        t_k = kelvin(row["T_C"])
        delta_g, how = delta_g_w_vs_solid_J(row["ratio"], t_k)
        delta_g_rxn, _rxn_how = delta_g_co_reaction_J(t_k)
        anchors.append((t_k, delta_g, row))
        print(
            f"  {row['id']}  T_C={row['T_C']}  T_K={t_k}  "
            f"pCO2/pCO={row['ratio']}  "
            f"ΔG°(CO+1/2O2=CO2)={fmt(delta_g_rxn / 1000, '0.000')} kJ/mol  "
            f"ΔG_w,vs_solid={fmt(delta_g / 1000, '0.000')} kJ/mol  "
            f"janaf={how}"
        )

    xs = [t for t, _, _ in anchors]
    ys = [g for _, g, _ in anchors]
    intercept, slope = fit_line(xs, ys)
    print(
        f"FIT ΔG_w,vs_solid = {fmt(intercept / 1000, '0.000')} kJ/mol "
        f"+ ({fmt(slope, '0.000')} J/mol/K) * T"
    )
    print("FIT RESIDUALS (derived inconsistency of the three printed ratios; not a printed ±)")
    for t_k, delta_g, row in anchors:
        predicted = intercept + slope * t_k
        residual = delta_g - predicted
        print(
            f"  {row['id']}  residual={fmt(residual, '0.0')} J/mol  "
            f"({fmt(residual / 1000, '0.000')} kJ/mol)"
        )

    fusion = iron_fusion_from_janaf()
    print("IRON FUSION FROM Fe-003 1809 K LINES")
    print(
        f"  ΔH_fus={fmt(fusion['delta_H_J_per_mol'] / 1000, '0.000')} kJ/mol  "
        f"ΔS_fus={fmt(fusion['delta_S_J_per_mol_K'], '0.000')} J/mol/K  "
        f"ΔH/Tm={fmt(fusion['delta_H_J_per_mol'] / fusion['Tm_K'], '0.000')} J/mol/K  "
        f"labels={fusion['solid_label']!r} -> {fusion['liquid_label']!r}"
    )
    print(
        f"  ΔG_fus(Tm)={fmt(delta_g_fus_J(fusion['Tm_K'], fusion), '0.0')} J/mol  "
        f"(must be ~0; nonzero is the printed-digit mismatch of ΔH and Tm ΔS)"
    )

    print("IRON-BOUNDARY COMPOSITION (Table III A, liquid oxide in equilibrium with iron)")
    rs = [row["r"] for row in compositions]
    ys_comp = [o_over_fe(r) for r in rs]
    for row in compositions:
        y = o_over_fe(row["r"])
        print(f"  {row['id']}  T_C={row['T_C']}  r={row['r']}  O/Fe={fmt(y, '0.0000')}")
    print(
        f"  n={len(compositions)}  r=[{min(rs)}, {max(rs)}]  "
        f"O/Fe=[{fmt(min(ys_comp), '0.0000')}, {fmt(max(ys_comp), '0.0000')}]"
    )
    y_quoted = o_over_fe(Decimal("1.067"))
    print(f"  paper's stated r=1.067 -> O/Fe={fmt(y_quoted, '0.00000')}  x={fmt(1 - 1 / y_quoted, '0.00000')}")
    print(f"  limit r->1: O/Fe={o_over_fe(Decimal(1))}  (stoichiometric; partial and integral agree)")

    holzheid_rows = feo_reference_rows(load_yaml(HOLZHEID_PATH))
    distin_rows = feo_reference_rows(load_yaml(DISTIN_PATH))
    holzheid_t = [t for row in holzheid_rows for t in row["temperatures"]]
    distin_t = [t for row in distin_rows for t in row["temperatures"]]
    h_n, h_lo, h_hi = span(holzheid_t)
    d_n, d_lo, d_hi = span(distin_t)
    print("ROW TEMPERATURE SPANS (extract temperatures on Fe(1-x)O reference rows)")
    for name, rows, temperatures in (
        ("Holzheid", holzheid_rows, holzheid_t),
        ("Distin", distin_rows, distin_t),
    ):
        n, lo, hi = span(temperatures)
        multi = [row["id"] for row in rows if len(row["temperatures"]) != 1]
        types = sorted({str(row["type"]) for row in rows})
        quantities = sorted({str(row["quantity"]) for row in rows})
        print(
            f"  {name} observations={len(rows)} temperatures={n}  "
            f"T_K=[{lo}, {hi}]  T_C=[{lo - KELVIN_OFFSET}, {hi - KELVIN_OFFSET}]"
        )
        print(f"    type={types}  values.quantity={quantities}  rows_with_not_one_T={len(multi)}")
    dg_liquidus = kelvin(Decimal("1376"))
    feo_melt = Decimal("1650")
    fe_melt = fusion["Tm_K"]
    print("TEMPERATURE REGIMES")
    print(
        f"  boundaries: DG first liquid-oxide gas point {dg_liquidus} K; "
        f"JANAF FeO CRYSTAL<->LIQUID marker {feo_melt} K; Fe melting {fe_melt} K"
    )
    for name, temperatures in (("Holzheid", holzheid_t), ("Distin", distin_t)):
        below_dg = sum(1 for t in temperatures if t < dg_liquidus)
        below_feo = sum(1 for t in temperatures if t < feo_melt)
        in_band = sum(1 for t in temperatures if dg_liquidus <= t <= Decimal("1766.15"))
        feo_to_fe = sum(1 for t in temperatures if feo_melt <= t <= fe_melt)
        above_fe = sum(1 for t in temperatures if t > fe_melt)
        unique = sorted(set(temperatures))
        print(
            f"  {name}  below_DG_liquidus={below_dg}  below_FeO_melt={below_feo}  "
            f"inside_DG_gas_band={in_band}  FeO_melt_to_Fe_melt={feo_to_fe}  "
            f"above_Fe_melt={above_fe}"
        )
        print("    unique_T_K=" + ",".join(str(t) for t in unique))
    feo_points = formation_points("Fe-019")
    print(
        f"JANAF Fe-019 FeO(l) ΔfG nodes: n={len(feo_points)}  "
        f"T=[{feo_points[0][0]}, {feo_points[-1][0]}] K  "
        f"CRYSTAL<->LIQUID marker 1650 K  GLASS<->LIQUID marker 1100 K"
    )
    print(f"JANAF Fe melting marker on Fe-003: {fusion['Tm_K']} K")

    targets: list[tuple[str, Decimal]] = []
    for t_k, _, row in anchors:
        targets.append((f"anchor {row['id']}", t_k))
    targets.append(("FeO(l) melting marker", Decimal("1650")))
    targets.append(("Fe melting", fusion["Tm_K"]))
    for t_c, _factor in HOLZHEID_FACTORS:
        targets.append((f"Holzheid quoted {t_c} C", kelvin(t_c)))
    targets.append(("Holzheid min row", h_lo))
    targets.append(("Holzheid max row", h_hi))
    for t_c in (Decimal("1785"), Decimal("1880"), Decimal("1960")):
        targets.append((f"Distin {t_c} C", kelvin(t_c)))

    # Jackknife: each pair of anchors, evaluated at the targets.
    pairs = (
        (anchors[0], anchors[1]),
        (anchors[0], anchors[2]),
        (anchors[1], anchors[2]),
    )
    pair_fits = []
    for left, right in pairs:
        pair_fits.append(fit_line([left[0], right[0]], [left[1], right[1]]))

    print("CORRECTION  factor = a_FeO,stoich / a_Fe(1-x)O")
    print(
        "  primary = three-point fit; envelope = min/max of the three leave-one-in pair fits"
    )
    print(
        "  last-digit width = largest |Δ ln(factor)| when one printed ratio moves by 0.001"
    )
    header = (
        f"{'label':<28} {'T_K':>10} {'factor':>8} {'ln':>8} {'dex':>8} "
        f"{'env_lo':>8} {'env_hi':>8} {'digit':>8} {'Fe':>6} {'read':<28}"
    )
    print(header)
    for label, t_k in targets:
        primary = evaluate_factor(t_k, intercept, slope, fusion)
        envelope = [
            evaluate_factor(t_k, pair_intercept, pair_slope, fusion)["factor"]
            for pair_intercept, pair_slope in pair_fits
        ]
        # One-at-a-time last-digit shift of each anchor, refit, change in ln(factor).
        digit_shifts = []
        for index, row in enumerate(gas):
            for sign in (Decimal(1), Decimal(-1)):
                shifted_ys = []
                for j, (t_anchor, _, original) in enumerate(anchors):
                    ratio = original["ratio"] + (sign * LAST_DIGIT if j == index else 0)
                    shifted, _how = delta_g_w_vs_solid_J(ratio, t_anchor)
                    shifted_ys.append(shifted)
                s_intercept, s_slope = fit_line(xs, shifted_ys)
                shifted_eval = evaluate_factor(t_k, s_intercept, s_slope, fusion)
                digit_shifts.append(abs(shifted_eval["ln_factor"] - primary["ln_factor"]))
        digit = max(digit_shifts)
        print(
            f"{label:<28} {fmt(t_k, '0.00'):>10} {fmt(primary['factor'], '0.000'):>8} "
            f"{fmt(primary['ln_factor'], '0.000'):>8} {fmt(primary['log10_factor'], '0.000'):>8} "
            f"{fmt(min(envelope), '0.000'):>8} {fmt(max(envelope), '0.000'):>8} "
            f"{fmt(digit, '0.00000'):>8} {primary['iron_phase']:>6} {primary['janaf_read']:<28}"
        )

    print("EXACT ANCHORS (measured ΔG_w at that T, no three-point fit; all three are below Fe melting)")
    for t_k, delta_g, row in anchors:
        exact = evaluate_factor(t_k, delta_g, Decimal(0), fusion)
        fitted = evaluate_factor(t_k, intercept, slope, fusion)
        print(
            f"  {row['id']}  exact_factor={fmt(exact['factor'], '0.000')}  "
            f"fitted_factor={fmt(fitted['factor'], '0.000')}  "
            f"Δln={fmt(fitted['ln_factor'] - exact['ln_factor'], '0.00000')}"
        )

    print("FUSION SHIFT (same fit; ΔH_fus and ΔS_fus set to zero so the solid-iron line is not shifted)")
    no_fusion = dict(fusion)
    no_fusion["delta_H_J_per_mol"] = Decimal(0)
    no_fusion["delta_S_J_per_mol_K"] = Decimal(0)
    for label, t_k in (
        ("Holzheid 1600 C", kelvin(Decimal("1600"))),
        ("Holzheid max row", h_hi),
        ("Distin 1785 C", kelvin(Decimal("1785"))),
        ("Distin 1960 C", kelvin(Decimal("1960"))),
    ):
        with_fusion = evaluate_factor(t_k, intercept, slope, fusion)
        without = evaluate_factor(t_k, intercept, slope, no_fusion)
        print(
            f"  {label}  with_fusion={fmt(with_fusion['factor'], '0.000')}  "
            f"without={fmt(without['factor'], '0.000')}  "
            f"ΔG_fus={fmt(with_fusion['delta_g_fus_J'] / 1000, '0.000')} kJ/mol  "
            f"Fe={with_fusion['iron_phase']}"
        )

    print("PRESSURE OFFSET 1 atm vs JANAF 1 bar (not applied): 1/2 RT ln(1.01325)")
    atm_over_bar = Decimal("1.01325")
    for t_c in (Decimal("1300"), Decimal("1400"), Decimal("1600"), Decimal("1960")):
        t_k = kelvin(t_c)
        offset = Decimal("0.5") * R * t_k * atm_over_bar.ln()
        print(f"  {t_c} C  {fmt(offset, '0.0')} J/mol")

    print("IRON ΔCp OF FUSION (not applied; constant-ΔCp bound from the nearest printed Cp nodes)")
    cp_solid = heat_capacity_J("Fe-003", Decimal("1800"))
    cp_liquid_1800 = heat_capacity_J("Fe-006", Decimal("1800"))
    cp_liquid_1900 = heat_capacity_J("Fe-006", Decimal("1900"))
    cp_liquid_2200 = heat_capacity_J("Fe-006", Decimal("2200"))
    print(
        f"  Fe-003 Cp(1800 K)={cp_solid}  Fe-006 Cp(1800 K)={cp_liquid_1800}  "
        f"Fe-006 Cp(1900 K)={cp_liquid_1900}  Fe-006 Cp(2200 K)={cp_liquid_2200}  J/mol/K"
    )
    if cp_solid is not None and cp_liquid_1900 is not None:
        delta_cp = cp_liquid_1900 - cp_solid
        for t_c in (Decimal("1600"), Decimal("1960")):
            t_k = kelvin(t_c)
            if t_k <= fusion["Tm_K"]:
                extra = Decimal(0)
            else:
                extra = delta_cp * (
                    (t_k - fusion["Tm_K"]) - t_k * (t_k / fusion["Tm_K"]).ln()
                )
            print(
                f"  ΔCp={fmt(delta_cp, '0.000')} J/mol/K from Fe-006(1900)-Fe-003(1800); "
                f"extra ΔG_fus at {t_c} C = {fmt(extra, '0.0')} J/mol"
            )

    print("HOLZHEID COMPARISON (their eq 9 and their printed factors; not used as the correction)")
    for t_c, quoted in HOLZHEID_FACTORS:
        t_k = kelvin(t_c)
        ours = evaluate_factor(t_k, intercept, slope, fusion)
        eq9 = holzheid_eq9_J(t_k)
        # Their factor and their eq 9 imply the stoichiometric ΔG they subtracted.
        implied_stoich = eq9 - R * t_k * quoted.ln()
        print(
            f"  {t_c} C  quoted_factor={quoted}  our_factor={fmt(ours['factor'], '0.000')}  "
            f"eq9={fmt(eq9 / 1000, '0.000')} kJ/mol  "
            f"our_ΔG_w={fmt(ours['delta_g_w_vs_stable_J'] / 1000, '0.000')} kJ/mol  "
            f"JANAF_FeO(l)={fmt(ours['delta_fG_FeO_l_J'] / 1000, '0.000')} kJ/mol  "
            f"eq9_implied_stoich={fmt(implied_stoich / 1000, '0.000')} kJ/mol"
        )

    # Effect of using the integral reaction instead of the partial, at the
    # paper's stated boundary composition. Shown so the y->1 limit is visible.
    print("STOICHIOMETRY SENSITIVITY AT THE STATED BOUNDARY (not applied)")
    y = y_quoted
    for t_c in (Decimal("1400"), Decimal("1600"), Decimal("1785"), Decimal("1960")):
        t_k = kelvin(t_c)
        primary = evaluate_factor(t_k, intercept, slope, fusion)
        # Integral formation per mole of Fe is y times the partial, because
        # Fe + (y/2) O2 consumes y times as much 1/2 O2.
        delta_partial = primary["delta_g_w_vs_stable_J"]
        delta_integral = y * delta_partial
        ln_integral = (delta_integral - primary["delta_fG_FeO_l_J"]) / (R * t_k)
        print(
            f"  {t_c} C  partial_factor={fmt(primary['factor'], '0.000')}  "
            f"integral_factor={fmt(ln_integral.exp(), '0.000')}  "
            f"ratio_of_factors={fmt((ln_integral - primary['ln_factor']).exp(), '0.000')}"
        )

    # Dissolved-oxygen sensitivity from Distin's printed saturation contents.
    # Not applied: the pure-iron convention sets a_Fe = 1 on both sides.
    print("DISSOLVED-OXYGEN SENSITIVITY (Distin saturation wt% O; not applied)")
    atomic_fe = Decimal("55.845")
    atomic_o = Decimal("15.999")
    for t_c, wt in (
        (Decimal("1785"), Decimal("0.44")),
        (Decimal("1880"), Decimal("0.63")),
        (Decimal("1960"), Decimal("0.83")),
    ):
        # mole fraction of O in Fe-O, wt% on a 100 g basis
        n_o = wt / atomic_o
        n_fe = (Decimal(100) - wt) / atomic_fe
        x_o = n_o / (n_o + n_fe)
        a_fe = 1 - x_o
        # Δ ln(factor) = ln(a_Fe) if the term were included in ΔG_w
        print(
            f"  {t_c} C  O={wt} wt%  X_O={fmt(x_o, '0.0000')}  "
            f"a_Fe~{fmt(a_fe, '0.0000')}  factor_multiplier={fmt(a_fe.ln().exp(), '0.000')}"
        )


if __name__ == "__main__":
    main()
