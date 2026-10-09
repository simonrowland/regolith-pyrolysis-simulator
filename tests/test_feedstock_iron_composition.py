from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.feedstock_composition import (
    fe2o3_equivalent_wt_pct,
    fe_metal,
    feot_equivalent_moles,
    resolve_feedstock_composition,
    total_fe,
)
from simulator.fe_redox import (
    feot_equivalent_wt_pct,
    intrinsic_melt_fO2,
    melt_mol_fractions_for_kress91,
)
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.base import InternalAnalyticalBackend


_DATA = Path(__file__).resolve().parents[1] / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((_DATA / name).read_text())


def _measured_split() -> dict:
    return {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 2.0, "Fe2O3": 1.0},
        "composition_basis": {
            "FeO": {"method": "wet chemistry", "source": "synthetic assay"},
            "Fe2O3": {"method": "Mössbauer", "source": "synthetic assay"},
        },
        "elemental_composition_wt_pct": {"Fe": 0.25},
    }


def _unknown_split() -> dict:
    return {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 3.0},
        "fe_redox_split_unknown": True,
        "composition_basis": {
            "fe_reporting_convention": "total Fe as FeO",
        },
        "elemental_composition_wt_pct": {"Fe": 0.25},
    }


def test_unknown_split_metadata_covers_every_total_basis_entry_without_a_prior() -> None:
    # CI, CM, MGS-1, and mars_basalt leave this set once a cited prior is seated.
    # The six terrestrial lunar simulants are total-basis and join it.
    # NU-LHT entries stay out: they are blocked and were not named.
    expected = {
        "s_type_asteroid_silicate",
        "m_type_silicate_phase",
        "v_type_vesta_hed",
        "e_type_enstatite_aubrite",
        "ceres_regolith",
        "comet_nucleus",
        "mars_sulfate_rich",
        "mars_phyllosilicate_clay",
        "mars_perchlorate_rich",
        "lunar_highlands_lhs1",
        "lunar_highlands_lhs1_yu_2025_reference",
        "lunar_mare_lms1",
        "lunar_mare_oprl2n",
        "lunar_eac_1a",
        "lunar_mls_1a",
    }
    feedstocks = _load_yaml("feedstocks.yaml")
    flagged = {
        key
        for key, entry in feedstocks.items()
        if entry.get("fe_redox_split_unknown") is True
    }

    assert flagged == expected
    for key in expected:
        assert feedstocks[key]["composition_basis"][
            "fe_reporting_convention"
        ] == "total Fe as FeO"


def test_resolver_exposes_canonical_map_provenance_and_explicit_iron_accessors() -> None:
    entry = _measured_split()
    resolved = resolve_feedstock_composition(entry)

    assert resolved.canonical_wt_pct == entry["composition_wt_pct"]
    assert resolved.provenance == entry["composition_basis"]
    assert resolved.fe_redox_split_unknown is False
    assert resolved.total_fe_basis == (
        "FeO-equivalent wt% on the declared composition basis"
    )
    assert resolved.total_fe == feot_equivalent_wt_pct(entry["composition_wt_pct"])
    assert total_fe(entry["composition_wt_pct"]) == resolved.total_fe
    assert resolved.canonical_wt_pct["FeO"] == 2.0
    assert resolved.canonical_wt_pct["Fe2O3"] == 1.0
    assert fe_metal(entry) == 0.25


def test_fe2o3_equivalent_preserves_the_declared_mass_model_factor() -> None:
    composition = _measured_split()["composition_wt_pct"]

    assert fe2o3_equivalent_wt_pct(composition) == pytest.approx(3.2226)
    assert fe2o3_equivalent_wt_pct(
        composition, feo_to_fe2o3_factor=2.0
    ) == pytest.approx(5.0)


def test_feot_equivalent_moles_counts_two_iron_atoms_per_ferric_formula_unit() -> None:
    # Two FeO formula units and three Fe2O3 formula units contain eight Fe atoms.
    assert feot_equivalent_moles({"FeO": 2.0, "Fe2O3": 3.0}) == pytest.approx(
        8.0
    )


def test_redox_helpers_consume_both_measured_oxides() -> None:
    composition = _measured_split()["composition_wt_pct"]

    fractions = melt_mol_fractions_for_kress91(composition)
    split_fO2 = intrinsic_melt_fO2(composition, 2000.0)
    ferrous_only_fO2 = intrinsic_melt_fO2(
        {"SiO2": 70.0, "FeO": 2.0}, 2000.0
    )

    assert fractions["FeOt"] > 0.0
    assert fractions["FeOt"] > melt_mol_fractions_for_kress91(
        {"SiO2": 70.0, "FeO": 2.0}
    )["FeOt"]
    assert split_fO2 != ferrous_only_fO2


