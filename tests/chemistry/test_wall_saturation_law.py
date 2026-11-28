"""Wall-saturation law: mass action on resolved Gibbs terms, not a melt curve.

The SiO Gibbs ratios are the NASA values quoted in the b-1188 design at
1323.15 K (NG-1135, NG-1858, NG-1864, NG-0937). Expected pressures are those
published values, and the mass-action identity uses
``reaction_equilibrium_constant`` on the same rows.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from simulator.reference_data import janaf
from simulator.reference_data.janaf import TABLES_DIR
from simulator.reference_data.nasa_glenn import COMPILATION_ROOT, load_record_document
from simulator.vapour_rail.catalog import (
    HotTrainInapplicable,
    _formula_atoms,
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    WALL_STANDARD_PRESSURE_PA,
    Nasa9Segment,
    NasaCeaDomainError,
    NasaCeaPolynomial,
    WallCondensedTerm,
    WallSaturationLawRefusal,
    WallSaturationReaction,
    operative_wall_saturation,
    reaction_equilibrium_constant,
    wall_saturation_pressure_pa,
)
from simulator.vapour_rail.wall_saturation import (
    admissible_wall_reactions,
    janaf_record_g_over_rt,
    nasa_record_g_over_rt,
    resolve_wall_saturation,
    wall_reactions_declaration,
)
from simulator.yaml_cache import load_cached_safe_yaml

# NASA G°/(RT) at 1323.15 K, design section 4.1.
_T_K = 1323.15
_G_SIO = -37.23464072
_G_SI = -4.28282613
_G_SIO2 = -92.94099129
_G_O2 = -27.38464409
_P_STD = WALL_STANDARD_PRESSURE_PA


def _sio_disp() -> WallSaturationReaction:
    return WallSaturationReaction(
        reaction_id="disproportionation",
        nu_vapour=2.0,
        g_vapour=_G_SIO,
        condensed=(
            WallCondensedTerm("Si", 1.0, _G_SI),
            WallCondensedTerm("SiO2", 1.0, _G_SIO2),
        ),
    )


def _sio_ox() -> WallSaturationReaction:
    return WallSaturationReaction(
        reaction_id="oxidative",
        nu_vapour=1.0,
        g_vapour=_G_SIO,
        condensed=(WallCondensedTerm("SiO2", 1.0, _G_SIO2),),
        nu_O2=0.5,
        g_oxygen=_G_O2,
    )


def _mass_action_residual(
    reaction: WallSaturationReaction,
    pressure_Pa: float,
    pO2_Pa: float | None,
) -> float:
    """(p_v/P°)^ν_v · (pO2/P°)^ν_O2 · K − 1, with K from the K owner."""

    terms: list[tuple[float, float]] = [(-reaction.nu_vapour, _G_SIO)]
    for product in reaction.condensed:
        terms.append((product.stoichiometry, float(product.g_over_RT)))
    if reaction.nu_O2 != 0.0:
        terms.append((-reaction.nu_O2, _G_O2))
    equilibrium_K = reaction_equilibrium_constant(terms, T_K=_T_K)
    ratio = (pressure_Pa / _P_STD) ** reaction.nu_vapour
    if reaction.nu_O2 != 0.0:
        assert pO2_Pa is not None and pO2_Pa > 0.0
        ratio *= (pO2_Pa / _P_STD) ** reaction.nu_O2
    return ratio * equilibrium_K - 1.0


@pytest.mark.parametrize(
    "temperature_K,expected_pressure_Pa",
    [
        (500.0, 5.100364215161105e-41),
        (1000.0, 4.256671819358322e-14),
        (1500.0, 2.5101771894980384e-5),
    ],
)
def test_feo_congruent_sublimation_matches_janaf_rows(
    temperature_K, expected_pressure_Pa,
) -> None:
    result = resolve_wall_saturation(
        _species_rows()["FeO_association_gas"],
        species_id="FeO_association_gas",
        T_K=temperature_K,
    )

    assert [(part.role, part.record_id) for part in result.participants] == [
        ("vapour", "Fe-021"),
        ("product", "Fe-018"),
    ]
    assert result.pressure_Pa == pytest.approx(
        expected_pressure_Pa, rel=1e-8, abs=0.0,
    )


def test_sio_disproportionation_matches_published_nasa_pressure() -> None:
    pressure_Pa = wall_saturation_pressure_pa(_sio_disp(), T_K=_T_K)
    assert pressure_Pa == pytest.approx(1.14529, rel=1e-5)
    assert _mass_action_residual(_sio_disp(), pressure_Pa, None) == pytest.approx(
        0.0, abs=1e-9
    )


def test_sio_oxidative_pressures_and_crossover() -> None:
    p_floor_Pa = 1e-9 * _P_STD
    p_parcel_Pa = 94.0
    at_floor = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=p_floor_Pa
    )
    at_parcel = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=p_parcel_Pa
    )
    assert at_floor == pytest.approx(1.793e-9, rel=1e-3)
    assert at_parcel == pytest.approx(1.849e-12, rel=1e-3)
    assert _mass_action_residual(_sio_ox(), at_floor, p_floor_Pa) == pytest.approx(
        0.0, abs=1e-6
    )

    # Below the Si/SiO2 buffer, disproportionation is the lower Psat.
    # The published crossover is 2.450e-27 bar.
    p_cross_Pa = 2.450e-27 * _P_STD
    below = operative_wall_saturation(
        (_sio_disp(), _sio_ox()),
        T_K=_T_K,
        pO2_Pa=p_cross_Pa / 10.0,
    )
    above = operative_wall_saturation(
        (_sio_disp(), _sio_ox()),
        T_K=_T_K,
        pO2_Pa=p_cross_Pa * 10.0,
    )
    assert below.reaction_id == "disproportionation"
    assert below.products_mol_per_mol_vapour == (("Si", 0.5), ("SiO2", 0.5))
    assert below.o2_mol_per_mol_vapour == 0.0
    assert above.reaction_id == "oxidative"
    assert above.products_mol_per_mol_vapour == (("SiO2", 1.0),)
    assert above.o2_mol_per_mol_vapour == pytest.approx(0.5)


def test_missing_po2_uses_flagged_free_route_and_refuses_only_without_one() -> None:
    result = operative_wall_saturation((_sio_disp(), _sio_ox()), T_K=_T_K)
    assert result.reaction_id == _sio_disp().reaction_id
    assert result.pressure_Pa == pytest.approx(1.14529, rel=1e-5)
    # Pin the current computed value before consolidating the 1 bar owner.
    assert result.pressure_Pa.hex() == "0x1.2531b1eddf47ap+0"
    assert result.notices == ("wall_pO2_unknown_pO2_free_route_used",)
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        operative_wall_saturation((_sio_ox(),), T_K=_T_K)
    assert exc_info.value.reason == "wall_pO2_required"


def test_po2_limits_match_the_signed_oxygen_coefficient() -> None:
    oxidative_at_zero = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=0.0
    )
    assert math.isinf(oxidative_at_zero)
    reductive = WallSaturationReaction(
        reaction_id="reductive",
        nu_vapour=1.0,
        g_vapour=_G_SIO,
        condensed=(WallCondensedTerm("SiO2", 1.0, _G_SIO2),),
        nu_O2=-0.25,
        g_oxygen=_G_O2,
    )
    assert wall_saturation_pressure_pa(reductive, T_K=_T_K, pO2_Pa=0.0) == 0.0
    high = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=1.0e8
    )
    low = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=1.0e-8
    )
    assert high < low


def test_invalid_po2_is_a_typed_refusal() -> None:
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        wall_saturation_pressure_pa(_sio_ox(), T_K=_T_K, pO2_Pa=-1.0)
    assert exc_info.value.reason == "wall_pO2_invalid"
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        wall_saturation_pressure_pa(_sio_ox(), T_K=_T_K, pO2_Pa=math.nan)
    assert exc_info.value.reason == "wall_pO2_invalid"


def test_perturbing_one_gibbs_term_moves_psat() -> None:
    baseline = wall_saturation_pressure_pa(_sio_disp(), T_K=_T_K)
    shifted = WallSaturationReaction(
        reaction_id="disproportionation",
        nu_vapour=2.0,
        g_vapour=_G_SIO + 0.1,
        condensed=(
            WallCondensedTerm("Si", 1.0, _G_SI),
            WallCondensedTerm("SiO2", 1.0, _G_SIO2),
        ),
    )
    assert wall_saturation_pressure_pa(shifted, T_K=_T_K) != pytest.approx(baseline)


# Hot-train inventory from the b-1188 design, section 1. The compiled catalog's
# truthy source exponents are this same set of 67.
_HOT_TRAIN = frozenset(
    {
        "SiO",
        "CrO",
        "CrO2",
        "CrO3",
        "AlO",
        "Al2O",
        "Al2O2",
        "AlO2",
        "Al2O3_gas",
        "TiO",
        "TiO2_gas",
        "MgO_gas",
        "CaO_gas",
        "SiO2_gas",
        "Na2O_gas",
        "K2O_gas",
        "NiO_gas",
        "CoO_gas",
        "MnO_gas",
        "FeO_association_gas",
        "Al2",
        "Ca2",
        "Mg2",
        "Na2",
        "K2",
        "Si2",
        "Si3",
    }
)
_MISSING_THERMO = frozenset({"NiO_gas", "CoO_gas", "MnO_gas"})
_PURE_METALS = frozenset({"Al", "Ca", "Cr", "Mg", "Mn", "Ti", "K", "Si", "Na"})
_OFF_TRAIN = frozenset(
    {
        "P2",
        "P4",
        "P4O6",
        "P4O10",
        "PO",
        "PO2",
        "Ba",
        "Ba2",
        "Cu",
        "Cu2",
        "FeO_gas",
        "Ga",
        "Ga2O",
        "GaO",
        "Ge",
        "Ge2",
        "GeO",
        "In",
        "InO",
        "Li",
        "Li2",
        "LiO",
        "Nb",
        "NO2F",
        "NO3F",
        "NOF",
        "Sn",
        "Sn2",
        "UOF2",
        "W",
        "WO",
    }
)
_TABLE_T_K = 1500.0
_PO2_PA = 1.0e-9 * _P_STD
_REPO = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _species_rows() -> dict:
    payload = load_cached_safe_yaml((_REPO / "data" / "vapor_pressures.yaml").read_text())
    rows: dict = {}
    for family in payload["families"].values():
        rows.update(family["physical_properties"]["species"])
    return rows


@lru_cache(maxsize=1)
def _compiled_catalog():
    payload = load_cached_safe_yaml((_REPO / "data" / "vapor_pressures.yaml").read_text())
    return compile_vapour_rail_catalog(payload)


def _truthy_exponent_species() -> set[str]:
    found: set[str] = set()
    for species_id, compiled in _compiled_catalog().species.items():
        evaluator = compiled.evaluator
        if evaluator is None:
            continue
        if evaluator.activity_exponent or evaluator.pO2_exponent:
            found.add(species_id)
    return found


def _assert_reaction_atoms(species_id: str, reaction: dict) -> None:
    """Atom balance of the declared stoichiometry, using the formula owner."""

    balance: dict[str, float] = {}
    nu_v = float(reaction["vapour_stoichiometry"])
    nu_o2 = float(reaction["oxygen_stoichiometry"])
    for element, count in _formula_atoms(reaction["vapour"]["formula"]).items():
        balance[element] = balance.get(element, 0.0) - nu_v * count
    if nu_o2 != 0.0:
        for element, count in _formula_atoms("O2").items():
            balance[element] = balance.get(element, 0.0) - nu_o2 * count
    for product in reaction["products"]:
        stoich = float(product["stoichiometry"])
        for element, count in _formula_atoms(product["formula"]).items():
            balance[element] = balance.get(element, 0.0) + stoich * count
    unbalanced = {
        element: amount for element, amount in balance.items() if abs(amount) > 1.0e-9
    }
    assert not unbalanced, f"{species_id}.{reaction['id']} {unbalanced}"


def _nasa_g_and_phase(record_id: str, temperature_K: float) -> tuple[float, str]:
    """G°/(RT) from the cited JSON, skipping collapsed intervals."""

    document = load_record_document(COMPILATION_ROOT / "records" / f"{record_id}.json")
    phase = str(document["phase"])
    segments: list[Nasa9Segment] = []
    for interval in document["intervals"]:
        t_min = float(interval["T_min_K"]["value"])
        t_max = float(interval["T_max_K"]["value"])
        if t_min >= t_max:
            continue
        segments.append(
            Nasa9Segment(
                t_min,
                t_max,
                tuple(float(coefficient["value"]) for coefficient in interval["a_coefficients"]),
                float(interval["b1"]["value"]),
                float(interval["b2"]["value"]),
            )
        )
    standard_state = "gas" if phase == "gas" else "condensed_liquid" if phase == "L" else "condensed_solid"
    polynomial = NasaCeaPolynomial(
        name=record_id,
        family="nasa_cea_9",
        standard_state=standard_state,  # type: ignore[arg-type]
        segments=tuple(segments),
        formula=str(document.get("formula") or "") or None,
    )
    return polynomial.evaluate(temperature_K).g_over_RT, phase


def _covering_nasa_record(record_ids: list[str], temperature_K: float) -> tuple[str, float]:
    """Minimum-G cited record whose NASA interval contains T.

    This is the independent Gibbs comparison, not a call to the selector.
    A domain error means that record does not cover T.
    """

    covering: list[tuple[float, int, str]] = []
    for index, record_id in enumerate(record_ids):
        try:
            g_over_rt, _phase = _nasa_g_and_phase(record_id, temperature_K)
        except NasaCeaDomainError:
            continue
        covering.append((g_over_rt, index, record_id))
    assert covering, record_ids
    _g, _index, record_id = min(covering, key=lambda item: (item[0], item[1]))
    g_over_rt, _phase = _nasa_g_and_phase(record_id, temperature_K)
    return record_id, g_over_rt


def _janaf_g_over_rt(table_id: str, temperature_K: float) -> float:
    g_kJ = janaf._formation_gibbs_energy_kj_per_mol_at(table_id, temperature_K)
    return (float(g_kJ) * 1000.0) / (R_J_PER_MOL_K * temperature_K)


def _covering_janaf_record(table_ids: list[str], temperature_K: float) -> tuple[str, float]:
    """Minimum-G cited JANAF table whose printed rows contain T.

    Independent of the selector. ``JanafParseError`` means that table does
    not cover T.
    """

    covering: list[tuple[float, int, str]] = []
    for index, table_id in enumerate(table_ids):
        try:
            g_over_rt = _janaf_g_over_rt(str(table_id), temperature_K)
        except janaf.JanafParseError:
            continue
        covering.append((g_over_rt, index, str(table_id)))
    assert covering, table_ids
    g_over_rt, _index, table_id = min(covering, key=lambda item: (item[0], item[1]))
    return table_id, g_over_rt


def _independent_operative(row: dict, temperature_K: float, pO2_Pa: float | None):
    """Psat from the cited rows via the K owner's pressure function."""

    block = row["wall_reactions"]
    thermo = str(block.get("thermo") or "nasa")
    built: list[WallSaturationReaction] = []
    records: dict[str, tuple[str, ...]] = {}
    for reaction in block["reactions"]:
        nu_v = float(reaction["vapour_stoichiometry"])
        nu_o2 = float(reaction["oxygen_stoichiometry"])
        if thermo == "janaf":
            vapour_id = str(reaction["vapour"]["janaf"])
            g_vapour = _janaf_g_over_rt(vapour_id, temperature_K)
            g_oxygen = None
            if nu_o2 != 0.0:
                g_oxygen = _janaf_g_over_rt(str(block["oxygen"]["janaf"]), temperature_K)
            condensed = []
            product_records = []
            for product in reaction["products"]:
                table_ids = product["janaf"]
                if isinstance(table_ids, str):
                    table_ids = [table_ids]
                table_id, g_over_rt = _covering_janaf_record(list(table_ids), temperature_K)
                condensed.append(
                    WallCondensedTerm(
                        product["species"],
                        float(product["stoichiometry"]),
                        g_over_rt,
                    )
                )
                product_records.append(table_id)
        else:
            vapour_id = str(reaction["vapour"]["nasa"])
            g_vapour, _phase = _nasa_g_and_phase(vapour_id, temperature_K)
            g_oxygen = None
            if nu_o2 != 0.0:
                g_oxygen, _phase = _nasa_g_and_phase(
                    str(block["oxygen"]["nasa"]), temperature_K
                )
            condensed = []
            product_records = []
            for product in reaction["products"]:
                record_id, g_over_rt = _covering_nasa_record(
                    list(product["nasa"]), temperature_K
                )
                condensed.append(
                    WallCondensedTerm(
                        product["species"],
                        float(product["stoichiometry"]),
                        g_over_rt,
                    )
                )
                product_records.append(record_id)
        built.append(
            WallSaturationReaction(
                reaction_id=str(reaction["id"]),
                nu_vapour=nu_v,
                g_vapour=g_vapour,
                condensed=tuple(condensed),
                nu_O2=nu_o2,
                g_oxygen=g_oxygen,
            )
        )
        records[str(reaction["id"])] = tuple(product_records)
    winner = operative_wall_saturation(tuple(built), T_K=temperature_K, pO2_Pa=pO2_Pa)
    return winner, records[winner.reaction_id]


