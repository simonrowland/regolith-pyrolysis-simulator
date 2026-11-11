import math
from types import SimpleNamespace

import pytest
import yaml

from simulator.condensation import (
    DATA_DIR,
    HKL_BAND_SAMPLES,
    CondensationModel,
    STAGE_PURITY_NO_CAPTURED_MASS,
    STAGE_PURITY_VERDICT_INDETERMINATE,
    STAGE_PURITY_VERDICT_PURE,
    stage_purity_report,
    stage_visit_key,
)
from simulator.condensation_routing import (
    COLLECTOR_STAGE_KEY_BY_NUMBER,
    DESIGNATED_STAGE,
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
    PRODUCT_DESTINATIONS,
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    product_stage_number,
    stage_list_index_for_destination,
    target_species_for_stage_number,
)
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


@pytest.mark.parametrize("stage_number", [1, 2, 3, 4])
def test_default_train_targets_use_canonical_registry(stage_number):
    train = CondensationTrain.create_default()

    assert train.stages[stage_number].target_species == (
        target_species_for_stage_number(stage_number)
    )


def test_stage_list_index_resolves_stage_keys_for_all_collector_bands():
    train = CondensationTrain.create_default()

    assert stage_list_index_for_destination(train, "stage_0_hot_duct") == 0
    assert stage_list_index_for_destination(
        train, "stage_5_vortex_dust_filter") == 5
    assert stage_list_index_for_destination(
        train, K_TRAP_STAGE_KEY) == 8


@pytest.mark.parametrize(
    ("recipe", "species", "stage_number"),
    [
        ("MRE", "Fe", 1),
        ("MRE", "Cr", 2),
        ("MRE", "Mn", 2),
        ("MRE", "Mg", 4),
        ("C3", "Cr", 2),
        ("C3", "Ti", None),
        ("C6", "Al", None),
        ("C6", "Si", None),
    ],
)
def test_recipe_product_destinations_are_canonical(recipe, species, stage_number):
    assert recipe in PRODUCT_DESTINATIONS
    assert product_stage_number(recipe, species) == stage_number


def test_stage_purity_report_empty_is_indeterminate_not_pure():
    train = CondensationTrain.create_default()
    empty = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[1]]

    assert empty["total_kg"] == 0
    assert empty["purity_fraction"] is None
    assert empty["verdict"] == STAGE_PURITY_VERDICT_INDETERMINATE
    assert empty["reason"] == STAGE_PURITY_NO_CAPTURED_MASS

    train.stages[1].collected_kg["Fe"] = 1e-9
    tiny = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[1]]

    assert tiny["total_kg"] == pytest.approx(1e-9)
    assert tiny["purity_fraction"] == pytest.approx(1.0)
    assert tiny["verdict"] == STAGE_PURITY_VERDICT_PURE
    assert "reason" not in tiny

    train.stages[1].collected_kg["Fe"] = math.nan
    with pytest.raises(
        ValueError,
        match="stage 1 inventory for Fe must be finite and non-negative",
    ):
        stage_purity_report(train)


def test_stage_purity_report_flags_non_designated_stage_landings():
    train = CondensationTrain.create_default()
    train.stages[1].collected_kg["Fe"] = 9.0
    train.stages[1].collected_kg["SiO2"] = 1.0

    report = stage_purity_report(train)
    stage = report[STAGE_KEY_BY_NUMBER[1]]

    assert stage["accepted_species"] == sorted(
        accepted_species_for_stage_number(1))
    assert stage["designated_species_kg"] == {"Fe": 9.0}
    assert stage["impurity_species_kg"] == {"SiO2": 1.0}
    assert stage["purity_fraction"] == pytest.approx(0.9)
    assert stage["verdict"] == "MIXED"


def _populated_stage_four_purity_train(*, empty_keys=False):
    na_key = "" if empty_keys else NA_COLLECTOR_STAGE_KEY
    k_key = "" if empty_keys else K_TRAP_STAGE_KEY
    return SimpleNamespace(
        stages=[
            SimpleNamespace(
                stage_number=4,
                sub_order=0,
                stage_key=na_key,
                label="Na/Mg collector",
                collected_kg={"Na": 5.0, "Mg": 5.5},
            ),
            SimpleNamespace(
                stage_number=4,
                sub_order=1,
                stage_key=k_key,
                label="K trap",
                collected_kg={"K": 9.0},
            ),
        ]
    )


