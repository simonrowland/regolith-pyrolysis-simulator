from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest

from simulator.accounting.formulas import parse_formula
from simulator.battery.oxygen_balance import (
    _OXYGEN_GAS_ALPHA,
    _OXYGEN_GAS_ALPHA_SOURCE,
    _VacuumOxygenChannel,
    _solve_vacuum_oxygen_balance,
)
from simulator.physical_constants import GAS_CONSTANT


ROOT = Path(__file__).resolve().parents[2]
COMMON_UNITY_SOURCE = (
    "Hashimoto assumed_unity_ratio_not_measured_alpha; "
    "declared common-unity sensitivity"
)
PA_PER_BAR = 100_000.0


def _hashimoto_start() -> tuple[float, float, dict[str, float]]:
    import yaml

    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/kems-015-hashimoto-1983.yaml").read_text()
    )
    run = next(
        experiment
        for experiment in source["experiments"]
        if experiment["experiment_id"] == "hashimoto-1983-run-18b6-1"
    )
    temperature_K = float(run["conditions"]["temperature_K"]["state"]["value"])
    pressure_pa = float(
        run["pressure_environment"]["total_pressure_Pa"]["state"]["value"]["point"]
    )
    printed_mass_kg = float(
        run["sample"]["mass_kg"]["state"]["value"]["point"]
    )
    oxide_wt_pct = run["sample"]["printed_composition"]["state"]["value"]
    composition_kg = {
        oxide: float(wt_pct) * printed_mass_kg / 100.0
        for oxide, wt_pct in oxide_wt_pct.items()
    }
    return temperature_K, pressure_pa / PA_PER_BAR, composition_kg


def _runtime_catalog_rows() -> dict[str, dict[str, object]]:
    import yaml

    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    document = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    legacy = vapor_pressure_legacy_view(document)
    rows: dict[str, dict[str, object]] = {}
    for group in legacy.values():
        if not isinstance(group, dict):
            continue
        for species, row in group.items():
            if isinstance(row, dict) and row.get("formula"):
                rows[str(species)] = row
    return rows


def _species_metadata(
    formula_text: str, parent_oxide: str | None, *, alpha: float
) -> _VacuumOxygenChannel:
    gas_formula = parse_formula(formula_text)
    oxygen_atoms = float(gas_formula.elements.get("O", 0.0))
    metal_atoms = math.fsum(
        float(count)
        for element, count in gas_formula.elements.items()
        if element != "O"
    )
    if parent_oxide:
        parent_formula = parse_formula(parent_oxide)
        parent_metals = math.fsum(
            float(count)
            for element, count in parent_formula.elements.items()
            if element != "O"
        )
        parent_oxygen_demand = (
            oxygen_atoms
            if parent_metals == 0.0
            else metal_atoms
            * float(parent_formula.elements.get("O", 0.0))
            / parent_metals
        )
    else:
        parent_oxygen_demand = 0.0
    pO2_exponent = (oxygen_atoms - parent_oxygen_demand) / 2.0
    return _VacuumOxygenChannel(
        species="",
        molar_mass_kg_per_mol=gas_formula.molar_mass_kg_per_mol(),
        oxygen_atoms=oxygen_atoms,
        parent_oxygen_demand=parent_oxygen_demand,
        pO2_exponent=pO2_exponent,
        alpha=alpha,
        alpha_source=COMMON_UNITY_SOURCE,
    )


def _alpha_source_for_catalog_row(
    species: str, row: dict[str, object]
) -> str:
    alpha = row.get("evaporation_alpha")
    source = alpha.get("source") if isinstance(alpha, dict) else None
    if source:
        return f"{COMMON_UNITY_SOURCE}; runtime catalog {species}: {source}"
    return f"{COMMON_UNITY_SOURCE}; runtime catalog {species}: no alpha row"


def _catalog_match(
    rows: dict[str, dict[str, object]], formula: str, parent_oxide: str
) -> tuple[str, dict[str, object]] | None:
    matches = [
        (species, row)
        for species, row in rows.items()
        if row.get("formula") == formula and row.get("parent_oxide") == parent_oxide
    ]
    if not matches:
        return None
    active = [
        item for item in matches if item[1].get("flux_dormant") is not True
    ]
    return (active or matches)[0]


