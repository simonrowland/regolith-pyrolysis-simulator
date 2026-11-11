"""b-314: a tier-2 ``broad_proxy_not_intrinsic`` alpha row is proxy evidence,
not a measurement — it must not satisfy the flux path's measured-alpha
requirement.

Pre-fix, ``_load_evaporation_alpha_by_species`` returned CrO2's inherited
``0.9`` as a bare float, so ``engines.builtin.evaporation_flux``'s
presence-based ``_alpha_is_unmeasured`` admitted it and the SC-67 fail-loud
``missing_alpha`` refusal never fired: the guard was bypassed by supplying
the thing it tests for.

The fix has three parts, all on the simulator side of the provider seam:

1. The loader emits tagged rows as provenance-carrying mappings
   (``alpha_proxy_tag``), never bare numbers — so no consumer can mistake
   the proxy for a measured coefficient.
2. The full map (proxy rows included) still feeds the pressure gate
   (``_legacy_evaporation_shadow_pressure_map``): the species' pressure
   must reach the provider or the refusal could never fire — dropping the
   row from the loader map was tried and silently removed the species from
   the flux-driving pressure path instead (same defect class, one layer up).
3. The provider-facing ``_measured_alpha_control_view`` strips tagged rows
   so the SC-67 unmeasured-alpha refusal (or the explicitly gated
   ``allow_unmeasured_alpha_fallback`` prototype path) fires.
"""

from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.accounting.ledger import DEFAULT_BALANCE_TOLERANCE_KG
from simulator.evaporation import (
    _legacy_evaporation_shadow_pressure_map,
    _load_evaporation_alpha_by_species,
    _measured_alpha_control_view,
    screened_missing_alpha_records,
)
from simulator.evaporation_classes import (
    BROAD_PROXY_NOT_INTRINSIC_TAG,
    is_broad_proxy_not_intrinsic_row,
)
from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY

VAPOR_PRESSURES_PATH = Path("data/vapor_pressures.yaml")

# Rows carrying ``tag: broad_proxy_not_intrinsic`` inside the loader's
# (metals, oxide_vapors) groups on the current data. This set is the class
# the tag marks: inherited from another measurement family, never an
# intrinsic melt coefficient.
BROAD_PROXY_SPECIES = {
    "AlO",
    "Al2O",
    "Al2",
    "Al2O2",
    "Al2O3_gas",
    "AlO2",
    "CrO",
    "CrO2",
}

# Untagged bare-scalar rows that remain admitted. Cr is the same bare-float
# shape as CrO2 but carries NO proxy tag (Pound McCabe-Hudson Paxton
# pure-solid-Cr measurement family); withdrawing untagged rows is outside
# this fix's scope.
UNTAGGED_BARE_STILL_ADMITTED = {"Cr", "Fe", "Mg", "K"}

# Rows with OTHER structured proxy tags (``broad_proxy``, ``proxy``,
# ``pure_elemental_only``, ``proxy_not_intrinsic``) — several are
# owner-ratified admissions (Mn per docs/model-limitations.md). The b-314
# wiring covers only ``broad_proxy_not_intrinsic``; these stay admitted and
# are reported in the sibling sweep for owner adjudication.
OTHER_PROXY_TAG_STILL_ADMITTED = {
    "Al",
    "Si",
    "Mn",
    "CrO3",
    "K2",
    "K2O_gas",
    "Mg2",
    "MgO_gas",
    "Si2",
    "Si3",
    "SiO2_gas",
}


def _raw_data() -> dict:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())


def test_proxy_rows_carry_provenance_not_bare_floats():
    """The brief's sharp point: Na and SiO carry provenance into the
    consumption path; CrO2's 0.9 was a naked number indistinguishable from
    a measurement. Tagged rows now load as mappings naming the tag, tier,
    and source."""
    alpha_by_species = _load_evaporation_alpha_by_species(_raw_data())
    for species in BROAD_PROXY_SPECIES:
        spec = alpha_by_species.get(species)
        assert isinstance(spec, dict), (
            f"{species}: proxy row must load as a provenance-carrying "
            f"mapping, got {spec!r}"
        )
        assert spec["alpha_proxy_tag"] == BROAD_PROXY_NOT_INTRINSIC_TAG
        assert spec["alpha_proxy_tier"] == 2
        assert spec["source"].strip()
    assert alpha_by_species["CrO2"]["value"] == pytest.approx(0.9)


def test_measured_view_strips_proxy_rows_for_flux_admission():
    """Every row tagged ``broad_proxy_not_intrinsic`` is stripped from the
    provider-facing measured view so the SC-67 unmeasured-alpha refusal
    fires instead of a proxy number satisfying the gate."""
    measured = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    admitted = BROAD_PROXY_SPECIES & set(measured)
    assert not admitted, (
        f"tag {BROAD_PROXY_NOT_INTRINSIC_TAG!r} rows must not satisfy the "
        f"measured-alpha requirement, but loaded: {sorted(admitted)}"
    )
    # Sanity: the tags are still present in the data (the tag, not the
    # species, drives stripping — deleting the row would also pass the
    # assertion above but for the wrong reason).
    raw_text = VAPOR_PRESSURES_PATH.read_text()
    assert raw_text.count(f"tag: {BROAD_PROXY_NOT_INTRINSIC_TAG}") >= len(
        BROAD_PROXY_SPECIES
    )


def test_stripping_is_data_driven_not_hardcoded():
    """The class fix: a synthetic tagged row is marked and stripped even for
    a species no production data carries, and removing the tag re-admits
    the row as a plain value."""
    data = {"metals": {"TestSpecies": {
        "evaporation_alpha": {
            "value": 0.9,
            "source": "synthetic test proxy",
            "tag": BROAD_PROXY_NOT_INTRINSIC_TAG,
            "tier": 2,
        }
    }}}
    loaded = _load_evaporation_alpha_by_species(data)
    assert loaded["TestSpecies"]["alpha_proxy_tag"] == (
        BROAD_PROXY_NOT_INTRINSIC_TAG
    )
    assert "TestSpecies" not in _measured_alpha_control_view(loaded)
    untagged = {"metals": {"TestSpecies": {
        "evaporation_alpha": {"value": 0.9, "source": "synthetic"},
    }}}
    loaded_untagged = _load_evaporation_alpha_by_species(untagged)
    assert loaded_untagged["TestSpecies"] == 0.9
    assert _measured_alpha_control_view(loaded_untagged)["TestSpecies"] == 0.9


