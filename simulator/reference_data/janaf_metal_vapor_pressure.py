"""Pure-metal saturation vapour pressure derived from NIST-JANAF tables.

The generated table is a reference-data input for a later wall/crown source
selection change. Runtime consumers intentionally remain on their current
Antoine sidecars until that separate change is reviewed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from simulator.reference_data.janaf import (
    JANAF_R_J_MOL_K,
    TABLES_DIR,
    load_table_document,
    parse_published_number,
)
from simulator.thermochemical_pressure import (
    log10_equilibrium_constant_from_delta_g_kj_mol,
    log10_pressure_ratio_from_pa,
    pressure_pa_from_log10_equilibrium_constant,
)

STANDARD_PRESSURE_PA = 100_000.0  # JANAF p° = 0.1 MPa = 1 bar.
JANAF_R_J_MOL_K_FLOAT = float(JANAF_R_J_MOL_K)
TABULATION_MAX_TEMPERATURE_K = 3500.0
BOILING_CHECK_TOLERANCE_LOG10_BAR = 0.002

# Each condensed table is JANAF's combined crystal/liquid path. The reference
# table supplies the published melting and normal-boiling transition rows.
METAL_TABLES: dict[str, dict[str, str]] = {
    "Na": {"condensed": "Na-004", "gas": "Na-005", "reference": "Na-001"},
    "K": {"condensed": "K-004", "gas": "K-005", "reference": "K-001"},
    "Mg": {"condensed": "Mg-004", "gas": "Mg-005", "reference": "Mg-001"},
    "Ca": {"condensed": "Ca-005", "gas": "Ca-006", "reference": "Ca-001"},
    "Fe": {"condensed": "Fe-007", "gas": "Fe-008", "reference": "Fe-003"},
    "Al": {"condensed": "Al-004", "gas": "Al-005", "reference": "Al-001"},
    "Si": {"condensed": "Si-004", "gas": "Si-005", "reference": "Si-001"},
    "Ti": {"condensed": "Ti-005", "gas": "Ti-006", "reference": "Ti-001"},
    "Cr": {"condensed": "Cr-004", "gas": "Cr-005", "reference": "Cr-001"},
    "Mn": {"condensed": "Mn-004", "gas": "Mn-005", "reference": "Mn-001"},
}


def _load_element_table(
    table_id: str,
    *,
    element: str,
    expected_state: str,
    tables_dir: Path,
) -> dict[str, Any]:
    document = load_table_document(tables_dir / f"{table_id}.yaml")
    table = document.get("table")
    if not isinstance(table, Mapping) or table.get("table_id") != table_id:
        raise ValueError(f"JANAF table identity mismatch: {table_id}")
    index_entry = table.get("index_entry")
    if (
        not isinstance(index_entry, Mapping)
        or index_entry.get("formula_normalised") != element
        or index_entry.get("charge") != 0
        or index_entry.get("state") != expected_state
    ):
        raise ValueError(
            f"{table_id} is not neutral {element}({expected_state}) JANAF data"
        )
    standard_state = str(table.get("standard_state_as_published") or "")
    if "0.1 MPa" not in standard_state:
        raise ValueError(f"{table_id} does not declare the JANAF 0.1 MPa standard state")
    return dict(table)


def _cell_value(row: Mapping[str, Any], column: str) -> float | None:
    cell = row.get(column)
    if not isinstance(cell, Mapping):
        return None
    value = cell.get("value")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _cell_provenance(row: Mapping[str, Any], column: str) -> dict[str, Any]:
    cell = row.get(column)
    if not isinstance(cell, Mapping):
        return {"as_published": None, "locator": None}
    return {
        "as_published": cell.get("as_published"),
        "locator": cell.get("locator"),
    }


def _rows_by_temperature(table: Mapping[str, Any]) -> dict[float, Mapping[str, Any]]:
    rows: dict[float, Mapping[str, Any]] = {}
    for row in table.get("values", []):
        if not isinstance(row, Mapping):
            continue
        temperature = _cell_value(row, "temperature")
        if temperature is None or temperature < 298.0:
            continue
        if temperature in rows:
            raise ValueError(
                f"duplicate JANAF temperature {temperature:g} K in "
                f"{table.get('table_id')}"
            )
        rows[temperature] = row
    return rows


def _transition_marker(
    table: Mapping[str, Any],
    *,
    description: str,
    matches_label: Callable[[str], bool],
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for ambiguity in table.get("parse_ambiguities", []):
        if not isinstance(ambiguity, Mapping):
            continue
        raw_line = str(ambiguity.get("raw_line") or "")
        fields = raw_line.split("\t")
        if len(fields) < 2 or not matches_label(fields[-1].strip()):
            continue
        temperature = parse_published_number(fields[0])
        if temperature is None:
            continue
        matches.append(
            {
                "temperature_K": temperature,
                "temperature_as_published": fields[0],
                "raw_line": raw_line,
                "locator": ambiguity.get("locator"),
            }
        )
    temperatures = {item["temperature_K"] for item in matches}
    if len(matches) != 1 or len(temperatures) != 1:
        raise ValueError(
            f"expected one {description} transition in "
            f"{table.get('table_id')}; found {len(matches)}"
        )
    return matches[0]


def _gibbs_from_janaf_function(
    row: Mapping[str, Any],
    *,
    reference_enthalpy_kj_mol: float,
    temperature_K: float,
) -> float:
    """Return G°(T) in kJ/mol from JANAF's -[G°-H°(Tr)]/T column."""

    gibbs_function = _cell_value(row, "negative_gibbs_enthalpy_function")
    if gibbs_function is None:
        raise ValueError("JANAF Gibbs-enthalpy-function cell is absent")
    # JANAF gives -[G°(T)-H°(Tr)]/T in J mol-1 K-1. Thus
    # G°(T) = H°(Tr) - T*function, with formation_enthalpy at Tr=298.15 K
    # supplying H°(Tr) on the common elemental reference basis.
    return reference_enthalpy_kj_mol - temperature_K * gibbs_function / 1000.0