@lru_cache(maxsize=1)
def _engine_models():
    """Build each engine's own melt-pressure model at Hashimoto's start."""

    pytest.importorskip("openimcc")
    from openimcc import evaluate_gas, load_gas_datapack
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        _InternalAnalyticalBatteryBackend,
        _openimcc_gas_channels_and_omission_notices,
    )
    from simulator.melt_backend import openimcc_bridge

    temperature_K, total_pressure_bar, composition_kg = _hashimoto_start()
    catalog_rows = _runtime_catalog_rows()
    gas_pack = load_gas_datapack()
    openimcc_state = openimcc_bridge.evaluate(
        temperature_K=temperature_K,
        composition_kg=composition_kg,
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    openimcc_channels, _ = _openimcc_gas_channels_and_omission_notices(
        openimcc_state.parent_oxides, gas_pack
    )
    openimcc_channel_rows: list[_VacuumOxygenChannel] = []
    for species, (parent, _gas_molecules, _oxygen_molecules) in openimcc_channels:
        if species in {"O", "O2"}:
            is_active = True
            alpha_source = _OXYGEN_GAS_ALPHA_SOURCE
        else:
            if parent and float(
                openimcc_state.parent_oxide_activities.get(parent, 0.0) or 0.0
            ) <= 0.0:
                continue
            match = _catalog_match(catalog_rows, species, parent)
            assert match is not None, f"runtime catalog has no {species}/{parent} channel"
            catalog_name, row = match
            is_active = row.get("flux_dormant") is not True
            alpha_source = _alpha_source_for_catalog_row(catalog_name, row)
        channel = _species_metadata(
            species, parent or None, alpha=_OXYGEN_GAS_ALPHA
        )
        openimcc_channel_rows.append(
            replace(
                channel,
                species=species,
                alpha_source=alpha_source,
                active=is_active,
            )
        )

    def openimcc_pressure_model(log10_pO2_bar: float) -> dict[str, float]:
        pressures = evaluate_gas(
            openimcc_state.parent_oxide_activities,
            temperature_K,
            10.0**log10_pO2_bar,
            gas_pack,
            parent_oxides=openimcc_state.parent_oxides,
            allow_extrapolation=True,
        )
        return {str(species): float(value) * PA_PER_BAR for species, value in pressures.items()}

    ia_backend = _InternalAnalyticalBatteryBackend()
    ia_probe = ia_backend.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg=composition_kg,
        fO2_log=-5.0,
        pressure_bar=total_pressure_bar,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-5),
    )
    assert ia_probe.status == "ok"
    ia_channel_rows: list[_VacuumOxygenChannel] = []
    for species in sorted(ia_probe.vapor_pressures_Pa):
        match = catalog_rows.get(species)
        assert match is not None, f"runtime catalog has no internal channel {species!r}"
        formula = str(match["formula"])
        parent = str(match.get("parent_oxide") or "")
        channel = _species_metadata(
            formula, parent or None, alpha=_OXYGEN_GAS_ALPHA
        )
        ia_channel_rows.append(
            replace(
                channel,
                species=species,
                alpha_source=_alpha_source_for_catalog_row(species, match),
                active=match.get("flux_dormant") is not True,
            )
        )

    # The internal-analytical melt kernel has no O/O2 entries in its
    # vapor-pressure table. O2 is the surface fugacity itself; atomic O follows
    # the shared gas-phase O2(g) <=> 2 O(g) thermochemical relation. This uses
    # only the oxygen coproduct law, never OpenIMCC's solved pO2 for IA.
    oxygen_at_1_bar = evaluate_gas(
        openimcc_state.parent_oxide_activities,
        temperature_K,
        1.0,
        gas_pack,
        parent_oxides=openimcc_state.parent_oxides,
        allow_extrapolation=True,
    )
    oxygen_pressure_factor_bar = float(oxygen_at_1_bar["O"])
    for species, exponent in (("O", 0.5), ("O2", 1.0)):
        channel = _species_metadata(
            species, None, alpha=_OXYGEN_GAS_ALPHA
        )
        ia_channel_rows.append(
            replace(
                channel,
                species=species,
                pO2_exponent=exponent,
                alpha_source=_OXYGEN_GAS_ALPHA_SOURCE,
            )
        )

    @lru_cache(maxsize=256)
    def ia_pressure_model(log10_pO2_bar: float) -> dict[str, float]:
        pO2_bar = 10.0**log10_pO2_bar
        result = ia_backend.equilibrate(
            temperature_C=temperature_K - 273.15,
            composition_kg=composition_kg,
            fO2_log=log10_pO2_bar,
            pressure_bar=total_pressure_bar,
            po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=pO2_bar),
        )
        if result.status != "ok":
            raise AssertionError(f"internal-analytical pressure model refused: {result.status}")
        pressures = {
            species: float(result.vapor_pressures_Pa.get(species, 0.0))
            for species in (channel.species for channel in ia_channel_rows)
            if species not in {"O", "O2"}
        }
        pressures["O"] = (
            PA_PER_BAR * oxygen_pressure_factor_bar * math.sqrt(pO2_bar)
        )
        pressures["O2"] = PA_PER_BAR * pO2_bar
        return pressures

    return {
        "openimcc": (openimcc_pressure_model, tuple(openimcc_channel_rows)),
        "internal-analytical": (ia_pressure_model, tuple(ia_channel_rows)),
    }