def test_compiled_exponents_are_the_design_inventory() -> None:
    assert _truthy_exponent_species() == _HOT_TRAIN | _PURE_METALS | _OFF_TRAIN


@pytest.mark.parametrize("species_id", sorted(_HOT_TRAIN | _PURE_METALS | _OFF_TRAIN))
def test_production_resolver_over_every_truthy_exponent_species(species_id: str) -> None:
    row = _species_rows()[species_id]
    if species_id in _OFF_TRAIN:
        assert wall_reactions_declaration(row) is None
        with pytest.raises(HotTrainInapplicable):
            _compiled_catalog().assert_hot_train_applicable(species_id)
        if species_id == "FeO_gas":
            evaluator = _compiled_catalog().species[species_id].evaluator
            assert evaluator is not None
            # The melt exponent of Fe0.95O. It is not an integer wall exponent.
            assert evaluator.pO2_exponent == pytest.approx(-0.02631578947368418)
            assert not float(evaluator.pO2_exponent).is_integer()
        return
    if species_id in _PURE_METALS:
        assert wall_reactions_declaration(row) is None
        _compiled_catalog().assert_hot_train_applicable(species_id)
        return

    if species_id in _MISSING_THERMO:
        with pytest.raises(WallSaturationLawRefusal) as exc_info:
            resolve_wall_saturation(
                row, species_id=species_id, T_K=_TABLE_T_K, pO2_Pa=_PO2_PA
            )
        assert exc_info.value.reason == "wall_condensation_thermo_missing"
        return

    for reaction in row["wall_reactions"]["reactions"]:
        _assert_reaction_atoms(species_id, reaction)
    result = resolve_wall_saturation(
        row, species_id=species_id, T_K=_TABLE_T_K, pO2_Pa=_PO2_PA
    )
    expected, product_records = _independent_operative(row, _TABLE_T_K, _PO2_PA)
    assert math.isfinite(result.pressure_Pa) and result.pressure_Pa > 0.0
    assert result.pressure_Pa == pytest.approx(expected.pressure_Pa, rel=1.0e-9)
    assert result.reaction_id == expected.reaction_id
    assert result.nu_O2 == pytest.approx(expected.nu_O2)
    assert result.products_mol_per_mol_vapour == expected.products_mol_per_mol_vapour
    assert result.o2_mol_per_mol_vapour == pytest.approx(expected.o2_mol_per_mol_vapour)
    resolved_products = tuple(
        participant.record_id
        for participant in result.participants
        if participant.role == "product"
    )
    assert resolved_products == product_records

    low = admissible_wall_reactions(row, species_id=species_id, T_K=_TABLE_T_K)
    high_pO2 = 1.0e-3 * _P_STD
    for reaction in low:
        at_low = wall_saturation_pressure_pa(
            reaction, T_K=_TABLE_T_K, pO2_Pa=_PO2_PA
        )
        at_high = wall_saturation_pressure_pa(
            reaction, T_K=_TABLE_T_K, pO2_Pa=high_pO2
        )
        if reaction.nu_O2 > 0.0:
            assert at_high < at_low
        elif reaction.nu_O2 < 0.0:
            assert at_high > at_low
        else:
            assert at_high == pytest.approx(at_low, rel=0.0, abs=0.0)
    needs_oxygen = all(reaction.nu_O2 != 0.0 for reaction in low)
    if needs_oxygen:
        with pytest.raises(WallSaturationLawRefusal) as exc_info:
            resolve_wall_saturation(
                row, species_id=species_id, T_K=_TABLE_T_K, pO2_Pa=None
            )
        assert exc_info.value.reason == "wall_pO2_required"
    else:
        without_oxygen = resolve_wall_saturation(
            row, species_id=species_id, T_K=_TABLE_T_K, pO2_Pa=None
        )
        assert without_oxygen.nu_O2 == 0.0
        assert without_oxygen.pressure_Pa >= result.pressure_Pa
        assert "wall_pO2_unknown_pO2_free_route_used" in without_oxygen.notices


