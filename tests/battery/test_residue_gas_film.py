"""Spherical gas film on the residue series-resistance path.

The mutation forces the series owner back to Hertz–Knudsen on the
printed 1 atm Sossi row. That is the old residue rule, and it must
collapse Mn. The production film must not.
"""

from __future__ import annotations

import math
import subprocess
import warnings
from pathlib import Path

import pytest
import yaml

import simulator.battery.residue as residue
from engines.builtin.evaporation_flux import (
    EvaporationFluxConfigurationError,
    _EVAPORATION_LJ_PROXY_MOLAR_MASSES,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.battery.residue import ResidueChannel
from simulator.condensation import (
    _blanc_mixture_d_ab_m2_s,
    _chapman_enskog_d_ab_m2_s,
    _sphere_ranz_marshall_sherwood,
    _stirring_enhanced_sherwood,
)


def _emit(message: str) -> None:
    """Surface a census line in the pytest warnings summary.

    The remote gate refuses ``-s``, so a passing test's stdout is dropped.
    """

    warnings.warn(message, UserWarning, stacklevel=2)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"
OUTLIER = "sossi-2019-run-2m-16-07-16d"
_OPENIMCC_TREE_PREFIX = "308672269d6e"


def _load_sossi():
    return yaml.safe_load(SOURCE.read_text())


def _outlier_quote(source: dict) -> str:
    for species in source["species"].values():
        for observation in species.get("observations") or []:
            if observation.get("experiment") != OUTLIER:
                continue
            for point in (observation.get("values") or {}).get("series") or []:
                locator = point.get("locator") or {}
                quote = locator.get("quote") or ""
                if "188" in quote and "-9.56" in quote:
                    return quote
    raise AssertionError("outlier Table 2 quote is not in the extract")


def _observation_values(source: dict, element: str) -> dict:
    for observation in source["species"][element]["observations"]:
        if observation.get("experiment") == OUTLIER:
            return observation["values"]
    raise AssertionError(f"no {element} observation for {OUTLIER}")


def test_sossi_table_covers_every_extract_run_and_the_outlier_quote() -> None:
    source = _load_sossi()
    experiment_ids = {
        experiment["experiment_id"] for experiment in source["experiments"]
    }
    assert experiment_ids == set(residue._SOSSI_TABLE2_CO_CO2_SCCM)
    assert "188   12    -9.56" in _outlier_quote(source)
    assert residue._SOSSI_TABLE2_CO_CO2_SCCM[OUTLIER] == (188.0, 12.0)

    fractions, flow_sccm, notices = residue._sossi_carrier(OUTLIER)
    assert flow_sccm == 200.0
    assert fractions == (("CO", 188.0 / 200.0), ("CO2", 12.0 / 200.0))
    assert notices == ()

    air_fractions, air_flow, air_notices = residue._sossi_carrier(
        "sossi-2019-run-2m-15-07-16c"
    )
    assert air_flow is None
    assert air_fractions == residue._SOSSI_DRY_AIR
    assert ("gas_flow_unprinted_stagnant_sherwood", "stated_default") in air_notices
    assert all(reason != "carrier_gas_not_printed" for reason, _band in air_notices)

    pure, anu_flow, anu_notices = residue._sossi_carrier(
        "sossi-2019-run-c18-12-15"
    )
    assert anu_flow == 27.0
    assert pure == (("CO2", 1.0),)
    assert anu_notices == ()


def test_outlier_film_uses_bead_diameter_and_printed_flow() -> None:
    film = residue._sossi_gas_film(
        OUTLIER,
        temperature_K=1823.15,
        overhead_pressure_Pa=101325.0,
        sample_mass_kg=25.0e-6,
    )
    stagnant = residue._stagnant_sphere_sherwood()
    pipe_floor = _stirring_enhanced_sherwood(radial_stir_factor=0.0)
    assert stagnant == _sphere_ranz_marshall_sherwood(0.0, 1.0)
    assert stagnant == 2.0
    assert film.reynolds is not None and film.schmidt is not None
    assert film.sherwood == pytest.approx(
        _sphere_ranz_marshall_sherwood(film.reynolds, film.schmidt)
    )
    # Printed flow lifts Sh above the stagnant sphere. It stays below
    # the unstirred pipe floor, so this is not that correlation.
    assert stagnant < film.sherwood < pipe_floor
    assert 0.002 < film.bead_diameter_m < 0.004
    assert film.free_stream_diameter_m == 0.042
    assert film.free_stream_diameter_m != film.bead_diameter_m
    assert film.carrier_gas == "CO"
    assert film.overhead_pressure_Pa == 101325.0
    assert ("bead_diameter_not_printed", "stated_default") in film.notices
    assert "bc_open_furnace_langmuir_limit_diagnostic" not in {
        reason for reason, _band in film.notices
    }
    _emit(
        "SOSSI_FILM_SHAPE "
        f"Sh={film.sherwood:.6g} "
        f"Re={film.reynolds:.6g} "
        f"Sc={film.schmidt:.6g} "
        f"d={film.bead_diameter_m:.6g}"
    )


def test_blanc_pure_carrier_matches_the_binary_owner() -> None:
    temperature_K = 1823.15
    pressure_Pa = 101325.0
    binary_co = _chapman_enskog_d_ab_m2_s(
        "Mn", temperature_K, pressure_Pa, carrier="CO"
    )
    binary_co2 = _chapman_enskog_d_ab_m2_s(
        "Mn", temperature_K, pressure_Pa, carrier="CO2"
    )
    pure = _blanc_mixture_d_ab_m2_s(
        "Mn", temperature_K, pressure_Pa, {"CO": 1.0}
    )
    mixture = _blanc_mixture_d_ab_m2_s(
        "Mn", temperature_K, pressure_Pa, {"CO": 0.94, "CO2": 0.06}
    )
    assert binary_co > 0.0 and binary_co2 > 0.0
    assert pure == pytest.approx(binary_co)
    low, high = sorted((binary_co, binary_co2))
    assert low <= mixture <= high


def test_tio2_uses_the_existing_proxy_row_and_feo_does_not() -> None:
    assert "TiO2" not in _EVAPORATION_LJ_PROXY_MOLAR_MASSES
    assert "TiO2_gas" in _EVAPORATION_LJ_PROXY_MOLAR_MASSES
    film = residue._sossi_gas_film(
        OUTLIER,
        temperature_K=1823.15,
        overhead_pressure_Pa=101325.0,
        sample_mass_kg=25.0e-6,
    )
    tio2 = _series_resistance_evaporation_flux_kg_m2_s(
        species="TiO2",
        P_eq_pa=1.0e-6,
        P_bulk_pa=0.0,
        T_surface_K=1823.15,
        molar_mass_kg_mol=0.079866,
        alpha_i=0.1,
        pipe_diameter_m=film.bead_diameter_m,
        overhead_pressure_pa=film.overhead_pressure_Pa,
        gas_resistance_enabled=True,
        sherwood=film.sherwood,
        carrier_gas=film.carrier_gas,
        carrier_mole_fractions=dict(film.carrier_mole_fractions or ()),
        molecular_diameter_m=film.molecular_diameter_m,
    )
    assert tio2.gas_resistance_weight == 1.0
    assert tio2.d_ab_m2_s > 0.0
    with pytest.raises(EvaporationFluxConfigurationError):
        _series_resistance_evaporation_flux_kg_m2_s(
            species="FeO",
            P_eq_pa=1.0e-6,
            P_bulk_pa=0.0,
            T_surface_K=1823.15,
            molar_mass_kg_mol=0.071844,
            alpha_i=1.0,
            pipe_diameter_m=film.bead_diameter_m,
            overhead_pressure_pa=film.overhead_pressure_Pa,
            gas_resistance_enabled=True,
            sherwood=film.sherwood,
            carrier_gas=film.carrier_gas,
            carrier_mole_fractions=dict(film.carrier_mole_fractions or ()),
            molecular_diameter_m=film.molecular_diameter_m,
        )


def _mn_terms():
    return residue._channel_terms(
        (ResidueChannel("Mn", "Mn", "MnO", 1.0, "test"),),
        require_oxygen_channels=False,
    )


def test_vacuum_film_flux_matches_hertz_knudsen() -> None:
    terms = _mn_terms()
    inventory = {"MnO": 1.0e-6, "SiO2": 1.0e-4}
    for pressure_Pa in (0.013332, 0.1, 1.333e-4):
        on = _series_resistance_evaporation_flux_kg_m2_s(
            species="Mn",
            P_eq_pa=0.04,
            P_bulk_pa=0.0,
            T_surface_K=1700.0,
            molar_mass_kg_mol=0.054938,
            alpha_i=1.0,
            pipe_diameter_m=0.004,
            overhead_pressure_pa=pressure_Pa,
            gas_resistance_enabled=True,
            sherwood=2.0,
            carrier_gas="N2",
        )
        off = _series_resistance_evaporation_flux_kg_m2_s(
            species="Mn",
            P_eq_pa=0.04,
            P_bulk_pa=0.0,
            T_surface_K=1700.0,
            molar_mass_kg_mol=0.054938,
            alpha_i=1.0,
            pipe_diameter_m=0.004,
            overhead_pressure_pa=pressure_Pa,
            gas_resistance_enabled=False,
            sherwood=2.0,
            carrier_gas="N2",
        )
        assert on.gas_resistance_weight == 0.0
        assert on.flux_kg_s_m2 == off.flux_kg_s_m2
        film = residue._unprinted_stagnant_gas_film(
            overhead_pressure_Pa=pressure_Pa,
            bead_diameter_m=0.004,
            bead_notice="bead_diameter_not_printed",
        )
        with_film = residue._finite_step_fluxes(
            terms,
            inventory,
            temperature_K=1700.0,
            area_m2=1.0e-5,
            pressures_Pa={"Mn": 0.04},
            gas_film=film,
            include_below_threshold=True,
        )
        without_film = residue._finite_step_fluxes(
            terms,
            inventory,
            temperature_K=1700.0,
            area_m2=1.0e-5,
            pressures_Pa={"Mn": 0.04},
            include_below_threshold=True,
        )
        assert with_film["Mn"] == pytest.approx(without_film["Mn"])


def test_one_atmosphere_film_cuts_the_manganese_rate() -> None:
    terms = residue._channel_terms(
        (
            ResidueChannel("Mn", "Mn", "MnO", 1.0, "test"),
            ResidueChannel("TiO2", "TiO2", "TiO2", 0.1, "test"),
        ),
        require_oxygen_channels=False,
    )
    inventory = {"MnO": 1.0e-6, "TiO2": 1.0e-6, "SiO2": 1.0e-4}
    film = residue._sossi_gas_film(
        OUTLIER,
        temperature_K=1823.15,
        overhead_pressure_Pa=101325.0,
        sample_mass_kg=25.0e-6,
    )
    pressures = {"Mn": 0.04, "TiO2": 1.0e-8}
    filmed = residue._finite_step_fluxes(
        terms,
        inventory,
        temperature_K=1823.15,
        area_m2=1.0e-5,
        pressures_Pa=pressures,
        gas_film=film,
        include_below_threshold=True,
    )
    langmuir = residue._finite_step_fluxes(
        terms,
        inventory,
        temperature_K=1823.15,
        area_m2=1.0e-5,
        pressures_Pa=pressures,
        include_below_threshold=True,
    )
    assert filmed["Mn"] < langmuir["Mn"] * 0.1
    assert "TiO2" in filmed


def _pressure_token(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    state = node.get("state")
    value = state.get("value") if isinstance(state, dict) else node.get("value")
    if isinstance(value, dict):
        if "point" in value:
            return str(value.get("point"))
        if "bound_value" in value:
            return f"{value.get('bound_operator')}{value.get('bound_value')}"
    if value is None:
        return ""
    return str(value)


def test_residue_source_pressures_are_printed() -> None:
    extracts = ROOT / "data/literature/extracts"
    names = [
        "kems-012-sossi-2019.yaml",
        "kems-015-hashimoto-1983.yaml",
        "kems-025-markova-1983.yaml",
        "kems-026-markova-1984.yaml",
        "richter-et-al-2007.yaml",
        "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation.yaml",
        "zhang-2011-boron-sodium-f-free-evaporation.yaml",
    ]
    for pattern in ("*zhang*2021*", "*wang*2001*", "*ivanova*"):
        names.extend(path.name for path in extracts.glob(pattern))
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = extracts / name
        if not path.is_file():
            _emit(f"SOURCE_TABLE {name} ABSENT")
            continue
        document = yaml.safe_load(path.read_text())
        experiments = document.get("experiments") or []
        pressures: list[str] = []
        methods: list[str] = []
        for experiment in experiments:
            if not isinstance(experiment, dict):
                continue
            environment = experiment.get("pressure_environment") or {}
            pressures.append(
                _pressure_token(environment.get("total_pressure_Pa"))
            )
            method = experiment.get("method") or {}
            state = method.get("state") if isinstance(method, dict) else None
            methods.append(
                str(state.get("value")) if isinstance(state, dict) else ""
            )
        _emit(
            "SOURCE_TABLE "
            f"{document.get('source_id') or name} "
            f"n={len(experiments)} "
            "P=" + ",".join(sorted(set(pressures))[:8]) + " "
            "method=" + ",".join(sorted(set(methods))[:6])
        )
    sossi = _load_sossi()
    sossi_pressures = {
        _pressure_token(
            (experiment.get("pressure_environment") or {}).get("total_pressure_Pa")
        )
        for experiment in sossi["experiments"]
    }
    assert sossi_pressures == {"101325"}


def _openimcc_tree_hash() -> str:
    import openimcc

    package = Path(openimcc.__file__).resolve().parent
    _emit(f"OPENIMCC_FILE {openimcc.__file__}")
    completed = subprocess.run(
        ["bash", "-c", "find . -name '*.py' | sort | xargs shasum | shasum"],
        cwd=package,
        check=True,
        capture_output=True,
        text=True,
    )
    digest = completed.stdout.strip().split()[0]
    _emit(f"OPENIMCC_TREE {digest}")
    return digest


def _dex(predicted_ppm: float, measured_ppm: float) -> float:
    if predicted_ppm <= 0.0 or not math.isfinite(predicted_ppm):
        return -math.inf
    return math.log10(predicted_ppm / measured_ppm)


# engine_cache_off: the mutation patches the series flux the cohort calls.
@pytest.mark.engine_cache_off
def test_sossi_outlier_hkl_mutation_collapses_manganese(monkeypatch) -> None:
    pytest.importorskip("openimcc")
    assert _openimcc_tree_hash().startswith(_OPENIMCC_TREE_PREFIX)

    source = _load_sossi()
    experiment = next(
        item for item in source["experiments"] if item["experiment_id"] == OUTLIER
    )
    manganese = _observation_values(source, "Mn")
    titanium = _observation_values(source, "Ti")
    measured_mn = float(manganese["series"][0]["residue_ppm"])
    measured_ti = float(titanium["series"][0]["residue_ppm"])
    starting = {
        "Mn": float(manganese["starting_measured_ppm"]),
        "Ti": float(titanium["starting_measured_ppm"]),
    }
    buffered = float(manganese["series"][0]["log10_fO2"]["value"])
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())

    # Cap the integrator steps. Leave _SOSSI_GEOMETRIES at its real three
    # names: the success writer indexes every entry.
    monkeypatch.setattr(residue, "_HASHIMOTO_N_CAP", 8)
    recorded: dict[str, object] = {}
    real_integrate = residue.integrate_residue_inventory

    def capture(inventory, channels, pressure_model, **kwargs):
        if "model" not in recorded:
            recorded["inventory"] = dict(inventory)
            recorded["channels"] = channels
            recorded["model"] = pressure_model
            recorded["kwargs"] = dict(kwargs)
        return real_integrate(inventory, channels, pressure_model, **kwargs)

    monkeypatch.setattr(residue, "integrate_residue_inventory", capture)
    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        catalog,
        starting_trace_ppm_by_experiment={OUTLIER: starting},
        buffered_fO2_log_by_experiment={OUTLIER: buffered},
        engine="openimcc",
    )[0]
    assert prediction.provenance.get("run_refusal") is None
    flags = prediction.provenance["assumption_flags"]
    assert "bc_open_furnace_langmuir_limit_diagnostic" not in flags
    assert flags["bead_diameter_not_printed"] is True
    film = recorded["kwargs"]["gas_film"]
    assert film.overhead_pressure_Pa == 101325.0
    cohort_mn = prediction.primary_element_ppm["Mn"]
    cohort_ti = prediction.primary_element_ppm["Ti"]
    _emit(
        "SOSSI_COHORT "
        f"Mn_ppm={cohort_mn:.6g} "
        f"Mn_dex={_dex(cohort_mn, measured_mn):.4f} "
        f"Ti_ppm={cohort_ti:.6g} "
        f"Ti_dex={_dex(cohort_ti, measured_ti):.4f} "
        f"Sh={film.sherwood:.6g}"
    )

    def one_shot(gas_film):
        result = real_integrate(
            recorded["inventory"],
            recorded["channels"],
            recorded["model"],
            temperature_K=recorded["kwargs"]["temperature_K"],
            duration_s=900.0,
            area_evolution_m2=recorded["kwargs"]["area_evolution_m2"],
            buffered_fO2_log=recorded["kwargs"]["buffered_fO2_log"],
            gas_film=gas_film,
        )
        return residue._sossi_project_element_ppm(
            result.residue_mol,
            result.buffer_oxygen_exchange_mol,
            ("Mn", "Ti"),
        )

    printed = one_shot(film)
    stagnant_film = residue.ResidueGasFilm(
        overhead_pressure_Pa=film.overhead_pressure_Pa,
        bead_diameter_m=film.bead_diameter_m,
        sherwood=residue._stagnant_sphere_sherwood(),
        carrier_gas=film.carrier_gas,
        carrier_mole_fractions=film.carrier_mole_fractions,
        molecular_diameter_m=film.molecular_diameter_m,
        notices=film.notices,
        flow_sccm=film.flow_sccm,
        free_stream_diameter_m=film.free_stream_diameter_m,
    )
    stagnant = one_shot(stagnant_film)
    _emit(
        "SOSSI_ONESHOT_FILM "
        f"Mn_ppm={printed['Mn']:.6g} "
        f"Mn_dex={_dex(printed['Mn'], measured_mn):.4f} "
        f"Ti_ppm={printed['Ti']:.6g} "
        f"Ti_dex={_dex(printed['Ti'], measured_ti):.4f} "
        f"Sh={film.sherwood:.6g}"
    )
    _emit(
        "SOSSI_ONESHOT_SH2 "
        f"Mn_ppm={stagnant['Mn']:.6g} "
        f"Mn_dex={_dex(stagnant['Mn'], measured_mn):.4f} "
        f"Ti_ppm={stagnant['Ti']:.6g} "
        f"Ti_dex={_dex(stagnant['Ti'], measured_ti):.4f}"
    )

    import engines.builtin.evaporation_flux as flux

    original = flux._series_resistance_evaporation_flux_kg_m2_s

    def force_hkl(*args, **kwargs):
        kwargs["gas_resistance_enabled"] = False
        return original(*args, **kwargs)

    monkeypatch.setattr(
        flux, "_series_resistance_evaporation_flux_kg_m2_s", force_hkl
    )
    collapsed = one_shot(film)
    hkl_dex = _dex(collapsed["Mn"], measured_mn)
    film_dex = _dex(printed["Mn"], measured_mn)
    _emit(
        "SOSSI_ONESHOT_HKL "
        f"Mn_ppm={collapsed['Mn']:.6g} "
        f"Mn_dex={hkl_dex:.4f} "
        f"Ti_ppm={collapsed['Ti']:.6g} "
        f"Ti_dex={_dex(collapsed['Ti'], measured_ti):.4f}"
    )
    assert hkl_dex < -10.0, f"SOSSI_ONESHOT_HKL Mn_dex={hkl_dex}"
    assert film_dex > -1.0, f"SOSSI_ONESHOT_FILM Mn_dex={film_dex}"
    assert _dex(cohort_mn, measured_mn) > -1.0, (
        f"SOSSI_COHORT Mn_dex={_dex(cohort_mn, measured_mn)}"
    )
    assert _dex(stagnant["Mn"], measured_mn) > -1.0, (
        f"SOSSI_ONESHOT_SH2 Mn_dex={_dex(stagnant['Mn'], measured_mn)}"
    )
