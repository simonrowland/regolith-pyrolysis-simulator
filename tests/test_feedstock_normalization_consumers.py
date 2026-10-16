from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest
import yaml

from simulator.accounting.formulas import resolve_species_formula
from simulator.additive_calculations import k_shuttle_potassium_additive_kg
from simulator.core import PyrolysisSimulator
from simulator.equipment import EquipmentDesigner
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.optimize import job_runner as optimizer_job_runner
from simulator.optimize.objective import compute_objectives
from simulator import equipment as equipment_module
from web import routes as web_routes


DATA_DIR = Path(__file__).parent.parent / "data"
FEEDSTOCK_KEY = "mars_phyllosilicate_clay"
MASS_KG = 1000.0


class _Ledger:
    def __init__(self, registry: dict) -> None:
        self.registry = registry
        self._balances = {"process.cleaned_melt": {}}

    def mol_by_account(self, account: str | None = None):
        if account is None:
            return {key: dict(value) for key, value in self._balances.items()}
        return dict(self._balances.get(account, {}))


class _ObjectiveSim:
    def __init__(self, product_kg: dict[str, float], registry: dict) -> None:
        self.atom_ledger = _Ledger(registry)
        self._product_kg = dict(product_kg)
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key=FEEDSTOCK_KEY,
            batch_mass_kg=MASS_KG,
            products_kg={},
            oxygen_stored_kg=0.0,
            oxygen_vented_kg=0.0,
            energy_electrical_plus_evaporation_kWh=1.0,
            total_hours=1,
        )
        self.melt = SimpleNamespace(hour=1)
        self.energy_electrical_plus_evaporation_cumulative_kWh = 1.0

    def product_ledger(self) -> dict[str, float]:
        return dict(self._product_kg)

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 0.0, "vented": 0.0, "total": 0.0}


@pytest.fixture
def feedstocks() -> dict:
    return yaml.safe_load((DATA_DIR / "feedstocks.yaml").read_text())


@pytest.fixture
def normalized(feedstocks: dict) -> dict[str, float]:
    return normalized_feedstock_component_masses_kg(feedstocks[FEEDSTOCK_KEY], MASS_KG)


@pytest.fixture
def prepared_components(feedstocks: dict) -> dict[str, float]:
    sim = _sim(feedstocks)
    feedstock = feedstocks[FEEDSTOCK_KEY]
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(
        feedstock, MASS_KG
    )
    sim.load_batch(FEEDSTOCK_KEY, MASS_KG, additives_kg={"C": required_c})
    return dict(sim.inventory.raw_components_kg)


@pytest.fixture
def client(tmp_path):
    optimizer_job_runner.reset_runner_cache()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    yield app.test_client()
    optimizer_job_runner.reset_runner_cache()


def _sim(feedstocks: dict) -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _element_mol(species: str, kg: float, element: str, registry: dict) -> float:
    formula = resolve_species_formula(species, registry)
    return kg / formula.molar_mass_kg_per_mol() * formula.elements.get(element, 0.0)


def test_normalized_helper_matches_ledger_component_masses(
    feedstocks: dict,
    normalized: dict[str, float],
) -> None:
    feedstock = feedstocks[FEEDSTOCK_KEY]
    declared_sum = sum(feedstock["composition_wt_pct"].values())
    assert declared_sum > 100.0

    sim = _sim(feedstocks)
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(feedstock, MASS_KG)
    sim.load_batch(FEEDSTOCK_KEY, MASS_KG, additives_kg={"C": required_c})

    assert sum(normalized.values()) == pytest.approx(MASS_KG)
    assert normalized["FeO"] == pytest.approx(sim.inventory.raw_components_kg["FeO"])
    assert normalized["H2O"] == pytest.approx(sim.inventory.raw_components_kg["H2O"])
    assert normalized["FeO"] != pytest.approx(
        MASS_KG * feedstock["composition_wt_pct"]["FeO"] / 100.0
    )
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_normalized_helper_rejects_negative_declared_component() -> None:
    feedstock = {"composition_wt_pct": {"SiO2": 110.0, "FeO": -10.0}}

    with pytest.raises(ValueError, match="composition_wt_pct.FeO.*negative"):
        normalized_feedstock_component_masses_kg(feedstock, MASS_KG)