def _interpolate(points: Mapping[float, float], temperature_K: float) -> float:
    temperatures = sorted(points)
    if not temperatures or temperature_K < temperatures[0] or temperature_K > temperatures[-1]:
        raise ValueError(f"temperature {temperature_K:g} K is outside JANAF table coverage")
    lower = max(value for value in temperatures if value <= temperature_K)
    upper = min(value for value in temperatures if value >= temperature_K)
    if lower == upper:
        return points[lower]
    fraction = (temperature_K - lower) / (upper - lower)
    return points[lower] + fraction * (points[upper] - points[lower])


def _table_row_limit(
    *,
    gas_rows: Mapping[float, Mapping[str, Any]],
    condensed_rows: Mapping[float, Mapping[str, Any]],
) -> tuple[list[float], float]:
    common = sorted(set(gas_rows).intersection(condensed_rows))
    common = [temperature for temperature in common if temperature >= 298.0]
    if not common:
        raise ValueError("gas and condensed JANAF tables have no common nodes")
    return common, common[0]


def build_janaf_metal_vapor_pressure_data(
    *,
    tables_dir: Path = TABLES_DIR,
    max_temperature_K: float = TABULATION_MAX_TEMPERATURE_K,
) -> dict[str, Any]:
    """Build pure-metal P_sat rows and their JANAF source-cell provenance."""

    metals: dict[str, Any] = {}
    for element, table_ids in METAL_TABLES.items():
        condensed = _load_element_table(
            table_ids["condensed"],
            element=element,
            expected_state="cr,l",
            tables_dir=tables_dir,
        )
        gas = _load_element_table(
            table_ids["gas"],
            element=element,
            expected_state="g",
            tables_dir=tables_dir,
        )
        reference = _load_element_table(
            table_ids["reference"],
            element=element,
            expected_state="ref",
            tables_dir=tables_dir,
        )
        gas_rows = _rows_by_temperature(gas)
        condensed_rows = _rows_by_temperature(condensed)
        common_temperatures, first_temperature = _table_row_limit(
            gas_rows=gas_rows,
            condensed_rows=condensed_rows,
        )
        reference_temperature = first_temperature
        if reference_temperature != 298.15:
            raise ValueError(
                f"{element} JANAF elemental functions start at "
                f"{reference_temperature:g} K, expected 298.15 K reference rows"
            )
        gas_h298 = _cell_value(gas_rows[reference_temperature], "formation_enthalpy")
        condensed_h298 = _cell_value(
            condensed_rows[reference_temperature], "formation_enthalpy"
        )
        if gas_h298 is None or condensed_h298 is None:
            raise ValueError(f"{element} JANAF formation enthalpy at 298.15 K is absent")

        boiling_marker = _transition_marker(
            reference,
            description="liquid-to-ideal-gas",
            matches_label=lambda label: label == "LIQUID <--> IDEAL GAS",
        )
        melting_marker = _transition_marker(
            reference,
            description="condensed-to-liquid",
            matches_label=lambda label: label.endswith("<--> LIQUID")
            and label != "LIQUID <--> IDEAL GAS",
        )
        boiling_temperature = float(boiling_marker["temperature_K"])
        melting_temperature = float(melting_marker["temperature_K"])

        gas_gibbs = {
            temperature: _gibbs_from_janaf_function(
                gas_rows[temperature],
                reference_enthalpy_kj_mol=gas_h298,
                temperature_K=temperature,
            )
            for temperature in common_temperatures
        }
        condensed_gibbs = {
            temperature: _gibbs_from_janaf_function(
                condensed_rows[temperature],
                reference_enthalpy_kj_mol=condensed_h298,
                temperature_K=temperature,
            )
            for temperature in common_temperatures
        }

        # Premise: for M(condensed) <=> M(g), JANAF's neutral-atom formation
        # equilibrium constant below the normal boiling point is Kf = P/P°.
        # Therefore log10(P_sat/bar) = log10(Kf). Above the boiling marker the
        # gas is the standard elemental state, so equilibrium gives
        # log10(P_sat/bar) = -[G(g)-G(condensed)]/[R*T*ln(10)]. Here G is in
        # kJ/mol, hence the factor 1000 converts the numerator to J/mol; R is
        # J/(mol K), and the result is dimensionless log10(bar/bar). Finally,
        # P_sat(Pa) = 100000 Pa * 10**log10(P_sat/bar), since p° is 1 bar.
        # Sanity check: interpolate both condensed and gas Gibbs functions at
        # each JANAF normal-boiling marker. P must reproduce 1 bar within the
        # table's 0.002 dex resolution before any values are generated.
        # Agreement between pre-boiling log Kf and the Gibbs difference also
        # catches a wrong phase table or standard-state assumption.
        preboiling_residuals: list[float] = []
        for temperature in common_temperatures:
            if temperature >= boiling_temperature:
                continue
            gas_log_k = _cell_value(
                gas_rows[temperature], "log10_formation_equilibrium_constant"
            )
            if gas_log_k is None:
                raise ValueError(
                    f"{element} gas log Kf is absent at {temperature:g} K"
                )
            delta_g_kj_mol = gas_gibbs[temperature] - condensed_gibbs[temperature]
            gibbs_log_pressure = log10_equilibrium_constant_from_delta_g_kj_mol(
                delta_g_kj_mol,
                temperature,
                JANAF_R_J_MOL_K_FLOAT,
            )
            preboiling_residuals.append(abs(gas_log_k - gibbs_log_pressure))
        max_preboiling_residual = max(preboiling_residuals, default=0.0)
        if max_preboiling_residual > BOILING_CHECK_TOLERANCE_LOG10_BAR:
            raise ValueError(
                f"{element} log Kf/Gibbs reference mismatch: "
                f"{max_preboiling_residual:.6g} dex"
            )

        boiling_delta_g = _interpolate(gas_gibbs, boiling_temperature) - _interpolate(
            condensed_gibbs, boiling_temperature
        )
        boiling_log_pressure = log10_equilibrium_constant_from_delta_g_kj_mol(
            boiling_delta_g,
            boiling_temperature,
            JANAF_R_J_MOL_K_FLOAT,
        )
        if abs(boiling_log_pressure) > BOILING_CHECK_TOLERANCE_LOG10_BAR:
            raise ValueError(
                f"{element} JANAF normal-boiling check is "
                f"10^{boiling_log_pressure:.6g} bar, outside table resolution"
            )

        last_temperature = min(
            max_temperature_K,
            common_temperatures[-1],
        )
        tabulated_temperatures = [
            temperature
            for temperature in common_temperatures
            if first_temperature <= temperature <= last_temperature
        ]
        rows: list[dict[str, Any]] = []
        for temperature in tabulated_temperatures:
            gas_row = gas_rows[temperature]
            condensed_row = condensed_rows[temperature]
            if temperature < boiling_temperature:
                log_pressure_bar = _cell_value(
                    gas_row, "log10_formation_equilibrium_constant"
                )
                if log_pressure_bar is None:
                    raise ValueError(
                        f"{element} gas log Kf is absent at {temperature:g} K"
                    )
                method = "JANAF log10 Kf[M(g)] relative to the condensed reference"
            else:
                delta_g_kj_mol = gas_gibbs[temperature] - condensed_gibbs[temperature]
                log_pressure_bar = log10_equilibrium_constant_from_delta_g_kj_mol(
                    delta_g_kj_mol,
                    temperature,
                    JANAF_R_J_MOL_K_FLOAT,
                )
                method = "JANAF G(g) - G(condensed) from the stable crystal/liquid table"

            pressure_pa = pressure_pa_from_log10_equilibrium_constant(
                log_pressure_bar, STANDARD_PRESSURE_PA
            )
            if not math.isfinite(pressure_pa) or pressure_pa <= 0.0:
                raise ValueError(
                    f"{element} generated invalid pressure at {temperature:g} K"
                )
            rows.append(
                {
                    "temperature_K": temperature,
                    "condensed_phase": "cr"
                    if temperature < melting_temperature
                    else "l",
                    "log10_pressure_bar": log_pressure_bar,
                    "pressure_Pa": pressure_pa,
                    "method": method,
                    "provenance": {
                        "gas_table_id": table_ids["gas"],
                        "gas_temperature": _cell_provenance(gas_row, "temperature"),
                        "gas_log10_formation_equilibrium_constant": _cell_provenance(
                            gas_row, "log10_formation_equilibrium_constant"
                        )
                        if temperature < boiling_temperature
                        else None,
                        "gas_negative_gibbs_enthalpy_function": _cell_provenance(
                            gas_row, "negative_gibbs_enthalpy_function"
                        ),
                        "gas_formation_enthalpy_at_298_15_K": _cell_provenance(
                            gas_rows[reference_temperature], "formation_enthalpy"
                        ),
                        "condensed_table_id": table_ids["condensed"],
                        "condensed_temperature": _cell_provenance(
                            condensed_row, "temperature"
                        ),
                        "condensed_negative_gibbs_enthalpy_function": _cell_provenance(
                            condensed_row, "negative_gibbs_enthalpy_function"
                        ),
                        "condensed_formation_enthalpy_at_298_15_K": _cell_provenance(
                            condensed_rows[reference_temperature], "formation_enthalpy"
                        ),
                    },
                }
            )

        metals[element] = {
            "normal_boiling_point_K": boiling_temperature,
            "melting_point_K": melting_temperature,
            "temperature_range_K": [
                tabulated_temperatures[0],
                tabulated_temperatures[-1],
            ],
            "source_table_ids": {
                "gas": table_ids["gas"],
                "condensed_crystal_liquid": table_ids["condensed"],
                "reference_transitions": table_ids["reference"],
            },
            "condensed_phase_boundary": melting_marker,
            "boiling_point_check": {
                "transition": boiling_marker,
                "pressure_bar_from_interpolated_G_difference": 10.0**boiling_log_pressure,
                "log10_pressure_bar": boiling_log_pressure,
                "absolute_log10_error_from_1_bar": abs(boiling_log_pressure),
                "maximum_preboiling_logK_Gibbs_residual_dex": max_preboiling_residual,
            },
            "rows": rows,
        }

    return {
        "schema_version": "janaf_pure_metal_vapor_pressure.v1",
        "source_id": "nist-janaf-4th",
        "citation": (
            "Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, "
            "J. Phys. Chem. Ref. Data Monograph 9 (1998)"
        ),
        "standard_state": "p° = 0.1 MPa = 1 bar",
        "standard_pressure_Pa": STANDARD_PRESSURE_PA,
        "temperature_unit": "K",
        "pressure_unit": "Pa",
        "tabulation_max_temperature_K": max_temperature_K,
        "derivation": (
            "For M(condensed) ⇌ M(g), below the JANAF normal boiling marker "
            "log10(P_sat/bar) = log10 Kf[M(g)]. At and above that marker the "
            "gas is the standard elemental phase, so log10(P_sat/bar) = "
            "-[G(g)-G(cr or l)]/(R T ln 10). JANAF supplies p°=0.1 MPa=1 bar; "
            "P_sat(Pa)=100000×10^log10(P_sat/bar). G(T)=ΔfH°(298.15 K) - "
            "T×(-[G°-H°(298.15 K)]/T)/1000, using kJ/mol for enthalpy and "
            "J/(mol K) for the Gibbs-enthalpy function. The normal-boiling "
            "transition marker must recover 1 bar within 0.002 dex after "
            "interpolating the JANAF Gibbs functions."
        ),
        "metals": metals,
    }


