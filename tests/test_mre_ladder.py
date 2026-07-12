from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from simulator import mre_ladder
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.evaporation import EvaporationFlux
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import PyrolysisRun, build_per_hour_summary
from simulator.session import SimSession
from simulator.state import CampaignPhase, EnergyRecord, MeltState, MOLAR_MASS


def _repo_setpoints() -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    return yaml.safe_load((repo_root / "data" / "setpoints.yaml").read_text())


def _sim(setpoints: dict) -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        setpoints,
        {"x": {"label": "X", "composition_wt_pct": {"SiO2": 100}}},
        {"metals": {}, "oxide_vapors": {}},
    )


def _seed_cleaned_melt_kg(
    sim: PyrolysisSimulator,
    species_kg: dict[str, float],
) -> None:
    sim.atom_ledger = sim._new_atom_ledger()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {
            species: float(kg) / (MOLAR_MASS[species] / 1000.0)
            for species, kg in species_kg.items()
        },
        source="test cleaned melt seed",
    )
    sim._chem_kernel = sim._build_chemistry_kernel()


def _species_names(sequence: list[dict]) -> list[str]:
    return [entry["species"][0] for entry in sequence]


def _voltage_pairs(sequence: list[dict] | tuple[dict, ...]) -> tuple[tuple[str, float], ...]:
    return tuple((entry["species"][0], entry["voltage"]) for entry in sequence)


def test_c5_fields_default_off_and_pass_through_session_config():
    melt = MeltState()

    assert melt.c5_enabled is False
    assert melt.mre_target_species == ""
    assert melt.mre_max_voltage_V == pytest.approx(0.0)

    config = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        c5_enabled=True,
        mre_target_species="SiO2",
        mre_max_voltage_V=1.7,
    )._session_config()
    session = SimSession().start(config)

    assert config.c5_enabled is True
    assert config.mre_target_species == "SiO2"
    assert config.mre_max_voltage_V == pytest.approx(1.7)
    assert session.simulator.melt.c5_enabled is True
    assert session.simulator.melt.mre_target_species == "SiO2"
    assert session.simulator.melt.mre_max_voltage_V == pytest.approx(1.7)
    assert session.simulator.campaign_mgr.c5_enabled is True


def test_build_mre_voltage_sequence_matches_published_yaml_ladder():
    setpoints = _repo_setpoints()

    sequence = mre_ladder.build_mre_voltage_sequence(setpoints)

    assert _species_names(sequence) == [
        "K2O",
        "NiO",
        "Na2O",
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
        "TiO2",
        "MgO",
        "Al2O3",
        "CaO",
    ]
    assert [entry["voltage"] for entry in sequence] == pytest.approx([
        0.023465,
        0.39,
        0.408926,
        0.804340,
        1.118868,
        1.254731,
        1.491058,
        1.575521,
        1.792604,
        1.857324,
        2.208316,
    ], abs=1e-6)
    assert [entry["min_hold_hours"] for entry in sequence] == [
        2,
        2,
        2,
        3,
        2,
        2,
        5,
        3,
        5,
        8,
        10,
    ]
    assert sequence[1]["voltage_authority"] == "ellingham_fallback"
    assert sequence[5]["voltage_authority"] == "ellingham_graph"
    assert sequence[5]["voltage_authoritative"] is False
    assert sequence[5]["voltage_status"] == mre_ladder.MRE_MN_DIAGNOSTIC_STATUS
    assert all(
        entry["voltage_authority"] == "ellingham_graph"
        for idx, entry in enumerate(sequence)
        if idx != 1
    )


@pytest.mark.parametrize(
    "setpoints",
    [
        "malformed outer block",
        ["malformed outer block"],
        {"mre_voltage_sequence": "malformed ladder block"},
        {"mre_voltage_sequence": ["malformed ladder block"]},
    ],
)
def test_unusable_mre_yaml_blocks_use_documented_fallback(setpoints):
    sequence = mre_ladder.build_mre_voltage_sequence(setpoints)

    assert sequence
    assert _voltage_pairs(sequence) == _voltage_pairs(
        mre_ladder.MRE_VOLTAGE_LADDER_FALLBACK
    )