def _with_parent_alpha(
    channels: tuple[_VacuumOxygenChannel, ...], alpha: float
) -> tuple[_VacuumOxygenChannel, ...]:
    return tuple(
        replace(
            channel,
            alpha=alpha if channel.parent_oxygen_demand > 0.0 else 1.0,
            alpha_source=(
                f"{COMMON_UNITY_SOURCE}; parent-bearing channels alpha={alpha}"
                if channel.parent_oxygen_demand > 0.0
                else channel.alpha_source
            ),
        )
        for channel in channels
    )


def _relative_residual_at(
    pressure_model,
    channels: tuple[_VacuumOxygenChannel, ...],
    temperature_K: float,
    pO2_bar: float,
) -> float:
    pressures = pressure_model(math.log10(pO2_bar))
    oxygen_flux = 0.0
    parent_flux = 0.0
    for channel in channels:
        if not channel.active or channel.alpha == 0.0:
            continue
        molar_flux = channel.alpha * pressures[channel.species] / math.sqrt(
            2.0 * math.pi * GAS_CONSTANT * temperature_K
            * channel.molar_mass_kg_per_mol
        )
        oxygen_flux += channel.oxygen_atoms * molar_flux
        parent_flux += channel.parent_oxygen_demand * molar_flux
    return abs(oxygen_flux - parent_flux) / max(oxygen_flux, parent_flux, 1.0e-300)


def test_hashimoto_vacuum_oxygen_balance_is_engine_specific_and_alpha_weighted() -> None:
    temperature_K, _total_pressure_bar, _composition_kg = _hashimoto_start()
    models = _engine_models()
    solved: dict[str, dict[float, object]] = {}

    for engine, (pressure_model, catalog_channels) in models.items():
        solved[engine] = {}
        for parent_alpha in (1.0, 0.25):
            channels = _with_parent_alpha(catalog_channels, parent_alpha)
            result = _solve_vacuum_oxygen_balance(
                pressure_model,
                channels,
                temperature_K=temperature_K,
            )
            assert result.relative_residual < 1.0e-9
            assert set(result.alpha_sources) == {
                channel.species
                for channel in channels
                if channel.active and channel.alpha > 0.0
            }
            assert result.alpha_sources["O"] == _OXYGEN_GAS_ALPHA_SOURCE
            assert result.alpha_sources["O2"] == _OXYGEN_GAS_ALPHA_SOURCE
            dormant_species = (
                "FeO" if engine == "openimcc" else "FeO_association_gas"
            )
            assert dormant_species not in result.alpha_sources
            solved[engine][parent_alpha] = result

        unity = solved[engine][1.0]
        quarter = solved[engine][0.25]
        assert not math.isclose(unity.pO2_bar, quarter.pO2_bar, rel_tol=1.0e-3)
        assert _relative_residual_at(
            pressure_model,
            _with_parent_alpha(catalog_channels, 0.25),
            temperature_K,
            unity.pO2_bar,
        ) > 1.0e-3

        uniformly_halved = tuple(
            replace(
                channel,
                alpha=channel.alpha * 0.5,
                alpha_source="common factor cancellation check",
            )
            for channel in _with_parent_alpha(catalog_channels, 1.0)
        )
        common_factor_result = _solve_vacuum_oxygen_balance(
            pressure_model,
            uniformly_halved,
            temperature_K=temperature_K,
        )
        assert common_factor_result.pO2_bar == pytest.approx(
            unity.pO2_bar, rel=1.0e-9
        )

    # openimcc 4fe8eaf refit the CaO(l) continuation used by this Hashimoto
    # probe; at 30c51c8 the alpha=1 and 0.25 results are 0.8286161505495706 Pa
    # and 0.3003449996328013 Pa, respectively.
    # The two engines still use their own melt channel pressures for both arms.
    assert solved["openimcc"][1.0].pO2_bar.hex() == "0x1.160995d98f6e4p-17"
    assert (
        solved["openimcc"][1.0].pO2_bar * PA_PER_BAR
    ).hex() == "0x1.a8406047187bap-1"
    assert solved["openimcc"][0.25].pO2_bar * PA_PER_BAR == pytest.approx(
        0.3003449996328013, rel=2.0e-5
    )
    assert solved["internal-analytical"][1.0].pO2_bar * PA_PER_BAR == pytest.approx(
        1.025329, rel=2.0e-5
    )
    assert solved["internal-analytical"][0.25].pO2_bar * PA_PER_BAR == pytest.approx(
        0.374487, rel=2.0e-5
    )

    # Replacing either engine's root by the other's does not close that engine's
    # channel balance, which catches the old IA adapter's borrowed-root pattern.
    ia_unity = solved["internal-analytical"][1.0]
    imcc_unity = solved["openimcc"][1.0]
    ia_model, ia_channels = models["internal-analytical"]
    imcc_model, imcc_channels = models["openimcc"]
    assert _relative_residual_at(
        ia_model, _with_parent_alpha(ia_channels, 1.0), temperature_K, imcc_unity.pO2_bar
    ) > 1.0e-3
    assert _relative_residual_at(
        imcc_model, _with_parent_alpha(imcc_channels, 1.0), temperature_K, ia_unity.pO2_bar
    ) > 1.0e-3