def test_pressure_gate_keeps_proxy_species():
    """Guard against the wrong-layer fix: the species' pressure must still
    reach the provider, or the refusal can never fire and the species
    evaporates silently to zero (the same absence-toward-confidence class
    this ticket fixes, one layer up)."""
    shadow = _legacy_evaporation_shadow_pressure_map(
        _raw_data(),
        {"CrO2": 3.7e-9, "AlO": 1.0, "Fe": 2.0},
        read_context="test",
    )
    assert shadow == pytest.approx({"CrO2": 3.7e-9, "AlO": 1.0, "Fe": 2.0})


def test_untagged_rows_still_admitted_with_values():
    measured = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    for species in UNTAGGED_BARE_STILL_ADMITTED:
        assert species in measured, species
    assert measured["Cr"] == pytest.approx(0.9)
    # Provenance-carrying mappings survive untouched.
    na = measured["Na"]
    assert isinstance(na, dict)
    assert na["alpha_authority_status"] == "analytical_upper_bound"
    sio = measured["SiO"]
    assert isinstance(sio, dict) and sio["form"] == "arrhenius"
    assert sio["status"] == "UNCERTIFIED"


def test_other_proxy_tag_rows_stay_admitted_scope_marker():
    """Scope marker for the sweep: other structured proxy tags are NOT
    wired by b-314 (several are owner-ratified). If the owner later widens
    the wiring, this test is the deliberate place that must change."""
    measured = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    missing = OTHER_PROXY_TAG_STILL_ADMITTED - set(measured)
    assert not missing, f"unexpectedly stripped: {sorted(missing)}"


def test_flux_dormant_rows_keep_declared_zero():
    """Declared dormancy (``flux_dormant: true``) is a data declaration, not
    an absence: dormant rows keep their explicit 0.0."""
    measured = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    for species in ("FeO_association_gas", "NiO_gas", "MnO_gas", "CoO_gas"):
        assert measured.get(species) == 0.0, species


def test_no_data_rows_still_absent():
    """Exact ``status: no_data`` remains absence (unchanged semantics)."""
    data = {"metals": {"Ghost": {"evaporation_alpha": {"status": "no_data"}}}}
    loaded = _load_evaporation_alpha_by_species(data)
    assert "Ghost" not in loaded
    assert "Ghost" not in _measured_alpha_control_view(loaded)


def test_predicate_shapes():
    assert is_broad_proxy_not_intrinsic_row(
        {"tag": "broad_proxy_not_intrinsic", "value": 0.9}
    )
    assert is_broad_proxy_not_intrinsic_row({"tag": " Broad_Proxy_Not_Intrinsic "})
    assert not is_broad_proxy_not_intrinsic_row({"tag": "proxy_not_intrinsic"})
    assert not is_broad_proxy_not_intrinsic_row({"value": 0.9})
    assert not is_broad_proxy_not_intrinsic_row(None)
    assert not is_broad_proxy_not_intrinsic_row("broad_proxy_not_intrinsic")