def test_authoritative_mre_voltage_reference_matches_ellingham_graph():
    cr = mre_ladder.mre_decomposition_voltage_reference(
        "Cr2O3",
        temperature_K=1873.15,
    )
    ti = mre_ladder.mre_decomposition_voltage_reference(
        "TiO2",
        temperature_K=1873.15,
    )

    assert cr is not None
    assert ti is not None
    assert cr.authority == "ellingham_graph"
    assert ti.authority == "ellingham_graph"
    assert cr.voltage == pytest.approx(1.119, abs=0.001)
    assert ti.voltage == pytest.approx(1.576, abs=0.001)

    standard_oxides = [
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
        "TiO2",
        "Al2O3",
        "MgO",
        "CaO",
    ]
    ordered = sorted(
        standard_oxides,
        key=lambda oxide: mre_ladder.canonical_mre_decomposition_voltage(
            oxide,
            temperature_K=1873.15,
        ),
    )
    assert ordered == [
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
        "TiO2",
        "MgO",
        "Al2O3",
        "CaO",
    ]

    al_low = mre_ladder.canonical_mre_decomposition_voltage(
        "Al2O3",
        temperature_K=1823.15,
    )
    mg_low = mre_ladder.canonical_mre_decomposition_voltage(
        "MgO",
        temperature_K=1823.15,
    )
    al_high = mre_ladder.canonical_mre_decomposition_voltage(
        "Al2O3",
        temperature_K=1873.15,
    )
    mg_high = mre_ladder.canonical_mre_decomposition_voltage(
        "MgO",
        temperature_K=1873.15,
    )
    assert al_low == pytest.approx(1.885517, abs=1e-6)
    assert mg_low == pytest.approx(1.845957, abs=1e-6)
    assert al_high == pytest.approx(1.857324, abs=1e-6)
    assert mg_high == pytest.approx(1.792604, abs=1e-6)
    assert mg_high < al_high


def test_mre_reference_carries_gas_phase_basis_for_mg_anchor():
    ref = mre_ladder.mre_decomposition_voltage_reference(
        "MgO",
        temperature_K=1848.15,
    )

    assert ref is not None
    assert ref.authority == "ellingham_graph"
    assert ref.authoritative is True
    assert ref.voltage == pytest.approx(1.81928, abs=1e-5)
    assert ref.metal_product_phase == "gas"
    assert "Mg(g)" in str(ref.ellingham_phase_basis)


def test_mn_graph_voltage_is_diagnostic_for_mre_consumption():
    ref = mre_ladder.mre_decomposition_voltage_reference(
        "MnO",
        temperature_K=1600.0,
    )

    assert ref is not None
    assert ref.authority == "ellingham_graph"
    assert ref.authoritative is False
    assert ref.status == mre_ladder.MRE_MN_DIAGNOSTIC_STATUS


def test_uncovered_mre_voltage_falls_back_to_static_with_flag():
    ref = mre_ladder.mre_decomposition_voltage_reference("NiO", temperature_K=1873.15)

    assert ref is not None
    assert ref.voltage == pytest.approx(mre_ladder.DECOMP_VOLTAGES["NiO"])
    assert ref.authority == "ellingham_fallback"
    assert ref.authoritative is False
    assert ref.status == "ellingham_query_failed:species_not_graph_covered"


@pytest.mark.parametrize(
    ("temperature_K", "status"),
    [
        (500.0, "ellingham_extrapolation_refused:extrapolation_limited"),
        (5000.0, "ellingham_nonpositive_refused:voltage"),
    ],
)
def test_graph_mre_voltage_refuses_extrapolated_or_nonphysical_authority(
    temperature_K,
    status,
):
    ref = mre_ladder.mre_decomposition_voltage_reference(
        "FeO",
        temperature_K=temperature_K,
    )

    assert ref is not None
    assert ref.voltage == pytest.approx(mre_ladder.DECOMP_VOLTAGES["FeO"])
    assert ref.authority == "ellingham_fallback"
    assert ref.authoritative is False
    assert ref.status == status
    if temperature_K == 500.0:
        assert ref.raw_graph_voltage_V is not None
        assert ref.raw_graph_voltage_V > ref.voltage
    else:
        assert ref.raw_graph_voltage_V is None


