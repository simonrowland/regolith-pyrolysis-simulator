"""The headspace ledger posts the solve's integrated additions.

Constructed hours, each driven through ``_step_one_hour``. The
vapour reaction extent is the integrated gross. The vapour still in
the headspace is the integral of q. The redox parcel is the integral
of S. The endpoint is recorded and not posted. The pressure gate on
published versus ledger is 1e-9 Pa. These hours are not the canon walk.
"""

from __future__ import annotations

import copy
import math
from contextlib import contextmanager
from dataclasses import dataclass, replace

from engines.builtin.evaporation_transition import _NUMERICAL_FLOOR_KG
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.lots import MaterialLot
from simulator.capacity_coupling import (
    DEFAULT_ABS_TOL_PA,
    CapacityCouplingRefusalError,
    CapacityShadowRefusal,
    headspace_integral_added_kg,
)
from simulator.chemistry.kernel.dto import IntentResult
from simulator.core import (
    FO2_BUFFER_ACCOUNT,
    OXYGEN_RESERVOIR_NOOP_MOL,
    OXYGEN_SPECIES,
    _headspace_buffer_release_roundoff_slack_mol,
)
from simulator.evaporation import (
    headspace_buffer_release_stock_mol,
    production_vapor_molar_mass_g_per_mol,
)
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR
from simulator.state import MOLAR_MASS, CampaignPhase
from tests.chemistry.conftest import _build_sim, _load_yaml
from tests.test_headspace_balance_hour import _hold_temperature, _seed

# Published partials against the ledger. The production inventory
# refusal is 1e-6 Pa. These hours are held to the tighter review gate.
_PRESSURE_GATE_PA = 1.0e-9
_ATOM_BOUND_MOL = 1.0e-12
_POSTED_ABS_KG = 1.0e-18


@dataclass(frozen=True)
class Hour:
    sim: object
    partials_Pa: dict[str, float]
    endpoint_kg_hr: dict[str, float]
    extent_kg: dict[str, float]
    posted_kg: dict[str, dict[str, float]]
    credit_accounts: dict[str, dict[str, str]]
    gaps_Pa: dict[str, float]
    redox_mol: dict[str, float]
    bled_mol: dict[str, float]
    pipe: float
    binding: str
    iterations: int
    shut_path: str | None
    atom_worst_mol: float

    @property
    def total_Pa(self) -> float:
        return math.fsum(self.partials_Pa.values())


def _pressure_total(partials) -> float:
    return math.fsum(
        max(0.0, float(pressure)) for pressure in partials.values()
    )


def _sim(campaign: CampaignPhase, temperature_C: float, downstream_Pa: float):
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    sim.melt.campaign = campaign
    sim._configure_overhead_headspace(campaign)
    # The overhead model is built on first use. The downstream pressure
    # is a config value that use reads. Touching the private model here
    # leaves it unset.
    sim._overhead_headspace_config["downstream_pressure_bar"] = (
        float(downstream_Pa) / PA_PER_BAR
    )
    _hold_temperature(sim, temperature_C)
    return sim


def _seed_pa(sim, species: str, pascals: float, source: str) -> None:
    if pascals <= 0.0:
        return
    moles = (
        pascals
        * sim._headspace_volume_m3()
        / (GAS_CONSTANT * sim._headspace_temperature_K())
    )
    _seed(sim, "process.overhead_gas", {species: moles}, source)


@contextmanager
def _patched(law, fraction_of):
    """One flux law and one capture fraction, on both capture sites.

    The residual reads ``headspace_capture_fractions``. The fixed point
    remeasures ``CondensationModel.route``. The two fractions have to
    agree or the capture move never falls inside the pressure gate.
    """

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.condensation import CondensationModel
    import simulator.capacity_coupling as coupling

    saved = (
        BuiltinEvaporationFluxProvider.dispatch,
        CondensationModel.route,
        coupling.headspace_capture_fractions,
    )

    def dispatch(self, request):
        partials = dict(
            (getattr(request, "control_inputs", None) or {}).get(
                "overhead_partials_Pa"
            )
            or {}
        )
        return IntentResult(
            intent=request.intent,
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": dict(law(partials))},
        )

    original_route = CondensationModel.route

    def route(self, evap_flux, melt):
        result = original_route(self, evap_flux, melt)
        remaining = dict(getattr(result, "remaining_by_species", {}) or {})
        for species, rate in dict(evap_flux.species_kg_hr).items():
            label = str(species)
            if label == OXYGEN_SPECIES:
                continue
            captured = min(1.0, max(0.0, float(fraction_of(label))))
            remaining[label] = max(0.0, float(rate)) * (1.0 - captured)
        return replace(result, remaining_by_species=remaining)

    def fractions(gross, partials_Pa, *, capture_C, fractions_frozen):
        del partials_Pa, capture_C, fractions_frozen
        out = {}
        for species, rate in dict(gross or {}).items():
            label = str(species)
            if label == OXYGEN_SPECIES:
                continue
            if float(rate or 0.0) <= 0.0:
                out[label] = 0.0
                continue
            out[label] = min(1.0, max(0.0, float(fraction_of(label))))
        return out

    BuiltinEvaporationFluxProvider.dispatch = dispatch
    CondensationModel.route = route
    coupling.headspace_capture_fractions = fractions
    try:
        yield
    finally:
        (
            BuiltinEvaporationFluxProvider.dispatch,
            CondensationModel.route,
            coupling.headspace_capture_fractions,
        ) = saved