def pressure_pa_at_temperature(
    data: Mapping[str, Any], element: str, temperature_K: float
) -> float:
    """Interpolate the JANAF curve linearly in log pressure versus temperature."""

    rows = data["metals"][element]["rows"]
    points = {
        float(row["temperature_K"]): float(row["log10_pressure_bar"])
        for row in rows
    }
    log_pressure_bar = _interpolate(points, temperature_K)
    return pressure_pa_from_log10_equilibrium_constant(
        log_pressure_bar, STANDARD_PRESSURE_PA
    )


def wall_temperature_for_pressure(
    data: Mapping[str, Any], element: str, pressure_Pa: float
) -> float:
    """Invert the generated P_sat curve by linear interpolation in log P."""

    if not math.isfinite(pressure_Pa) or pressure_Pa <= 0.0:
        raise ValueError("partial pressure must be finite and positive")
    target_log_pressure_bar = log10_pressure_ratio_from_pa(
        pressure_Pa, STANDARD_PRESSURE_PA
    )
    rows = data["metals"][element]["rows"]
    for lower, upper in zip(rows, rows[1:]):
        lower_log = float(lower["log10_pressure_bar"])
        upper_log = float(upper["log10_pressure_bar"])
        if lower_log <= target_log_pressure_bar <= upper_log:
            if upper_log == lower_log:
                raise ValueError(f"{element} JANAF pressure curve is not monotonic")
            fraction = (target_log_pressure_bar - lower_log) / (upper_log - lower_log)
            return float(lower["temperature_K"]) + fraction * (
                float(upper["temperature_K"]) - float(lower["temperature_K"])
            )
    raise ValueError(
        f"{element} JANAF table does not bracket {pressure_Pa:g} Pa"
    )