def test_sc67_refusal_fires_for_cro2_with_real_loader_map():
    """End-to-end with the REAL loader output through the production control
    view: CrO2 enters missing_alpha and its species_refusals record names
    missing_evaporation_alpha, while Na (admitted, provenance-carrying)
    still evaporates."""
    alpha_by_species = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0, "Na2O": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {"CrO2": 100.0, "Na": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"CrO2": 0.084, "Na": 0.023},
            "stoich_by_species": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
                "Na": {
                    "parent_oxide": "Na2O",
                    "oxide_per_product_kg": 1.347,
                    "O2_per_product_kg": 0.347,
                },
            },
            "available_oxide_kg": {"CrO2": 10.0, "Na": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    assert "CrO2" not in result.diagnostic["evaporation_flux_kg_hr"]
    assert set(result.diagnostic["missing_alpha"]) == {"CrO2"}
    refusal = result.diagnostic["species_refusals"]["CrO2"]
    assert refusal["policy"] == "fail_loud_missing_alpha"
    assert refusal["reason"] == "missing_evaporation_alpha"
    assert refusal["status"] == "refused"
    assert refusal["disposition"] == "retained_in_condensed_parent_oxide"
    assert refusal["parent_oxide"] == "Cr2O3"
    assert "per-species evaporation refusal" in result.warnings[0]


def test_fallback_path_marks_proxy_species_when_explicitly_enabled():
    """With ``allow_unmeasured_alpha_fallback`` explicitly set, a stripped
    proxy species takes the marked alpha=1.0 prototype fallback — recorded
    in ``unmeasured_alpha_fallback_species``, never silently measured."""
    alpha_by_species = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(_raw_data())
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {"CrO2": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"CrO2": 0.084},
            "stoich_by_species": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
            },
            "available_oxide_kg": {"CrO2": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": ["CrO2"],
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert "missing_alpha" not in result.diagnostic
    assert result.diagnostic["evaporation_flux_kg_hr"]["CrO2"] > 0.0
    assert result.diagnostic["unmeasured_alpha_fallback_species"] == ["CrO2"]
    assert result.diagnostic["alpha_used_by_species"]["CrO2"] == 1.0


def _sole_alpha_refusal(detail: str = "channel contract incomplete: missing alpha"):
    from types import SimpleNamespace

    return SimpleNamespace(
        channels_by_species={
            "CrO2": SimpleNamespace(
                refusal_code="missing_channel_contract",
                extra={"detail": detail},
            )
        }
    )


def test_screened_missing_alpha_omits_only_a_proved_sub_tolerance_bound():
    """A sole missing alpha never enters the flux map, so the kernel cannot
    record it. Stamp the refusal unless that carrier's own alpha=1
    Hertz-Knudsen tick mass is strictly below the closure tolerance.
    The proved-below case is an authorized zero with the bound recorded,
    not an empty result.

    Hot arithmetic, T = 1800 K, M = 0.084 kg/mol, P = 1 Pa, A = 1 m^2,
    t = 3600 s, R = 8.314462618 J/mol/K:
        k = sqrt(M / (2 * pi * R * T))
          = sqrt(0.084 / (2 * pi * 8.314462618 * 1800))
          = 9.451404790080771e-4 kg/(m^2*Pa*s)
        mass = k * P * A * t = 3.4025057244290777 kg
    which is not below 1e-12. The negligible case is the same product at
    P = 1e-40 Pa, about 3.4e-40 kg. A missing seam pressure is not that proof.
    """

    common = dict(
        molar_mass_g_mol={"CrO2": 84.0},
        temperature_K=1800.0,
        area_m2=1.0,
        duration_s=3600.0,
        balance_tolerance_kg=DEFAULT_BALANCE_TOLERANCE_KG,
    )
    hot = screened_missing_alpha_records(
        _sole_alpha_refusal(),
        flux_pressures_Pa={},
        seam_pressure_pa={"CrO2": 1.0},
        **common,
    )
    assert set(hot.refusals) == {"CrO2"}
    assert hot.authorized_zeros == {}
    assert hot.refusals["CrO2"]["policy"] == "fail_loud_missing_alpha"
    assert hot.refusals["CrO2"]["baseline_alpha_1_rate_kg_hr"] > (
        DEFAULT_BALANCE_TOLERANCE_KG
    )

    cold = screened_missing_alpha_records(
        _sole_alpha_refusal(),
        flux_pressures_Pa={},
        seam_pressure_pa={"CrO2": 1.0e-40},
        **common,
    )
    assert cold.refusals == {}
    record = cold.authorized_zeros["CrO2"]
    assert record["reason"] == "below_evaporation_upper_bound"
    assert record["law"] == "alpha_1_hertz_knudsen_on_kernel_seam"
    assert record["authority"] == "kernel_seam"
    assert record["pressure_pa"] == 1.0e-40
    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    # Same product as the hot line, scaled by P = 1e-40.
    cold_mass_kg = (
        hertz_knudsen_k_kg_s_m2_pa(1800.0, 0.084) * 1.0e-40 * 1.0 * 3600.0
    )
    assert record["bound_kg"] == pytest.approx(cold_mass_kg, rel=1e-12, abs=0.0)
    assert record["bound_kg"] < DEFAULT_BALANCE_TOLERANCE_KG

    unavailable = screened_missing_alpha_records(
        _sole_alpha_refusal(),
        flux_pressures_Pa={},
        seam_pressure_pa={},
        **common,
    )
    assert unavailable.authorized_zeros == {}
    assert unavailable.refusals["CrO2"]["policy"] == "fail_loud_missing_alpha"
    assert unavailable.refusals["CrO2"]["bound"] == "unavailable"

    already_debiting = screened_missing_alpha_records(
        _sole_alpha_refusal(),
        flux_pressures_Pa={"CrO2": 1.0},
        seam_pressure_pa={"CrO2": 1.0},
        **common,
    )
    assert already_debiting.refusals == {}
    assert already_debiting.authorized_zeros == {}

    other_field = screened_missing_alpha_records(
        _sole_alpha_refusal(
            "channel contract incomplete: missing formula"
        ),
        flux_pressures_Pa={},
        seam_pressure_pa={"CrO2": 1.0},
        **common,
    )
    assert other_field.refusals == {}
    assert other_field.authorized_zeros == {}


# Every sole-missing-alpha input class. The species set is the shipped
# broad-proxy carriers plus the carbon carriers whose coverage gap is the
# same missing-alpha class. Each one is a refusal or an authorized zero.
_SOLE_MISSING_ALPHA_SPECIES = tuple(sorted(
    BROAD_PROXY_SPECIES | {"CH4", "CO", "CO2"}
))
_SOLE_MISSING_ALPHA_BRANCHES = (
    "above_tolerance",
    "below_tolerance",
    "zero_pressure",
    "missing_pressure",
    "negative_pressure",
    "missing_molar_mass",
    "missing_area",
    "missing_duration",
    "missing_tolerance",
    "already_debiting",
    "other_missing_field",
)


def _branch_numbers(species: str, branch: str) -> dict:
    """Inputs for one sole-missing-alpha class.

    ``P`` is chosen from ``k_HK * P * A * t`` against the ledger
    tolerance. The screen's recorded mass is not an input.
    """

    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    temperature_K = 1800.0
    molar_g = 84.0
    area_m2 = 1.0
    duration_s = 3600.0
    k_hk = hertz_knudsen_k_kg_s_m2_pa(temperature_K, molar_g / 1000.0)
    scale = k_hk * area_m2 * duration_s
    pressure = (DEFAULT_BALANCE_TOLERANCE_KG * 2.0) / scale
    if branch == "below_tolerance":
        pressure = (DEFAULT_BALANCE_TOLERANCE_KG * 0.5) / scale
    elif branch == "zero_pressure":
        pressure = 0.0
    elif branch == "negative_pressure":
        pressure = -1.0
    detail = "channel contract incomplete: missing alpha"
    if branch == "other_missing_field":
        detail = "channel contract incomplete: missing formula"
    expected_mass = None
    if branch in {"above_tolerance", "below_tolerance", "zero_pressure"}:
        expected_mass = k_hk * pressure * area_m2 * duration_s
    return {
        "temperature_K": temperature_K,
        "pressure": pressure,
        "seam": {} if branch == "missing_pressure" else {species: pressure},
        "molar": {} if branch == "missing_molar_mass" else {species: molar_g},
        "area_m2": None if branch == "missing_area" else area_m2,
        "duration_s": None if branch == "missing_duration" else duration_s,
        "tolerance": (
            None if branch == "missing_tolerance" else DEFAULT_BALANCE_TOLERANCE_KG
        ),
        "detail": detail,
        "expected_mass": expected_mass,
    }


def _screen_branch(species: str, branch: str):
    from types import SimpleNamespace

    numbers = _branch_numbers(species, branch)
    batch = SimpleNamespace(
        channels_by_species={
            species: SimpleNamespace(
                refusal_code="missing_channel_contract",
                extra={"detail": numbers["detail"]},
            )
        }
    )
    flux = (
        {species: numbers["pressure"]} if branch == "already_debiting" else {}
    )
    screen = screened_missing_alpha_records(
        batch,
        flux_pressures_Pa=flux,
        seam_pressure_pa=numbers["seam"],
        molar_mass_g_mol=numbers["molar"],
        temperature_K=numbers["temperature_K"],
        area_m2=numbers["area_m2"],
        duration_s=numbers["duration_s"],
        balance_tolerance_kg=numbers["tolerance"],
    )
    return screen, numbers["expected_mass"]


@pytest.mark.parametrize("species", _SOLE_MISSING_ALPHA_SPECIES)
@pytest.mark.parametrize("branch", _SOLE_MISSING_ALPHA_BRANCHES)
def test_sole_missing_alpha_is_refusal_or_authorized_zero(
    species: str,
    branch: str,
) -> None:
    """INVARIANT: a sole missing alpha is never a silent zero.

    Already-debiting and non-alpha contract misses are outside this
    class. Every other branch is exactly one of a typed
    ``fail_loud_missing_alpha`` refusal or an authorized
    ``below_evaporation_upper_bound`` zero whose bound is the alpha=1
    Hertz-Knudsen tick mass.
    """

    screen, expected_mass = _screen_branch(species, branch)
    in_refusal = species in screen.refusals
    in_zero = species in screen.authorized_zeros
    assert not (in_refusal and in_zero)
    if branch in {"already_debiting", "other_missing_field"}:
        assert screen.refusals == {}
        assert screen.authorized_zeros == {}
        return
    if branch in {"below_tolerance", "zero_pressure"}:
        assert in_zero and not in_refusal
        record = screen.authorized_zeros[species]
        assert record["reason"] == "below_evaporation_upper_bound"
        assert record["authority"] == "kernel_seam"
        assert record["law"] == "alpha_1_hertz_knudsen_on_kernel_seam"
        assert expected_mass is not None
        assert record["bound_kg"] == pytest.approx(expected_mass, rel=1e-12, abs=0.0)
        assert record["bound_kg"] < DEFAULT_BALANCE_TOLERANCE_KG
        assert "policy" not in record
        return
    assert in_refusal and not in_zero
    refusal = screen.refusals[species]
    assert refusal["policy"] == "fail_loud_missing_alpha"
    if branch == "above_tolerance":
        assert expected_mass is not None
        assert expected_mass >= DEFAULT_BALANCE_TOLERANCE_KG
        assert refusal["baseline_alpha_1_rate_kg_hr"] == pytest.approx(
            expected_mass, rel=1e-12, abs=0.0
        )
    else:
        assert refusal["bound"] == "unavailable"


# Evaporation-path temperature. 1600 C is 1873.15 K, above the 400 K gate
# where an empty flux refuses and a partial refusal is noted. The screen
# tests above never enter those decisions.
_PATH_TEMPERATURE_C = 1600.0
_PATH_CARRIER = "CrO2"
_PATH_MOLAR_G_MOL = 84.0
_PATH_AREA_M2 = 1.0
_PATH_PRESSURE_PA = 1.0e-30
_PATH_DURATION_S = 3600.0


def _path_tick_mass_kg() -> float:
    """Alpha=1 Hertz-Knudsen tick mass the evaporation path must record.

    T = 1600 + 273.15 = 1873.15 K, M = 0.084 kg/mol, P = 1e-30 Pa,
    A = 1 m^2, t = 3600 s, R = 8.314462618 J/mol/K:
        k = sqrt(M / (2 * pi * R * T))
        mass = k * P * A * t
    The product is about 3.3e-30 kg, below the 1e-12 kg closure tolerance.
    """

    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    temperature_K = _PATH_TEMPERATURE_C + 273.15
    return (
        hertz_knudsen_k_kg_s_m2_pa(temperature_K, _PATH_MOLAR_G_MOL / 1000.0)
        * _PATH_PRESSURE_PA
        * _PATH_AREA_M2
        * _PATH_DURATION_S
    )


def _missing_alpha_answer(
    species_id: str,
    detail: str = "channel contract incomplete: missing alpha",
):
    from types import MappingProxyType

    from simulator.vapour_rail.batch import (
        FluxRefusal,
        PressureRefusal,
        VapourAnswer,
    )
    from simulator.vapour_rail.request import REFUSAL_MISSING_CHANNEL_CONTRACT
    refusal = PressureRefusal(
        code=REFUSAL_MISSING_CHANNEL_CONTRACT,
        detail=detail,
    )
    flux = FluxRefusal(code=REFUSAL_MISSING_CHANNEL_CONTRACT, detail=detail)
    return VapourAnswer(
        species_id=species_id,
        pressure=refusal,
        selected_runtime_pressure=refusal,
        flux=flux,
        source_label="refusal_closure",
        formula_id=species_id,
        source_account="process.cleaned_melt",
        solve_group_id="g",
        state_fingerprint="s",
        validation_status="pending_validation",
        refusal_code=REFUSAL_MISSING_CHANNEL_CONTRACT,
        extra=MappingProxyType({"detail": detail}),
    )


def _debiting_answer(species_id: str, pressure_pa: float):
    from simulator.vapour_rail.batch import (
        FluxEligible,
        PressureValue,
        VapourAnswer,
    )

    pressure = PressureValue(pa=pressure_pa)
    return VapourAnswer(
        species_id=species_id,
        pressure=pressure,
        selected_runtime_pressure=pressure,
        flux=FluxEligible(alpha_ref=f"alpha:{species_id}"),
        source_label="test",
        formula_id=species_id,
        source_account="process.cleaned_melt",
        solve_group_id="g",
        state_fingerprint="s",
        validation_status="pending_validation",
    )


# Request resolution freezes these before a later classification rewrite.
# The dormant count and the bundle count are sentinels, not the lengths
# of the empty sets on these batches. A recount that also copies the
# catalog's flux-set update would replace them.
_SNAPSHOT_CLOSURE_ITERATIONS = 4
_SNAPSHOT_DORMANT = 7
_SNAPSHOT_SOLVE_BUNDLES = 2
_SNAPSHOT_EPOCH = "pre_rg_legacy_live"


def _request_refusal_snapshot(channels, flux_active) -> dict:
    """Metadata ``resolve_vapour_batch`` freezes from the pre-rewrite answers.

    ``n_refused`` counts those answers. The other keys are request facts
    and must survive a refusal recount.
    """

    return {
        "refusal_closure_fixed_point": True,
        "refusal_closure_iterations": _SNAPSHOT_CLOSURE_ITERATIONS,
        "n_requested": len(channels),
        "n_refused": sum(1 for answer in channels.values() if answer.is_refused),
        "n_flux_active": len(flux_active),
        "n_flux_dormant_by_epoch": _SNAPSHOT_DORMANT,
        "flux_activation_epoch": _SNAPSHOT_EPOCH,
        "n_solve_bundles": _SNAPSHOT_SOLVE_BUNDLES,
    }


def _assert_snapshot_except_refusal_count(metadata, snapshot) -> None:
    for key, value in snapshot.items():
        if key == "n_refused":
            continue
        assert metadata[key] == value


def _path_batch(peer: bool, species: str = _PATH_CARRIER):
    from simulator.vapour_rail.batch import VapourBatch

    channels = {species: _missing_alpha_answer(species)}
    active: frozenset[str] = frozenset()
    if peer:
        channels["Na"] = _debiting_answer("Na", 1.5)
        active = frozenset({"Na"})
    return VapourBatch(
        requested_species_ids=frozenset(channels),
        channels_by_species=channels,
        flux_active_species_ids=active,
        metadata=_request_refusal_snapshot(channels, active),
    )


def _path_alpha_row() -> dict:
    return {
        "molar_mass_g_mol": _PATH_MOLAR_G_MOL,
        "evaporation_alpha": {
            "value": 0.9,
            "source": "synthetic test proxy",
            "tag": "broad_proxy_not_intrinsic",
            "tier": 2,
        },
    }


def _path_host(batch, dispatch, species: str = _PATH_CARRIER):
    from types import MethodType, SimpleNamespace

    from simulator.evaporation import EvaporationMixin

    host = SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=_PATH_TEMPERATURE_C,
            melt_surface_area_m2=_PATH_AREA_M2,
        ),
        setpoints={},
        vapor_pressures={"metals": {species: _path_alpha_row()}},
        _last_vapor_pressure_diagnostic={},
        _last_vapour_batch_resolve_error={},
        _silent_zero_notes=[],
        _record_degraded_path_engagement=lambda *args, **kwargs: None,
        _resolve_evaporation_vapour_batch=(
            lambda equilibrium, temperature_K, effective_pressure_source: batch
        ),
        _evaporation_bulk_partial_pressure_pa=lambda species: 0.0,
        _build_partial_melt_offgassing_diagnostic=(
            lambda *args, **kwargs: {"reached_partial_loop": True}
        ),
        _dispatch_only=dispatch,
        _freeze_gate_enabled=lambda: False,
    )
    host._resolve_evaporation_batch_flux_state = MethodType(
        EvaporationMixin._resolve_evaporation_batch_flux_state,
        host,
    )
    return host