def _residue_catalog_alpha(species: str) -> tuple[float, str]:
    row = _runtime_catalog_rows()[species]
    alpha_row = row["evaporation_alpha"]
    assert isinstance(alpha_row, dict)
    return float(alpha_row["value"]), str(alpha_row["source"])


def _residue_channels(*, fe_alpha: float | None = None):
    from simulator.battery.oxygen_balance import _OXYGEN_GAS_ALPHA_SOURCE
    from simulator.battery.residue import ResidueChannel

    catalog_fe_alpha, fe_source = _residue_catalog_alpha("Fe")
    catalog_mg_alpha, mg_source = _residue_catalog_alpha("Mg")
    return (
        ResidueChannel(
            "Fe", "Fe", "FeO",
            catalog_fe_alpha if fe_alpha is None else fe_alpha,
            fe_source,
        ),
        ResidueChannel("Mg", "Mg", "MgO", catalog_mg_alpha, mg_source),
        ResidueChannel("O2", "O2", None, 1.0, _OXYGEN_GAS_ALPHA_SOURCE),
    )


def _residue_pressure_model(inventory: dict[str, float], log10_pO2_bar: float):
    pO2_bar = 10.0**log10_pO2_bar
    pO2_root = math.sqrt(pO2_bar)
    return {
        "Fe": 1.0e-3 * inventory.get("FeO", 0.0) / pO2_root,
        "Mg": 1.0e-3 * inventory.get("MgO", 0.0) / pO2_root,
        "O2": 1.0e5 * pO2_bar,
    }


def _assert_residue_atoms_close(
    starting: dict[str, float], residue: dict[str, float], evaporated: dict[str, float]
) -> None:
    def atom_totals(inventory: dict[str, float]) -> dict[str, float]:
        totals: dict[str, float] = {}
        for species, amount in inventory.items():
            formula = parse_formula(species)
            for element, count in formula.elements.items():
                totals[element] = totals.get(element, 0.0) + amount * float(count)
        return totals

    start_atoms = atom_totals(starting)
    end_atoms = atom_totals({**residue, **evaporated})
    for element in set(start_atoms) | set(end_atoms):
        assert start_atoms.get(element, 0.0) == pytest.approx(
            end_atoms.get(element, 0.0), rel=5.0e-12, abs=1.0e-15
        )