def test_graph_mre_voltage_refuses_nonfinite_delta_g(monkeypatch):
    class FakeSegment:
        phase_basis = "2 Fe(l) + O2 -> 2 FeO(s); test"

        def delta_g_kJ_per_mol_O2(self, temperature_K: float) -> float:
            return float("inf")

    def fake_segment(species: str, temperature_K: float) -> FakeSegment:
        assert species == "Fe"
        return FakeSegment()

    monkeypatch.setattr(mre_ladder, "ellingham_segment_for_temperature", fake_segment)

    ref = mre_ladder.mre_decomposition_voltage_reference(
        "FeO",
        temperature_K=1873.15,
    )

    assert ref is not None
    assert ref.voltage == pytest.approx(mre_ladder.DECOMP_VOLTAGES["FeO"])
    assert ref.authority == "ellingham_fallback"
    assert ref.authoritative is False
    assert ref.status == "ellingham_nonfinite_refused:delta_g"


def test_published_ladder_former_copy_sites_resolve_from_single_source():
    from engines.builtin.electrolysis_step import MRE_DECOMP_VOLTAGE_PROVENANCE
    from simulator.electrolysis import DECOMP_VOLTAGES as electrolysis_voltages

    setpoints = _repo_setpoints()
    sequence_entries = setpoints["mre_voltage_sequence"]["sequence"]
    hold_entries = setpoints["mre_stepped_voltage_holds"]["sequence"]
    token = mre_ladder.CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN

    assert all(entry.get("decomposition_V") == token for entry in sequence_entries)
    assert all(entry.get("voltage") == token for entry in hold_entries)
    assert electrolysis_voltages is mre_ladder.DECOMP_VOLTAGES

    expected_sequence_pairs = tuple(sorted(
        (
            (
                entry["species"],
                mre_ladder.canonical_mre_decomposition_voltage(entry["species"]),
            )
            for entry in sequence_entries
        ),
        key=lambda pair: pair[1],
    ))
    parsed_pairs = _voltage_pairs(mre_ladder.parse_ladder_from_setpoints(setpoints))
    provenance_pairs = tuple(
        (species, MRE_DECOMP_VOLTAGE_PROVENANCE[species]["standard_voltage_V"])
        for species, _voltage in expected_sequence_pairs
    )
    hold_pairs = tuple(sorted(
        (
            (
                entry["species"][0],
                mre_ladder.resolve_mre_decomposition_voltage(
                    entry["species"][0],
                    entry["voltage"],
                ),
            )
            for entry in hold_entries
        ),
        key=lambda pair: pair[1],
    ))
    fallback_pairs = _voltage_pairs(mre_ladder.MRE_VOLTAGE_LADDER_FALLBACK)

    assert parsed_pairs == expected_sequence_pairs
    assert provenance_pairs == expected_sequence_pairs
    assert hold_pairs == tuple(
        pair for pair in expected_sequence_pairs
        if pair[0] not in {"NiO", "Na2O", "K2O"}
    )
    assert fallback_pairs == tuple(
        pair for pair in expected_sequence_pairs
        if pair[0] not in {"Na2O", "K2O"}
    )


def test_parse_ladder_from_setpoints_matches_repo_yaml_shape():
    setpoints = _repo_setpoints()

    sequence = mre_ladder.parse_ladder_from_setpoints(setpoints)

    assert _species_names(sequence)[:3] == ["K2O", "NiO", "Na2O"]
    assert _species_names(sequence)[6:8] == ["SiO2", "TiO2"]
    assert sequence[6]["voltage"] == pytest.approx(1.491058, abs=1e-6)
    assert sequence[7]["voltage"] == pytest.approx(1.575521, abs=1e-6)