def test_sio_resolver_matches_the_published_anchors() -> None:
    row = _species_rows()["SiO"]
    p_cross = 2.450e-27 * _P_STD
    below = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=p_cross / 10.0
    )
    assert below.reaction_id == "disproportionation"
    assert below.pressure_Pa == pytest.approx(1.14529, rel=1.0e-5)
    assert below.products_mol_per_mol_vapour == (("Si", 0.5), ("SiO2", 0.5))
    assert below.o2_mol_per_mol_vapour == 0.0
    assert "optimistic capture ceiling" in below.phase_note
    above = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=p_cross * 10.0
    )
    assert above.reaction_id == "oxidative"
    assert above.products_mol_per_mol_vapour == (("SiO2", 1.0),)
    assert above.o2_mol_per_mol_vapour == pytest.approx(0.5)
    at_floor = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    at_parcel = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=94.0
    )
    assert at_floor.pressure_Pa == pytest.approx(1.793e-9, rel=1.0e-3)
    assert at_parcel.pressure_Pa == pytest.approx(1.849e-12, rel=1.0e-3)
    unknown = resolve_wall_saturation(row, species_id="SiO", T_K=_T_K)
    assert unknown.reaction_id == "disproportionation"
    assert unknown.pressure_Pa == below.pressure_Pa
    assert unknown.notices == ("wall_pO2_unknown_pO2_free_route_used",)


