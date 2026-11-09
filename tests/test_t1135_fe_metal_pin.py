"""Base production pins for the metallic iron feedstock representation."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from flask import Flask
from scripts.grid_pregrind import (
    canonical_json,
    composition_wt_pct_to_mol,
    normalize_composition,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.melt_backend.openimcc_bridge import _cleaned_melt_projection
from simulator.optimize.evalspec import feedstock_recipe_digest
from simulator.electrolysis import ElectrolysisModel
from simulator.state import MeltState, OXIDE_SPECIES
from test_t1135_fe_schema_pins import (
    FEEDSTOCKS,
    _digest,
    _normalize_core_mass_pin,
)
from web import routes as web_routes

from simulator.core import PyrolysisSimulator
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg


_DATA = Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml"
_MASS_KG = 1000.0
_EXPECTED_COMPONENT_KG_HEX = {
    "Ni": "0x1.2df6774db060ep+6",
    "Co": "0x1.01acc56103bc8p+2",
    "S": "0x1.929df46795d68p+3",
    "P": "0x1.01acc56103bc8p+1",
    "Fe": "0x1.c4f1b2f488916p+9",
}


def _load_feedstock() -> dict[str, Any]:
    return yaml.safe_load(_DATA.read_text())["m_type_metallic_phase"]


def _hex_masses(masses: dict[str, float]) -> dict[str, str]:
    return {key: value.hex() for key, value in masses.items()}


def test_base_metal_kg_normalization_and_core_projection_are_pinned() -> None:
    feedstock = _load_feedstock()

    normalized = normalized_feedstock_component_masses_kg(feedstock, _MASS_KG)
    assert _hex_masses(normalized) == _EXPECTED_COMPONENT_KG_HEX
    assert sum(normalized.values()) == pytest.approx(_MASS_KG, abs=1e-10)

    base_composition = dict(feedstock.get("composition_wt_pct", {}) or {})
    if "Fe" not in base_composition:
        base_composition["Fe"] = feedstock[
            "elemental_composition_wt_pct"
        ]["Fe"]
    core_masses = PyrolysisSimulator._component_masses_from_wt_pct(
        base_composition,
        _MASS_KG,
    )
    _normalize_core_mass_pin(core_masses, _MASS_KG)
    assert _hex_masses(core_masses) == _EXPECTED_COMPONENT_KG_HEX


def test_base_adapter_and_grid_vectors_are_pinned_for_every_fe_oxide_entry() -> None:
    alpha = AlphaMELTSBackend()
    magemin = MAGEMinBackend()
    projections: dict[str, dict[str, Any]] = {}

    for key, feedstock in FEEDSTOCKS.items():
        composition = feedstock.get("composition_wt_pct", {}) or {}
        if not {"FeO", "Fe2O3"}.intersection(composition):
            continue
        alphamelts_vector = alpha._normalize_composition_to_melts_basis(
            composition
        )
        magemin_vector = magemin._build_ig_bulk_vector(alphamelts_vector)
        grid_moles = composition_wt_pct_to_mol(alphamelts_vector)
        openimcc_wt_pct, openimcc_moles = _cleaned_melt_projection(grid_moles)
        projections[key] = {
            "alphamelts": alphamelts_vector,
            "magemin": magemin_vector,
            "vaporock": composition,
            "grid_moles": grid_moles,
            "grid_composition_key": canonical_json(
                normalize_composition(alphamelts_vector)
            ),
            "openimcc_wt_pct": openimcc_wt_pct,
            "openimcc_moles": openimcc_moles,
        }

    assert len(projections) == 26
    expected = {
        "alphamelts": "287c8a34bb0dafa25f958b19dd7b62da5f900757f59c1b8e3db9426c2cfc4bfb",
        "magemin": "b60afc1a4bd192214fa747157fe0dbf9be60c67c2e047881531da1df18aa5858",
        "vaporock": "a0c22b9fa2a27cf1659d64afd56078d49b90d3d62feb2faa5d580b1c317ec122",
        "grid_moles": "a2ee1f7c15ab2dbc335aefabfb780cc7e17a587e2a3560c5e34ae7f1550693e2",
        "grid_composition_key": "51f7be8a7f89d22949d5a58cfcd406622fdc1fe68c50a141ef3209aa8709dc96",
        "openimcc_wt_pct": "217799933395ac9f66ba6c5dbe8bddca5f58ced384dc575eb7d2aa24e3b9a940",
        "openimcc_moles": "20914fe207c1443fdc254a2b5f81cf7f21453b363dfcc1e5a88e1967af4e4e84",
    }
    assert {
        name: _digest({key: projection[name] for key, projection in projections.items()})
        for name in expected
    } == expected


def test_base_char_electrolysis_and_web_additive_outputs_are_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        key: entry
        for key, entry in FEEDSTOCKS.items()
        if {"FeO", "Fe2O3"}.intersection(
            entry.get("composition_wt_pct", {}) or {}
        )
        or "Fe" in (entry.get("composition_wt_pct", {}) or {})
        or (entry.get("elemental_composition_wt_pct", {}) or {}).get("Fe")
        is not None
    }
    char = {
        key: PyrolysisSimulator._carbon_reductant_required_kg(entry, _MASS_KG)
        for key, entry in entries.items()
    }

    model = ElectrolysisModel()
    electrolysis: dict[str, dict[str, Any]] = {}
    for key, entry in entries.items():
        masses = normalized_feedstock_component_masses_kg(entry, _MASS_KG)
        oxides = {
            name: value
            for name, value in masses.items()
            if name in OXIDE_SPECIES
        }
        result = model.step_hour(
            MeltState(composition_kg=oxides),
            voltage_V=3.0,
            current_A=100.0,
            T_C=1600.0,
        )
        electrolysis[key] = {
            name: result[name]
            for name in (
                "oxides_reduced_kg",
                "metals_produced_kg",
                "O2_produced_kg",
                "energy_kWh",
                "reason_refused",
            )
            if name in result
        }

    monkeypatch.setattr(
        web_routes,
        "get_visible_feedstock",
        lambda key, include_custom=True: entries.get(key),
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(web_routes.bp)
    client = app.test_client()
    web = {
        key: client.get(f"/api/additive-calc/{key}?mass_kg=1000").get_json()
        for key in entries
    }

    assert len(entries) == 27
    assert _digest(char) == (
        "bed92af234928b16635372df91a7981b0e7ad9debaed9813cb7ba1e0531d2bc8"
    )
    assert _digest(electrolysis) == (
        "c9a981b084c0f62c5d53e3aab226014001a5f793829bd9ab1f3308ef6e8eddf7"
    )
    assert _digest(web) == (
        "20c0071f01eb6bb5436057568c57f42842add55b5f7c812ac3df1550d33e2c4c"
    )


def test_base_optimizer_composition_identity_is_pinned_for_fe_oxide_entries() -> None:
    identities = {
        key: feedstock_recipe_digest(entry["composition_wt_pct"])
        for key, entry in FEEDSTOCKS.items()
        if {"FeO", "Fe2O3"}.intersection(
            entry.get("composition_wt_pct", {}) or {}
        )
    }

    assert len(identities) == 26
    assert _digest(identities) == (
        "b3412a61f0e2f81eb22bc1287f6ac295283e84c9b9f17c41fdf0371e4f33faa9"
    )


def test_base_feedstock_api_json_round_trip_is_pinned_for_fe_oxide_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        key: entry
        for key, entry in FEEDSTOCKS.items()
        if {"FeO", "Fe2O3"}.intersection(
            entry.get("composition_wt_pct", {}) or {}
        )
    }
    monkeypatch.setattr(
        web_routes,
        "get_visible_feedstock",
        lambda key, **_kwargs: entries.get(key),
    )
    app = Flask(__name__)
    app.register_blueprint(web_routes.bp)
    client = app.test_client()

    round_tripped = {}
    for key in entries:
        response = client.get(f"/api/feedstock/{key}")
        assert response.status_code == 200, key
        round_tripped[key] = response.get_json()["composition_wt_pct"]

    assert len(round_tripped) == 26
    assert _digest(round_tripped) == (
        "a0c22b9fa2a27cf1659d64afd56078d49b90d3d62feb2faa5d580b1c317ec122"
    )


def test_base_mars_basalt_feedstock_card_output_is_pinned() -> None:
    app = Flask(__name__)
    app.register_blueprint(web_routes.bp)
    response = app.test_client().get("/partials/feedstock-card/mars_basalt")

    assert response.status_code == 200
    assert _digest(response.get_data(as_text=True)) == (
        "e97fad8f11300ea895c9d0692914391c2313a96ec5e19653a0f8c3b7528b988d"
    )