def test_max_voltage_for_target_uses_ladder_ground_truth():
    sequence = mre_ladder.parse_ladder_from_setpoints(_repo_setpoints())

    assert mre_ladder.max_voltage_for_target("SiO2", sequence) == pytest.approx(
        1.491058,
        abs=1e-6,
    )
    assert mre_ladder.max_voltage_for_target("TiO2", sequence) == pytest.approx(
        1.575521,
        abs=1e-6,
    )
    assert mre_ladder.max_voltage_for_target("CaO", sequence) == pytest.approx(
        2.208316,
        abs=1e-6,
    )
    assert mre_ladder.max_voltage_for_target("not-an-oxide", sequence) == pytest.approx(0.0)


def test_filter_steps_up_to_target_max_selects_physical_prefixes():
    sequence = mre_ladder.parse_ladder_from_setpoints(_repo_setpoints())

    si_steps = mre_ladder.filter_steps_up_to_max_v(
        sequence, mre_ladder.max_voltage_for_target("SiO2", sequence)
    )
    ti_steps = mre_ladder.filter_steps_up_to_max_v(
        sequence, mre_ladder.max_voltage_for_target("TiO2", sequence)
    )

    assert "SiO2" in _species_names(si_steps)
    assert "TiO2" not in _species_names(si_steps)
    assert "CaO" not in _species_names(si_steps)
    assert "SiO2" in _species_names(ti_steps)
    assert "TiO2" in _species_names(ti_steps)
    assert "CaO" not in _species_names(ti_steps)


def test_preset_catalog_includes_disabled_alkali_targets():
    presets = mre_ladder.preset_catalog(_repo_setpoints())
    by_target = {preset.get("mre_target_species"): preset for preset in presets}

    assert by_target[""]["c5_enabled"] is False
    assert by_target["SiO2"]["enabled"] is True
    assert by_target["SiO2"]["mre_max_voltage_V"] == pytest.approx(
        1.491058,
        abs=1e-6,
    )
    assert by_target["Na2O"]["enabled"] is False
    assert by_target["K2O"]["enabled"] is False
    assert "pre-depleted" in by_target["Na2O"]["disabled_reason"]
    assert "pre-depleted" in by_target["K2O"]["disabled_reason"]


def test_step_mre_dispatch_uses_selected_runtime_max_voltage():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {
                    "species": "SiO2",
                    "decomposition_V": 1.7,
                    "min_hold_hours": 0,
                },
            ],
            "voltage_strategy": {
                "branch_two": {
                    "max_V": 1.7,
                },
            },
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.7
    captured: dict = {}

    def fake_dispatch(
        _intent,
        *,
        control_inputs,
        fO2_log=None,
        fe_redox_policy="intrinsic",
    ):
        captured.update(control_inputs)
        captured["fO2_log"] = fO2_log
        captured["fe_redox_policy"] = fe_redox_policy
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured["voltage_V"] == pytest.approx(1.7)
    assert captured["current_A"] == pytest.approx(mre_ladder.C5_LIMITED_MRE_CURRENT_A)
    assert captured["allowed_oxides"] == ["SiO2"]
    assert captured["melt_fO2_log"] == pytest.approx(-9.0)
    assert captured["fO2_log"] == pytest.approx(-9.0)
    assert captured["fe_redox_policy"] == "kress91_live"