def test_phase_at_1323_selects_cristobalite_not_beta_quartz() -> None:
    row = _species_rows()["SiO"]
    result = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    product_records = {
        participant.species: participant.record_id
        for participant in result.participants
        if participant.role == "product"
    }
    assert product_records["SiO2"] == "NG-1864"
    assert "NG-1863" not in product_records.values()
    below = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=2.450e-27 * _P_STD / 10.0
    )
    records = {
        participant.species: participant.record_id
        for participant in below.participants
        if participant.role == "product"
    }
    assert records == {"Si": "NG-1858", "SiO2": "NG-1864"}


def _product_record_ids(resolved) -> dict[str, str]:
    return {
        participant.species: participant.record_id
        for participant in resolved.participants
        if participant.role == "product"
    }


def test_condensed_phase_is_minimum_g_not_metastable_solid() -> None:
    """The covering condensed phase is the one with the lowest G.

    JANAF marks crystal Si ↔ liquid at 1685 K and keeps Si-002 rows through
    2500 K, so the crystal still covers 1773.15 K. Liquid Si-003 is lower
    there and is the stable phase. Below the melting point the crystal is
    lower. NASA's crystal interval ends at 1690 K, so at 1773.15 K only
    liquid NG-1859 covers.
    """

    row = copy.deepcopy(_species_rows()["SiO"])
    row["wall_reactions"]["thermo"] = "janaf"

    below = resolve_wall_saturation(row, species_id="SiO", T_K=1500.0, pO2_Pa=None)
    assert _product_record_ids(below)["Si"] == "Si-002"
    assert janaf_record_g_over_rt("Si-002", 1500.0) < janaf_record_g_over_rt(
        "Si-003", 1500.0
    )

    above_melt = resolve_wall_saturation(
        row, species_id="SiO", T_K=1687.0, pO2_Pa=None
    )
    assert _product_record_ids(above_melt)["Si"] == "Si-003"
    assert janaf_record_g_over_rt("Si-003", 1687.0) < janaf_record_g_over_rt(
        "Si-002", 1687.0
    )

    hot = 1773.15
    resolved = resolve_wall_saturation(row, species_id="SiO", T_K=hot, pO2_Pa=None)
    assert resolved.reaction_id == "disproportionation"
    assert _product_record_ids(resolved) == {"Si": "Si-003", "SiO2": "O-035"}
    assert janaf_record_g_over_rt("Si-003", hot) < janaf_record_g_over_rt("Si-002", hot)
    expected, product_records = _independent_operative(row, hot, None)
    assert product_records == ("Si-003", "O-035")
    assert resolved.pressure_Pa == pytest.approx(expected.pressure_Pa, rel=1.0e-9)

    at_2000 = resolve_wall_saturation(row, species_id="SiO", T_K=2000.0, pO2_Pa=None)
    assert _product_record_ids(at_2000) == {"Si": "Si-003", "SiO2": "O-038"}
    assert janaf_record_g_over_rt("O-038", 2000.0) < janaf_record_g_over_rt(
        "O-035", 2000.0
    )

    nasa = resolve_wall_saturation(
        _species_rows()["SiO"], species_id="SiO", T_K=hot, pO2_Pa=None
    )
    assert _product_record_ids(nasa) == {"Si": "NG-1859", "SiO2": "NG-1864"}
    with pytest.raises(NasaCeaDomainError):
        _nasa_g_and_phase("NG-1858", hot)