def _path_equilibrium(
    species: str = _PATH_CARRIER,
    pressure_pa: float = _PATH_PRESSURE_PA,
):
    from types import SimpleNamespace

    return SimpleNamespace(
        liquid_fraction=1.0,
        temperature_C=_PATH_TEMPERATURE_C,
        vapor_pressures_Pa={species: pressure_pa},
        vapor_pressures_source={},
        status="ok",
        diagnostics={},
    )


# Surfaces that read the batch answer. An authorized zero is one
# classification across all of them. A refusal is the other.
_PUBLISHED_ZERO_SURFACES = (
    "answer.is_refused",
    "answer.refusal_code",
    "answer.pressure",
    "answer.flux",
    "overlay.batch_channel_states",
    "overlay.selected_pressure_source_by_species",
    "overlay.flux_map",
    "serialized.pressure.kind",
    "serialized.selected_runtime_pressure.kind",
    "serialized.flux.kind",
    "serialized.is_refused",
    "serialized.refusal_code",
    "serialized.refusals_by_species",
    "serialized.n_refused",
    "batch.metadata.n_refused",
    "serialized.metadata.n_refused",
    "carrier_authority.is_refused",
    "carrier_authority.refusal_code",
    "carrier_authority.status",
)


def _assert_published_authorized_zero(
    species: str,
    *,
    answer,
    overlay: dict,
    report: dict,
    authority: dict,
) -> None:
    """Every publisher of ``species`` says ``zero_by_physics``, not a refusal."""

    from simulator.vapour_rail.batch import FluxEligible, ZeroByPhysics
    from simulator.vapour_rail.instrumentation import (
        VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
        vapour_carrier_authority_status,
    )

    assert isinstance(answer.pressure, ZeroByPhysics)
    assert isinstance(answer.selected_runtime_pressure, ZeroByPhysics)
    assert isinstance(answer.flux, FluxEligible)
    assert answer.is_refused is False
    assert answer.refusal_code is None
    assert answer.is_flux_active is False
    assert overlay["batch_channel_states"][species] == "zero_by_physics"
    assert overlay["selected_pressure_source_by_species"][species] == (
        "zero_by_physics"
    )
    assert species not in overlay.get("selected_runtime_pa_by_species", {})
    channel = report["channels_by_species"][species]
    assert channel["pressure"]["kind"] == "zero_by_physics"
    assert channel["selected_runtime_pressure"]["kind"] == "zero_by_physics"
    assert channel["flux"]["kind"] == "eligible"
    assert channel["is_refused"] is False
    assert channel["refusal_code"] is None
    assert "missing_channel_contract" not in channel["pressure"]
    assert "missing_channel_contract" not in channel["flux"]
    assert species not in report["refusals_by_species"]
    assert report["n_refused"] == len(report["refusals_by_species"])
    assert report["metadata"]["n_refused"] == report["n_refused"]
    assert report["metadata"]["n_flux_dormant_by_epoch"] == _SNAPSHOT_DORMANT
    assert report["metadata"]["n_solve_bundles"] == _SNAPSHOT_SOLVE_BUNDLES
    assert report["metadata"]["refusal_closure_iterations"] == (
        _SNAPSHOT_CLOSURE_ITERATIONS
    )
    assert report["metadata"]["flux_activation_epoch"] == _SNAPSHOT_EPOCH
    assert report["metadata"]["n_requested"] == len(
        report["channels_by_species"]
    )
    assert report["metadata"]["n_flux_active"] == report["n_flux_active"]
    assert authority[species] == channel
    assert authority[species]["is_refused"] is False
    assert authority[species]["refusal_code"] is None
    assert (
        vapour_carrier_authority_status(
            authority[species],
            expected_species_id=species,
        )
        == VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO
    )