def test_step_mre_restricts_reducible_oxides_to_target_rung():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
                {"species": "TiO2", "decomposition_V": 1.70, "min_hold_hours": 0},
                {"species": "CaO", "decomposition_V": 2.5, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 1
    captured: list[dict] = []

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured
    assert captured[0]["allowed_oxides"] == ["SiO2"]
    assert captured[0]["voltage_V"] == pytest.approx(1.45)


def test_c5_ellingham_ladder_diagnostic_emits_and_flags_synthetic_reordering(
    monkeypatch,
):
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 3},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 3},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 0.75
    sim.melt.temperature_C = 1600.0
    sim.melt.composition_kg = {"FeO": 10.0, "SiO2": 10.0}
    _seed_cleaned_melt_kg(sim, {"FeO": 10.0, "SiO2": 10.0})
    sim._mre_voltage_step_idx = 0
    captured: list[dict] = []

    def fake_delta_g(species: str, temperature_K: float) -> float:
        assert temperature_K == pytest.approx(1873.15)
        if species == "Si":
            return -200.0
        if species == "Fe":
            return -500.0
        raise KeyError(species)

    class FakeSegment:
        def __init__(self, species: str):
            self.species = species
            self.phase_basis = f"{species}(l) + O2 -> oxide; test"

        def delta_g_kJ_per_mol_O2(self, temperature_K: float) -> float:
            return fake_delta_g(self.species, temperature_K)

    def fake_segment(species: str, temperature_K: float) -> FakeSegment:
        return FakeSegment(species)

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    monkeypatch.setattr(mre_ladder, "ellingham_segment_for_temperature", fake_segment)
    monkeypatch.setattr(mre_ladder, "ellingham_fit_extrapolation", lambda *a, **k: None)
    monkeypatch.setattr(mre_ladder, "ellingham_metal_phase_kind", lambda *a, **k: "condensed")
    sim._commanded_pO2_bar = lambda: 1.0
    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured[0]["allowed_oxides"] == ["FeO"]
    assert captured[0]["voltage_V"] == pytest.approx(0.75)
    diagnostic = sim._mre_ellingham_ladder_diagnostic
    assert diagnostic["certification"] == "diagnostic_uncertified"
    assert diagnostic["authority"] == "authoritative_ellingham_graph_with_static_fallback"
    assert diagnostic["activity_basis"] == "gamma_x_single_cation_cleaned_melt_account"
    assert diagnostic["declared_rung_V"] == pytest.approx(0.75)
    assert diagnostic["rung_species"] == ["FeO"]
    assert diagnostic["species"]["FeO"]["oxide_activity"] == pytest.approx(
        0.4554261068621283
    )
    assert diagnostic["species"]["FeO"]["voltage_authority"] == "ellingham_graph"
    assert diagnostic["species"]["FeO"]["voltage_authoritative"] is True
    assert diagnostic["derived_Ed_V"]["FeO"] > 0.75
    assert diagnostic["species"]["SiO2"]["derived_Ed_V"] < 0.75
    assert diagnostic["species"]["SiO2"]["declared_after_held_rung"] is True
    assert diagnostic["reordering"]["ordering_divergence_detected"] is True
    assert diagnostic["reordering"]["other_species_below_declared_rung"] == ["SiO2"]

    snapshot = sim._make_snapshot()
    assert snapshot.mre_ellingham_ladder_diagnostic == diagnostic
    summary = build_per_hour_summary(sim, snapshot)
    assert summary["mre_ellingham_ladder_diagnostic"]["schema"] == (
        "c5_ellingham_ladder_diagnostic_v1"
    )


def test_c5_ladder_summary_records_non_authoritative_voltage_fallback():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": "canonical", "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim.melt.temperature_C = 5000.0 - 273.15
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = mre_ladder.DECOMP_VOLTAGES["FeO"]
    sim.melt.composition_kg = {"FeO": 10.0}
    _seed_cleaned_melt_kg(sim, {"FeO": 10.0})
    sim._mre_voltage_step_idx = 0

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._commanded_pO2_bar = lambda: 1.0
    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    snapshot = sim._make_snapshot()
    summary = build_per_hour_summary(sim, snapshot)
    diagnostic = summary["mre_ellingham_ladder_diagnostic"]
    feo = diagnostic["species"]["FeO"]
    non_authoritative = diagnostic["non_authoritative_voltage_by_oxide"]["FeO"]
    assert feo["voltage_authority"] == "ellingham_fallback"
    assert feo["voltage_authoritative"] is False
    assert non_authoritative["authoritative"] is False
    assert non_authoritative["authority"] == "ellingham_fallback"
    assert non_authoritative["status"] == (
        "ellingham_fallback:ellingham_nonpositive_refused:voltage"
    )