def _molar_mass_kg(sim, species: str) -> float:
    return resolve_species_formula(
        species,
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()


def _posted(sim, parent: str) -> tuple[dict[str, float], dict[str, str]]:
    """Kilograms and the account of each credited species on one evaporate."""

    kilograms: dict[str, float] = {}
    accounts: dict[str, str] = {}
    for transition in sim.atom_ledger.transitions:
        if f"evaporate_{parent}" not in (
            transition.reason,
            transition.name,
        ):
            continue
        for lot in transition.credits:
            for name, amount in dict(lot.species_kg).items():
                label = str(name)
                kilograms[label] = kilograms.get(label, 0.0) + float(amount)
                if float(amount) > 0.0:
                    accounts[label] = str(lot.account)
    return kilograms, accounts


def _record(sim) -> Hour:
    cap = sim._headspace_capacity_result
    assert cap is not None and cap.authoritative is True
    published = {
        str(species): float(mbar) * 100.0
        for species, mbar in dict(sim.overhead.composition or {}).items()
    }
    hold = dict(sim.atom_ledger.mol_by_account("process.overhead_gas") or {})
    per_mol = (
        GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    ledger = {
        str(species): float(moles) * per_mol
        for species, moles in hold.items()
        if float(moles) != 0.0
    }
    names = set(published) | set(ledger)
    gaps = {
        name: published.get(name, 0.0) - ledger.get(name, 0.0)
        for name in names
    }
    registry = sim.species_formula_registry
    worst = 0.0
    for transition in sim.atom_ledger.transitions:
        debit = transition.debit_atom_moles(registry)
        credit = transition.credit_atom_moles(registry)
        for element in set(debit) | set(credit):
            worst = max(
                worst,
                abs(
                    float(debit.get(element, 0.0))
                    - float(credit.get(element, 0.0))
                ),
            )
    paths = [
        str(notice.get("detail"))
        for notice in cap.species_notices
        if notice.get("reason") == "shut_path"
    ]
    assert len(paths) <= 1
    posted: dict[str, dict[str, float]] = {}
    accounts: dict[str, dict[str, str]] = {}
    for transition in sim.atom_ledger.transitions:
        if not str(transition.reason).startswith("evaporate_"):
            continue
        parent = str(transition.reason).split("evaporate_", 1)[1]
        kilograms, credited = _posted(sim, parent)
        posted[parent] = kilograms
        accounts[parent] = credited
    return Hour(
        sim=sim,
        partials_Pa={
            str(species): float(pressure)
            for species, pressure in cap.partial_pressures_Pa.items()
            if float(pressure) != 0.0
        },
        endpoint_kg_hr={
            str(species): float(rate)
            for species, rate in dict(
                sim._headspace_committed_gross_kg_hr or {}
            ).items()
        },
        extent_kg={
            str(species): float(kilograms)
            for species, kilograms in dict(cap.vapour_extent_kg).items()
            if float(kilograms) > 0.0
        },
        posted_kg=posted,
        credit_accounts=accounts,
        gaps_Pa=gaps,
        redox_mol={
            str(species): float(moles)
            for species, moles in dict(cap.redox_added_mol).items()
            if float(moles) != 0.0
        },
        bled_mol={
            str(species): float(moles)
            for species, moles in dict(cap.bled_species_mol).items()
        },
        pipe=float(cap.saturation.pipe),
        binding=str(cap.saturation.binding_cause),
        iterations=int(cap.iterations),
        shut_path=None if not paths else paths[0],
        atom_worst_mol=worst,
    )


def _step(sim, law, fraction_of) -> Hour:
    with _patched(law, fraction_of):
        sim._step_one_hour()
    return _record(sim)


def _vapour_added_kg(sim, hour: Hour, species: str, pre_mol: float) -> float:
    """∫q for one species, from the production holdup identity.

    added = (n_end − n0 + bled) M. The end partial alone still holds
    the opening seed, so it is not the hour's vapour integral.
    """

    mass_g = production_vapor_molar_mass_g_per_mol(
        sim.vapor_pressures,
        species,
    )
    assert mass_g is not None and mass_g > 0.0
    added = headspace_integral_added_kg(
        {species: hour.partials_Pa.get(species, 0.0)},
        {species: pre_mol},
        {species: hour.bled_mol.get(species, 0.0)},
        {species: float(mass_g) / 1000.0},
        temperature_K=sim._headspace_temperature_K(),
        volume_m3=sim._headspace_volume_m3(),
    )
    return float(added.get(species, 0.0))


def _assert_closed(hour: Hour) -> None:
    assert hour.gaps_Pa, hour.gaps_Pa
    worst = max(abs(gap) for gap in hour.gaps_Pa.values())
    assert worst < _PRESSURE_GATE_PA, hour.gaps_Pa
    assert abs(hour.gaps_Pa.get(OXYGEN_SPECIES, 0.0)) < _PRESSURE_GATE_PA
    assert hour.atom_worst_mol < _ATOM_BOUND_MOL, hour.atom_worst_mol


def _assert_posted_extent(hour: Hour, species: str) -> None:
    extent = float(hour.extent_kg[species])
    posted = float(hour.posted_kg[species][species])
    assert abs(posted - extent) <= _POSTED_ABS_KG, (posted, extent)


def test_sio_sliver_posts_the_integral_and_captured_cro_posts_the_gross():
    """SiO's integral sits inside one floor above an above-floor endpoint.

    CrO's gross is flat and fully captured, so the vapour left behind
    is empty while the endpoint is the gross. Posting that vapour
    integral would drop CrO's oxygen. Posting the endpoint on SiO
    would drop the sliver.
    """

    def sio_law(partials):
        total = _pressure_total(partials)
        return {
            "SiO": 1.2e-11 + 2.5e-12 * math.exp(-total / 4.0e-5),
        }

    sliver = _step(
        _sim(CampaignPhase.C3_NA, 1150.0, 100.0),
        sio_law,
        lambda _species: 0.0,
    )
    endpoint = sliver.endpoint_kg_hr["SiO"]
    extent = sliver.extent_kg["SiO"]
    assert endpoint > _NUMERICAL_FLOOR_KG
    assert 0.0 < extent - endpoint <= _NUMERICAL_FLOOR_KG
    _assert_posted_extent(sliver, "SiO")
    _assert_closed(sliver)
    assert sliver.partials_Pa["SiO"] > 0.0
    assert sliver.partials_Pa[OXYGEN_SPECIES] > 0.0
    assert sliver.shut_path is None

    def cro_law(_partials):
        return {"CrO": 5.0e-12}

    control = _step(
        _sim(CampaignPhase.C3_NA, 1150.0, 100.0),
        cro_law,
        lambda species: 1.0 if species == "CrO" else 0.0,
    )
    assert control.extent_kg["CrO"] == control.endpoint_kg_hr["CrO"]
    assert control.endpoint_kg_hr["CrO"] > _NUMERICAL_FLOOR_KG
    assert control.partials_Pa.get("CrO", 0.0) == 0.0
    remaining = _vapour_added_kg(control.sim, control, "CrO", 0.0)
    assert remaining < control.extent_kg["CrO"]
    _assert_posted_extent(control, "CrO")
    _assert_closed(control)
    assert control.partials_Pa[OXYGEN_SPECIES] > 1.0e-6
    assert control.shut_path is None
    return sliver, control


def test_radau_and_midpoint_hours_post_the_gross_integral():
    """Scenario A integrates below its endpoint. Scenario B captures half.

    A shut rising SiO rate makes the gross integral smaller than the
    endpoint. A falling rate with capture one half leaves a vapour
    integral below the endpoint while the gross integral is above it.
    The ledger posts the gross in both.
    """

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 100.0)
    per_mol = (
        GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    sio_mass = MOLAR_MASS["SiO"] / 1000.0

    def rising(partials):
        pressure = max(0.0, float(partials.get("SiO", 0.0) or 0.0))
        # The review's forcing: 5 * n * M per hour, n = P / (R T / V).
        return {"SiO": 5.0 * (pressure / per_mol) * sio_mass}

    _seed_pa(sim, "SiO", 0.1, "scenario A")
    rising_hour = _step(sim, rising, lambda _species: 0.0)
    assert rising_hour.shut_path == "radau_fallback"
    assert (
        rising_hour.extent_kg["SiO"] + _NUMERICAL_FLOOR_KG
        < rising_hour.endpoint_kg_hr["SiO"]
    )
    _assert_posted_extent(rising_hour, "SiO")
    _assert_closed(rising_hour)
    assert rising_hour.partials_Pa["SiO"] > 1.0

    other = _sim(CampaignPhase.C3_NA, 1150.0, 100.0)

    def falling(partials):
        total = _pressure_total(partials)
        return {"SiO": 2.0e-10 / max(total, 1.0e-6)}

    seed_pa = 1.0e-4
    seed_mol = (
        seed_pa
        * other._headspace_volume_m3()
        / (GAS_CONSTANT * other._headspace_temperature_K())
    )
    _seed_pa(other, "SiO", seed_pa, "scenario B")
    falling_hour = _step(other, falling, lambda _species: 0.5)
    assert falling_hour.shut_path == "midpoint"
    extent = falling_hour.extent_kg["SiO"]
    endpoint = falling_hour.endpoint_kg_hr["SiO"]
    remaining = _vapour_added_kg(other, falling_hour, "SiO", seed_mol)
    assert extent > endpoint
    assert 0.0 < remaining < endpoint, (remaining, endpoint, extent)
    assert remaining < extent
    _assert_posted_extent(falling_hour, "SiO")
    _assert_closed(falling_hour)
    return rising_hour, falling_hour


def test_c4_open_hour_posts_the_integrated_buffer_release():
    """A finite capacity after the vapour commit still posts R.

    The stored capacity is the noop, so the solve saturates and S
    carries the buffer excess. The vapour commit writes a finite
    capacity and the oxidative debit lowers fO2 under the ceiling, so
    the settle's saturation check would post no exchange. The commit
    posts the solve's integrated R. The pipe is open. Ledger overhead
    O2 matches the published partial. The buffer ends at its opening
    plus the parent-oxygen credits, minus the oxidative debits, minus
    R, and it stays non-negative.
    """

    sim = _sim(CampaignPhase.C4, 1160.0, 20.0)
    reservoir = sim.melt.oxygen_reservoir
    upper = sim._melt_redox_fO2_upper_bound_log()
    reservoir.melt_intrinsic_fO2_log = upper
    reservoir.melt_redox_capacity_mol_per_ln_fO2 = 0.0
    sim._melt_redox_source_capacity_mol_per_ln_fO2 = lambda **_kwargs: 1.0
    opening = 5.0
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: opening}, "c4 excess")

    def law(_partials):
        return {"Na2": 1.0e-8, "CrO3": 1.0e-2, "SiO": 1.0e-6}

    hour = _step(
        sim,
        law,
        lambda species: 0.5 if species == "CrO3" else 0.0,
    )
    assert hour.shut_path is None
    assert hour.pipe > 0.0
    assert 20.0 < hour.total_Pa < 40.0
    _assert_closed(hour)
    redox = hour.redox_mol[OXYGEN_SPECIES]
    assert redox > 1.0
    assert not sim._evaporative_redox_is_saturated()
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < upper

    credits = 0.0
    debits = 0.0
    exchanged = 0.0
    exchanges = 0
    for transition in sim.atom_ledger.transitions:
        name = str(transition.name)
        signed = _signed_account_o2_mol(sim, transition, FO2_BUFFER_ACCOUNT)
        if name == "oxygen_reservoir_exchange":
            exchanges += 1
            exchanged += -signed
            continue
        if signed > 0.0:
            credits += signed
        elif signed < 0.0:
            debits += -signed
    assert exchanges == 1
    assert abs(exchanged - redox) <= 1.0e-12 * redox
    end_buffer = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    expected = opening + credits - debits - redox
    assert end_buffer >= 0.0
    assert abs(end_buffer - expected) <= 1.0e-12 * max(redox, 1.0), (
        end_buffer,
        expected,
        credits,
        debits,
        redox,
    )
    assert hour.credit_accounts["Na2"][OXYGEN_SPECIES] == FO2_BUFFER_ACCOUNT
    assert hour.credit_accounts["SiO"][OXYGEN_SPECIES] == "process.overhead_gas"
    # The gross is flat, so the stock S was defined against is the
    # integral. The release does not exceed it. A second formula that
    # only checks the ledger end would stay green if S and the post
    # drifted apart by the same amount.
    stock = float(sim._headspace_buffer_release_stock_mol)
    assert exchanged <= stock + OXYGEN_RESERVOIR_NOOP_MOL
    assert abs(exchanged - stock) <= 1.0e-12 * max(stock, 1.0)
    return hour