def test_stage_purity_report_pins_explicit_stage_four_collectors():
    report = stage_purity_report(_populated_stage_four_purity_train())

    assert report == {
        "stage_4_alkali_mg_cyclone": {
            "stage_number": 4,
            "label": "Na/Mg collector",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {"Mg": 5.5, "Na": 5.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 10.5,
            "impurity_kg": 0.0,
            "total_kg": 10.5,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"Mg": True, "Na": True},
        },
        "stage_4b_k_trap": {
            "stage_number": 4,
            "label": "K trap",
            "accepted_species": ["K"],
            "designated_species_kg": {"K": 9.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 9.0,
            "impurity_kg": 0.0,
            "total_kg": 9.0,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"K": True},
        },
    }


def test_stage_purity_report_pins_empty_stage_four_collapse():
    report = stage_purity_report(
        _populated_stage_four_purity_train(empty_keys=True)
    )

    # The K trap contents previously overwrote the Na/Mg row for empty keys.
    assert report == {
        "stage_4_alkali_mg_cyclone": {
            "stage_number": 4,
            "label": "Na/Mg collector",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {"Mg": 5.5, "Na": 5.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 10.5,
            "impurity_kg": 0.0,
            "total_kg": 10.5,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"Mg": True, "Na": True},
        },
        "stage_4b_k_trap": {
            "stage_number": 4,
            "label": "K trap",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {},
            "coproduct_species_kg": {},
            "impurity_species_kg": {"K": 9.0},
            "designated_kg": 0.0,
            "impurity_kg": 9.0,
            "total_kg": 9.0,
            "purity_fraction": 0.0,
            "verdict": "CONTAMINATED",
            "warning": "non-designated condensate present",
        },
    }


def test_stage_purity_activity_uses_explicit_accepted_species_capture_state():
    train = CondensationTrain.create_default()
    train.stages[2].collected_kg.update({"Cr": 1.0, "Mn": 0.0})

    stage = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[2]]

    assert stage["activity"] == {"Cr": True, "Mn": False}
    assert "Cr2O3" not in stage["activity"]
    assert "activity" not in stage_purity_report(train)[STAGE_KEY_BY_NUMBER[3]]


def test_route_result_records_scaled_stage_impurity_without_changing_capture():
    train = CondensationTrain.create_default()
    model = CondensationModel(train)
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"K": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in train.stages
        },
    )
    flux = EvaporationFlux({"K": 1.0})
    flux.update_totals()

    route = model.route(flux, MeltState(temperature_C=1650.0))
    condensed = route.condensed_for_species("K")
    impurity = sum(
        species_kg.get("K", 0.0)
        for species_kg in route.impurity_by_stage_species.values()
    )

    assert condensed > 0.0
    assert impurity >= 0.0
    total_deposited = condensed + route.wall_deposit_by_species.get("K", 0.0)
    assert route.remaining_by_species["K"] == pytest.approx(1.0 - total_deposited)


def _printed_formation_gibbs(table_id):
    table = yaml.safe_load(
        (
            DATA_DIR / 'literature' / 'compilations' / 'janaf' / 'tables'
            / f'{table_id}.yaml'
        ).read_text()
    )
    rows = []
    for row in table['table']['values']:
        gibbs = row['formation_gibbs_energy']['value']
        rows.append((
            float(row['temperature']['value']),
            None if gibbs is None else float(gibbs),
        ))
    return sorted(rows)


def _gibbs_at(rows, temperature):
    """Linear ΔfG between the adjacent printed rows. No extrapolation."""

    for index, (row_temperature, gibbs) in enumerate(rows):
        if row_temperature == temperature:
            return gibbs
        if row_temperature > temperature:
            left_temperature, left_gibbs = rows[index - 1]
            weight = (temperature - left_temperature) / (
                row_temperature - left_temperature
            )
            return left_gibbs + (gibbs - left_gibbs) * weight
    raise AssertionError(temperature)