def test_collapsed_interval_does_not_change_si_gibbs() -> None:
    document = load_record_document(COMPILATION_ROOT / "records" / "NG-1858.json")
    collapsed = document["intervals"][0]
    assert float(collapsed["T_min_K"]["value"]) >= float(collapsed["T_max_K"]["value"])
    covering = document["intervals"][1]
    segment = Nasa9Segment(
        float(covering["T_min_K"]["value"]),
        float(covering["T_max_K"]["value"]),
        tuple(float(coefficient["value"]) for coefficient in covering["a_coefficients"]),
        float(covering["b1"]["value"]),
        float(covering["b2"]["value"]),
    )
    _cp, _h, _s, g_over_rt = segment.evaluate_ratios(_T_K)
    assert nasa_record_g_over_rt("NG-1858", _T_K) == g_over_rt
    # Ca(a) has the same collapsed first interval and still evaluates.
    assert math.isfinite(nasa_record_g_over_rt("NG-1366", 400.0))


def test_nasa_record_identity_gate_refuses_inert_slot_cards(monkeypatch) -> None:
    from simulator.accounting.formulas import parse_formula
    from simulator.vapour_rail import wall_saturation

    def assert_inert_record_refused() -> None:
        with pytest.raises(WallSaturationLawRefusal) as exc_info:
            nasa_record_g_over_rt("NG-2032", 500.0)
        assert exc_info.value.reason == "wall_nasa_source_identity_mismatch"
        assert math.isfinite(nasa_record_g_over_rt("NG-1858", _T_K))

    assert_inert_record_refused()

    def bypass_card_decision(record):
        return parse_formula(record["formula"]), None

    with monkeypatch.context() as mutation:
        mutation.setattr(wall_saturation, "accepted_formula", bypass_card_decision)
        with pytest.raises(AssertionError):
            assert_inert_record_refused()


