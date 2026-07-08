"""DIAGNOSTIC-first foulant disposition arms (C1/C2/C4a/C4b/C4c/C5)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.foulant_disposition import (
    chi_decomp,
    chi_escape_salt,
    load_foulant_registry,
    partition_carbon,
)
from engines.builtin.stage0_pretreatment import (
    BuiltinStage0PretreatmentProvider,
    REACTION_FAMILY_CARBONATE_DECOMPOSITION,
    REACTION_FAMILY_INERT_TO_RUMP,
    REACTION_FAMILY_PARTITION_CARBON,
    REACTION_FAMILY_SILICATE_DISPLACEMENT,
    REACTION_FAMILY_SULFATE_DECOMP,
    REACTION_FAMILY_VOLATILIZATION,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.accounting import resolve_species_formula
from simulator.core import (
    STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
    STAGE0_FOULANT_PHASE1_TEMP_C,
    STAGE0_FOULANT_PHASE2_OVERHEAD_BAR,
    STAGE0_FOULANT_PHASE2_TEMP_C,
    PyrolysisSimulator,
)
from simulator.melt_backend.base import StubBackend
from tests.chemistry.conftest import _build_sim

REPO_ROOT = Path(__file__).resolve().parents[2]
FOULANT_THERMO = REPO_ROOT / "data" / "foulant_thermo.yaml"
CARBON_PARTITION = REPO_ROOT / "data" / "stage0_carbon_partition.yaml"
VAPOR_PRESSURES = REPO_ROOT / "data" / "vapor_pressures.yaml"
GAS_CONSTANT_J_PER_MOL_K = 8.314462618
PA_PER_BAR = 100_000.0


def _provider_view(sim: PyrolysisSimulator) -> ProviderAccountView:
    return ProviderAccountView(
        accounts={},
        species_formula_registry=sim.species_formula_registry,
    )


def _dispatch(sim: PyrolysisSimulator, controls: dict):
    provider = BuiltinStage0PretreatmentProvider()
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=_provider_view(sim),
        temperature_C=STAGE0_FOULANT_PHASE1_TEMP_C,
        pressure_bar=STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
        control_inputs=controls,
    )
    return provider.dispatch(request)


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _dg_points(source_row: dict) -> list[tuple[float, float]]:
    return [
        (float(point["T_K"]), float(point["dG_kJ_per_mol"]))
        for point in source_row["points"]
    ]


def _source_onset_k(points: list[tuple[float, float]]) -> float:
    ordered = sorted(points, key=lambda row: row[0])
    for (t_lo, dg_lo), (t_hi, dg_hi) in zip(ordered, ordered[1:]):
        if dg_lo == 0.0:
            return t_lo
        if dg_hi == 0.0:
            return t_hi
        if dg_lo * dg_hi < 0.0:
            frac = abs(dg_lo) / (abs(dg_lo) + abs(dg_hi))
            return t_lo + frac * (t_hi - t_lo)
    raise AssertionError("source dG row has no zero crossing")


def _source_sigmoid_width_c(points: list[tuple[float, float]], onset_k: float) -> float:
    nearest = sorted(points, key=lambda row: abs(row[0] - onset_k))[:2]
    (t0, dg0), (t1, dg1) = sorted(nearest, key=lambda row: row[0])
    slope_j_per_mol_k = ((dg1 - dg0) / (t1 - t0)) * 1000.0
    return 2.0 * abs(GAS_CONSTANT_J_PER_MOL_K * onset_k) / abs(slope_j_per_mol_k)


def _source_dg_sigmoid_extent(
    points: list[tuple[float, float]],
    T_C: float,
    *,
    pX_bar: float = 0.0,
    o2_reference_bar: float | None = None,
) -> float:
    onset_k = _source_onset_k(points)
    width_c = _source_sigmoid_width_c(points, onset_k)
    x = (T_C - (onset_k - 273.15)) / width_c
    extent = 1.0 / (1.0 + math.exp(-x))
    if o2_reference_bar is not None and pX_bar > 0.0:
        extent *= 1.0 / (1.0 + pX_bar / o2_reference_bar)
    return extent


@pytest.fixture(scope="module")
def foulant_registry():
    return load_foulant_registry(FOULANT_THERMO)


def test_nacl_escape_fraction_anchored_to_stull_source_row(foulant_registry):
    row = _load_yaml(VAPOR_PRESSURES)["foulant_vapor"]["NaCl"]
    coeff = row["pure_component_antoine"]
    assert coeff["source"] == (
        "REF-002 NIST Chemistry WebBook SRD 69 Stull 1947 NaCl "
        "(C7647145); CRC normal bp 1465 C"
    )
    assert {key: coeff[key] for key in ("A", "B", "C")} == {
        "A": 10.07184,
        "B": 8388.497,
        "C": -82.638,
    }
    assert row["valid_range_K"] == [1138, 1738]

    T_C = 1200.0
    p_overhead_bar = 0.2
    T_K = T_C + 273.15
    p_sat_pa = 10.0 ** (coeff["A"] - coeff["B"] / (T_K + coeff["C"]))
    expected_escaped = p_sat_pa / (p_sat_pa + p_overhead_bar * PA_PER_BAR)
    assert expected_escaped == pytest.approx(0.35366909122306056)

    split = chi_escape_salt("NaCl", T_C, p_overhead_bar, foulant_registry)
    assert split.escaped_frac == pytest.approx(expected_escaped, abs=1e-12)
    assert split.retained_frac == pytest.approx(1.0 - expected_escaped, abs=1e-12)


def test_caso4_decomposition_anchored_to_nist_dg_rows(foulant_registry):
    row = _load_yaml(FOULANT_THERMO)["foulant_dG"]["CaSO4_thermal_decomp"]
    points = _dg_points(row)
    assert row["source"] == (
        "REF-012 NIST WebBook dagger; E1 sec 1.1 (1/2O2 path); C0-corrected"
    )
    assert points == [
        (1373.15, 80.0),
        (1573.15, 57.0),
        (1673.15, 28.0),
        (1773.15, 0.0),
    ]
    assert _source_onset_k(points) == pytest.approx(1773.15)

    expected_extent = _source_dg_sigmoid_extent(
        points,
        1450.0,
        pX_bar=0.01,
        o2_reference_bar=0.2,
    )
    assert expected_extent == pytest.approx(0.36521790186993264)

    observed = chi_decomp("CaSO4", 1450.0, 0.01, 0.0, foulant_registry)
    assert observed.path == "thermal"
    assert observed.onset_K == pytest.approx(1773.15)
    assert observed.extent == pytest.approx(expected_extent, abs=1e-12)


def test_caco3_decomposition_anchored_to_nist_dg_rows(foulant_registry):
    row = _load_yaml(FOULANT_THERMO)["foulant_dG"]["CaCO3_calcination"]
    points = _dg_points(row)
    assert row["source"] == "REF-012 NIST WebBook dagger; E1 sec 2.1"
    assert points == [
        (873.15, 20.0),
        (1173.15, -8.0),
        (1273.15, -35.0),
    ]
    onset_k = _source_onset_k(points)
    assert onset_k == pytest.approx(1087.4357142857143)

    expected_extent = _source_dg_sigmoid_extent(
        points,
        STAGE0_FOULANT_PHASE1_TEMP_C,
    )
    assert expected_extent == pytest.approx(0.9712377493632139)

    observed = chi_decomp(
        "CaCO3",
        STAGE0_FOULANT_PHASE1_TEMP_C,
        0.0,
        0.0,
        foulant_registry,
    )
    assert observed.path == "thermal"
    assert observed.onset_K == pytest.approx(onset_k)
    assert observed.extent == pytest.approx(expected_extent, abs=1e-12)


def test_carbon_partition_diagnostic_anchored_to_sephton_source_row(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    row = _load_yaml(CARBON_PARTITION)["phase_partitions"]["ci_carbonaceous_chondrite"]
    refractory = row["f_refractory_organic_C"]
    assert refractory["source"] == "REF-024 sephton_2004_murchison_hydropyrolysis"
    assert refractory["floor"] == 0.39
    assert refractory["iom_anchor"] == 0.56
    assert row["f_carbonate_C"] == {"value": None, "status": "not_speciated"}

    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    feed_kg = 100.0
    feed_formula = resolve_species_formula(
        "carbonaceous_organic", sim.species_formula_registry,
    )
    species_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    declared_c_mol = feed_formula.atom_moles(species_mol).get("C", 0.0)
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_PARTITION_CARBON,
        "carrier": "carbonaceous_organic",
        "feed_kg": feed_kg,
        "carbon_partition_row": row,
        "phase_specs": (
            {"phase": 1, "T_C": STAGE0_FOULANT_PHASE1_TEMP_C, "pO2_bar": 0.2},
        ),
        "foulant_registry": foulant_registry,
    })

    assert result.diagnostic["declared_c_mol"] == pytest.approx(declared_c_mol)
    assert result.diagnostic["refractory_mol"] == pytest.approx(
        declared_c_mol * 0.39, abs=1e-6,
    )
    assert result.diagnostic["labile_mol"] == pytest.approx(
        declared_c_mol * 0.61, abs=1e-6,
    )
    assert result.diagnostic["carbonate_mol"] == "not_speciated"
    assert result.diagnostic["not_speciated"] == ["f_carbonate_C"]


def test_provider_declares_foulant_residual_accounts():
    provider = BuiltinStage0PretreatmentProvider()
    accounts = provider.capability_profile().declared_accounts
    assert "terminal.stage0_residual_refractory_carbon" in accounts
    assert "terminal.stage0_residual_carbonate_carbon" in accounts


def test_volatilization_diagnostic_matches_helper(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    phase_specs = (
        {"phase": 1, "T_C": 1200.0, "p_overhead_bar": 0.2},
        {"phase": 2, "T_C": 1200.0, "p_overhead_bar": 0.001},
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_VOLATILIZATION,
        "carrier": "NaCl",
        "feed_kg": 10.0,
        "phase_specs": phase_specs,
        "foulant_registry": foulant_registry,
    })
    assert result.transition is None
    diag = result.diagnostic
    assert diag["cumulative_escaped_frac"] == pytest.approx(
        1.0
        - (1.0 - chi_escape_salt("NaCl", 1200.0, 0.2, foulant_registry).escaped_frac)
        * (1.0 - chi_escape_salt("NaCl", 1200.0, 0.001, foulant_registry).escaped_frac),
        abs=1e-9,
    )
    assert diag["wall_deposit_frac"] == diag["cumulative_escaped_frac"]


def test_sulfate_decomp_diagnostic_matches_helper(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_SULFATE_DECOMP,
        "carrier": "CaSO4",
        "feed_kg": 5.0,
        "T_C": 1450.0,
        "pO2_bar": 0.01,
        "foulant_registry": foulant_registry,
    })
    expected = chi_decomp("CaSO4", 1450.0, 0.01, 0.0, foulant_registry)
    assert result.diagnostic["extent"] == pytest.approx(expected.extent, abs=1e-9)
    assert result.diagnostic["fiat_extent"] == 1.0
    assert result.transition is None


def test_silicate_displacement_na2co3_not_bare_thermal_at_cap(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "ceres_regolith",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_SILICATE_DISPLACEMENT,
        "carrier": "Na2CO3",
        "feed_kg": 12.0,
        "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
        "melt_sio2_kg": 0.0,
        "foulant_registry": foulant_registry,
    })
    assert result.diagnostic["melt_sio2_gate"] == 0.0
    assert result.diagnostic["extent"] == 0.0
    assert result.diagnostic["product_melt_species"] == "Na2SiO3"


def test_carbonate_decomposition_diagnostic_extent(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_CARBONATE_DECOMPOSITION,
        "diagnostic_only": True,
        "species": "CaCO3",
        "feed_kg": 8.0,
        "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
        "foulant_registry": foulant_registry,
    })
    expected = chi_decomp("CaCO3", STAGE0_FOULANT_PHASE1_TEMP_C, 0.0, 0.0, foulant_registry)
    assert result.diagnostic["extent"] == pytest.approx(expected.extent, abs=1e-9)
    assert result.diagnostic["fiat_extent"] == 1.0


def test_partition_carbon_diagnostic_sephton_anchors(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    with CARBON_PARTITION.open(encoding="utf-8") as handle:
        row = yaml.safe_load(handle)["phase_partitions"]["ci_carbonaceous_chondrite"]
    feed_kg = 100.0
    feed_formula = resolve_species_formula(
        "carbonaceous_organic", sim.species_formula_registry,
    )
    species_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    declared_c_mol = feed_formula.atom_moles(species_mol).get("C", 0.0)
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_PARTITION_CARBON,
        "carrier": "carbonaceous_organic",
        "feed_kg": feed_kg,
        "carbon_partition_row": row,
        "phase_specs": (
            {"phase": 1, "T_C": STAGE0_FOULANT_PHASE1_TEMP_C, "pO2_bar": 0.2},
        ),
        "foulant_registry": foulant_registry,
    })
    expected = partition_carbon("carbonaceous_organic", declared_c_mol, row)
    assert result.diagnostic["refractory_mol"] == pytest.approx(
        expected.refractory_mol, abs=1e-6,
    )
    assert result.diagnostic["refractory_interval"]["reason"] is not None


def test_naf_volatilization_interval_not_certified_point(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    naf_row = vapor_pressure_data["foulant_vapor"]["NaF"]
    assert naf_row["interval_required"] is True
    assert naf_row["certified_point"] is None
    assert "antoine" not in naf_row

    split = chi_escape_salt("NaF", 1200.0, 0.2, foulant_registry)
    assert split.escaped_frac == 0.0
    assert split.retained_frac == 1.0
    assert split.confidence == "interval_only"
    assert split.status == "uncertified"
    assert split.warning is not None
    assert "NaF foulant volatilization uncertified" in split.warning

    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_VOLATILIZATION,
        "carrier": "NaF",
        "feed_kg": 1.0,
        "phase_specs": (
            {"phase": 1, "T_C": 1200.0, "p_overhead_bar": 0.2},
        ),
        "foulant_registry": foulant_registry,
    })
    assert result.status == "ok"
    assert result.transition is None
    assert result.warnings == (
        "NaF foulant volatilization uncertified - not modeled",
    )
    diag = result.diagnostic
    assert diag["cumulative_escaped_frac"] == 0.0
    assert diag["cumulative_retained_frac"] == 1.0
    assert diag["wall_deposit_frac"] == 0.0
    assert diag["warnings"] == result.warnings
    assert diag["phase_splits"] == [
        {
            "phase": 1,
            "T_C": 1200.0,
            "p_overhead_bar": 0.2,
            "escaped_frac": 0.0,
            "retained_frac": 1.0,
            "confidence": "interval_only",
            "status": "uncertified",
            "warning": "NaF foulant volatilization uncertified - not modeled",
        },
    ]


def test_refractory_fluoride_inert_to_rump(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_INERT_TO_RUMP,
        "carrier": "CaF2",
        "feed_kg": 3.0,
        "foulant_registry": foulant_registry,
    })
    assert result.diagnostic["rump_frac"] == 1.0
    assert result.diagnostic["escaped_frac"] == 0.0


def _load_batch_sim(
    feedstock_key: str,
    *,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    diagnostics_enabled: bool = True,
) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints_data,
        feedstocks_data,
        vapor_pressure_data,
    )
    sim._foulant_diagnostics_enabled = diagnostics_enabled
    additives = None
    if feedstock_key == "mars_sulfate_rich":
        fs = feedstocks_data[feedstock_key]
        additives = {
            "C": PyrolysisSimulator._carbon_reductant_required_kg(fs, 1000.0),
        }
    sim.load_batch(feedstock_key, mass_kg=1000.0, additives_kg=additives)
    return sim


@pytest.mark.parametrize(
    "feedstock_key",
    ["lunar_mare_low_ti", "mars_sulfate_rich", "ci_carbonaceous_chondrite"],
)
def test_foulant_diagnostics_golden_neutral_mass_balance(
    feedstock_key,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _load_batch_sim(
        feedstock_key,
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
    )
    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)
    assert isinstance(sim._stage0_foulant_diagnostics, list)


@pytest.mark.parametrize(
    "feedstock_key,expected_families",
    [
        (
            "ci_carbonaceous_chondrite",
            {
                REACTION_FAMILY_PARTITION_CARBON,
                REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                REACTION_FAMILY_SILICATE_DISPLACEMENT,
            },
        ),
        (
            "cm_carbonaceous_chondrite",
            {
                REACTION_FAMILY_PARTITION_CARBON,
                REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                REACTION_FAMILY_SILICATE_DISPLACEMENT,
            },
        ),
        (
            "ceres_regolith",
            {
                REACTION_FAMILY_PARTITION_CARBON,
                REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                REACTION_FAMILY_SILICATE_DISPLACEMENT,
            },
        ),
        (
            "mars_sulfate_rich",
            {
                REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                REACTION_FAMILY_SILICATE_DISPLACEMENT,
                REACTION_FAMILY_SULFATE_DECOMP,
                REACTION_FAMILY_VOLATILIZATION,
            },
        ),
    ],
)
def test_messy_feedstocks_emit_nonzero_runtime_diagnostics(
    feedstock_key,
    expected_families,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _load_batch_sim(
        feedstock_key,
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
    )
    families = {row["reaction_family"] for row in sim._stage0_foulant_diagnostics}
    assert families == expected_families
    assert len(sim._stage0_foulant_diagnostics) > 0


@pytest.mark.parametrize(
    "feedstock_key",
    ["lunar_mare_low_ti", "mars_sulfate_rich", "ci_carbonaceous_chondrite"],
)
def test_foulant_diagnostics_byte_identical_golden_neutral(
    feedstock_key,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim_on = _load_batch_sim(
        feedstock_key,
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
        diagnostics_enabled=True,
    )
    sim_off = _load_batch_sim(
        feedstock_key,
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
        diagnostics_enabled=False,
    )
    assert sim_on.atom_ledger.mol_by_account() == sim_off.atom_ledger.mol_by_account()
    assert sim_on.inventory.melt_oxide_kg == sim_off.inventory.melt_oxide_kg


def test_mars_sulfate_emits_sulfate_decomp_diagnostic(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints_data,
        feedstocks_data,
        vapor_pressure_data,
    )
    fs = feedstocks_data["mars_sulfate_rich"]
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(fs, 1000.0)
    sim.load_batch(
        "mars_sulfate_rich",
        mass_kg=1000.0,
        additives_kg={"C": required_c},
    )
    families = {row["reaction_family"] for row in sim._stage0_foulant_diagnostics}
    assert REACTION_FAMILY_SULFATE_DECOMP in families


def test_phased_chloride_accumulation_order(
    vapor_pressure_data, feedstocks_data, setpoints_data, foulant_registry,
):
    sim = _build_sim(
        "ceres_regolith",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    phase_specs = (
        {
            "phase": 1,
            "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
            "p_overhead_bar": STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
        },
        {
            "phase": 2,
            "T_C": STAGE0_FOULANT_PHASE2_TEMP_C,
            "p_overhead_bar": STAGE0_FOULANT_PHASE2_OVERHEAD_BAR,
        },
    )
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_VOLATILIZATION,
        "carrier": "KCl",
        "feed_kg": 4.0,
        "phase_specs": phase_specs,
        "foulant_registry": foulant_registry,
    })
    p1 = result.diagnostic["phase_splits"][0]["escaped_frac"]
    p2 = result.diagnostic["phase_splits"][1]["escaped_frac"]
    assert p2 > p1
    assert result.diagnostic["cumulative_escaped_frac"] > p1