def test_volatiles_train_uses_normalized_composition(
    feedstocks: dict,
    prepared_components: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}
    normalize = equipment_module.normalized_feedstock_component_masses_kg

    def capture_components(feedstock: dict, mass_kg: float) -> dict[str, float]:
        components = normalize(feedstock, mass_kg)
        observed.update(components)
        return components

    monkeypatch.setattr(
        equipment_module,
        "normalized_feedstock_component_masses_kg",
        capture_components,
    )
    spec = EquipmentDesigner().size_volatiles_train(MASS_KG, feedstocks[FEEDSTOCK_KEY])
    expected_volatile_kg = sum(
        prepared_components.get(species, 0.0)
        for species in ("Na2O", "K2O", "H2O", "S", "Cl")
    )
    assert observed.get("FeO", 0.0) == pytest.approx(prepared_components["FeO"])
    assert observed.get("Fe2O3", 0.0) == pytest.approx(prepared_components["Fe2O3"])
    raw_volatile_kg = MASS_KG * (
        feedstocks[FEEDSTOCK_KEY]["composition_wt_pct"]["Na2O"]
        + feedstocks[FEEDSTOCK_KEY]["composition_wt_pct"]["K2O"]
    ) / 100.0

    assert spec.design_point_kg_hr == pytest.approx(expected_volatile_kg / 10.0)
    assert spec.design_point_kg_hr != pytest.approx(raw_volatile_kg / 10.0)


def test_additive_calc_uses_normalized_oxide_kg(
    client,
    feedstocks: dict,
    prepared_components: dict[str, float],
) -> None:
    response = client.get(f"/api/additive-calc/{FEEDSTOCK_KEY}?mass_kg={MASS_KG}")

    assert response.status_code == 200
    payload = response.get_json()
    expected_k = round(
        k_shuttle_potassium_additive_kg(
            prepared_components["FeO"], prepared_components["Fe2O3"]
        ),
        1,
    )
    raw_k = round(
        k_shuttle_potassium_additive_kg(
            MASS_KG * feedstocks[FEEDSTOCK_KEY]["composition_wt_pct"]["FeO"] / 100.0,
            MASS_KG * feedstocks[FEEDSTOCK_KEY]["composition_wt_pct"].get("Fe2O3", 0.0) / 100.0,
        ),
        1,
    )
    assert payload["K"] == pytest.approx(expected_k)
    assert payload["K"] != pytest.approx(raw_k)


def test_composition_target_scoring_uses_normalized_feedstock_input_mol(
    feedstocks: dict,
    prepared_components: dict[str, float],
) -> None:
    registry = _sim(feedstocks)._registry_for_feedstock(feedstocks[FEEDSTOCK_KEY])
    input_fe_mol = sum(
        _element_mol(species, kg, "Fe", registry)
        for species, kg in prepared_components.items()
    )
    captured_fe_kg = input_fe_mol * resolve_species_formula("Fe").molar_mass_kg_per_mol()
    profile = {
        "profile_id": "normalized-feedstock-regression",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": FEEDSTOCK_KEY,
        "objectives": [
            {
                "type": "composition_target",
                "id": "normalized-feedstock-input",
                "metric": "composition_target:normalized-feedstock-input",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "regression for normalized feedstock input mol",
                "target": {
                    "pool": "captured_products",
                    "species_vector": {"Fe": "extract"},
                    "extraction": {
                        "basis": "input_element_mol",
                        "captured_pool": "captured_products",
                        "completeness_min": {"Fe": 1.0},
                    },
                    "score_weights": {"extraction": 1.0, "composition": 0.0},
                },
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "run": {"campaign": "C0", "hours": 1, "mass_kg": MASS_KG, "backend_name": "internal-analytical"},
        "fidelities": {"internal-analytical": {"backend_name": "internal-analytical", "hours": 1}},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
    }
    run = SimpleNamespace(
        simulator=_ObjectiveSim({"Fe": captured_fe_kg}, registry),
        trace=None,
    )

    result = compute_objectives(profile, run)
    evidence = result.evidence["composition_target:normalized-feedstock-input"][
        "composition_target"
    ]["extraction_completeness"]["species"]["Fe"]

    assert result.as_mapping()["composition_target:normalized-feedstock-input"] == pytest.approx(1.0)
    assert evidence["input_mol"] == pytest.approx(input_fe_mol)