@pytest.mark.parametrize(
    ("thermo", "record_id", "source_path", "root_name"),
    (
        (
            "nasa",
            "NG-1858",
            COMPILATION_ROOT / "records" / "NG-1858.json",
            "COMPILATION_ROOT",
        ),
        (
            "janaf",
            "Cr-010",
            TABLES_DIR / "Cr-010.yaml",
            "TABLES_DIR",
        ),
    ),
)
def test_record_state_cache_tracks_path_and_file_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    thermo: str,
    record_id: str,
    source_path: Path,
    root_name: str,
) -> None:
    """Parsed records may be reused; source identity and temperature stay live."""

    from simulator.vapour_rail import wall_saturation as wall_module

    relative_path = source_path.relative_to(
        COMPILATION_ROOT if thermo == "nasa" else TABLES_DIR
    )
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    target_a = root_a / relative_path
    target_b = root_b / relative_path
    target_a.parent.mkdir(parents=True)
    target_b.parent.mkdir(parents=True)
    shutil.copy2(source_path, target_a)
    shutil.copy2(source_path, target_b)

    loader_name = "load_record_document" if thermo == "nasa" else "load_table_document"
    loader_owner = wall_module if thermo == "nasa" else janaf
    original_loader = getattr(loader_owner, loader_name)
    loaded_paths: list[Path] = []
    polynomial_calls = []
    polynomial_evaluations = []
    thermo_evaluations = []

    if thermo == "nasa":
        original_polynomial = wall_module.NasaCeaPolynomial
        original_evaluate = original_polynomial.evaluate
        original_segment_evaluate = wall_module.Nasa9Segment.evaluate_ratios

        def counted_polynomial(*args: object, **kwargs: object):
            polynomial_calls.append((args, kwargs))
            return original_polynomial(*args, **kwargs)

        def counted_evaluate(polynomial: object, temperature_K: float):
            polynomial_evaluations.append(temperature_K)
            return original_evaluate(polynomial, temperature_K)

        def counted_segment_evaluate(segment: object, temperature_K: float):
            thermo_evaluations.append(temperature_K)
            return original_segment_evaluate(segment, temperature_K)

        monkeypatch.setattr(wall_module, "NasaCeaPolynomial", counted_polynomial)
        monkeypatch.setattr(original_polynomial, "evaluate", counted_evaluate)
        monkeypatch.setattr(
            wall_module.Nasa9Segment, "evaluate_ratios", counted_segment_evaluate
        )
    else:
        original_interpolate = janaf._interpolate_formation_gibbs

        def counted_interpolate(points, temperature_K, *args, **kwargs):
            thermo_evaluations.append(float(temperature_K))
            return original_interpolate(points, temperature_K, *args, **kwargs)

        monkeypatch.setattr(janaf, "_interpolate_formation_gibbs", counted_interpolate)

    def counted_loader(path: Path) -> dict[str, object]:
        loaded_paths.append(path)
        return original_loader(path)

    monkeypatch.setattr(loader_owner, loader_name, counted_loader)
    monkeypatch.setattr(wall_module, root_name, root_a)

    def evaluate(temperature_K: float) -> float:
        state = (
            wall_module._nasa_record_state(record_id, temperature_K)
            if thermo == "nasa"
            else wall_module._janaf_record_state(record_id, temperature_K)
        )
        return state[0]

    first_temperature_K, second_temperature_K = 1323.15, 1573.15
    first = evaluate(first_temperature_K)
    assert evaluate(first_temperature_K) == first
    assert evaluate(second_temperature_K) != first
    assert thermo_evaluations == [first_temperature_K, second_temperature_K]
    assert loaded_paths == [target_a]
    if thermo == "nasa":
        assert len(polynomial_calls) == 1
        assert polynomial_evaluations == []

    # Same record ID and file metadata in a different root is a different source.
    monkeypatch.setattr(wall_module, root_name, root_b)
    assert evaluate(first_temperature_K) == first
    assert thermo_evaluations == [
        first_temperature_K,
        second_temperature_K,
        first_temperature_K,
    ]
    assert loaded_paths == [target_a, target_b]
    if thermo == "nasa":
        assert len(polynomial_calls) == 2
        assert polynomial_evaluations == []

    before = target_b.stat()
    document = original_loader(target_b)
    if thermo == "nasa":
        interval = next(
            item
            for item in document["intervals"]
            if item["T_min_K"]["value"] < item["T_max_K"]["value"]
        )
        interval["b2"]["value"] = float(interval["b2"]["value"]) + 1.0
        target_b.write_text(json.dumps(document), encoding="utf-8")
    else:
        table = document["table"]
        rows = [
            item
            for item in table["values"]
            if item["temperature"].get("value") is not None
            and item["formation_gibbs_energy"].get("value") is not None
        ]
        row = min(
            rows,
            key=lambda item: abs(
                float(item["temperature"]["value"]) - first_temperature_K
            ),
        )
        # The checked owner consumes printed cells, not a second numeric
        # projection. Change the printed value and its projection together.
        from decimal import Decimal
        changed = Decimal(str(row["formation_gibbs_energy"]["as_published"])) + 1
        row["formation_gibbs_energy"]["as_published"] = str(changed)
        row["formation_gibbs_energy"]["value"] = float(changed)
        target_b.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    os.utime(
        target_b,
        ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000),
    )

    assert evaluate(first_temperature_K) != first
    assert thermo_evaluations == [
        first_temperature_K,
        second_temperature_K,
        first_temperature_K,
        first_temperature_K,
    ]
    assert loaded_paths == [target_a, target_b, target_b]
    if thermo == "nasa":
        assert len(polynomial_calls) == 3
        assert polynomial_evaluations == []


def test_cro_janaf_log_sits_beside_nasa() -> None:
    """JANAF oxidative anchors from Cr-010 and Cr-014. NASA is not fitted to them."""

    row = _species_rows()["CrO"]
    pO2_Pa = 1.0e-9 * _P_STD
    for temperature_K, janaf_log, nasa_log in (
        (1373.15, -9.091, -9.162),
        (1573.15, -5.537, -5.602),
    ):
        g_vapour = _janaf_g_over_rt("Cr-010", temperature_K)
        g_oxide = _janaf_g_over_rt("Cr-014", temperature_K)
        g_oxygen = _janaf_g_over_rt("O-029", temperature_K)
        janaf_pressure = wall_saturation_pressure_pa(
            WallSaturationReaction(
                reaction_id="oxidative",
                nu_vapour=1.0,
                g_vapour=g_vapour,
                condensed=(WallCondensedTerm("Cr2O3", 0.5, g_oxide),),
                nu_O2=0.25,
                g_oxygen=g_oxygen,
            ),
            T_K=temperature_K,
            pO2_Pa=pO2_Pa,
        )
        nasa = resolve_wall_saturation(
            row, species_id="CrO", T_K=temperature_K, pO2_Pa=pO2_Pa
        )
        assert math.log10(janaf_pressure) == pytest.approx(janaf_log, abs=5.0e-4)
        assert math.log10(nasa.pressure_Pa) == pytest.approx(nasa_log, abs=5.0e-4)
        assert nasa.reaction_id == "oxidative"
        assert nasa.products_mol_per_mol_vapour == (("Cr2O3", 0.5),)
        assert nasa.o2_mol_per_mol_vapour == pytest.approx(0.25)
        assert math.log10(nasa.pressure_Pa) != pytest.approx(janaf_log, abs=1.0e-3)


def test_cro_disproportionation_and_cro2_products() -> None:
    cro = _species_rows()["CrO"]
    # Below the NASA crossover the metal path wins. 1e-30 bar is under both
    # published crossovers (1.48e-20 bar at 1373.15 K, 6.38e-17 bar at 1573.15 K).
    disp = resolve_wall_saturation(
        cro, species_id="CrO", T_K=1373.15, pO2_Pa=1.0e-30 * _P_STD
    )
    assert disp.reaction_id == "disproportionation"
    assert disp.products_mol_per_mol_vapour[0][0] == "Cr"
    assert disp.products_mol_per_mol_vapour[0][1] == pytest.approx(1.0 / 3.0)
    assert disp.products_mol_per_mol_vapour[1][0] == "Cr2O3"
    assert disp.products_mol_per_mol_vapour[1][1] == pytest.approx(1.0 / 3.0)
    assert disp.o2_mol_per_mol_vapour == 0.0

    cro2 = resolve_wall_saturation(
        _species_rows()["CrO2"],
        species_id="CrO2",
        T_K=_TABLE_T_K,
        pO2_Pa=_PO2_PA,
    )
    assert cro2.pressure_Pa > 0.0
    assert cro2.reaction_id == "reductive"
    assert cro2.products_mol_per_mol_vapour == (("Cr2O3", 0.5),)
    assert cro2.o2_mol_per_mol_vapour == pytest.approx(-0.25)


