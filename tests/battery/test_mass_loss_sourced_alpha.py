from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

import simulator.battery.score as score_module
from simulator.battery.enums import Engine, Quantity, RefusalReason, UncertaintyKind
from simulator.battery.migrate import LITERATURE, Migrator
from simulator.battery.records import Uncertainty
from simulator.battery.residue import ResidueInventoryRefusal, _mass_loss_sourced_alphas
from simulator.battery.score import predict_with_engine, quantity_token


@pytest.fixture
def fig3_source():
    # Exact executable fields of 04e03ba08's Fig. 3 context, restricted to R-11.
    # The source domain includes the six Richter 2002 H2 runs at 1500 C;
    # its local applicability list covers vacuum runs and excludes R-7.
    document = {"source_id": "kems-010-richter-2007", "species": {}}
    for formula, gas, prefix, energy, sigma in (
        ("Mg", "Mg(g)", 143.0, 121000.0, 53000.0),
        ("SiO", "SiO(g)", 25.3, 92000.0, 37000.0),
    ):
        document["species"][formula] = {"context": [{
            "observation_id": f"richter_2007_{formula.lower()}_cai_langmuir_alpha_arrhenius",
            "type": "best_fit_model_context",
            "locator": {
                "source_path": "docs/references/pdfs/99-kems-langmuir/kems-010-richter-2007.pdf",
                "published_page": 5553,
                "figure": "3",
            },
            "T_range_K": [1773.15, 2173.15],
            "applies_to_experiments": ["cai-r-11"],
            "units": "dimensionless",
            "uncertainty_convention": "2_sigma",
            "values": {"gas_species": gas, "alpha_form": {
                "type": "arrhenius", "gamma_o": prefix, "c0": prefix,
                "E_J_per_mol": energy, "E_uncertainty_J_per_mol": sigma,
                "R_J_per_mol_K": 8.314,
            }},
        }]}
    return document


def test_fig3_alphas_use_printed_fit_and_locator(fig3_source):
    experiment = SimpleNamespace(experiment_id="work::experiment::cai-r-11")
    fits = _mass_loss_sourced_alphas(experiment, fig3_source, 2073.15)
    # Independent 50-digit Decimal evaluation of the printed fits, not engine output.
    assert fits["Mg"]["alpha"] == pytest.approx(0.12780152611500154, rel=1e-13)
    assert fits["SiO"]["alpha"] == pytest.approx(0.12162550464734534, rel=1e-13)
    assert fits["Mg"]["locator"]["figure"] == "3"
    assert fits["Mg"]["T_range_K"] == [1773.15, 2173.15]
    assert not fits["Mg"]["extrapolated"]


@pytest.mark.parametrize("temperature,extrapolated", [(1773.15, False), (2473.0, True)])
def test_fit_domain_includes_1500C_and_labels_extrapolation(
    fig3_source, temperature, extrapolated
):
    experiment = SimpleNamespace(experiment_id="work::experiment::cai-r-11")
    fits = _mass_loss_sourced_alphas(experiment, fig3_source, temperature)
    assert all(fit["extrapolated"] is extrapolated for fit in fits.values())


@pytest.mark.parametrize("local_id,document_present", [("cai-r-7", True), ("cai-r-11", False)])
def test_absent_or_inapplicable_fits_keep_d204(fig3_source, local_id, document_present):
    experiment = SimpleNamespace(experiment_id=f"work::experiment::{local_id}")
    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_sourced_alphas(
            experiment, fig3_source if document_present else {}, 2073.15
        )
    assert failure.value.reason == "evaporation_coefficients_not_sourced"


@pytest.mark.parametrize("uncertainty_provided", [False, True])
def test_sourced_alpha_reports_implementation_limit_without_inventing_missing_data(
    fig3_source, monkeypatch, uncertainty_provided
):
    migrator = Migrator()
    migrator._migrate_extract(LITERATURE / "extracts/kems-010-richter-2007.yaml")
    row = next(
        row for row in migrator.result.observations.values()
        if quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and "row=r-11:" in row.observation_id
    )
    if uncertainty_provided:
        # Synthetic evidence probes the refusal label; this is not a claim
        # that Richter supplies a bulk-loss uncertainty.
        row = replace(row, uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED, verbatim="1% of initial sample mass",
            value=Decimal("0.01"), basis="absolute_fraction_of_initial_mass",
        ))
    original = score_module.load_yaml
    monkeypatch.setattr(score_module, "load_yaml", lambda path: (
        deepcopy(fig3_source) if path.name == "kems-010-richter-2007.yaml"
        else original(path)
    ))
    prediction = predict_with_engine(
        Engine.OPENIMCC, row,
        experiment=migrator.result.experiments[row.experiment_id],
    )
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "sourced_alpha_mass_loss_integration_incomplete"
    assert "missing_input" not in prediction.refusal_detail
    detail = json.loads(prediction.refusal_detail["detail"])
    assert detail["sourced_alphas"]["Mg"]["alpha"] == pytest.approx(0.12780152611500154)
    assert detail["unsourced_bound_fraction"] is None