def _dissociation_kp_pa(monomer_rows, dimer_rows, temperature_k):
    """K_p = P° exp(ΔG_assoc / (R T)) from the printed formation rows.

    ΔG_assoc = ΔfG°(M2) - 2 ΔfG°(M), kJ/mol. P° = 1e5 Pa. At 313.15 K
    the K2 rows are 300 K and 400 K; that arithmetic is written out in
    test_janaf_kp_at_313_K_is_in_band_and_replaces_the_cea_polynomial.
    """

    delta_g = _gibbs_at(dimer_rows, temperature_k) - 2.0 * _gibbs_at(
        monomer_rows, temperature_k,
    )
    return 1.0e5 * math.exp(delta_g * 1000.0 / (GAS_CONSTANT * temperature_k))


def _potassium_balance_inputs():
    vapor = yaml.safe_load((DATA_DIR / 'vapor_pressures.yaml').read_text())
    families = vapor['families']
    potassium = families['metals_k_family']['physical_properties']['species']['K']
    sticking = yaml.safe_load(
        (DATA_DIR / 'literature' / 'vacuum_pyrolysis_sticking.yaml').read_text()
    )
    antoine = potassium['pure_component_antoine']
    return {
        'A': float(antoine['A']),
        'B': float(antoine['B']),
        'C': float(antoine['C']),
        'molar_mass_k': float(potassium['molar_mass_g_mol']),
        'alpha': float(sticking['species']['K']['value']),
        'k_rows': _printed_formation_gibbs('K-005'),
        'k2_rows': _printed_formation_gibbs('K-011'),
    }


def _antoine_pa(temperature_k, inputs):
    """log10(P/Pa) = A - B / (T + C), including the flagged flank."""

    return 10.0 ** (
        inputs['A'] - inputs['B'] / (temperature_k + inputs['C'])
    )


def _respeciated_monomer_pa(element_partial_pa, kp_pa):
    """Conjugate root of p_M + 2 p_M^2 / K_p = p_elem."""

    if not math.isfinite(element_partial_pa) or element_partial_pa <= 0.0:
        return 0.0
    if math.isinf(kp_pa) and kp_pa > 0.0:
        return element_partial_pa
    if not math.isfinite(kp_pa) or kp_pa <= 0.0:
        return 0.0
    ratio = element_partial_pa / kp_pa
    if ratio > 1.0e16:
        return math.sqrt(kp_pa * element_partial_pa / 2.0)
    return (2.0 * element_partial_pa) / (math.sqrt(1.0 + 8.0 * ratio) + 1.0)


def _element_saturation_pa(saturation_pa, kp_pa):
    """p_elem where both J_M and J_M2 are zero: P_sat + 2 P_sat^2 / K_p."""

    return saturation_pa + 2.0 * saturation_pa * saturation_pa / kp_pa


def _parallel_hk_rate_pa_s(element_partial_pa, strips):
    """dp_elem/dt = -(J_M + 2 J_M2) summed over the wall strips."""

    rate = 0.0
    for k_monomer, k_dimer, saturation_pa, kp_pa in strips:
        monomer_pa = _respeciated_monomer_pa(element_partial_pa, kp_pa)
        drive = monomer_pa - saturation_pa
        if drive <= 0.0:
            continue
        if k_monomer > 0.0:
            rate -= k_monomer * drive
        if k_dimer > 0.0 and math.isfinite(kp_pa) and kp_pa > 0.0:
            dimer_drive = drive * (monomer_pa + saturation_pa) / kp_pa
            if dimer_drive > 0.0:
                rate -= 2.0 * k_dimer * dimer_drive
    return rate


def _next_floor_pa(partial, strips):
    """Hottest p_elem,sat strictly below ``partial``."""

    floor = None
    slack = max(1.0e-15, abs(partial) * 1.0e-14)
    for k_monomer, k_dimer, saturation_pa, kp_pa in strips:
        if k_monomer <= 0.0 and k_dimer <= 0.0:
            continue
        saturated = _element_saturation_pa(saturation_pa, kp_pa)
        if not math.isfinite(saturated) or saturated >= partial - slack:
            continue
        if floor is None or saturated > floor:
            floor = saturated
    return floor