def test_open_buffer_hour_posts_the_integral_of_s():
    """An open hour whose incoming headspace is not its end state.

    Na2's oxygen credit follows total pressure. The pipe is open and
    not on the fast root, so the integral of S is not the endpoint
    excess. The saturated settle posts that integral. Na2's parent
    oxygen is credited to the fO2 buffer.
    """

    sim = _sim(CampaignPhase.C4, 1160.0, 20.0)
    reservoir = sim.melt.oxygen_reservoir
    reservoir.melt_redox_capacity_mol_per_ln_fO2 = 0.0
    assert sim._evaporative_redox_is_saturated(internal_o2_capacity_mol=1.0)

    def law(partials):
        return {"Na2": 1.0e-4 * math.exp(-_pressure_total(partials) / 40.0)}

    _seed_pa(sim, "N2", 80.0, "open drain")
    hour = _step(sim, law, lambda _species: 0.0)
    assert hour.shut_path is None
    assert 0.0 < hour.pipe < 0.95
    assert 20.0 < hour.total_Pa < 30.0
    assert hour.partials_Pa.get("N2", 0.0) < 1.0
    extent = hour.extent_kg["Na2"]
    endpoint = hour.endpoint_kg_hr["Na2"]
    assert abs(extent - endpoint) > _NUMERICAL_FLOOR_KG
    _assert_posted_extent(hour, "Na2")
    posted = hour.posted_kg["Na2"]
    accounts = hour.credit_accounts["Na2"]
    assert accounts[OXYGEN_SPECIES] == FO2_BUFFER_ACCOUNT
    assert accounts["Na2"] != FO2_BUFFER_ACCOUNT
    o2_per_kg = posted[OXYGEN_SPECIES] / posted["Na2"]
    end_mol = endpoint * o2_per_kg / _molar_mass_kg(hour.sim, OXYGEN_SPECIES)
    redox = hour.redox_mol[OXYGEN_SPECIES]
    per_mol = (
        GAS_CONSTANT
        * hour.sim._headspace_temperature_K()
        / hour.sim._headspace_volume_m3()
    )
    # The endpoint sample times the booked oxygen ratio is S(end) * dt.
    # The redox state is the integral. This hour keeps them apart.
    assert abs(redox - end_mol) * per_mol > 1.0e-6, (redox, end_mol)
    _assert_closed(hour)
    assert hour.partials_Pa["Na2"] > 1.0
    assert hour.partials_Pa[OXYGEN_SPECIES] > 1.0
    return hour


def test_buffer_release_stock_is_the_excess_above_the_noop():
    """The release stock is the opening plus credits, minus debits and uptake.

    Independent arithmetic: 5 + 0.2 − 0.05 − 0.1 = 5.05 mol. A
    non-positive excess is an empty stock. A figure at or below the
    noop is the same zero the exchange skips. Mutation: dropping the
    noop test posts dust, and clipping only the negative leaves that
    dust as a source.
    """

    noop = OXYGEN_RESERVOIR_NOOP_MOL
    opening = 5.0
    credit = 0.2
    debit = 0.05
    uptake = 0.1
    stock = headspace_buffer_release_stock_mol(
        opening, credit, debit, uptake, noop_mol=noop,
    )
    # 5 + 0.2 - 0.05 - 0.1. Left to right, the same binary64 sum the
    # figures have. 5.05 is not that sum (it lands one ulp high).
    assert stock == 5.0 + 0.2 - 0.05 - 0.1
    assert stock > opening
    assert headspace_buffer_release_stock_mol(
        1.0, 0.0, 2.0, 0.0, noop_mol=noop,
    ) == 0.0
    assert headspace_buffer_release_stock_mol(
        noop, 0.0, 0.0, 0.0, noop_mol=noop,
    ) == 0.0
    assert headspace_buffer_release_stock_mol(
        noop * 0.1, 0.0, 0.0, 0.0, noop_mol=noop,
    ) == 0.0


def test_diagnostic_opening_sample_leaves_the_callback_unchanged():
    """The opening-scale probe does not latch the callback.

    Discovery has already recorded. The probe evaluates partials that
    include the zero-holdup species discovery omitted, so its gross is
    not the discovery gross. Mutation: deleting the arm leaves that
    gross in the holder, and deleting the undo leaves the arm count
    ahead of the undo count.
    """

    import simulator.capacity_coupling as coupling

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 20.0)
    counts = {"arm": 0, "undo": 0}
    original_record = coupling._HeadspaceLaw._record_opening_scales
    original_arm = coupling._DiagnosticFluxSample.arm

    def counting_arm(self, undo):
        if self.armed:
            return original_arm(self, undo)
        counts["arm"] += 1

        def wrapped():
            counts["undo"] += 1
            undo()

        return original_arm(self, wrapped)

    def watching_record(self, n0, dt_s):
        holder = self.extent_holder
        before_holder = (
            copy.deepcopy(holder) if isinstance(holder, dict) else None
        )
        before_partials = copy.deepcopy(
            getattr(sim, "_headspace_publisher_partials_Pa", None)
        )
        before_rates = copy.deepcopy(
            getattr(sim, "_headspace_publisher_rates_at_open_kg_hr", None)
        )
        had_stock = hasattr(sim, "_headspace_buffer_release_stock_mol")
        before_stock = (
            copy.deepcopy(sim._headspace_buffer_release_stock_mol)
            if had_stock
            else None
        )
        original_record(self, n0, dt_s)
        if isinstance(holder, dict):
            assert holder == before_holder
        assert (
            getattr(sim, "_headspace_publisher_partials_Pa", None)
            == before_partials
        )
        assert (
            getattr(sim, "_headspace_publisher_rates_at_open_kg_hr", None)
            == before_rates
        )
        assert hasattr(sim, "_headspace_buffer_release_stock_mol") == had_stock
        if had_stock:
            assert sim._headspace_buffer_release_stock_mol == before_stock

    def law(partials):
        # Discovery's opening partials omit a zero holdup. The probe's
        # partials include every carried species, so this rate moves.
        extra = 1.0e-8 if "Na2" in partials else 0.0
        return {"SiO": 1.0e-6 + extra, "Na2": 1.0e-9}

    coupling._HeadspaceLaw._record_opening_scales = watching_record
    coupling._DiagnosticFluxSample.arm = counting_arm
    try:
        hour = _step(sim, law, lambda _species: 0.0)
    finally:
        coupling._HeadspaceLaw._record_opening_scales = original_record
        coupling._DiagnosticFluxSample.arm = original_arm
    assert counts["arm"] >= 1
    assert counts["undo"] == counts["arm"]
    _assert_closed(hour)
    return hour