def _assert_authorized_carrier(host, flux, expected_mass_kg: float) -> None:
    from simulator.silent_zero import CATEGORY_REFUSE, ZeroBecause

    diagnostic = host._last_evaporation_flux_diagnostic
    assert _PATH_CARRIER not in (diagnostic.get("missing_alpha") or {})
    record = diagnostic["below_evaporation_upper_bound"][_PATH_CARRIER]
    assert record["reason"] == "below_evaporation_upper_bound"
    assert record["law"] == "alpha_1_hertz_knudsen_on_kernel_seam"
    assert record["authority"] == "kernel_seam"
    assert record["pressure_pa"] == _PATH_PRESSURE_PA
    assert record["bound_kg"] == pytest.approx(
        expected_mass_kg, rel=1e-12, abs=0.0
    )
    assert 0.0 < record["bound_kg"] < DEFAULT_BALANCE_TOLERANCE_KG
    _assert_published_authorized_zero(
        _PATH_CARRIER,
        answer=host._last_vapour_batch.channel(_PATH_CARRIER),
        overlay=host._last_vapour_batch_flux_overlay,
        report=host._last_vapour_batch_report,
        authority=flux.carrier_authority_by_species,
    )
    notes = list(diagnostic.get("silent_zero_notes") or [])
    notes.extend(host._silent_zero_notes or [])
    refused = [
        note
        for note in notes
        if note.get("zero_because") == ZeroBecause.REFUSED_UPSTREAM.value
        and note.get("species") == _PATH_CARRIER
        and note.get("doctrine_category") == CATEGORY_REFUSE
    ]
    assert refused == []