def test_residue_two_channel_hkl_and_parent_stoichiometric_anchors() -> None:
    from simulator.battery.residue import integrate_residue_inventory

    temperature_K = 2073.0
    duration_s = 100.0
    area_m2 = 1.0e-4
    starting = {"FeO": 1.0e-3}
    channels = (_residue_channels()[0], _residue_channels()[2])

    def pressure_model(_inventory: dict[str, float], log10_pO2_bar: float):
        pO2_bar = 10.0**log10_pO2_bar
        return {"Fe": 1.0e-3 / math.sqrt(pO2_bar), "O2": 1.0e5 * pO2_bar}

    result = integrate_residue_inventory(
        starting,
        channels,
        pressure_model,
        temperature_K=temperature_K,
        duration_s=duration_s,
        area_evolution_m2=(area_m2,),
    )
    alpha, _source = _residue_catalog_alpha("Fe")
    fe_molar_mass = parse_formula("Fe").molar_mass_kg_per_mol()
    root_bar = result.pO2_bar_by_step[0]
    assert root_bar is not None
    p_fe_pa = 1.0e-3 / math.sqrt(root_bar)
    # External Safarian–Engh Hertz–Knudsen–Langmuir form, converted to mol/s.
    requested_fe_mol_s = alpha * p_fe_pa / math.sqrt(
        2.0 * math.pi * fe_molar_mass * GAS_CONSTANT * temperature_K
    ) * area_m2
    expected_fe_mol = starting["FeO"] * -math.expm1(
        -requested_fe_mol_s * duration_s / starting["FeO"]
    )
    assert result.evaporated_mol["Fe"] == pytest.approx(expected_fe_mol, rel=1.0e-10)
    # FeO -> Fe + 1/2 O2: one parent oxide molecule is debited per Fe atom.
    assert result.residue_mol["FeO"] == pytest.approx(
        starting["FeO"] - expected_fe_mol, rel=1.0e-12
    )
    assert result.evaporated_mol["O2"] == pytest.approx(expected_fe_mol / 2.0, rel=1.0e-12)
    _assert_residue_atoms_close(
        starting, dict(result.residue_mol), dict(result.evaporated_mol)
    )


def test_buffered_residue_keeps_printed_fo2_and_accounts_reservoir_exchange() -> None:
    from simulator.battery.residue import ResidueChannel, integrate_residue_inventory

    requested_logp = -3.25
    seen_logp: list[float] = []
    starting = {"FeO": 1.0e-3}
    fe_alpha, alpha_source = _residue_catalog_alpha("Fe")
    result = integrate_residue_inventory(
        starting,
        (ResidueChannel("Fe", "Fe", "FeO", fe_alpha, alpha_source),),
        lambda _inventory, logp: seen_logp.append(logp) or {"Fe": 1.0e-3},
        temperature_K=1773.15,
        duration_s=60.0,
        area_evolution_m2=(1.0e-4, 1.0e-4),
        buffered_fO2_log=requested_logp,
    )

    assert seen_logp == [requested_logp, requested_logp]
    assert result.pO2_bar_by_step == (10.0**requested_logp,) * 2
    assert result.residue_mol["FeO"] < starting["FeO"]
    assert result.buffer_oxygen_exchange_mol < 0.0
    assert result.atom_closure_mol["O"] == pytest.approx(0.0, abs=1.0e-15)
    assert result.atom_closure_mol["Fe"] == pytest.approx(0.0, abs=1.0e-15)


@pytest.mark.parametrize(
    ("alpha", "zero_parent_pressure"),
    ((0.0, False), (0.2, True)),
)
def test_residue_zero_alpha_or_zero_pressure_is_no_change(
    alpha: float, zero_parent_pressure: bool
) -> None:
    from simulator.battery.residue import integrate_residue_inventory

    channels = list(_residue_channels())
    channels[0] = replace(channels[0], alpha=alpha)
    if alpha == 0.0:
        channels[1] = replace(channels[1], alpha=0.0)
        channels[2] = replace(channels[2], alpha=0.0)
    starting = {"FeO": 0.01, "MgO": 0.01}

    def pressure_model(inventory: dict[str, float], log10_pO2_bar: float):
        pressures = _residue_pressure_model(inventory, log10_pO2_bar)
        if zero_parent_pressure:
            pressures["Fe"] = 0.0
            pressures["Mg"] = 0.0
        return pressures

    result = integrate_residue_inventory(
        starting,
        tuple(channels),
        pressure_model,
        temperature_K=2073.0,
        duration_s=60.0,
        area_evolution_m2=(0.01, 0.01),
    )
    assert result.residue_mol == starting
    assert result.evaporated_mol == {}
    _assert_residue_atoms_close(starting, dict(result.residue_mol), {})