def _fast_law(partials) -> float:
    return 2.0e-8 * math.exp(-_pressure_total(partials) / 2.0)


def test_fast_root_books_the_end_rate_for_the_hour():
    """The fast root holds q fixed, so the integral is the end rate.

    The rate at the 5 Pa opening is not the rate at the drained end.
    A slow integral of this law would not equal the endpoint. The
    admitted root still books the end rate, bit for bit with the
    endpoint sample, on a one-hour tick.
    """

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 0.0)

    def law(partials):
        return {"SiO": _fast_law(partials)}

    _seed_pa(sim, "SiO", 5.0, "fast opening")
    hour = _step(sim, law, lambda _species: 0.0)
    assert hour.shut_path is None
    assert hour.pipe > 0.99
    assert hour.total_Pa < 0.1
    extent = hour.extent_kg["SiO"]
    endpoint = hour.endpoint_kg_hr["SiO"]
    assert extent == endpoint
    opening = _fast_law({"SiO": 5.0})
    assert abs(extent - opening) > 1.0e-9
    assert abs(extent - _fast_law(hour.partials_Pa)) < 1.0e-15
    _assert_posted_extent(hour, "SiO")
    _assert_closed(hour)
    # Bitwise with the hour recorded before the buffer debit. The
    # law has no oxygen-consuming vapour, so moving that debit and
    # restoring the oxygen clamp must not move a published figure.
    assert sim._headspace_balance_terms["path"] == "fast_root"
    assert hour.iterations == 102
    assert hour.pipe == 0.9959780454515504
    assert hour.total_Pa == 6.624917521208937e-06
    assert hour.partials_Pa[OXYGEN_SPECIES] == 2.0e-06
    assert hour.partials_Pa["SiO"] == 4.6249175212089375e-06
    assert hour.extent_kg["SiO"] == 1.9999933750338163e-08
    return hour


def _signed_account_o2_mol(sim, transition, account: str) -> float:
    """Signed O2 moles one transition posted to one account."""

    registry = sim.species_formula_registry
    net = 0.0
    for lot in transition.credits:
        if str(lot.account) != account:
            continue
        net += float(
            lot.species_moles_for(registry).get(OXYGEN_SPECIES, 0.0) or 0.0
        )
    for lot in transition.debits:
        if str(lot.account) != account:
            continue
        net -= float(
            lot.species_moles_for(registry).get(OXYGEN_SPECIES, 0.0) or 0.0
        )
    return net


def test_open_hour_counts_vapour_coproduct_once():
    """A vapour coproduct and a buffer excess share one open hour.

    SiO's oxygen is overhead gas at once. Na2's oxygen is the buffer
    excess the saturated settle exchanges. CrO2 draws its oxidant
    from the fO2 buffer and returns oxygen to the overhead when
    captured. That return is overhead gas. The debit is not. The
    solve's O2 addition is the ledger's addition. Counting the
    coproduct in the buffer credit as well as in q leaves a gap
    equal to that coproduct.
    """

    sim = _sim(CampaignPhase.C4, 1160.0, 0.0)
    sim.melt.oxygen_reservoir.melt_redox_capacity_mol_per_ln_fO2 = 0.0
    assert sim._evaporative_redox_is_saturated(internal_o2_capacity_mol=1.0)
    # CrO2 draws buffer oxygen. An empty buffer drops that vapour
    # before the credit sees it. The buffer seed keeps the draw.
    # The overhead seed is the opening holdup. Downstream vacuum
    # admits the fast root.
    _seed(sim, "process.overhead_gas", {OXYGEN_SPECIES: 1.0e-5}, "coproduct carrier")
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: 1.0e-5}, "coproduct oxidant")

    def law(_partials):
        return {"SiO": 1.0e-11, "Na2": 1.0e-6, "CrO2": 1.0e-7}

    def fraction(species):
        return 1.0 if species == "CrO2" else 0.0

    hour = _step(sim, law, fraction)
    assert hour.shut_path is None
    assert sim._headspace_balance_terms["path"] == "fast_root"
    _assert_closed(hour)

    terms = sim._headspace_balance_terms
    assert isinstance(terms, dict)
    added = float(terms["A_mol"][OXYGEN_SPECIES])
    opening = float(terms["n0_mol"][OXYGEN_SPECIES])
    bled = float(terms["B_mol"][OXYGEN_SPECIES])
    ledger = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    # The ledger's addition over the solved hour. n0 is the holdup
    # the solve opened on, and B is the bleed it booked. What remains
    # is what the hour added.
    ledger_addition = ledger - opening + bled
    scale = max(abs(added), abs(ledger_addition))
    assert scale > 0.0
    assert abs(added - ledger_addition) <= 1.0e-15 * scale, (
        added,
        ledger_addition,
        added - ledger_addition,
    )

    coproduct = 0.0
    returned = 0.0
    buffer_debit = 0.0
    overhead_debit = 0.0
    exchanged = 0.0
    for transition in sim.atom_ledger.transitions:
        name = str(transition.name)
        overhead_o2 = _signed_account_o2_mol(
            sim, transition, "process.overhead_gas"
        )
        buffer_o2 = _signed_account_o2_mol(
            sim, transition, FO2_BUFFER_ACCOUNT
        )
        if name == "evaporate_SiO":
            coproduct += overhead_o2
        elif name == "evaporate_CrO2":
            buffer_debit += buffer_o2
            overhead_debit += overhead_o2
        elif name == "condense_CrO2":
            returned += overhead_o2
        elif name == "oxygen_reservoir_exchange":
            exchanged += overhead_o2
    per_mol = (
        GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    # The coproduct on its own is wider than the inventory gate.
    # A double count of it is the refusal. The end holdup is larger
    # than this coproduct, so the bleed is not clipped down to it.
    assert coproduct * per_mol > DEFAULT_ABS_TOL_PA, coproduct
    assert buffer_debit < 0.0, buffer_debit
    assert abs(overhead_debit) < 1.0e-15, overhead_debit
    assert returned > 0.0, returned
    buffer_mol = (
        hour.posted_kg["Na2"][OXYGEN_SPECIES]
        / _molar_mass_kg(sim, OXYGEN_SPECIES)
    )
    assert hour.credit_accounts["Na2"][OXYGEN_SPECIES] == FO2_BUFFER_ACCOUNT
    assert hour.credit_accounts["SiO"][OXYGEN_SPECIES] == "process.overhead_gas"
    assert buffer_mol > 1.0e-9, buffer_mol
    assert exchanged > 1.0e-9, exchanged
    return hour


def test_open_hour_counts_oxidative_oxygen_with_its_return():
    """CrO3 consumes buffer oxygen and condensation returns part of it.

    The capture is one half, so the overhead return is not the debit.
    The debit is on the fO2 buffer. SiO and CrO still release a
    coproduct, CrO2's captured oxygen comes back to the overhead, and
    Na2 is a buffer excess on the same open hour. The solve's O2
    addition is the ledger's overhead addition.
    """

    sim = _sim(CampaignPhase.C4, 1250.0, 0.0)
    sim.melt.oxygen_reservoir.melt_redox_capacity_mol_per_ln_fO2 = 0.0
    assert sim._evaporative_redox_is_saturated(internal_o2_capacity_mol=1.0)
    # The consumers draw buffer oxygen. An empty buffer drops them.
    # The overhead seed is the opening holdup. Downstream vacuum
    # admits the fast root.
    _seed(sim, "process.overhead_gas", {OXYGEN_SPECIES: 1.0e-5}, "oxidative carrier")
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: 1.0e-3}, "oxidative oxidant")
    buffer_before = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )

    def law(_partials):
        return {
            "SiO": 1.0e-11,
            "CrO": 1.0e-11,
            "CrO2": 1.0e-9,
            "CrO3": 1.0e-10,
            "Na2": 1.0e-6,
        }

    def fraction(species):
        if species == "CrO2":
            return 1.0
        if species == "CrO3":
            return 0.5
        return 0.0

    hour = _step(sim, law, fraction)
    assert hour.shut_path is None
    assert hour.pipe > 0.0
    assert sim._headspace_balance_terms["path"] == "fast_root"
    _assert_closed(hour)

    terms = sim._headspace_balance_terms
    added = float(terms["A_mol"][OXYGEN_SPECIES])
    opening = float(terms["n0_mol"][OXYGEN_SPECIES])
    bled = float(terms["B_mol"][OXYGEN_SPECIES])
    redox = float(terms["R_mol"][OXYGEN_SPECIES])
    ledger = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    ledger_addition = ledger - opening + bled
    scale = max(abs(added), abs(ledger_addition))
    assert scale > 0.0
    assert abs(added - ledger_addition) <= 1.0e-15 * scale, (
        added,
        ledger_addition,
        added - ledger_addition,
    )

    debit = 0.0
    overhead_evaporate = 0.0
    returned = 0.0
    vapour_overhead = 0.0
    exchanged = 0.0
    cro3 = None
    for transition in sim.atom_ledger.transitions:
        name = str(transition.name)
        overhead_o2 = _signed_account_o2_mol(
            sim, transition, "process.overhead_gas"
        )
        buffer_o2 = _signed_account_o2_mol(
            sim, transition, FO2_BUFFER_ACCOUNT
        )
        if name == "evaporate_CrO3":
            debit += buffer_o2
            overhead_evaporate += overhead_o2
            cro3 = transition
        elif name == "condense_CrO3":
            returned += overhead_o2
        if name in {
            "evaporate_SiO",
            "evaporate_CrO",
            "evaporate_CrO2",
            "condense_CrO2",
            "evaporate_CrO3",
            "condense_CrO3",
        }:
            vapour_overhead += overhead_o2
        elif name == "oxygen_reservoir_exchange":
            exchanged += overhead_o2
    per_mol = (
        GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    # The debit is on the buffer. Half of it comes back to the
    # overhead, and that return is wider than the inventory gate.
    assert cro3 is not None
    assert debit < 0.0, debit
    assert abs(overhead_evaporate) <= 1.0e-15, overhead_evaporate
    assert returned > 0.0, returned
    assert returned * per_mol > DEFAULT_ABS_TOL_PA, returned
    assert vapour_overhead > 0.0, vapour_overhead
    buffer_after = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    posted_buffer = 0.0
    for transition in sim.atom_ledger.transitions:
        posted_buffer += _signed_account_o2_mol(
            sim, transition, FO2_BUFFER_ACCOUNT
        )
    buffer_move = buffer_after - buffer_before
    assert abs(buffer_move - posted_buffer) <= 1.0e-15 * max(
        abs(buffer_move), abs(posted_buffer), 1.0
    )
    redox_terms = sim._evaporative_redox_source_terms_from_transition(cro3)
    # The buffer debit is reducing. `debit` is the signed posting.
    reducing = float(
        redox_terms["redox_source:evaporative_oxygen_loss"]
    )
    assert reducing < 0.0
    assert abs(reducing - debit) <= 1.0e-15 * max(abs(reducing), abs(debit))
    stored = dict(
        sim.melt.oxygen_reservoir.redox_source_terms_mol_o2_equiv or {}
    )
    skipped = dict(
        sim.melt.oxygen_reservoir.redox_source_skipped_terms_mol_o2_equiv
        or {}
    )
    assert (
        "redox_source:evaporative_oxygen_loss" in stored
        or "redox_source:evaporative_oxygen_loss" in skipped
    )
    assert math.isfinite(sim._current_melt_redox_fO2_log())
    from simulator.evaporation import _oxidative_vapour_o2_available_kg

    available_kg = _oxidative_vapour_o2_available_kg(sim.atom_ledger)
    buffer_kg = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert available_kg == buffer_kg
    # The buffer excess is the exchange. R is that excess, so the
    # vapour net is the part of A the exchange does not carry.
    assert abs(redox - exchanged) <= 1.0e-15 * max(abs(redox), abs(exchanged))
    assert hour.credit_accounts["Na2"][OXYGEN_SPECIES] == FO2_BUFFER_ACCOUNT
    assert hour.credit_accounts["SiO"][OXYGEN_SPECIES] == "process.overhead_gas"
    assert exchanged > 1.0e-9, exchanged
    return hour


def open_fast_hour():
    """Step the fast hour and return the simulator.

    The base-versus-candidate dump reads published partials and ledger
    moles from this hour. It does not read the extent fields.
    """

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 0.0)

    def law(partials):
        return {"SiO": _fast_law(partials)}

    _seed_pa(sim, "SiO", 5.0, "fast opening")
    with _patched(law, lambda _species: 0.0):
        sim._step_one_hour()
    return sim


