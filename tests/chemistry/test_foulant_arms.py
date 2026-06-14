"""DIAGNOSTIC-first foulant disposition arms (C1/C2/C4a/C4b/C4c/C5)."""

from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.fixture(scope="module")
def foulant_registry():
    return load_foulant_registry(FOULANT_THERMO)


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
    import yaml

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
    with pytest.raises(ValueError, match="certified-point"):
        chi_escape_salt("NaF", 1200.0, 0.2, foulant_registry)
    result = _dispatch(sim, {
        "reaction_family": REACTION_FAMILY_VOLATILIZATION,
        "carrier": "NaF",
        "feed_kg": 1.0,
        "phase_specs": (
            {"phase": 1, "T_C": 1200.0, "p_overhead_bar": 0.2},
        ),
        "foulant_registry": foulant_registry,
    })
    assert result.diagnostic["interval_required"] is True
    assert 0.0 <= result.diagnostic["phase_splits"][0]["escaped_frac"] <= 1.0


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
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints_data,
        feedstocks_data,
        vapor_pressure_data,
    )
    additives = None
    if feedstock_key == "mars_sulfate_rich":
        fs = feedstocks_data[feedstock_key]
        additives = {
            "C": PyrolysisSimulator._carbon_reductant_required_kg(fs, 1000.0),
        }
    sim.load_batch(feedstock_key, mass_kg=1000.0, additives_kg=additives)
    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)
    assert isinstance(sim._stage0_foulant_diagnostics, list)


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