def _rk4_above_floor(pressure, dt, strips, floor):
    """One classical RK4 step, or None if a stage crosses ``floor``.

    Below the floor the flux is clamped to zero, so a stage that
    crosses it is not a sample of the ODE. The floor is not written
    in place of that step.
    """

    k1 = _parallel_hk_rate_pa_s(pressure, strips)
    if not math.isfinite(k1):
        return None
    y2 = pressure + 0.5 * dt * k1
    if floor is not None and y2 < floor:
        return None
    k2 = _parallel_hk_rate_pa_s(y2, strips)
    if not math.isfinite(k2):
        return None
    y3 = pressure + 0.5 * dt * k2
    if floor is not None and y3 < floor:
        return None
    k3 = _parallel_hk_rate_pa_s(y3, strips)
    if not math.isfinite(k3):
        return None
    y4 = pressure + dt * k3
    if floor is not None and y4 < floor:
        return None
    k4 = _parallel_hk_rate_pa_s(y4, strips)
    if not math.isfinite(k4):
        return None
    updated = pressure + dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
    if not math.isfinite(updated):
        return None
    if floor is not None and updated < floor:
        return None
    return updated


def _adaptive_rk4_partial(partial, duration, strips, local_tol):
    """RK4 with step halving. Independent of the production exponential."""

    pressure = float(partial)
    t = 0.0
    for _guard in range(200000):
        remaining = duration - t
        if remaining <= 1.0e-15 * max(1.0, duration):
            return pressure
        floor = _next_floor_pa(pressure, strips)
        # On this strip's floor already. Land and let colder strips
        # keep the residence. An oversized step never takes this branch:
        # the partial has to arrive within local_tol on its own.
        if floor is not None and 0.0 < pressure - floor <= local_tol:
            pressure = floor
            continue
        rate = _parallel_hk_rate_pa_s(pressure, strips)
        if rate >= -1.0e-18 * max(1.0, abs(pressure)) or not math.isfinite(rate):
            return pressure
        dt = remaining
        if floor is not None:
            room = pressure - floor
            if room <= 0.0:
                pressure = floor
                continue
            dt = min(dt, 0.2 * room / abs(rate))
        accepted = None
        accepted_dt = None
        for _halve in range(60):
            full = _rk4_above_floor(pressure, dt, strips, floor)
            if full is None:
                dt *= 0.5
                continue
            mid = _rk4_above_floor(pressure, 0.5 * dt, strips, floor)
            if mid is None:
                dt *= 0.5
                continue
            second = _rk4_above_floor(mid, 0.5 * dt, strips, floor)
            if second is None:
                dt *= 0.5
                continue
            if abs(second - full) <= local_tol:
                accepted = second
                accepted_dt = dt
                break
            dt *= 0.5
        else:
            raise AssertionError('parallel element step did not converge')
        if accepted == pressure or accepted_dt is None:
            return pressure
        pressure = accepted
        t += accepted_dt
    raise AssertionError('parallel element integral did not finish')


def _integrate_parallel_hk_pa(element_partial_pa, residence_s, strips):
    """Converged integral of J_M + 2 J_M2. Not the production integrator.

    Production integrates a local exponential. This is classical RK4.
    A step that would cross the next strip's p_elem,sat is halved; the
    floor is not written into an oversized step. Two successive local
    tolerances must agree to 1e-12 of the inlet partial.
    """

    partial = float(element_partial_pa)
    duration = float(residence_s)
    if partial <= 0.0 or duration <= 0.0 or not strips:
        return partial
    if _parallel_hk_rate_pa_s(partial, strips) == 0.0:
        return partial
    tolerance = 1.0e-12 * max(1.0, abs(partial))
    local = tolerance
    previous = _adaptive_rk4_partial(partial, duration, strips, local)
    for _refine in range(6):
        local *= 0.5
        value = _adaptive_rk4_partial(partial, duration, strips, local)
        if abs(value - previous) < tolerance:
            return value
        previous = value
    raise AssertionError('parallel element integral did not converge')


def _configured_stage_area_m2(model, stage):
    areas = model.stage_area_m2_by_stage
    number = str(int(stage.stage_number))
    key = getattr(stage, 'stage_key', '') or ''
    for candidate in (key, f'stage_{number}', number):
        if candidate and candidate in areas:
            return float(areas[candidate])
    raise AssertionError(f'no configured area for stage {number}')