def _ledger_addition_matches(sim) -> None:
    terms = sim._headspace_balance_terms
    added = float(terms["A_mol"][OXYGEN_SPECIES])
    opening = float(terms["n0_mol"][OXYGEN_SPECIES])
    bled = float(terms["B_mol"][OXYGEN_SPECIES])
    ledger = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    ledger_addition = ledger - opening + bled
    scale = max(abs(added), abs(ledger_addition))
    assert scale > 0.0 or abs(added) == 0.0
    if scale == 0.0:
        assert added == 0.0
        assert ledger_addition == 0.0
        return
    assert abs(added - ledger_addition) <= 1.0e-15 * scale, (
        added,
        ledger_addition,
        added - ledger_addition,
    )


def test_first_c3_na_hour_publishes_without_an_oxygen_cap():
    """The reviewer's first C3_NA hour publishes.

    Open pipe, downstream 0, 1150 C, opening overhead O2 at the
    review pressure. CrO2 and CrO3 are negative. Their oxidant is
    a seeded fO2 buffer, so the vapours form. The overhead is not
    debited, its O2 stays non-negative, and the solve addition is
    the ledger addition.
    """

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 0.0)
    sim.melt.oxygen_reservoir.melt_redox_capacity_mol_per_ln_fO2 = 0.0
    _seed_pa(sim, OXYGEN_SPECIES, 15.456162335355332, "review opening")
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: 1.0e-3}, "c3 oxidant")

    def law(_partials):
        return {"CrO2": 1.0e-8, "CrO3": 1.0e-7}

    def fraction(species):
        if species == "CrO2":
            return 1.0
        if species == "CrO3":
            return 0.5
        return 0.0

    hour = _step(sim, law, fraction)
    assert hour.shut_path is None
    assert hour.iterations < 100_000
    assert sim._headspace_balance_terms["path"] != "headspace_balance_work_cap"
    _assert_closed(hour)
    _ledger_addition_matches(sim)
    end_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert end_o2 >= 0.0
    assert hour.partials_Pa.get(OXYGEN_SPECIES, 0.0) >= 0.0
    assert hour.extent_kg.get("CrO2", 0.0) > 0.0
    assert hour.extent_kg.get("CrO3", 0.0) > 0.0
    overhead_debit = 0.0
    buffer_debit = 0.0
    for transition in sim.atom_ledger.transitions:
        if str(transition.name) not in {"evaporate_CrO2", "evaporate_CrO3"}:
            continue
        overhead_debit += _signed_account_o2_mol(
            sim, transition, "process.overhead_gas"
        )
        buffer_debit += _signed_account_o2_mol(
            sim, transition, FO2_BUFFER_ACCOUNT
        )
    assert buffer_debit < 0.0, buffer_debit
    assert abs(overhead_debit) <= 1.0e-15, overhead_debit
    return hour


def test_empty_buffer_forms_no_cro2_or_cro3():
    """An empty fO2 buffer drops both oxidative chromium vapours."""

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 0.0)
    opening_kg = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert opening_kg <= 1.0e-12, opening_kg
    _seed_pa(sim, OXYGEN_SPECIES, 15.456162335355332, "empty-buffer opening")

    def law(_partials):
        return {"CrO2": 1.0e-8, "CrO3": 1.0e-7}

    hour = _step(sim, law, lambda _species: 0.0)
    assert hour.shut_path is None
    names = {str(transition.name) for transition in sim.atom_ledger.transitions}
    assert "evaporate_CrO2" not in names
    assert "evaporate_CrO3" not in names
    assert hour.extent_kg.get("CrO2", 0.0) == 0.0
    assert hour.extent_kg.get("CrO3", 0.0) == 0.0
    end_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert end_o2 >= 0.0
    end_buffer = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert end_buffer <= 1.0e-12
    return hour