def test_residue_finite_step_oxygen_tracks_actual_parent_depletion_and_re_solves() -> None:
    from simulator.battery.residue import integrate_residue_inventory

    starting = {"FeO": 1.0, "MgO": 1.0}
    channels = _residue_channels()
    sampled_compositions: list[dict[str, float]] = []

    def pressure_model(inventory: dict[str, float], log10_pO2_bar: float):
        if not sampled_compositions or sampled_compositions[-1] != inventory:
            sampled_compositions.append(dict(inventory))
        return _residue_pressure_model(inventory, log10_pO2_bar)

    result = integrate_residue_inventory(
        starting,
        channels,
        pressure_model,
        temperature_K=2073.0,
        duration_s=200.0,
        area_evolution_m2=(1.0, 1.0),
    )
    assert len(result.pO2_bar_by_step) == 2
    assert result.pO2_bar_by_step[0] != pytest.approx(result.pO2_bar_by_step[1], rel=1.0e-6)
    assert len(sampled_compositions) == 2
    for pO2_bar, composition in zip(result.pO2_bar_by_step, sampled_compositions, strict=True):
        assert pO2_bar is not None
        pressures = _residue_pressure_model(composition, math.log10(pO2_bar))
        channels_for_root = (
            replace(
                _species_metadata("Fe", "FeO", alpha=channels[0].alpha),
                species="Fe",
            ),
            replace(
                _species_metadata("Mg", "MgO", alpha=channels[1].alpha),
                species="Mg",
            ),
            replace(_species_metadata("O2", None, alpha=1.0), species="O2"),
        )
        root_residual = _relative_residual_at(
            lambda _logp, pressures=pressures: pressures,
            channels_for_root,
            2073.0,
            pO2_bar,
        )
        assert root_residual < 1.0e-9

    oxygen_from_parent_debits = (
        starting["FeO"] - result.residue_mol["FeO"]
        + starting["MgO"] - result.residue_mol["MgO"]
    )
    assert result.evaporated_mol["O2"] == pytest.approx(
        oxygen_from_parent_debits / 2.0, rel=1.0e-12
    )
    _assert_residue_atoms_close(
        starting, dict(result.residue_mol), dict(result.evaporated_mol)
    )


def test_residue_missing_area_evolution_is_typed_absence() -> None:
    from simulator.battery.residue import (
        ResidueInventoryRefusal,
        integrate_residue_inventory,
    )

    with pytest.raises(ResidueInventoryRefusal) as refusal:
        integrate_residue_inventory(
            {"FeO": 1.0},
            (_residue_channels()[0], _residue_channels()[2]),
            lambda _inventory, _logp: {},
            temperature_K=2073.0,
            duration_s=1.0,
        )
    assert refusal.value.reason == "melt_surface_area_evolution_missing"


@pytest.mark.parametrize("engine", ("IA", "openimcc"))
def test_hashimoto_exhaustion_is_removed_before_either_engine_and_atom_closes(
    engine: str,
) -> None:
    from simulator.battery.residue import _hashimoto_integrate_geometry

    # This is the ~6.84e-10 mole-fraction FeO that previously reached IMCC-SF04.
    starting = {"FeO": 6.84e-10, "MgO": 1.0}
    observed_compositions: list[dict[str, float]] = []

    def engine_pressure(inventory: dict[str, float], log10_pO2_bar: float):
        observed_compositions.append(dict(inventory))
        return _residue_pressure_model(inventory, log10_pO2_bar)

    # IA and OpenIMCC both enter the same engine-neutral integration policy.
    assert engine in {"IA", "openimcc"}
    values, _pO2, exhausted, refusal, atom_closure = _hashimoto_integrate_geometry(
        starting,
        _residue_channels(),
        engine_pressure,
        temperature_K=2073.0,
        duration_s=1.0,
        steps=1,
        geometry_policy_id="sphere_constant",
        initial_area_m2=1.0e-4,
    )

    assert refusal is None
    assert values["FeO"] == 0.0
    assert observed_compositions
    assert all("FeO" not in composition for composition in observed_compositions)
    assert len(exhausted) == 1
    assert exhausted[0]["reason"] == "component_exhausted"
    assert exhausted[0]["component"] == "FeO"
    assert exhausted[0]["remaining_moles"] == pytest.approx(6.84e-10)
    assert exhausted[0]["step"] == 1
    assert max(abs(value) for value in atom_closure.values()) < 1.0e-15


