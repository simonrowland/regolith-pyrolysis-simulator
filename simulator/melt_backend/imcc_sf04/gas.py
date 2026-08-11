"""IMCC-SF04 thin gas mass-action layer (chunk 5a).

Given melt parent activities from the IMCC-SF04 kernel/adapter plus gas-species
G(T) rows from the JANAF tables, compute equilibrium partial pressures for the
SF04 vaporization reaction set.

This module is intentionally thin: it performs no fO2 modeling (fO2 is pinned by
the caller), no melt equilibrium solve (activities are inputs), and no authority
claims.  It is a diagnostic shadow, consistent with the IMCC-SF04 spec r2.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from simulator.melt_backend.imcc_sf04.kernel import ImccRefusal


# --------------------------------------------------------------------------- #
# Physical constants and reference states
# --------------------------------------------------------------------------- #

R_J_MOL_K = 8.314462618
"""Molar gas constant, J / (mol K)."""

BAR = 1.0
"""Standard-state pressure in bar; partial pressures returned in bar."""


# --------------------------------------------------------------------------- #
# Typed refusal
# --------------------------------------------------------------------------- #


class ImccGasSpeciesNotFoundError(ImccRefusal):
    """Raised when a requested gas species or oxide has no G(T) row at T."""

    code = "imcc_gas_species_not_found"


class ImccGasTemperatureOutsideDomainError(ImccRefusal):
    """Raised when T falls outside the declared G(T) interval for a species."""

    code = "imcc_gas_T_outside_domain"


# --------------------------------------------------------------------------- #
# VapoRock data paths (sibling checkout, read-only)
# --------------------------------------------------------------------------- #


def _default_vaporock_root() -> Path:
    """Return the sibling VapoRock checkout root.

    ``simulator/melt_backend/imcc_sf04/gas.py`` -> workspace root -> ``../VapoRock``.
    """
    return Path(__file__).resolve().parents[3] / ".." / "VapoRock"


DEFAULT_GAS_DATABASE_PATH = (
    _default_vaporock_root() / "src" / "vaporock" / "data" / "JANAF-vapor-data-full.csv"
)
DEFAULT_CONDENSATE_DATABASE_PATH = (
    _default_vaporock_root() / "data" / "condensate-thermo-data.csv"
)


# --------------------------------------------------------------------------- #
# SF04 vaporization reaction set
# --------------------------------------------------------------------------- #

# Map retained gas species to the vaporization reaction
#     parent_oxide(l) -> n_gas * gas(g) + n_O2 * O2(g)
# where n_gas is the stoichiometric coefficient of the gas species and n_O2 is
# the stoichiometric coefficient of O2.  O2 itself is treated as a pinned input
# (p_O2 = fO2) and has no oxide parent.
_SF04_REACTIONS: dict[str, tuple[str, int, float]] = {
    "Na": ("Na2O", 2, 0.5),
    "K": ("K2O", 2, 0.5),
    "SiO": ("SiO2", 1, 0.5),
    "Fe": ("FeO", 1, 0.5),
    "FeO": ("FeO", 1, 0.0),
    "Mg": ("MgO", 1, 0.5),
    "MgO": ("MgO", 1, 0.0),
    "SiO2": ("SiO2", 1, 0.0),
    "O2": ("", 1, 0.0),  # special: p_O2 = fO2
}

IMCC_PARENT_OXIDES = (
    "SiO2",
    "MgO",
    "FeO",
    "CaO",
    "Al2O3",
    "TiO2",
    "Na2O",
    "K2O",
)


# --------------------------------------------------------------------------- #
# Data pack
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImccGasDatapack:
    """Loaded JANAF + condensate thermodynamic tables for the gas layer."""

    gas_df: pd.DataFrame
    oxide_df: pd.DataFrame
    gas_path: Path
    oxide_path: Path


def load_gas_datapack(
    gas_path: str | Path | None = None,
    oxide_path: str | Path | None = None,
) -> ImccGasDatapack:
    """Load the JANAF gas and condensate-oxide thermodynamic tables.

    Defaults point to the sibling VapoRock checkout.  Both files are read-only.
    """
    gas_path = Path(gas_path) if gas_path is not None else DEFAULT_GAS_DATABASE_PATH
    oxide_path = (
        Path(oxide_path) if oxide_path is not None else DEFAULT_CONDENSATE_DATABASE_PATH
    )

    gas_df = pd.read_csv(gas_path)
    if "species_name" not in gas_df.columns:
        raise ImccGasSpeciesNotFoundError(
            f"JANAF gas database {gas_path} missing 'species_name' column"
        )
    gas_df = gas_df.set_index("species_name")

    oxide_df = pd.read_csv(oxide_path)
    if "species_name" not in oxide_df.columns:
        raise ImccGasSpeciesNotFoundError(
            f"condensate database {oxide_path} missing 'species_name' column"
        )
    oxide_df = oxide_df.set_index("species_name")

    return ImccGasDatapack(
        gas_df=gas_df,
        oxide_df=oxide_df,
        gas_path=gas_path,
        oxide_path=oxide_path,
    )


# --------------------------------------------------------------------------- #
# G(T) evaluation
# --------------------------------------------------------------------------- #


def _janaf_gibbs(T: float, row: pd.Series) -> float:
    """Return G°(T) in J/mol for a JANAF gas species from Shomate coefficients.

    Derivation
    ----------
    The JANAF tables in VapoRock give the NASA/Shomate 7-coefficient form for
    each gas species.  With t = T/1000 K:

        H°(t) = A t + B/2 t^2 + C/3 t^3 + D/4 t^4 - E/t + F   (kJ/mol)
        S°(t) = A ln(t) + B t + C/2 t^2 + D/3 t^3 - E/(2 t^2) + G   (J/mol/K)
        G°(T) = H°(T) * 1000 - T * S°(T)                         (J/mol)

    Reference state: ideal gas at 1 bar, elements in their 298 K standard
    states.  All species in the VapoRock JANAF table share this single reference,
    so reference states cancel when forming a reaction ΔG°.

    Unit check: H in kJ/mol * 1000 -> J/mol; T * S (K * J/mol/K) -> J/mol.
    Sanity case: O2(g), the oxygen reference, has H°(298)=0 and S°(298)≈205
    J/mol/K, giving G°(298)≈-61 kJ/mol; the function reproduces this sign.
    """
    t = T / 1000.0
    dH = (
        row["A"] * t
        + row["B"] / 2.0 * t**2
        + row["C"] / 3.0 * t**3
        + row["D"] / 4.0 * t**4
        - row["E"] / t
        + row["F"]
    )
    S = (
        row["A"] * np.log(t)
        + row["B"] * t
        + row["C"] / 2.0 * t**2
        + row["D"] / 3.0 * t**3
        - row["E"] / 2.0 / t**2
        + row["G"]
    )
    return dH * 1000.0 - T * S


def _lamor_gibbs(T: float, row: pd.Series) -> float:
    """Return G°(T) in J/mol for a LAMOR condensate species.

    Derivation
    ----------
    The VapoRock condensate table (LAMOR / JANAF fits) stores a 5-coefficient
    polynomial fit plus ΔH°298/R.  With τ = T/1000 K:

        poly(τ) = dG_A + dG_B τ + dG_C τ^2 + dG_D τ^3 + dG_E τ^4
        G°(T) = -R T poly(τ) + R ΔH°298 * 1000

    The polynomial term captures the temperature dependence of ΔG°/R/T; the
    dH298 term anchors the absolute scale.  Result is the standard Gibbs energy
    of the condensed oxide in J/mol, sharing the same elemental reference as the
    JANAF gas table, so reaction ΔG° is reference-state-consistent.

    Unit check: R*T*dimensionless -> J/mol; R*ΔH°298*1000 -> J/mol.
    Sanity case: for a species with ΔH°298=0 and zero polynomial, G°(T)=0 at all T
    (the reference itself).
    """
    A = row["dG_A"]
    B = row["dG_B"]
    C = row["dG_C"]
    D = row["dG_D"]
    E = row["dG_E"]
    dH298 = row["dH298_R"]

    G_coefs = np.array([A, B, C, D, E])
    G_scale = np.array([1.0, 1e3, 1e6, 1e9, 1e12])
    G_poly_coefs = (G_coefs / G_scale)[::-1]
    return -R_J_MOL_K * T * np.polyval(G_poly_coefs, T) + R_J_MOL_K * dH298 * 1000.0


def _nearest_interval_row(
    df: pd.DataFrame, species: str, T: float, allow_extrapolation: bool = False
) -> pd.Series:
    """Select the thermodynamic interval closest to T.

    VapoRock's JANAF evaluation extends the lowest interval downward and the
    highest interval upward so that a single-T evaluation does not fail when the
    caller's temperature lies slightly outside the fitted range.  We replicate
    that selection rule, but by default we refuse when the selected interval does
    not actually contain T; extrapolation is allowed only when
    ``allow_extrapolation=True`` is passed explicitly.  This matches the V2
    refusal-semantics contract in the IMCC-SF04 spec §2.
    """
    rows = df.loc[df.index == species]
    if rows.empty:
        raise ImccGasSpeciesNotFoundError(
            f"no JANAF G(T) row for gas species {species!r}"
        )
    t_mins = rows["T_min"].astype(float).to_numpy()
    # Largest T_min that is <= T; fallback to the first (lowest-T) row if T is
    # below every interval.
    valid = t_mins <= T
    if np.any(valid):
        idx = int(np.argmax(t_mins * valid))  # argmax of masked mins gives largest <= T
    else:
        idx = 0
    selected = rows.iloc[idx]
    if not allow_extrapolation and (T < selected["T_min"] or T > selected["T_max"]):
        raise ImccGasTemperatureOutsideDomainError(
            f"T={T} K outside declared G(T) interval for {species!r} "
            f"[{selected['T_min']}, {selected['T_max']}] K"
        )
    return selected


def _oxide_row_for_T(
    df: pd.DataFrame, oxide: str, T: float, allow_extrapolation: bool = False
) -> pd.Series:
    """Select the condensate interval closest to T, refusing extrapolation by default."""
    if oxide not in df.index:
        raise ImccGasSpeciesNotFoundError(
            f"no condensate G(T) row for oxide {oxide!r}"
        )
    rows = df.loc[[oxide]] if df.index.name is None or not isinstance(df.loc[oxide], pd.DataFrame) else df.loc[oxide]
    # The condensate table has one row per oxide; wrap it if a single row.
    if isinstance(rows, pd.Series):
        selected = rows
    else:
        t_mins = rows["T_min"].astype(float).to_numpy()
        valid = t_mins <= T
        idx = int(np.argmax(t_mins * valid)) if np.any(valid) else 0
        selected = rows.iloc[idx]
    if not allow_extrapolation and (T < selected["T_min"] or T > selected["T_max"]):
        raise ImccGasTemperatureOutsideDomainError(
            f"T={T} K outside declared G(T) interval for {oxide!r} "
            f"[{selected['T_min']}, {selected['T_max']}] K"
        )
    return selected


# --------------------------------------------------------------------------- #
# Mass-action: Kp derivation and partial-pressure evaluation
# --------------------------------------------------------------------------- #


def evaluate_gas(
    activities: Mapping[str, float] | Sequence[float] | np.ndarray,
    T_K: float,
    fO2: float,
    datapack: ImccGasDatapack,
    parent_oxides: Sequence[str] | None = None,
    allow_extrapolation: bool = False,
) -> dict[str, float]:
    """Compute equilibrium partial pressures for the SF04 retained gas set.

    Parameters
    ----------
    activities:
        Parent-oxide activities.  Either a dict keyed by parent-oxide name, or a
        vector aligned with ``parent_oxides`` (default IMCC order).  Activities
        are relative to the pure liquid oxide standard state.
    T_K:
        Temperature in Kelvin.
    fO2:
        Oxygen partial pressure in bar (pinned by the caller; no internal fO2
        model is applied).
    datapack:
        Loaded JANAF + condensate thermodynamic tables.
    parent_oxides:
        Ordered parent-oxide names.  Defaults to the IMCC-SF04 8-oxide basis.
    allow_extrapolation:
        If False (default), raise ``ImccGasTemperatureOutsideDomainError`` when
        T falls outside the declared G(T) interval for any species consumed by
        the reaction set.  If True, select the nearest interval and evaluate
        silently (legacy run_gate.py behavior).

    Returns
    -------
    dict[str, float]
        Partial pressure of each retained gas species in bar.

    Derivation
    ----------
    Premise: for each retained gas species we write a single vaporization
    reaction with the parent oxide in the melt as the reactant and O2 as an
    explicit product when stoichiometry requires it:

        oxide(l)  =  n_gas * gas(g)  +  n_O2 * O2(g)                (1)

    The standard Gibbs free energy change for (1) is assembled from the JANAF
    gas-species G°(T) rows and the condensate oxide G°(T) rows:

        ΔG°(T) = n_gas * G°(gas, T) + n_O2 * G°(O2, T) - G°(oxide, T)   (2)

    Both gas and condensate tables share the same elemental reference state
    (elements in their 298 K standard states), so the reference-state bookkeeping
    cancels exactly in (2).  The equilibrium constant is

        Kp = exp(-ΔG°(T) / (R T))                                    (3)

    with R in J/(mol K), ΔG° in J/mol, T in K; Kp is therefore dimensionless.
    The mass-action expression for reaction (1), with the oxide activity a_oxide
    and gas pressures in bar, is

        Kp = (p_gas^n_gas * p_O2^n_O2) / a_oxide                     (4)

    Solving for p_gas at the caller-pinned p_O2 = fO2:

        p_gas = (Kp * a_oxide / fO2^n_O2)^(1 / n_gas)              (5)

    For the special retained species O2, p_O2 = fO2 by definition.  For
    non-O2-producing vaporization (n_O2 = 0), (5) reduces to p_gas = Kp * a_oxide.

    Unit check: ΔG° in J/mol; R*T in J/mol; Kp dimensionless.  p_gas in bar.
    Sanity case: ΔG° -> +infinity gives Kp -> 0 and p_gas -> 0 (vaporization
    forbidden); a_oxide -> 0 gives p_gas -> 0 (no oxide to vaporize); fO2 -> 0
    for an O2-producing reaction drives p_gas -> infinity as expected because
    equilibrium is pulled to the right.
    """
    if parent_oxides is None:
        parent_oxides = IMCC_PARENT_OXIDES

    T = float(T_K)
    p_O2 = float(fO2)
    if T <= 0.0:
        raise ValueError(f"temperature must be positive, got {T_K}")
    if p_O2 <= 0.0:
        raise ValueError(f"fO2 must be positive, got {fO2}")

    if isinstance(activities, Mapping):
        act = {name: float(activities.get(name, 0.0)) for name in parent_oxides}
    else:
        arr = np.asarray(activities, dtype=float)
        if arr.shape[0] != len(parent_oxides):
            raise ValueError(
                f"activities vector length {arr.shape[0]} does not match "
                f"{len(parent_oxides)} parent oxides"
            )
        act = {name: float(arr[i]) for i, name in enumerate(parent_oxides)}

    # G°(O2, T) is needed for every O2-producing reaction.
    O2_row = _nearest_interval_row(
        datapack.gas_df, "O2(g)", T, allow_extrapolation=allow_extrapolation
    )
    G_O2 = _janaf_gibbs(T, O2_row)

    result: dict[str, float] = {}
    for gas_name, (oxide, n_gas, n_O2) in _SF04_REACTIONS.items():
        if gas_name == "O2":
            result[gas_name] = p_O2
            continue

        gas_species = f"{gas_name}(g)"
        gas_row = _nearest_interval_row(
            datapack.gas_df, gas_species, T, allow_extrapolation=allow_extrapolation
        )
        G_gas = _janaf_gibbs(T, gas_row)

        oxide_name = f"{oxide}(l)"
        oxide_row = _oxide_row_for_T(
            datapack.oxide_df, oxide_name, T, allow_extrapolation=allow_extrapolation
        )
        G_oxide = _lamor_gibbs(T, oxide_row)

        # Reaction (1): oxide(l) -> n_gas * gas(g) + n_O2 * O2(g)
        dG = n_gas * G_gas + n_O2 * G_O2 - G_oxide
        Kp = np.exp(-dG / (R_J_MOL_K * T))

        a_oxide = act[oxide]
        if a_oxide < 0.0:
            raise ValueError(f"activity of {oxide!r} is negative: {a_oxide}")

        p_gas = (Kp * a_oxide / (p_O2**n_O2)) ** (1.0 / n_gas)
        result[gas_name] = float(p_gas)

    return result