def test_empty_buffer_drops_a_subfloor_oxidative_rate():
    """A CrO3 rate at or below 1e-12 kg/h still sees an empty buffer.

    The parent floor leaves the rate out of the debit map. The supply
    map used to keep it, and the headspace posted it onto a buffer the
    policy allows down to -1e15 kg. The empty-buffer gate now zeros
    that supply. A draw whose mass this step exceeds a positive stock
    is the same zero. A covered sub-floor rate stays the supply.
    """

    sim = _sim(CampaignPhase.C3_NA, 1150.0, 0.0)
    melt_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    covered: dict[str, float] = {}
    covered_rates = sim._analytic_evaporation_depletion_rates(
        {"CrO3": 5.0e-13},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg=melt_kg,
        available_o2_kg=1.0e-3,
        supply_out=covered,
    )
    assert "CrO3" not in covered_rates
    assert covered["CrO3"] == 5.0e-13
    empty_supply: dict[str, float] = {}
    empty_rates = sim._analytic_evaporation_depletion_rates(
        {"CrO3": 5.0e-13},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg=melt_kg,
        available_o2_kg=0.0,
        supply_out=empty_supply,
    )
    assert "CrO3" not in empty_rates
    assert empty_supply["CrO3"] == 0.0
    # draw_kg/h * dt_h exceeds 2e-12 kg. The rate is not posted.
    over_supply: dict[str, float] = {}
    over_rates = sim._analytic_evaporation_depletion_rates(
        {"CrO3": 5.0e-13},
        dt_hr=1.0e6,
        phase_scalar=1.0,
        cleaned_melt_kg=melt_kg,
        available_o2_kg=2.0e-12,
        supply_out=over_supply,
    )
    assert "CrO3" not in over_rates
    assert over_supply["CrO3"] == 0.0

    opening_kg = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert opening_kg <= 1.0e-12, opening_kg
    _seed_pa(sim, OXYGEN_SPECIES, 15.456162335355332, "subfloor empty buffer")

    def law(_partials):
        return {"CrO3": 5.0e-13}

    hour = _step(sim, law, lambda _species: 0.0)
    assert hour.shut_path is None
    assert sim._headspace_balance_terms["path"] == "fast_root"
    names = {str(transition.name) for transition in sim.atom_ledger.transitions}
    assert "evaporate_CrO3" not in names
    end_buffer_mol = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert end_buffer_mol >= 0.0
    assert end_buffer_mol > -1.0e-15
    return hour


def test_oxidative_supplies_cannot_silently_overdraw_the_buffer():
    """Every oxidative supply passes the empty-buffer gate.

    A CrO3 rate of 1.1e-12 kg/h clears the product floor and misses
    the parent-draw floor, so it used to stay out of both oxidative
    lists. A parent stock of 5e-13 kg leaves a positive product the
    debit map does not take. A saturated CrO2 draw clips to just
    under the stock and used to leave a sub-floor CrO3 draw compared
    with the opening stock. Each of those posted a negative buffer.
    The gate zeros the supply that does not fit. A covered rate and
    a non-oxidative SiO rate stay. The empty-buffer hour does not
    post the 1.1e-12 kg/h CrO3.
    """

    sim = _sim(CampaignPhase.C4, 1250.0, 0.0)
    melt_kg = dict(sim.atom_ledger.kg_by_account("process.cleaned_melt"))

    def _supply(rates, available, melt=None):
        out: dict[str, float] = {}
        sim._analytic_evaporation_depletion_rates(
            rates,
            dt_hr=1.0,
            phase_scalar=1.0,
            cleaned_melt_kg=melt if melt is not None else melt_kg,
            available_o2_kg=available,
            supply_out=out,
        )
        return out

    assert _supply({"CrO3": 1.1e-12}, 0.0)["CrO3"] == 0.0
    assert _supply({"CrO3": 1.1e-12}, 1.0e-3)["CrO3"] == 1.1e-12

    parent = ""
    for bucket in ("metals", "oxide_vapors"):
        row = (sim.vapor_pressures.get(bucket) or {}).get("CrO3") or {}
        if row.get("parent_oxide"):
            parent = str(row["parent_oxide"])
            break
    assert parent
    thin = dict(melt_kg)
    thin[parent] = 5.0e-13
    assert _supply({"CrO3": 1.0e-6}, 0.0, thin).get("CrO3", 0.0) == 0.0
    assert _supply({"CrO3": 1.0e-6}, 1.0e-3, thin)["CrO3"] > 0.0

    clipped = _supply({"CrO2": 1.0, "CrO3": 5.0e-13}, 1.0e-6)
    assert clipped.get("CrO3", 0.0) == 0.0
    assert clipped["CrO2"] > 0.0
    assert _supply({"SiO": 5.0e-13}, 0.0)["SiO"] == 5.0e-13

    opening_kg = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert opening_kg <= 1.0e-12, opening_kg
    _seed_pa(sim, OXYGEN_SPECIES, 15.456162335355332, "parent-draw opening")

    def law(_partials):
        return {"CrO3": 1.1e-12}

    hour = _step(sim, law, lambda _species: 0.0)
    assert hour.shut_path is None
    names = {str(transition.name) for transition in sim.atom_ledger.transitions}
    assert "evaporate_CrO3" not in names
    end_buffer_kg = float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert end_buffer_kg >= 0.0
    return hour


def test_refusing_solve_passes_its_iterations():
    """A path-bearing nonconvergence reports the solver's iterations.

    The shadow helper used to default the count to 0 when the caller
    passed only the path. A failed Radau step's nfev is the count a
    finished step reports. The commit copies that count onto the
    refusal, with the flux and integrator counts beside it.
    """

    from unittest.mock import patch

    import simulator.capacity_coupling as coupling
    from simulator.thermal_train import NoColdTrain

    molar = MOLAR_MASS["O2"] / 1000.0
    temperature_K = 1904.7700354769336
    volume_m3 = 0.08538380762699734

    class _Failed:
        success = False
        nfev = 17
        message = "forced"

    def _fail(*_args, **_kwargs):
        return _Failed()

    built = coupling._build_law(
        pre_holdup_mol={"O2": 1.0e-8},
        molar_mass_kg_mol={"O2": molar},
        flux_kg_hr_at_partials=lambda _partials: {"O2": 1.0e-6},
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        temperature_K=temperature_K,
        volume_m3=volume_m3,
        dt_hr=1.0,
        downstream_pressure_Pa=0.0,
        pipe_diameter_m=0.12,
        pipe_length_m=1.0,
        outflow_cap_kg_s=None,
        redox_source_mol_s=None,
    )
    assert not isinstance(built, coupling.CapacityShadowRefusal)
    law, n0 = built
    with patch("scipy.integrate.solve_ivp", _fail):
        try:
            coupling._slow_converge(law, n0, 3600.0)
        except coupling._BalanceRefused as refused:
            shadow = coupling._refused_shadow(law, refused)
        else:
            raise AssertionError("slow converge returned")
    assert law.balance_path == "radau_fallback"
    assert law.balance_iterations == 17
    assert shadow.path == "radau_fallback"
    assert shadow.iterations == 17

    def _refuse(one_law, *_args, **_kwargs):
        one_law.balance_path = "fast_root"
        one_law.balance_iterations = 17
        raise coupling._BalanceRefused(
            "headspace_balance_nonconvergence",
            flux_evaluations=11,
            integrator_evaluations=13,
        )

    with patch.object(coupling, "_commit_one", _refuse):
        committed = coupling._commit_headspace_balance(
            pre_holdup_mol={"O2": 1.0e-8},
            molar_mass_kg_mol={"O2": molar},
            flux_kg_hr_at_partials=lambda _partials: {"O2": 1.0e-6},
            capacity=NoColdTrain("runtime_enforcement_disabled"),
            temperature_K=temperature_K,
            volume_m3=volume_m3,
            dt_hr=1.0,
            downstream_pressure_Pa=0.0,
            pipe_diameter_m=0.12,
            pipe_length_m=1.0,
            outflow_cap_kg_s=None,
            redox_source_mol_s=None,
            vessel_rating_Pa=None,
        )
    assert isinstance(committed, CapacityShadowRefusal)
    assert committed.path == "fast_root"
    assert committed.iterations == 17
    assert committed.flux_evaluations == 11
    assert committed.integrator_evaluations == 13