def _potassium_stage_blocked(stage):
    """Cr harvest blocks K. The Na collector blocks K. The trap accepts it."""

    key = getattr(stage, 'stage_key', '') or ''
    if 'CrO2' in stage.target_species:
        return True
    return key == NA_COLLECTOR_STAGE_KEY


def _allocation_stage_key(stage):
    explicit = getattr(stage, 'stage_key', '') or ''
    if explicit:
        return explicit
    return COLLECTOR_STAGE_KEY_BY_NUMBER[int(stage.stage_number)]


def _potassium_pipe_walls_undersaturated(model, inlet_partial_pa, inputs):
    """A pure-K partial at the default pipe temperature is below P_sat(K)."""

    for segment in model.pipe_segments:
        temperature_k = float(segment.wall_temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        kp_pa = _dissociation_kp_pa(inputs['k_rows'], inputs['k2_rows'], temperature_k)
        monomer_pa = _respeciated_monomer_pa(inlet_partial_pa, kp_pa)
        if monomer_pa > _antoine_pa(temperature_k, inputs):
            return False
    return True


def _independent_potassium_capture_kg(model, *, inlet_partial_pa, supply_kg_hr):
    """Stage kilograms from this test's J_M + 2 J_M2 integral.

    The feed is pure K, so the carrier mole fraction is zero. Dalton's
    law then keeps p_K at the inlet partial after upstream capture: the
    mass falls and the partial does not. q = p_K / N_K uses the arriving
    monomer's molar mass. T_gas is the model's gas temperature, which
    this pin leaves at the pipe default. P_sat and K_p use each wall
    sample. Each form's coefficient is its series conductance. K2's
    accommodation coefficient is the monomer alpha, and K2's
    Chapman-Enskog diameter is the flagged monomer proxy.
    """

    from simulator.condensation import _element_series_coefficients_mol_m2_s_pa

    inputs = _potassium_balance_inputs()
    gas_temperature_k = float(model.gas_temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    supply = float(supply_kg_hr)
    condensed = {}
    impurity = {}
    for stage in sorted(model.train.stages, key=stage_visit_key):
        residence_s = float(model.residence_time_s.get(stage.stage_number, 1.0))
        if _potassium_stage_blocked(stage) or residence_s <= 0.0 or supply <= 1.0e-15:
            continue
        lo_c, hi_c = (float(stage.temp_range_C[0]), float(stage.temp_range_C[1]))
        width_c = hi_c - lo_c
        sample_count = 1 if width_c == 0.0 else HKL_BAND_SAMPLES
        inventory_mol = (
            supply / (inputs['molar_mass_k'] / 1000.0) / 3600.0 * residence_s
        )
        area_scale = (
            inlet_partial_pa / inventory_mol
            * _configured_stage_area_m2(model, stage)
            / sample_count
        )
        k_monomer_per_pa, k_dimer_per_pa = (
            _element_series_coefficients_mol_m2_s_pa(
                monomer='K',
                dimer='K2',
                alpha_s=inputs['alpha'],
                pipe_diameter_m=model._hydraulic_diameter_m_for_stage_number(
                    stage.stage_number,
                ),
                stir_factor=model.stir_factor,
                radial_stir_factor=model.radial_stir_factor,
                regime_factor=model.regime_factor,
                T_gas_K=gas_temperature_k,
                overhead_pressure_pa=float(model.overhead_pressure_mbar) * 100.0,
                carrier_gas=str(model.carrier_gas),
                vapor_pressure_data=model.vapor_pressure_data,
            )
        )
        k_monomer = area_scale * k_monomer_per_pa
        k_dimer = area_scale * k_dimer_per_pa
        strips = []
        for sample in range(sample_count):
            sample_c = (
                lo_c if width_c == 0.0
                else lo_c + width_c * (sample + 0.5) / HKL_BAND_SAMPLES
            )
            sample_k = sample_c + CELSIUS_TO_KELVIN_OFFSET
            kp_pa = _dissociation_kp_pa(inputs['k_rows'], inputs['k2_rows'], sample_k)
            saturation_pa = _antoine_pa(sample_k, inputs)
            if _respeciated_monomer_pa(inlet_partial_pa, kp_pa) > saturation_pa:
                strips.append((k_monomer, k_dimer, saturation_pa, kp_pa))
        if not strips:
            continue
        final_pa = _integrate_parallel_hk_pa(inlet_partial_pa, residence_s, strips)
        eta = (inlet_partial_pa - final_pa) / inlet_partial_pa
        deposit = supply * eta
        # A stage deposit at or below this threshold is not removed.
        if deposit > 1.0e-15:
            stage_key = _allocation_stage_key(stage)
            condensed[stage_key] = {'K': deposit}
            if stage_key != K_TRAP_STAGE_KEY:
                impurity[stage_key] = {'K': deposit}
            supply -= deposit
    return condensed, impurity, supply, _potassium_pipe_walls_undersaturated(
        model, inlet_partial_pa, inputs,
    )


def test_default_k_route_allocation_numeric_pin():
    """Pure-K capture follows the parallel series-conductance balance.

    The expected kilograms are this test's integral of J_M + 2 J_M2.
    Each coefficient is that form's series conductance. K_p comes from
    the printed JANAF rows. P_sat comes from the potassium Antoine
    equation. route() is the system under test, not the oracle.

    The oracle is classical RK4. Production is a local exponential
    whose step tolerance is 1e-10 relative. On the unsaturated spill
    those two integrators differ by about 1e-11 kg. The tolerance
    below accepts that and still rejects a dropped gas-side resistance,
    which moves the trap by about 3e-5 kg.
    """

    model = CondensationModel(CondensationTrain.create_default())
    assert tuple(stage_purity_report(model.train)) == (
        'stage_0', 'stage_1_fe_condenser', 'stage_2_cr_oxide_harvest',
        'stage_3_sio_zone', 'stage_4_alkali_mg_cyclone',
        'stage_5', 'stage_6', 'stage_7', 'stage_4b_k_trap',
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={'K': 1.0},
        stage_area_m2_by_stage={str(s.stage_number): 1.0 for s in model.train.stages},
    )
    condensed, impurity, remaining, walls_undersaturated = (
        _independent_potassium_capture_kg(
            model, inlet_partial_pa=100.0, supply_kg_hr=1.0,
        )
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={'K': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1650.0),
    )
    assert walls_undersaturated
    assert route.wall_deposit_by_species == {}
    assert set(route.condensed_by_stage_species) == set(condensed)
    for stage_key, species_kg in condensed.items():
        actual = route.condensed_by_stage_species[stage_key]
        assert set(actual) == set(species_kg)
        for species, kilograms in species_kg.items():
            assert actual[species] == pytest.approx(
                kilograms, rel=1.0e-6, abs=1.0e-10,
            )
    assert route.remaining_by_species['K'] == pytest.approx(
        remaining, rel=1.0e-6, abs=1.0e-10,
    )
    assert set(route.impurity_by_stage_species) == set(impurity)
    for stage_key, species_kg in impurity.items():
        actual = route.impurity_by_stage_species[stage_key]
        assert set(actual) == set(species_kg)
        for species, kilograms in species_kg.items():
            assert actual[species] == pytest.approx(
                kilograms, rel=1.0e-6, abs=1.0e-10,
            )


def _visit_labels(train: CondensationTrain) -> list[int | str]:
    labels: list[int | str] = []
    for stage in sorted(train.stages, key=stage_visit_key):
        if stage.stage_number == 0 and stage.sub_order == 0:
            continue
        if stage.stage_key == K_TRAP_STAGE_KEY:
            labels.append('4b')
        else:
            labels.append(stage.stage_number)
    return labels


def test_route_visits_k_trap_between_stage_4_and_stage_5():
    """Visit order is route()'s (stage_number, sub_order) sort.

    Stage 0 is walked first and is not part of the pinned product order.
    Setting the trap's stage_number above 7 is the mutation that moves 4b
    after stage 7; sub_order alone cannot, because (4, n) sorts before 5.
    """

    train = CondensationTrain.create_default()
    assert [stage.stage_number for stage in train.stages[:8]] == list(range(8))
    assert train.stages[8].stage_key == K_TRAP_STAGE_KEY
    assert train.stages[8].sub_order == 1
    assert _visit_labels(train) == [1, 2, 3, 4, '4b', 5, 6, 7]
    assert sorted(train.stages, key=stage_visit_key)[0].stage_number == 0

    trap = train.stages[8]
    trap.stage_number = 8
    swapped = _visit_labels(train)
    assert swapped != [1, 2, 3, 4, '4b', 5, 6, 7]
    assert swapped.index('4b') > swapped.index(7)


def _extrapolated_record_at(route, species: str, temperature_K: float):
    matches = [
        (str(key), record)
        for key, record in route.antoine_extrapolations.items()
        if str(key).split('#', 1)[0] == species
        and record.get('temperature_K') == temperature_K
        and record.get('status') == 'extrapolated'
    ]
    assert len(matches) == 1, (species, temperature_K, list(route.antoine_extrapolations))
    return matches[0]


def test_cold_alkali_walls_predict_and_flag_antoine_extrapolation():
    """250 C and 40 C are below the Na and K Antoine bands.

    The route still captures. The notice is the existing
    ``_record_antoine_extrapolation`` record (reason
    ``wall_saturation_pressure_out_of_validated_range``). At these two
    walls ``require_antoine_source_certified_temperature`` does not raise,
    so the ``#wall:`` key is not the record this route writes. Band
    sampling adds unique extrapolation records before the same 523.15 K
    and 313.15 K wall records, whose serial keys are now ``Na#67`` and
    ``K#67``.
    """

    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=2.0,
        species_partial_pressures_mbar={'Na': 1.0, 'K': 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={'Na': 1.0, 'K': 1.0}, total_kg_hr=2.0),
        MeltState(temperature_C=1650.0),
    )

    assert route.condensed_by_stage_species[NA_COLLECTOR_STAGE_KEY]['Na'] > 0.0
    assert route.condensed_by_stage_species[K_TRAP_STAGE_KEY]['K'] > 0.0
    assert 'Na' not in route.impurity_by_stage_species.get(
        NA_COLLECTOR_STAGE_KEY, {})
    assert 'K' not in route.impurity_by_stage_species.get(K_TRAP_STAGE_KEY, {})
    na_key, na_record = _extrapolated_record_at(route, 'Na', 523.15)
    k_key, k_record = _extrapolated_record_at(route, 'K', 313.15)
    assert na_key == 'Na#67'
    assert k_key == 'K#67'
    assert 'Na#wall:523.15' not in route.antoine_extrapolations
    assert 'K#wall:313.15' not in route.antoine_extrapolations
    assert tuple(na_record['valid_range_K']) == (924.0, 1118.0)
    assert tuple(k_record['valid_range_K']) == (679.4, 1033.0)
    for record in (na_record, k_record):
        assert record['authority_level'] == 'extrapolated'
        assert record['reason'] == 'wall_saturation_pressure_out_of_validated_range'
        assert record['status'] == 'extrapolated'
    warnings = ' '.join(route.antoine_extrapolation_warnings)
    assert 'valid_range_K [924, 1118] at 523.15 K' in warnings
    assert 'valid_range_K [679.4, 1033] at 313.15 K' in warnings


def test_k_moves_to_its_trap_and_other_stage_4_destinations_stay():
    assert DESIGNATED_STAGE['K'] == K_TRAP_STAGE_KEY
    assert DESIGNATED_STAGE['Na'] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE['Mg'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['C7']['Ca'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['Mg'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['Na'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['K'] == K_TRAP_STAGE_KEY
    # Public stage number stays 4. The trap is not a renumber of stages 5-7.
    assert product_stage_number('MRE', 'K') == 4
    assert product_stage_number('MRE', 'Na') == 4
    assert target_species_for_stage_number(4) == ['Na', 'Mg']
    train = CondensationTrain.create_default()
    na = train.stages[4]
    trap = train.stages[8]
    assert na.temp_range_C == (250.0, 250.0)
    assert trap.temp_range_C == (40.0, 40.0)
    assert na.target_species == ['Na', 'Mg']
    assert trap.target_species == ['K']