@pytest.mark.parametrize("engine", ("IA", "openimcc"))
def test_hashimoto_nonconvergence_is_partial_per_run_and_cohort_continues(
    engine: str,
) -> None:
    from simulator.battery.residue import (
        ResidueEngineNonconvergence,
        _hashimoto_integrate_geometry,
    )

    starting = {"FeO": 1.0, "MgO": 1.0}
    calls = 0

    def failed_engine(_inventory: dict[str, float], _log10_pO2_bar: float):
        nonlocal calls
        calls += 1
        raise ResidueEngineNonconvergence(engine, "injected nonconvergence")

    per_run_results = []
    per_run_results.append(
        _hashimoto_integrate_geometry(
            starting,
            _residue_channels(),
            failed_engine,
            temperature_K=2073.0,
            duration_s=2.0,
            steps=1,
            geometry_policy_id="sphere_constant",
            initial_area_m2=1.0e-4,
        )
    )
    assert calls == 2  # coarse interval, then the single half-step retry
    refusal = per_run_results[0][3]
    assert refusal is not None
    assert isinstance(refusal, ResidueEngineNonconvergence)
    assert refusal.reason == "engine_nonconvergence"
    assert refusal.engine == engine
    assert refusal.composition_mol == starting
    assert refusal.step == 1
    assert refusal.time_reached_s == 0.0
    assert "halving" in str(refusal.retry)

    # A refusal from one physical run is data for that run; it does not escape
    # the shared geometry runner or prevent the next run from completing.
    per_run_results.append(
        _hashimoto_integrate_geometry(
            starting,
            _residue_channels(),
            _residue_pressure_model,
            temperature_K=2073.0,
            duration_s=0.01,
            steps=1,
            geometry_policy_id="sphere_constant",
            initial_area_m2=1.0e-6,
        )
    )
    assert len(per_run_results) == 2
    assert per_run_results[1][3] is None


# Production integrate_residue_inventory outputs, recorded before the shared
# frozen-inventory depletion move. Hex is the integrator's result, not a
# reimplementation of the debit.
_RESIDUE_INVENTORY_HEX = {
    "two_channel": {
        "residue_mol": {"FeO": "0x1.048e1b5e354efp-10"},
        "evaporated_mol": {
            "Fe": "0x1.96c1d0e550c95p-18",
            "O2": "0x1.96c1d0e550c95p-19",
        },
        "pO2_bar_by_step": ("0x1.8046881184a9ep-23",),
        "atom_closure_mol": {"Fe": "0x0.0p+0", "O": "0x0.0p+0"},
        "buffer_oxygen_exchange_mol": "0x0.0p+0",
    },
    "buffered": {
        "residue_mol": {"FeO": "0x1.0624c085406a8p-10"},
        "evaporated_mol": {"Fe": "0x1.ca9da353faa7cp-30"},
        "pO2_bar_by_step": (
            "0x1.26d42cce9b24cp-11",
            "0x1.26d42cce9b24cp-11",
        ),
        "atom_closure_mol": {"Fe": "0x0.0p+0", "O": "0x1.5610000000000p-68"},
        "buffer_oxygen_exchange_mol": "-0x1.ca9da353faa7cp-30",
    },
    "zero_alpha": {
        "residue_mol": {
            "FeO": "0x1.47ae147ae147bp-7",
            "MgO": "0x1.47ae147ae147bp-7",
        },
        "evaporated_mol": {},
        "pO2_bar_by_step": (None, None),
        "atom_closure_mol": {
            "Fe": "0x0.0p+0",
            "Mg": "0x0.0p+0",
            "O": "0x0.0p+0",
        },
        "buffer_oxygen_exchange_mol": "0x0.0p+0",
    },
    "zero_pressure": {
        "residue_mol": {
            "FeO": "0x1.47ae147ae147bp-7",
            "MgO": "0x1.47ae147ae147bp-7",
        },
        "evaporated_mol": {},
        "pO2_bar_by_step": (None, None),
        "atom_closure_mol": {
            "Fe": "0x0.0p+0",
            "Mg": "0x0.0p+0",
            "O": "0x0.0p+0",
        },
        "buffer_oxygen_exchange_mol": "0x0.0p+0",
    },
    "finite_step": {
        "residue_mol": {
            "FeO": "0x1.e68d7fa6a7aa3p-1",
            "MgO": "0x1.d8d2dd4f5bc84p-2",
        },
        "evaporated_mol": {
            "Fe": "0x1.97280595855d4p-5",
            "Mg": "0x1.13969158521bep-1",
            "O2": "0x1.2d0911b1aa71bp-2",
        },
        "pO2_bar_by_step": (
            "0x1.33016061fb8fcp-20",
            "0x1.e991dede9f015p-21",
        ),
        "atom_closure_mol": {
            "Fe": "0x0.0p+0",
            "Mg": "0x0.0p+0",
            "O": "0x0.0p+0",
        },
        "buffer_oxygen_exchange_mol": "0x0.0p+0",
    },
}