def test_sole_subtolerance_missing_alpha_authorizes_on_evaporation_path() -> None:
    """A sole missing-alpha carrier below tolerance is an authorized zero.

    The screen classifies it. The batch answer has to carry that
    classification: a channel-state string alone leaves serialization,
    ``is_refused``, and ``refusals_by_species`` on the catalog refusal.
    """

    from simulator.evaporation import EvaporationFluxRefusal, EvaporationMixin

    def _kernel_must_not_run(*args, **kwargs):
        raise AssertionError("sole authorized zero must not reach the kernel")

    assert _PATH_TEMPERATURE_C + 273.15 >= 400.0
    expected_mass_kg = _path_tick_mass_kg()
    assert expected_mass_kg < DEFAULT_BALANCE_TOLERANCE_KG
    host = _path_host(_path_batch(peer=False), dispatch=_kernel_must_not_run)
    try:
        flux = EvaporationMixin._calculate_evaporation(host, _path_equilibrium())
    except EvaporationFluxRefusal as exc:
        raise AssertionError(
            f"authorized zero raised {exc.reason}"
        ) from exc
    assert flux.species_kg_hr == {}
    assert host._last_evaporation_flux_diagnostic.get("reason") == (
        "zero_by_physics"
    )
    _assert_authorized_carrier(host, flux, expected_mass_kg)