def test_query_outside_every_cited_interval_predicts_and_flags_extrapolation() -> None:
    resolved = resolve_wall_saturation(
        _species_rows()["SiO"],
        species_id="SiO",
        T_K=250.0,
        pO2_Pa=_PO2_PA,
    )
    assert math.isfinite(resolved.pressure_Pa) and resolved.pressure_Pa > 0.0
    assert "wall_condensation_thermo_extrapolated" in resolved.notices
    extrapolated = [
        item for item in resolved.participants
        if item.authority_level == "extrapolated"
    ]
    assert extrapolated
    assert all(item.valid_range_K for item in extrapolated)


def test_outside_every_interval_uses_the_nearest_condensed_record() -> None:
    resolved = resolve_wall_saturation(
        _species_rows()["SiO"],
        species_id="SiO",
        T_K=298.15,
        pO2_Pa=_PO2_PA,
    )
    selected_products = [
        item
        for item in resolved.participants
        if item.role == "product" and item.species == "SiO2"
    ]
    assert len(selected_products) == 1
    assert selected_products[0].record_id == "NG-1862"
    assert selected_products[0].authority_level == "extrapolated"
    assert "wall_condensation_thermo_extrapolated" in resolved.notices


def test_perturbing_the_gibbs_term_the_resolver_reads_moves_psat(monkeypatch) -> None:
    row = _species_rows()["SiO"]
    baseline = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    from simulator.vapour_rail import wall_saturation as wall_module

    original = wall_module._nasa_record_state

    def shifted(record_id: str, temperature_K: float):
        g_over_rt, phase, authority, valid_range = original(record_id, temperature_K)
        if record_id == "NG-1135":
            return g_over_rt + 0.1, phase, authority, valid_range
        return g_over_rt, phase, authority, valid_range

    monkeypatch.setattr(wall_module, "_nasa_record_state", shifted)
    moved = resolve_wall_saturation(
        row, species_id="SiO", T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    assert moved.pressure_Pa != pytest.approx(baseline.pressure_Pa)


def test_perturbing_one_oxygen_coefficient_moves_psat() -> None:
    row = _species_rows()["SiO"]
    reactions = admissible_wall_reactions(row, species_id="SiO", T_K=_T_K)
    baseline = operative_wall_saturation(
        reactions, T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    perturbed = tuple(
        replace(reaction, nu_O2=reaction.nu_O2 + 0.25)
        if reaction.reaction_id == "oxidative"
        else reaction
        for reaction in reactions
    )
    moved = operative_wall_saturation(
        perturbed, T_K=_T_K, pO2_Pa=1.0e-9 * _P_STD
    )
    assert moved.pressure_Pa != pytest.approx(baseline.pressure_Pa)
    assert moved.nu_O2 != pytest.approx(baseline.nu_O2)


def _declared_janaf_wall_tables():
    tables = set()
    vapours = []
    for species, row in _species_rows().items():
        block = row.get("wall_reactions") or {}
        reactions = block.get("reactions") or []
        if not reactions or not all(r["vapour"].get("janaf") for r in reactions):
            continue
        vapours.append((species, str(reactions[0]["vapour"]["janaf"])))
        citations = [block.get("oxygen") or {}]
        citations.extend(c for r in reactions for c in [r["vapour"], *r["products"]])
        for citation in citations:
            raw = citation.get("janaf") or []
            tables.update([raw] if isinstance(raw, str) else raw)
    return tuple(sorted(tables)), tuple(sorted(vapours))


_JANAF_WALL_TABLES, _JANAF_WALL_VAPOURS = _declared_janaf_wall_tables()


def _mutated_janaf_wall_document(table_id, mutation):
    from copy import deepcopy
    from decimal import Decimal, InvalidOperation
    from simulator.reference_data import janaf

    document = deepcopy(janaf.load_table_document(TABLES_DIR / f"{table_id}.yaml"))
    temperature = 1673.15
    if mutation in {"missing_cell", "missing_row"}:
        nodes = {}
        for row in document["table"]["values"]:
            try:
                t = Decimal(str(row["temperature"]["as_published"]))
                g = Decimal(str(row["formation_gibbs_energy"]["as_published"]))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
            if t.is_finite() and g.is_finite():
                nodes[t] = row
        # A dense interior 100 K node avoids the owner's declared sparse
        # high-temperature ladder and the 298.15--500 K reference interval.
        candidates = [t for t in nodes if t-100 in nodes and t+100 in nodes
                      and (t < 3000 or (t-200 in nodes and t+200 in nodes))]
        assert candidates, table_id
        node = min(candidates, key=lambda t: abs(t - 1700))
        # K-002 has 250 and 298.15 K rows below its 300 K node.
        # Query between the immediately preceding row and the removed node;
        # node-25 would exercise an intact chord in that nonuniform grid.
        preceding = max(t for t in nodes if t < node)
        temperature = float((preceding + node) / 2)
        if mutation == "missing_row":
            document["table"]["values"].remove(nodes[node])
        else:
            nodes[node]["formation_gibbs_energy"] = {"value": None, "as_published": None}
    elif mutation == "table_id":
        document["table"]["table_id"] = "fold265-wrong-table"
    else:
        assert mutation == "source_sidecar"
        document["extraction"]["source_sha256"] = "fold265-wrong-source"
    return document, temperature


def _install_janaf_wall_mutation(monkeypatch, table_id, document):
    from simulator.reference_data import janaf
    from simulator.vapour_rail import wall_saturation as wall

    original = janaf.load_table_document
    def loader(path):
        return document if path.stem == table_id else original(path)
    monkeypatch.setattr(janaf, "load_table_document", loader)
    wall._janaf_record_data.cache_clear()
    wall._janaf_state_for_source.cache_clear()
    return wall


@pytest.mark.parametrize("table_id", _JANAF_WALL_TABLES)
@pytest.mark.parametrize("mutation", ["missing_cell", "missing_row", "table_id", "source_sidecar"])
def test_every_declared_janaf_wall_table_uses_checked_owner(monkeypatch, table_id, mutation):
    document, temperature = _mutated_janaf_wall_document(table_id, mutation)
    wall = _install_janaf_wall_mutation(monkeypatch, table_id, document)
    try:
        with pytest.raises(WallSaturationLawRefusal) as error:
            wall._janaf_record_state(table_id, temperature)
        assert error.value.reason == "wall_condensation_thermo_missing"
        assert table_id in str(error.value)
        if mutation.startswith("missing"):
            assert "missing grid node" in str(error.value)
    finally:
        wall._janaf_record_data.cache_clear()
        wall._janaf_state_for_source.cache_clear()


@pytest.mark.parametrize("species,table_id", _JANAF_WALL_VAPOURS)
@pytest.mark.parametrize("mutation", ["missing_cell", "table_id", "source_sidecar"])
def test_every_janaf_wall_resolver_propagates_checked_refusal(monkeypatch, species, table_id, mutation):
    from copy import deepcopy
    document, temperature = _mutated_janaf_wall_document(table_id, mutation)
    wall = _install_janaf_wall_mutation(monkeypatch, table_id, document)
    row = deepcopy(_species_rows()[species])
    row["wall_reactions"]["thermo"] = "janaf"
    try:
        with pytest.raises(WallSaturationLawRefusal) as error:
            resolve_wall_saturation(row, species_id=species, T_K=temperature, pO2_Pa=_PO2_PA)
        assert error.value.reason == "wall_condensation_thermo_missing"
    finally:
        wall._janaf_record_data.cache_clear()
        wall._janaf_state_for_source.cache_clear()


@pytest.mark.parametrize("table_id", _JANAF_WALL_TABLES)
def test_janaf_wall_endpoint_continuation_retains_extrapolated_authority(table_id):
    from decimal import Decimal
    from simulator.vapour_rail import wall_saturation as wall

    table, _digest = janaf._checked_compiled_table_document(table_id)
    points, missing = janaf._formation_gibbs_rows_from_table(table_id, table)
    for temperature in (points[0][0] - Decimal("1"), points[-1][0] + Decimal("1")):
        expected = janaf._interpolate_formation_gibbs(
            points, temperature, missing, extrapolate=True,
        )
        g_over_rt, _phase, authority, band = wall._janaf_record_state(table_id, float(temperature))
        assert authority == "extrapolated"
        assert band == (float(points[0][0]), float(points[-1][0]))
        assert g_over_rt == (float(expected) * 1000.0) / (R_J_PER_MOL_K * float(temperature))


@pytest.mark.parametrize("table_id", ["O-037", "O-038"])
@pytest.mark.parametrize("temperature_K", [1700.0, 1750.0])
def test_janaf_wall_reaction_uses_sign_restored_repaired_silica_gibbs(
    table_id, temperature_K,
):
    from decimal import Decimal

    document = janaf.load_table_document(TABLES_DIR / f"{table_id}.yaml")
    rows = {
        Decimal(str(row["temperature"]["value"])): row
        for row in document["table"]["values"]
    }
    at_1700 = rows[Decimal("1700")]["formation_gibbs_energy"]
    assert at_1700["as_published"] == (
        "609.041" if table_id == "O-037" else "609.059"
    )
    assert at_1700["locator"]["parse_repair"] == janaf.NIST_TAIL_PARSE_REPAIR
    g_1700 = Decimal(str(at_1700["value"]))
    assert g_1700 < 0

    # Premise: JANAF interpolates G linearly between 1700 and 1800 K.
    # Algebra: G(1750) = (G(1700) + G(1800)) / 2, still in kJ/mol.
    # Limit: at 1700 K use the repaired node directly. Convert G/(RT) with
    # 1000 J/kJ, R in J/(mol K), and T in K; the units cancel.
    expected_g_kJ_mol = g_1700
    if temperature_K == 1750.0:
        g_1800 = Decimal(
            str(rows[Decimal("1800")]["formation_gibbs_energy"]["value"])
        )
        expected_g_kJ_mol = (g_1700 + g_1800) / 2

    row = copy.deepcopy(_species_rows()["SiO2_gas"])
    row["wall_reactions"]["thermo"] = "janaf"
    row["wall_reactions"]["reactions"][0]["products"][0]["janaf"] = table_id
    result = resolve_wall_saturation(
        row, species_id="SiO2_gas", T_K=temperature_K,
    )
    product = next(
        participant for participant in result.participants
        if participant.role == "product"
    )

    assert result.thermo == "janaf"
    assert product.record_id == table_id
    assert product.g_over_RT == pytest.approx(
        float(expected_g_kJ_mol) * 1000.0 / (R_J_PER_MOL_K * temperature_K),
    )


@pytest.mark.parametrize("temperature_index,temperature_K", [(0, 1323.15), (1, 1500.0)])
def test_default_wall_route_numeric_pins_before_janaf_owner(temperature_index, temperature_K):
    # Measured on d9ee6f0031 before the JANAF owner consolidation. These
    # exact pins establish refactor parity; literature anchors above remain
    # the independent physics checks. FeO's JANAF owner-card value is kept
    # alongside every default NASA route, never regenerated by this test.
    expected = json.loads(
        (_REPO / "tests/fixtures/wall_default_route_before_janaf_owner.json").read_text()
    )
    declared = {species for species, row in _species_rows().items()
                if (row.get("wall_reactions") or {}).get("reactions")}
    assert declared == set(expected)
    for species in sorted(declared):
        result = resolve_wall_saturation(
            _species_rows()[species], species_id=species,
            T_K=temperature_K, pO2_Pa=_PO2_PA,
        )
        assert [result.pressure_Pa.hex(), result.reaction_id] == expected[species][temperature_index], species