def _residue_inventory_hex(result) -> dict:
    return {
        "residue_mol": {
            key: value.hex() for key, value in result.residue_mol.items()
        },
        "evaporated_mol": {
            key: value.hex() for key, value in result.evaporated_mol.items()
        },
        "pO2_bar_by_step": tuple(
            None if value is None else value.hex()
            for value in result.pO2_bar_by_step
        ),
        "atom_closure_mol": {
            key: value.hex() for key, value in result.atom_closure_mol.items()
        },
        "buffer_oxygen_exchange_mol": result.buffer_oxygen_exchange_mol.hex(),
    }


def test_residue_inventory_outputs_are_pinned() -> None:
    from simulator.battery.residue import ResidueChannel, integrate_residue_inventory

    temperature_K = 2073.0
    two_channel_start = {"FeO": 1.0e-3}
    fe_and_oxygen = (_residue_channels()[0], _residue_channels()[2])

    def two_channel_pressure(_inventory: dict[str, float], log10_pO2_bar: float):
        pO2_bar = 10.0**log10_pO2_bar
        return {"Fe": 1.0e-3 / math.sqrt(pO2_bar), "O2": 1.0e5 * pO2_bar}

    cases = {
        "two_channel": integrate_residue_inventory(
            two_channel_start,
            fe_and_oxygen,
            two_channel_pressure,
            temperature_K=temperature_K,
            duration_s=100.0,
            area_evolution_m2=(1.0e-4,),
        ),
    }
    fe_alpha, alpha_source = _residue_catalog_alpha("Fe")
    cases["buffered"] = integrate_residue_inventory(
        {"FeO": 1.0e-3},
        (ResidueChannel("Fe", "Fe", "FeO", fe_alpha, alpha_source),),
        lambda _inventory, _logp: {"Fe": 1.0e-3},
        temperature_K=1773.15,
        duration_s=60.0,
        area_evolution_m2=(1.0e-4, 1.0e-4),
        buffered_fO2_log=-3.25,
    )
    for name, alpha, zero_parent_pressure in (
        ("zero_alpha", 0.0, False),
        ("zero_pressure", 0.2, True),
    ):
        channels = list(_residue_channels())
        channels[0] = replace(channels[0], alpha=alpha)
        if alpha == 0.0:
            channels[1] = replace(channels[1], alpha=0.0)
            channels[2] = replace(channels[2], alpha=0.0)

        def pressure_model(
            inventory: dict[str, float],
            log10_pO2_bar: float,
            *,
            _zero_parent=zero_parent_pressure,
        ):
            pressures = _residue_pressure_model(inventory, log10_pO2_bar)
            if _zero_parent:
                pressures["Fe"] = 0.0
                pressures["Mg"] = 0.0
            return pressures

        cases[name] = integrate_residue_inventory(
            {"FeO": 0.01, "MgO": 0.01},
            tuple(channels),
            pressure_model,
            temperature_K=temperature_K,
            duration_s=60.0,
            area_evolution_m2=(0.01, 0.01),
        )
    cases["finite_step"] = integrate_residue_inventory(
        {"FeO": 1.0, "MgO": 1.0},
        _residue_channels(),
        _residue_pressure_model,
        temperature_K=temperature_K,
        duration_s=200.0,
        area_evolution_m2=(1.0, 1.0),
    )
    assert set(cases) == set(_RESIDUE_INVENTORY_HEX)
    for name, result in cases.items():
        assert _residue_inventory_hex(result) == _RESIDUE_INVENTORY_HEX[name]