def test_unknown_split_keeps_total_fe_and_metal_without_a_ferric_oxide() -> None:
    entry = _unknown_split()
    resolved = resolve_feedstock_composition(entry)

    assert resolved.fe_redox_split_unknown is True
    assert resolved.total_fe == 3.0
    assert resolved.canonical_wt_pct.get("Fe2O3", 0.0) == 0.0
    assert resolved.canonical_wt_pct["FeO"] == 3.0
    assert fe_metal(entry) == 0.25


def test_feedstock_json_round_trip_keeps_unknown_split_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flask import Flask
    from web import routes

    entry = _unknown_split()
    monkeypatch.setattr(
        routes,
        "get_visible_feedstock",
        lambda _key, **_kwargs: entry,
    )
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    response = app.test_client().get("/api/feedstock/synthetic")

    assert response.status_code == 200
    assert response.get_json() == entry


def test_metallic_feedstock_fe_stays_outside_oxide_map_and_crosschecks_are_ignored():
    feedstocks = _load_yaml("feedstocks.yaml")
    metallic = resolve_feedstock_composition(feedstocks["m_type_metallic_phase"])
    eac = resolve_feedstock_composition(feedstocks["lunar_eac_1a"])

    assert "Fe" not in metallic.canonical_wt_pct
    assert metallic.fe_metal == 90.0
    assert eac.fe_metal is None


def test_engine_result_attaches_unknown_split_notice(monkeypatch) -> None:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    feedstocks = _load_yaml("feedstocks.yaml")
    feedstock = feedstocks["lunar_mare_low_ti"]
    # The catalog prior and the unknown-split flag refuse together. This
    # test is the lower-bound notice, so the seated prior is dropped first.
    feedstock.pop("fe_redox_prior", None)
    feedstock["fe_redox_split_unknown"] = True
    feedstock["composition_basis"] = {
        "fe_reporting_convention": "total Fe as FeO",
    }
    sim = PyrolysisSimulator(
        backend,
        _load_yaml("setpoints.yaml"),
        feedstocks,
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    assert sim._feedstock_fe_redox_split_unknown is True
    monkeypatch.setattr(
        PyrolysisSimulator,
        "_refresh_vapor_pressures_from_kernel",
        lambda _self, _result: None,
    )
    monkeypatch.setattr(
        PyrolysisSimulator,
        "_attach_post_equilibrium_sulfsat",
        lambda _self, _result: None,
    )
    monkeypatch.setattr(
        PyrolysisSimulator,
        "_note_engine_commissioning_from_last_diagnostics",
        lambda _self: None,
    )
    result = EquilibriumResult(status="unavailable")

    returned = PyrolysisSimulator._record_equilibrium_status(sim, result)

    assert returned is result
    assert result.diagnostics["feedstock_iron_notice"]["code"] == "fe_redox_split"
    assert result.diagnostics["feedstock_iron_notice"]["authority"] == "lower_bound"
    assert "lower bound" in result.diagnostics["feedstock_iron_notice"]["message"].lower()
    assert result.diagnostics["feedstock_iron_notice"]["message"] == (
        sim.melt_fO2_seed_run_notice()["message"]
    )


@pytest.mark.parametrize(
    "entry, message",
    [
        (
            {
                **_unknown_split(),
                "composition_wt_pct": {"FeO": 2.0, "Fe2O3": 1.0},
            },
            "cannot be combined with Fe2O3",
        ),
        (
            {
                "composition_wt_pct": {"SiO2": 70.0},
                "fe_redox_split_unknown": True,
                "composition_basis": {
                    "fe_reporting_convention": "total Fe as FeO",
                },
            },
            "requires FeO",
        ),
        (
            {
                "composition_wt_pct": {"FeO": 3.0},
                "fe_redox_split_unknown": True,
                "composition_basis": {},
            },
            "fe_reporting_convention",
        ),
        (
            {
                **_measured_split(),
                "composition_basis": {
                    "FeO": {"method": "wet chemistry"},
                    "Fe2O3": {"method": "Mössbauer", "source": "synthetic assay"},
                },
            },
            "FeO.method and source",
        ),
        (
            {
                **_measured_split(),
                "composition_wt_pct": {"Fe2O3": 1.0},
            },
            "requires both FeO and Fe2O3",
        ),
        (
            {
                "composition_wt_pct": {"Fe": 1.0},
            },
            "metallic iron must be outside",
        ),
        (
            {"composition_wt_pct": {"FeO_total": 3.0}},
            "FeO_total is not a canonical feedstock oxide",
        ),
        (
            {
                "composition_wt_pct": {"FeO": 3.0},
                "fe_redox_split_unknown": "true",
                "composition_basis": {
                    "fe_reporting_convention": "total Fe as FeO",
                },
            },
            "must be a boolean",
        ),
    ],
)
def test_resolver_rejects_invalid_iron_representations(
    entry: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_feedstock_composition(entry)