def test_subtolerance_missing_alpha_with_debiting_peer_is_not_refused_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proved missing-alpha zero beside a debit is not category-1.

    The peer still debits. The authorized carrier is omitted from the
    flux map, is absent from ``refusals_by_species``, and must not
    receive ``REFUSED_UPSTREAM``.
    """

    from types import SimpleNamespace

    from simulator.evaporation import EvaporationMixin
    from simulator.silent_zero import CATEGORY_REFUSE, ZeroBecause

    assert _PATH_TEMPERATURE_C + 273.15 >= 400.0
    expected_mass_kg = _path_tick_mass_kg()
    assert expected_mass_kg < DEFAULT_BALANCE_TOLERANCE_KG
    monkeypatch.setattr(
        EvaporationMixin,
        "_evaporation_flux_control_inputs",
        lambda self, equilibrium, **kwargs: ({}, {}),
    )
    host = _path_host(
        _path_batch(peer=True),
        dispatch=lambda *args, **kwargs: SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {"Na": 0.25}},
        ),
    )
    flux = EvaporationMixin._calculate_evaporation(host, _path_equilibrium())
    assert flux.species_kg_hr == {"Na": 0.25}
    assert _PATH_CARRIER not in flux.species_kg_hr
    assert host._last_partial_melt_offgassing_diagnostic == {
        "reached_partial_loop": True
    }
    states = host._last_vapour_batch_flux_overlay["batch_channel_states"]
    assert states["Na"] == "eligible"
    _assert_authorized_carrier(host, flux, expected_mass_kg)
    report = host._last_vapour_batch_report
    assert "Na" not in report["refusals_by_species"]
    assert report["n_refused"] == 0
    na_authority = flux.carrier_authority_by_species["Na"]
    assert na_authority["is_refused"] is False
    assert na_authority["pressure"]["kind"] == "value"
    peer_notes = [
        note
        for note in (host._silent_zero_notes or [])
        if note.get("species") == "Na"
        and note.get("zero_because") == ZeroBecause.REFUSED_UPSTREAM.value
        and note.get("doctrine_category") == CATEGORY_REFUSE
    ]
    assert peer_notes == []


def _published_classification(
    species: str,
    answer,
    overlay,
    report,
    authority,
    metadata,
):
    """Read every publisher. Callers compare this map; they do not re-derive it."""

    from simulator.vapour_rail.instrumentation import vapour_carrier_authority_status

    channel = report["channels_by_species"][species]
    auth = authority[species]
    found = {
        "answer.is_refused": bool(answer.is_refused),
        "answer.refusal_code": answer.refusal_code,
        "answer.pressure": type(answer.pressure).__name__,
        "answer.flux": type(answer.flux).__name__,
        "overlay.batch_channel_states": overlay["batch_channel_states"][species],
        "overlay.selected_pressure_source_by_species": (
            overlay.get("selected_pressure_source_by_species") or {}
        ).get(species),
        "overlay.flux_map": species in (
            overlay.get("selected_runtime_pa_by_species") or {}
        ),
        "serialized.pressure.kind": channel["pressure"]["kind"],
        "serialized.selected_runtime_pressure.kind": (
            channel["selected_runtime_pressure"]["kind"]
        ),
        "serialized.flux.kind": channel["flux"]["kind"],
        "serialized.is_refused": bool(channel["is_refused"]),
        "serialized.refusal_code": channel["refusal_code"],
        "serialized.refusals_by_species": species in report["refusals_by_species"],
        "serialized.n_refused": report["n_refused"],
        "batch.metadata.n_refused": metadata["n_refused"],
        "serialized.metadata.n_refused": report["metadata"]["n_refused"],
        "carrier_authority.is_refused": bool(auth["is_refused"]),
        "carrier_authority.refusal_code": auth["refusal_code"],
        "carrier_authority.status": vapour_carrier_authority_status(
            auth,
            expected_species_id=species,
        ),
    }
    assert set(found) == set(_PUBLISHED_ZERO_SURFACES)
    assert report["n_refused"] == len(report["refusals_by_species"])
    assert metadata["n_refused"] == report["n_refused"]
    assert report["metadata"]["n_refused"] == report["n_refused"]
    assert auth == channel
    return found


def _expected_published_classification(branch: str) -> dict:
    if branch in {"below_tolerance", "zero_pressure"}:
        return {
            "answer.is_refused": False,
            "answer.refusal_code": None,
            "answer.pressure": "ZeroByPhysics",
            "answer.flux": "FluxEligible",
            "overlay.batch_channel_states": "zero_by_physics",
            "overlay.selected_pressure_source_by_species": "zero_by_physics",
            "overlay.flux_map": False,
            "serialized.pressure.kind": "zero_by_physics",
            "serialized.selected_runtime_pressure.kind": "zero_by_physics",
            "serialized.flux.kind": "eligible",
            "serialized.is_refused": False,
            "serialized.refusal_code": None,
            "serialized.refusals_by_species": False,
            "serialized.n_refused": 0,
            "batch.metadata.n_refused": 0,
            "serialized.metadata.n_refused": 0,
            "carrier_authority.is_refused": False,
            "carrier_authority.refusal_code": None,
            "carrier_authority.status": "proven_zero",
        }
    if branch == "already_debiting":
        return {
            "answer.is_refused": False,
            "answer.refusal_code": None,
            "answer.pressure": "PressureValue",
            "answer.flux": "FluxEligible",
            "overlay.batch_channel_states": "eligible",
            "overlay.selected_pressure_source_by_species": (
                "vapour_batch_selected_runtime_pressure"
            ),
            "overlay.flux_map": True,
            "serialized.pressure.kind": "value",
            "serialized.selected_runtime_pressure.kind": "value",
            "serialized.flux.kind": "eligible",
            "serialized.is_refused": False,
            "serialized.refusal_code": None,
            "serialized.refusals_by_species": False,
            "serialized.n_refused": 0,
            "batch.metadata.n_refused": 0,
            "serialized.metadata.n_refused": 0,
            "carrier_authority.is_refused": False,
            "carrier_authority.refusal_code": None,
            "carrier_authority.status": "status_bearing",
        }
    return {
        "answer.is_refused": True,
        "answer.refusal_code": "missing_channel_contract",
        "answer.pressure": "PressureRefusal",
        "answer.flux": "FluxRefusal",
        "overlay.batch_channel_states": "refusal",
        "overlay.selected_pressure_source_by_species": None,
        "overlay.flux_map": False,
        "serialized.pressure.kind": "refusal",
        "serialized.selected_runtime_pressure.kind": "refusal",
        "serialized.flux.kind": "refusal",
        "serialized.is_refused": True,
        "serialized.refusal_code": "missing_channel_contract",
        "serialized.refusals_by_species": True,
        "serialized.n_refused": 1,
        "batch.metadata.n_refused": 1,
        "serialized.metadata.n_refused": 1,
        "carrier_authority.is_refused": True,
        "carrier_authority.refusal_code": "missing_channel_contract",
        "carrier_authority.status": "refused",
    }


def _classify_branch_through_batch(species: str, branch: str):
    """Screen, rewrite, then read the publishers production reads."""

    from simulator.evaporation import _apply_authorized_missing_alpha_zeros
    from simulator.vapour_rail.batch import VapourBatch
    from simulator.vapour_rail.instrumentation import (
        flux_pressures_from_batch,
        serialize_vapour_batch,
    )

    numbers = _branch_numbers(species, branch)
    if branch == "already_debiting":
        answer = _debiting_answer(species, numbers["pressure"])
        active = frozenset({species})
    else:
        answer = _missing_alpha_answer(species, detail=numbers["detail"])
        active = frozenset()
    channels = {species: answer}
    snapshot = _request_refusal_snapshot(channels, active)
    batch = VapourBatch(
        requested_species_ids=frozenset(channels),
        channels_by_species=channels,
        flux_active_species_ids=active,
        metadata=snapshot,
    )
    pressures, _overlay = flux_pressures_from_batch(batch)
    screen = screened_missing_alpha_records(
        batch,
        flux_pressures_Pa=pressures,
        seam_pressure_pa=numbers["seam"],
        molar_mass_g_mol=numbers["molar"],
        temperature_K=numbers["temperature_K"],
        area_m2=numbers["area_m2"],
        duration_s=numbers["duration_s"],
        balance_tolerance_kg=numbers["tolerance"],
    )
    rewritten = _apply_authorized_missing_alpha_zeros(batch, screen)
    debit_pressures, overlay = flux_pressures_from_batch(rewritten)
    report = serialize_vapour_batch(rewritten)
    authority = {
        str(sid): dict(channel)
        for sid, channel in report["channels_by_species"].items()
    }
    return (
        screen,
        pressures,
        debit_pressures,
        rewritten,
        overlay,
        report,
        authority,
        snapshot,
    )


@pytest.mark.parametrize("species", _SOLE_MISSING_ALPHA_SPECIES)
@pytest.mark.parametrize("branch", _SOLE_MISSING_ALPHA_BRANCHES)
def test_published_surfaces_share_one_missing_alpha_classification(
    species: str,
    branch: str,
) -> None:
    """INVARIANT: one answer classifies every published surface.

    A proved below-tolerance missing alpha is ``zero_by_physics`` on
    the gate, the serialized channel, ``refusals_by_species``, and
    carrier authority, and it is not in the flux map. Every other
    branch stays a typed refusal or a real debit. No branch is a
    silent 0 Pa.
    """

    (
        screen,
        pressures,
        debit_pressures,
        rewritten,
        overlay,
        report,
        authority,
        snapshot,
    ) = _classify_branch_through_batch(species, branch)
    assert snapshot["n_refused"] == (0 if branch == "already_debiting" else 1)
    _assert_snapshot_except_refusal_count(rewritten.metadata, snapshot)
    _assert_snapshot_except_refusal_count(report["metadata"], snapshot)
    assert set(debit_pressures) == set(pressures)
    if branch in {"below_tolerance", "zero_pressure"}:
        assert species in screen.authorized_zeros
        assert species not in screen.refusals
        assert species not in debit_pressures
    elif branch in {"already_debiting", "other_missing_field"}:
        assert screen.authorized_zeros == {}
        assert screen.refusals == {}
    else:
        assert species in screen.refusals
        assert species not in screen.authorized_zeros
        assert species not in debit_pressures
    found = _published_classification(
        species,
        rewritten.channel(species),
        overlay,
        report,
        authority,
        rewritten.metadata,
    )
    assert found == _expected_published_classification(branch)


_REFUSAL_COUNT_BRANCHES = (
    "below_tolerance",
    "zero_pressure",
    "above_tolerance",
    "missing_pressure",
    "already_debiting",
)


@pytest.mark.parametrize("species", _SOLE_MISSING_ALPHA_SPECIES)
@pytest.mark.parametrize("branch", _REFUSAL_COUNT_BRANCHES)
def test_metadata_n_refused_counts_only_channels_still_refused(
    species: str,
    branch: str,
) -> None:
    """INVARIANT: ``metadata.n_refused`` is the channels still refused.

    The snapshot is the request-time count, so a proved zero starts
    inside it. After the rewrite the count is that zero's absence plus
    the peer that stays a typed refusal. It is not zero merely because
    one carrier was authorized, and the closure, epoch, dormant, and
    bundle fields stay the request facts.
    """

    from simulator.evaporation import _apply_authorized_missing_alpha_zeros
    from simulator.vapour_rail.batch import VapourBatch
    from simulator.vapour_rail.instrumentation import (
        flux_pressures_from_batch,
        serialize_vapour_batch,
    )

    numbers = _branch_numbers(species, branch)
    if branch == "already_debiting":
        subject = _debiting_answer(species, numbers["pressure"])
        active = frozenset({species, "Na"})
    else:
        subject = _missing_alpha_answer(species, detail=numbers["detail"])
        active = frozenset({"Na"})
    channels = {
        species: subject,
        "Na": _debiting_answer("Na", 1.5),
        "SiO": _missing_alpha_answer(
            "SiO",
            detail="channel contract incomplete: missing formula",
        ),
    }
    snapshot = _request_refusal_snapshot(channels, active)
    subject_refused_before = branch != "already_debiting"
    assert snapshot["n_refused"] == 1 + int(subject_refused_before)
    batch = VapourBatch(
        requested_species_ids=frozenset(channels),
        channels_by_species=channels,
        flux_active_species_ids=active,
        solve_bundle_ids={"na": frozenset({"Na"})},
        metadata=snapshot,
    )
    pressures, _overlay = flux_pressures_from_batch(batch)
    flux = dict(pressures)
    if branch == "already_debiting":
        flux[species] = numbers["pressure"]
    screen = screened_missing_alpha_records(
        batch,
        flux_pressures_Pa=flux,
        seam_pressure_pa=numbers["seam"],
        molar_mass_g_mol=numbers["molar"],
        temperature_K=numbers["temperature_K"],
        area_m2=numbers["area_m2"],
        duration_s=numbers["duration_s"],
        balance_tolerance_kg=numbers["tolerance"],
    )
    rewritten = _apply_authorized_missing_alpha_zeros(batch, screen)
    report = serialize_vapour_batch(rewritten)
    subject_still_refused = branch not in {
        "below_tolerance",
        "zero_pressure",
        "already_debiting",
    }
    expected = 1 + int(subject_still_refused)
    assert "SiO" in report["refusals_by_species"]
    assert "Na" not in report["refusals_by_species"]
    assert (species in report["refusals_by_species"]) is subject_still_refused
    assert report["n_refused"] == expected
    assert rewritten.metadata["n_refused"] == expected
    assert report["metadata"]["n_refused"] == expected
    assert report["n_refused"] == sum(
        1 for answer in rewritten.channels_by_species.values() if answer.is_refused
    )
    _assert_snapshot_except_refusal_count(rewritten.metadata, snapshot)
    _assert_snapshot_except_refusal_count(report["metadata"], snapshot)
    if branch in {"below_tolerance", "zero_pressure"}:
        assert species in screen.authorized_zeros
        assert rewritten.channel(species).is_refused is False
    elif subject_still_refused:
        assert species in screen.refusals
        assert rewritten.channel(species).is_refused is True


@pytest.mark.parametrize("species", _SOLE_MISSING_ALPHA_SPECIES)
def test_evaporation_path_publishes_the_authorized_zero_for_every_carrier(
    species: str,
) -> None:
    """The evaporation path's returned authority matches the batch answer."""

    from simulator.evaporation import EvaporationFluxRefusal, EvaporationMixin

    def _kernel_must_not_run(*args, **kwargs):
        raise AssertionError("sole authorized zero must not reach the kernel")

    expected_mass_kg = _path_tick_mass_kg()
    assert expected_mass_kg < DEFAULT_BALANCE_TOLERANCE_KG
    host = _path_host(
        _path_batch(peer=False, species=species),
        dispatch=_kernel_must_not_run,
        species=species,
    )
    try:
        flux = EvaporationMixin._calculate_evaporation(
            host,
            _path_equilibrium(species),
        )
    except EvaporationFluxRefusal as exc:
        raise AssertionError(f"{species} authorized zero raised {exc.reason}") from exc
    assert flux.species_kg_hr == {}
    record = host._last_evaporation_flux_diagnostic[
        "below_evaporation_upper_bound"
    ][species]
    assert record["bound_kg"] == pytest.approx(expected_mass_kg, rel=1e-12, abs=0.0)
    assert species not in (
        host._last_evaporation_flux_diagnostic.get("missing_alpha") or {}
    )
    _assert_published_authorized_zero(
        species,
        answer=host._last_vapour_batch.channel(species),
        overlay=host._last_vapour_batch_flux_overlay,
        report=host._last_vapour_batch_report,
        authority=flux.carrier_authority_by_species,
    )