def test_c5_disabled_keeps_ellingham_ladder_diagnostic_empty_in_snapshot():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 3},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = False
    sim._mre_ellingham_ladder_diagnostic = {"schema": "stale"}

    sim._step_mre()

    assert sim._mre_ellingham_ladder_diagnostic == {}
    assert sim._make_snapshot().mre_ellingham_ladder_diagnostic == {}


def test_c5_ellingham_ladder_diagnostic_failure_does_not_change_dispatch(
    monkeypatch,
):
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 3},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 0.75
    sim.melt.temperature_C = 1600.0
    sim.melt.composition_kg = {"FeO": 10.0}
    _seed_cleaned_melt_kg(sim, {"FeO": 10.0})
    captured: list[dict] = []

    def fail_diagnostic(**_kwargs):
        raise RuntimeError("synthetic diagnostic failure")

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    monkeypatch.setattr(sim, "_build_c5_ellingham_ladder_diagnostic", fail_diagnostic)
    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured[0]["voltage_V"] == pytest.approx(0.75)
    assert captured[0]["allowed_oxides"] == ["FeO"]
    diagnostic = sim._mre_ellingham_ladder_diagnostic
    assert diagnostic["activity_basis"] == "gamma_x_single_cation_cleaned_melt_account"
    assert diagnostic["status"] == "diagnostic_failed:RuntimeError"
    assert diagnostic["declared_rung_V"] == pytest.approx(0.75)
    assert diagnostic["rung_species"] == ["FeO"]