def test_buffer_debit_is_the_redox_the_next_hour_reads():
    """A CrO3 debit reduces the buffer and lowers the melt fO2.

    The capacity is one mol per ln, below the gas-pressure ceiling,
    so the source is applied. Oxygen leaving the redox reservoir
    makes the melt more reducing. The next hour reads
    ``_current_melt_redox_fO2_log`` and the buffer kilograms the
    clip freezes.
    """

    from simulator.evaporation import _oxidative_vapour_o2_available_kg

    sim = _sim(CampaignPhase.C3_NA, 1250.0, 0.0)
    sim._refresh_oxygen_reservoir_without_exchange(
        melt_intrinsic_fO2_log=-10.0,
        reference_T_K=1523.15,
    )
    sim._melt_redox_source_capacity_mol_per_ln_fO2 = lambda **_kwargs: 1.0
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: 1.0e-3}, "redox oxidant")
    sp_data = (
        (sim.vapor_pressures.get("metals") or {}).get("CrO3")
        or (sim.vapor_pressures.get("oxide_vapors") or {}).get("CrO3")
        or {}
    )
    assert sp_data, "CrO3 vapour data missing"
    before_f = sim._current_melt_redox_fO2_log()
    before_mol = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT)[OXYGEN_SPECIES]
    )
    _credited, transition = sim._credit_evaporation_transition(
        "CrO3",
        1.0e-6,
        1.0e-6,
        sp_data,
        return_transition=True,
    )
    assert transition is not None
    after_mol = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    debit_mol = before_mol - after_mol
    assert debit_mol > 0.0, (before_mol, after_mol)
    terms = sim._evaporative_redox_source_terms_from_transition(transition)
    reducing = float(terms["redox_source:evaporative_oxygen_loss"])
    assert reducing < 0.0
    assert abs(reducing - (-debit_mol)) <= 1.0e-15 * debit_mol
    after_f = sim._current_melt_redox_fO2_log()
    # The reservoir stores ln, then the log10 round trip. The next
    # hour reads the log10 figure. The gap is that round trip.
    # fO2 falls by the debit per mol of capacity.
    delta_ln = (after_f - before_f) * math.log(10.0)
    assert after_f < before_f
    assert abs(delta_ln - (-debit_mol)) <= 1.0e-9 * debit_mol, (
        before_f,
        after_f,
        delta_ln,
        debit_mol,
    )
    assert _oxidative_vapour_o2_available_kg(sim.atom_ledger) == float(
        sim.atom_ledger.kg_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )


def test_balance_refusals_carry_the_buffer_write_log():
    """Negative bleed and nonconvergence carry the inventory payload.

    The write log records overhead O2 and fO2-buffer O2. Each row has
    the buffer balance before and after that lot. The solver's path
    wins when it set one. A bare negative bleed takes the path the
    solve stashed. The inventory reason is not replaced when the bleed
    booker would itself refuse.
    """

    from types import SimpleNamespace

    sim = _sim(CampaignPhase.C4, 1250.0, 0.0)
    _seed(sim, FO2_BUFFER_ACCOUNT, {OXYGEN_SPECIES: 1.0e-2}, "diagnostic buffer")
    before = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT)[OXYGEN_SPECIES]
    )
    moved = 1.0e-3
    mass = resolve_species_formula(
        OXYGEN_SPECIES,
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    sim._headspace_overhead_o2_writes = []
    sim.atom_ledger._overhead_o2_write_listener = sim._record_overhead_o2_write
    sim.atom_ledger.transfer(
        "oxygen_reservoir_exchange",
        debits=(
            MaterialLot(
                FO2_BUFFER_ACCOUNT,
                {OXYGEN_SPECIES: moved * mass},
                source="diagnostic exchange",
            ),
        ),
        credits=(
            MaterialLot(
                "process.overhead_gas",
                {OXYGEN_SPECIES: moved * mass},
                source="diagnostic exchange",
            ),
        ),
        reason="diagnostic exchange",
    )
    after = float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT)[OXYGEN_SPECIES]
    )
    assert abs((before - after) - moved) <= 1.0e-12 * moved
    writes = list(sim._headspace_overhead_o2_writes)
    accounts = {row["account"] for row in writes}
    assert accounts == {"process.overhead_gas", FO2_BUFFER_ACCOUNT}
    buffer_rows = [
        row for row in writes if row["account"] == FO2_BUFFER_ACCOUNT
    ]
    assert len(buffer_rows) == 1
    assert buffer_rows[0]["mol"] < 0.0
    assert abs(buffer_rows[0]["buffer_o2_mol_before"] - before) <= 1.0e-12
    assert abs(buffer_rows[0]["buffer_o2_mol_after"] - after) <= 1.0e-12
    overhead_rows = [
        row for row in writes if row["account"] == "process.overhead_gas"
    ]
    assert overhead_rows[0]["mol"] > 0.0
    assert abs(overhead_rows[0]["buffer_o2_mol_before"] - before) <= 1.0e-12
    assert (
        overhead_rows[0]["buffer_o2_mol_before"]
        == overhead_rows[0]["buffer_o2_mol_after"]
    )

    sim._headspace_capacity_result = SimpleNamespace(
        bled_species_mol={OXYGEN_SPECIES: -1.0},
    )
    sim._headspace_balance_terms = {
        "path": "fast_root",
        "iterations": 7,
        "n0_mol": {OXYGEN_SPECIES: before},
        "A_mol": {OXYGEN_SPECIES: 0.0},
        "B_mol": {OXYGEN_SPECIES: -1.0},
        "R_mol": {OXYGEN_SPECIES: 0.0},
        "G_kg": {},
    }
    negative = sim._headspace_diagnostic_refusal(
        CapacityShadowRefusal("headspace_negative_bleed", 0)
    )
    assert negative.reason == "headspace_negative_bleed"
    assert negative.path == "fast_root"
    assert negative.iterations == 7
    assert negative.species_balance[OXYGEN_SPECIES]["bleed_posted_mol"] == -1.0
    assert negative.species_balance[OXYGEN_SPECIES]["n0_mol"] == before
    overhead_mol = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    assert negative.species_balance[OXYGEN_SPECIES]["ledger_mol"] == overhead_mol
    logged = negative.overhead_o2_writes
    assert logged[0]["buffer_o2_mol_before"] == writes[0]["buffer_o2_mol_before"]
    assert any(row["account"] == FO2_BUFFER_ACCOUNT for row in logged)

    nonconvergence = sim._headspace_diagnostic_refusal(
        CapacityShadowRefusal(
            "headspace_balance_nonconvergence",
            4,
            path="radau_fallback",
            cap="integrator",
            flux_evaluations=3,
            integrator_evaluations=4,
        )
    )
    assert nonconvergence.reason == "headspace_balance_nonconvergence"
    assert nonconvergence.path == "radau_fallback"
    assert nonconvergence.iterations == 4
    assert nonconvergence.cap == "integrator"
    assert nonconvergence.flux_evaluations == 3
    assert nonconvergence.integrator_evaluations == 4
    assert nonconvergence.species_balance is not None
    assert nonconvergence.overhead_o2_writes

    # The production caller used to pass the path and leave iterations
    # at the default 0. That 0 replaced the stashed count. The path
    # still wins. The count stays the one the solve wrote.
    defaulted = sim._headspace_diagnostic_refusal(
        CapacityShadowRefusal(
            "headspace_balance_nonconvergence",
            0,
            path="radau_fallback",
            flux_evaluations=11,
            integrator_evaluations=13,
        )
    )
    assert defaulted.reason == "headspace_balance_nonconvergence"
    assert defaulted.path == "radau_fallback"
    assert defaulted.iterations == 7
    assert defaulted.flux_evaluations == 11
    assert defaulted.integrator_evaluations == 13
    assert defaulted.species_balance is not None

    bare = CapacityShadowRefusal("headspace_trial_committed", 0)
    assert sim._headspace_diagnostic_refusal(bare) is bare

    # A work-cap refusal already carries the last accepted step.
    # Rebuilding it from the ledger would drop S and the buffer stock.
    carried = CapacityShadowRefusal(
        "headspace_balance_work_cap",
        4,
        cap="integrator",
        species_balance={"O2": {"mol": 1.0, "partial_Pa": 2.0}},
        redox_mol_s={"O2": 3.0},
        buffer_o2_mol=0.0,
        buffer_o2_mol_signed=-1.0e-13,
    )
    assert sim._headspace_diagnostic_refusal(carried) is carried

    inventory = sim._headspace_inventory_mismatch_refusal()
    assert inventory.reason == "headspace_published_pressure_not_inventory"
    assert inventory.species_balance[OXYGEN_SPECIES]["bleed_posted_mol"] == -1.0
    assert inventory.path == "fast_root"
    assert any(
        row["account"] == FO2_BUFFER_ACCOUNT
        for row in inventory.overhead_o2_writes
    )