@pytest.mark.parametrize("species", _SOLE_MISSING_ALPHA_SPECIES)
def test_evaporation_path_keeps_an_above_tolerance_missing_alpha_refusal(
    species: str,
) -> None:
    """A bound that is not negligible stays a refusal on every surface.

    The authorized-zero rewrite must not swallow this carrier.
    """

    from simulator.evaporation import EvaporationFluxRefusal, EvaporationMixin
    from simulator.vapour_rail.batch import FluxRefusal, PressureRefusal

    def _kernel_must_not_run(*args, **kwargs):
        raise AssertionError("sole refusal must not reach the kernel")

    host = _path_host(
        _path_batch(peer=False, species=species),
        dispatch=_kernel_must_not_run,
        species=species,
    )
    with pytest.raises(EvaporationFluxRefusal) as raised:
        EvaporationMixin._calculate_evaporation(
            host,
            _path_equilibrium(species, pressure_pa=1.0),
        )
    answer = host._last_vapour_batch.channel(species)
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert answer.refusal_code == "missing_channel_contract"
    report = host._last_vapour_batch_report
    assert species in report["refusals_by_species"]
    assert report["channels_by_species"][species]["is_refused"] is True
    assert report["n_refused"] == 1
    assert report["metadata"]["n_refused"] == 1
    assert report["metadata"]["n_flux_dormant_by_epoch"] == _SNAPSHOT_DORMANT
    assert report["metadata"]["n_solve_bundles"] == _SNAPSHOT_SOLVE_BUNDLES
    assert host._last_vapour_batch.metadata["n_refused"] == 1
    overlay = host._last_vapour_batch_flux_overlay
    assert overlay["batch_channel_states"][species] == "refusal"
    diagnostic = raised.value.diagnostic
    assert species in (diagnostic.get("missing_alpha") or {})
    assert species not in (diagnostic.get("below_evaporation_upper_bound") or {})
    batch_in_diagnostic = diagnostic.get("vapour_batch") or {}
    assert species in (batch_in_diagnostic.get("refusals_by_species") or {})