def test_non_mre_step_clears_prior_c5_ellingham_ladder_diagnostic(monkeypatch):
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 3},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 0.75
    sim.melt.temperature_C = 1600.0
    sim.melt.composition_kg = {"FeO": 10.0}
    _seed_cleaned_melt_kg(sim, {"FeO": 10.0})

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None
    sim._step_mre()
    assert sim._mre_ellingham_ladder_diagnostic

    sim.melt.campaign = CampaignPhase.C0
    monkeypatch.setattr(
        sim.campaign_mgr,
        "apply_lab_schedule_controls",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        sim.campaign_mgr,
        "apply_c2a_staged_gas_controls",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        sim.campaign_mgr,
        "check_endpoint",
        lambda *_args, **_kwargs: False,
    )
    for name in (
        "_sync_c2a_staged_overhead_gas_control",
        "validate_lab_surface_temperature_resolver",
        "_update_temperature",
        "_apply_oxygen_reservoir_exchange",
        "_apply_o2_bubbler",
        "_apply_fe_redox_respeciation",
        "_refresh_oxygen_reservoir_transport_pO2_for_vapor",
        "_sync_oxygen_kg_counters",
        "_update_overlap_evaporation_diagnostic",
        "_update_extraction_completeness_diagnostic",
    ):
        monkeypatch.setattr(sim, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sim.melt, "validate_melt_pressures", lambda: None)
    monkeypatch.setattr(
        sim,
        "_apply_native_fe_saturation_split",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(sim, "_get_equilibrium", lambda: SimpleNamespace())
    monkeypatch.setattr(sim, "_calculate_evaporation", lambda _eq: EvaporationFlux())
    monkeypatch.setattr(sim, "_apply_analytic_evaporation_depletion", lambda flux: flux)
    monkeypatch.setattr(sim, "_update_melt_composition", lambda _flux: None)
    monkeypatch.setattr(
        sim,
        "_has_remaining_fe_redox_internal_o2_capacity",
        lambda: False,
    )
    monkeypatch.setattr(sim, "_get_turbine_spec", lambda: None)
    monkeypatch.setattr(sim, "_overhead_headspace_enabled", lambda: False)
    monkeypatch.setattr(sim, "_ledger_o2_kg", lambda _account: 0.0)
    monkeypatch.setattr(
        sim.overhead_model,
        "update",
        lambda *_args, **_kwargs: sim.overhead,
    )
    monkeypatch.setattr(
        sim,
        "_dispatch_overhead_bleed",
        lambda **_kwargs: SimpleNamespace(diagnostic={}),
    )
    monkeypatch.setattr(
        sim,
        "_attribute_o2_bubbler_vented_from_bleed",
        lambda _result: None,
    )
    monkeypatch.setattr(
        sim.energy_tracker,
        "calculate_hour",
        lambda *_args, **_kwargs: EnergyRecord(),
    )
    monkeypatch.setattr(sim.energy_tracker, "cumulative_breakdown", lambda: {})
    monkeypatch.setattr(
        sim,
        "_evap_plane_selectivity_diagnostic",
        lambda _flux: {},
    )
    monkeypatch.setattr(sim, "_compute_fe_redox_split_diagnostic", lambda: {})
    monkeypatch.setattr(sim, "_redox_source_breakdown_diagnostic", lambda: {})
    monkeypatch.setattr(sim, "_oxygen_total_kg", lambda: 0.0)

    snapshot = sim.step()

    assert sim._mre_ellingham_ladder_diagnostic == {}
    assert snapshot.mre_ellingham_ladder_diagnostic == {}


def test_c5_safety_max_hold_advances_without_low_current():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = ""
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = int(mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR) - 1
    sim._mre_effective_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert sim._mre_voltage_step_idx == 1
    assert sim._mre_hold_hours == 0


def test_c5_safety_max_hold_stops_after_terminal_rung():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = int(mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR) - 1
    sim._mre_effective_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
    dispatches = 0

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        nonlocal dispatches
        dispatches += 1
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()
    assert sim._mre_voltage_step_idx == 1
    assert sim._mre_hold_hours == 0

    sim._step_mre()
    assert dispatches == 1
    assert sim._mre_voltage_V == pytest.approx(0.0)
    assert sim._mre_current_A == pytest.approx(0.0)
    assert sim._mre_effective_current_A == pytest.approx(0.0)


def test_c5_kress91_live_ferric_inventory_becomes_ferrous_behavior():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 5.0, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim.atom_ledger = sim._new_atom_ledger()
    fe2o3_mol = 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"Fe2O3": fe2o3_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 5.0
    sim.melt.temperature_C = 1600.0

    produced_o2_kg = sim._step_mre()
    cleaned = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    o2 = sim.atom_ledger.mol_by_account("terminal.oxygen_mre_anode_stored")

    converted_fe2o3_mol = fe2o3_mol - cleaned.get("Fe2O3", 0.0)
    assert produced_o2_kg > 0.0
    assert converted_fe2o3_mol > 0.0
    assert cleaned["FeO"] == pytest.approx(2.0 * converted_fe2o3_mol)
    assert o2["O2"] == pytest.approx(0.5 * converted_fe2o3_mol)
    marker = sim._mre_uncertified_yield["FeO"]
    assert marker["certification"] == "uncertified_ferric_to_ferrous_reference"
    assert marker["reference_V"] == pytest.approx(0.65)
    assert marker["reference_status"] == (
        "uncertified_heuristic_reference_not_raw_thermo"
    )

    snapshot = sim._make_snapshot()
    assert snapshot.mre_uncertified_yield["FeO"]["produced_mol"] > 0.0
    summary = build_per_hour_summary(sim, snapshot)
    assert summary["mre_uncertified_yield"]["FeO"]["certification"] == (
        "uncertified_ferric_to_ferrous_reference"
    )


def test_c5_sio2_target_step_does_not_reduce_feo():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 1
    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": 10.0,
                "Fe2O3": 10.0,
                "SiO2": 10.0,
            },
        },
        species_formula_registry={},
    )
    reductions: dict[str, float] = {}

    def dispatch_with_provider(_intent, *, control_inputs, fO2_log, fe_redox_policy):
        result = provider.dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1600.0,
                pressure_bar=1e-9,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
            )
        )
        reductions.update(result.diagnostic.get("oxides_reduced_kg", {}))
        return SimpleNamespace(diagnostic=result.diagnostic, transition=None)

    sim._dispatch_only = dispatch_with_provider
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert reductions.get("SiO2", 0.0) > 0.0
    assert reductions.get("Fe2O3", 0.0) > 0.0
    assert "FeO" not in reductions