def _buffer_mol(sim) -> float:
    return float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )


def _set_buffer_kg(sim, kg: float) -> None:
    molar = MOLAR_MASS["O2"] / 1000.0
    mol = kg / molar
    current = _buffer_mol(sim)
    if mol > current:
        _seed(
            sim,
            FO2_BUFFER_ACCOUNT,
            {OXYGEN_SPECIES: mol - current},
            "hour-22 buffer",
        )
        return
    sim.atom_ledger._balances.setdefault(FO2_BUFFER_ACCOUNT, {})[
        OXYGEN_SPECIES
    ] = mol


def test_hour22_buffer_stays_nonnegative_and_overdraw_refuses():
    """The C4 hour after a 27.174 Pa close, on the real vapour law.

    Overhead O2 is the published hour-21 inventory, downstream is
    20 Pa, and the melt is 1170 C. A fresh lunar melt publishes this
    opening at a zero buffer, at one ulp, and at 1e-12 kg. None of
    those hit the integrator cap. The release is bounded by the stock,
    so the buffer ends at or above zero. An opening of -1e-13 kg makes
    the integrated release exceed that stock by more than the noop.
    The hour refuses ``headspace_buffer_release_exceeds_inventory``
    and does not debit the buffer through zero.
    """

    opening_pa = 27.174056558659704
    molar = MOLAR_MASS["O2"] / 1000.0

    def _open(kg: float):
        sim = _sim(CampaignPhase.C4, 1170.0, 20.0)
        _seed_pa(sim, OXYGEN_SPECIES, opening_pa, "hour-21 close")
        _set_buffer_kg(sim, kg)
        return sim

    for kg in (0.0, molar * math.ulp(1.0), 1.0e-12):
        sim = _open(kg)
        sim._step_one_hour()
        assert _buffer_mol(sim) >= 0.0, (kg, _buffer_mol(sim))
        assert sim._headspace_capacity_result.authoritative

    sim = _open(-1.0e-13)
    try:
        sim._step_one_hour()
    except CapacityCouplingRefusalError as exc:
        refusal = exc.refusal
    else:
        raise AssertionError(
            f"negative opening published, buffer {_buffer_mol(sim)}"
        )
    assert refusal.reason == "headspace_buffer_release_exceeds_inventory"
    assert refusal.buffer_o2_mol is not None
    assert refusal.buffer_o2_mol >= 0.0
    assert refusal.buffer_o2_mol_signed is not None
    assert _buffer_mol(sim) > 0.0
    assert _buffer_mol(sim) + 1.0e-15 >= refusal.buffer_o2_mol_signed


def test_buffer_release_roundoff_scales_with_stock_but_material_overdraw_refuses():
    """A two-ulp stock mismatch at five mol is roundoff, not a leak.

    Mutation: restoring the absolute 1e-15 mol slack refuses the first
    hour because its two-ulp mismatch is 1.78e-15 mol. A 1e-9 mol
    shortfall remains a real overdraw and must still refuse.
    """

    opening_pa = 27.174056558659704
    molar = MOLAR_MASS["O2"] / 1000.0

    def _run_with_shortfall(shortfall_mol: float):
        sim = _sim(CampaignPhase.C4, 1170.0, 20.0)
        _seed_pa(sim, OXYGEN_SPECIES, opening_pa, "hour-21 close")
        _set_buffer_kg(sim, 5.04514324972239 * molar)
        settle = sim._settle_evaporative_redox_after_batch

        def settle_with_shortfall(*args, **kwargs):
            result = settle(*args, **kwargs)
            balances = sim.atom_ledger._balances.setdefault(
                FO2_BUFFER_ACCOUNT, {}
            )
            balances[OXYGEN_SPECIES] = (
                float(balances.get(OXYGEN_SPECIES, 0.0) or 0.0)
                - shortfall_mol
            )
            return result

        sim._settle_evaporative_redox_after_batch = settle_with_shortfall
        try:
            sim._step_one_hour()
        except CapacityCouplingRefusalError as exc:
            return sim, exc.refusal
        return sim, None

    sim, refusal = _run_with_shortfall(2.0 * math.ulp(5.0))
    assert refusal is None
    release = next(
        row
        for row in sim._headspace_species_notices
        if row.get("reason") == "headspace_buffer_release"
    )
    mismatch = (
        release["excess_integrated"]
        - release["fo2_buffer_after_settle_mol"]
    )
    assert mismatch == 2.0 * math.ulp(5.0)
    assert math.ulp(release["excess_integrated"]) == math.ulp(5.0)

    _sim_overdraw, refusal = _run_with_shortfall(1.0e-9)
    assert refusal is not None
    assert refusal.reason == "headspace_buffer_release_exceeds_inventory"
    assert (
        refusal.buffer_release["excess_integrated"]
        - refusal.buffer_release["fo2_buffer_after_settle_mol"]
    ) > 0.99e-9


def test_buffer_release_roundoff_counts_the_reviewed_ten_postings():
    """Ten sequential ledger additions stay within the posting-aware budget.

    Mutation: with the prior fixed k=2 budget, this six-ulp stock/ledger
    gap exceeds slack and falsely refuses despite matching transfers.
    """

    opening_mol = 5.04514324972239
    changes_mol = (
        4.440892098500626e-16,
        4.440892098500626e-16,
        9.103828801926284e-14,
        1.7985439526579938e-13,
        -8.24024618984072e-6,
        -0.0005244570072657773,
        3.9968028886505635e-15,
        9.103655329578686e-14,
        0.04495806865660912,
        -0.001000000000000778,
    )
    ledger_mol = opening_mol
    for change_mol in changes_mol:
        ledger_mol += change_mol

    vapour_changes = changes_mol[:-1]
    credit_mol = math.fsum(
        change for change in vapour_changes if change > 0.0
    )
    debit_mol = -math.fsum(
        change for change in vapour_changes if change < 0.0
    )
    uptake_mol = -changes_mol[-1]
    stock_mol = headspace_buffer_release_stock_mol(
        opening_mol,
        credit_mol,
        debit_mol,
        uptake_mol,
        noop_mol=OXYGEN_RESERVOIR_NOOP_MOL,
    )
    assert len(changes_mol) == 10
    assert ledger_mol == 5.088568621125905
    assert stock_mol == 5.08856862112591

    record = {
        "n_open": opening_mol,
        "n_credit": credit_mol,
        "n_debit": debit_mol,
        "n_uptake": uptake_mol,
        "fo2_buffer_after_settle_mol": ledger_mol,
        "buffer_postings": len(changes_mol),
    }
    slack_mol = _headspace_buffer_release_roundoff_slack_mol(record)
    assert stock_mol > ledger_mol + OXYGEN_RESERVOIR_NOOP_MOL
    assert stock_mol <= ledger_mol + slack_mol
