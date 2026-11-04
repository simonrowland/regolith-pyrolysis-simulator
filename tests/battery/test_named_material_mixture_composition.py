"""t-1593: a named material plus an element or oxide binds as a mass-weighted mixture.

The named material's oxides come from a cited record in the same extract, or
from a loadable feedstock composition. Metallic Mg stays Mg0. The printed
JSC-1A numbers are Delgado 2015 Table 1 column "JSC-1A [9]".
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.composition import mass_weighted_mixture_wt_pct
from simulator.battery.enums import AdmissionStatus, NoticeKind, RefusalReason
from simulator.battery.identity import celsius_to_kelvin
from simulator.battery.migrate import (
    migrate,
    oxide_molar_mass,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import Composition
from simulator.battery.score import (
    ScoreContext,
    comparison_candidates,
    selected_lineage_level,
)
from simulator.battery.validate import validate_observation
from simulator.state_types import State

# Delgado 2015 Table 1, JSC-1A [9]. Sum is 98.1 wt%; the remainder stays out.
JSC_1A_WT_PCT = {
    "SiO2": Decimal("45.7"),
    "Al2O3": Decimal("16.2"),
    "Fe2O3": Decimal("12.4"),
    "CaO": Decimal("10.0"),
    "MgO": Decimal("8.7"),
    "Na2O": Decimal("3.2"),
    "TiO2": Decimal("1.9"),
}
# Steer: do not add the metal to MgO as 26 wt% * M(MgO) / M(Mg).
_FOLDED_MG_INTO_MGO = Decimal("26") * Decimal("40.30") / Decimal("24.31")
_INVALID_COMPONENTS = (
    "mass-percent composition contains invalid, duplicate, unsupported or ambiguous components"
)


def test_mass_weighted_mixture_keeps_metal_out_of_its_oxide() -> None:
    """Worked sanity: 50 wt% Mg + 50 wt% of (50 SiO2 / 50 MgO)."""

    mixed = mass_weighted_mixture_wt_pct(
        (
            ({"Mg": Decimal("100")}, Decimal("1") / Decimal("2")),
            (
                {"SiO2": Decimal("50"), "MgO": Decimal("50")},
                Decimal("1") / Decimal("2"),
            ),
        )
    )
    assert mixed == {
        "Mg": Decimal("50"),
        "SiO2": Decimal("25"),
        "MgO": Decimal("25"),
    }
    folded = Decimal("25") + Decimal("50") * Decimal("40.30") / Decimal("24.31")
    assert mixed["MgO"] != folded


def _write_tree(root: Path, document: dict) -> Path:
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": [
                    {
                        "source_id": "fixture-source",
                        "citation": "Fixture, A. (2026), Test Journal 1:1",
                        "doi": "10.1234/FIXTURE",
                        "corpus": {
                            "commit": "abc123",
                            "raw": {"path": "raw/fixture.pdf", "sha256": "deadbeef"},
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (extracts / "fixture-source.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _components(pairs: list[tuple[str, str]]) -> list[list[str]]:
    return [[name, amount] for name, amount in pairs]


def _composition(
    pairs: list[tuple[str, str]],
    locator: dict,
    records: dict | None = None,
) -> dict:
    body: dict = {
        "basis": "printed_reactant_mass_percent",
        "amount_basis": "mass_percent",
        "components": _components(pairs),
    }
    if records is not None:
        body["named_material_records"] = records
    return {"state": {"tag": "value", "value": body}, "locator": locator}


def _observation(
    observation_id: str,
    composition: dict,
    locator: dict,
) -> dict:
    return {
        "observation_id": observation_id,
        "type": "activity_coefficient",
        "locator": locator,
        "T_C": "560",
        "phase": "l",
        "units": "dimensionless",
        "point_conditions": {"composition": composition},
        "values": {
            "quantity": "activity",
            "method_class": "measured_direct",
            "status": "typed_refusal",
            "reason": "synthetic row; the composition binding is the datum",
            "activity": None,
        },
    }


def _jsc_pairs() -> list[tuple[str, str]]:
    return [(name, str(amount)) for name, amount in JSC_1A_WT_PCT.items()]


def _document() -> dict:
    table_locator = {"page": 3, "table": "1", "note": "Table 1 JSC-1A [9]"}
    figure_locator = {
        "page": 4,
        "figure": "3",
        "note": "Figure 3 caption: 74 wt% JSC-1A and 26 wt% Mg.",
    }
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-11-03", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "Mg": {
                "observations": [
                    _observation(
                        "jsc1a_table1",
                        _composition(_jsc_pairs(), table_locator),
                        table_locator,
                    ),
                    _observation(
                        "fig3_mg_jsc1a",
                        _composition(
                            [("Mg", "26"), ("JSC-1A", "74")],
                            figure_locator,
                            {"JSC-1A": "jsc1a_table1"},
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "oxide_plus_jsc1a",
                        _composition(
                            [("SiO2", "26"), ("JSC-1A", "74")],
                            figure_locator,
                            {"JSC-1A": "jsc1a_table1"},
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "missing_record",
                        _composition(
                            [("Mg", "26"), ("JSC-1A", "74")],
                            figure_locator,
                            {"JSC-1A": "no-such-record"},
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "empty_feedstock",
                        _composition(
                            [("Mg", "26"), ("JSC-1A", "74")],
                            figure_locator,
                            {"JSC-1A": {"feedstock_id": "lunar_mare_jsc_1a_legacy"}},
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "open_fractions",
                        _composition(
                            [("Mg", "26"), ("JSC-1A", "70")],
                            figure_locator,
                            {"JSC-1A": "jsc1a_table1"},
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "uncited_shape",
                        _composition(
                            [("Mg", "26"), ("JSC-1A", "74")],
                            figure_locator,
                        ),
                        figure_locator,
                    ),
                    _observation(
                        "mls_feedstock",
                        _composition(
                            [("Mg", "50"), ("MLS-1A", "50")],
                            figure_locator,
                            {"MLS-1A": {"feedstock_id": "lunar_mls_1a"}},
                        ),
                        figure_locator,
                    ),
                ]
            }
        },
    }


def _migrated(tmp_path: Path, document: dict):
    result = migrate(_write_tree(tmp_path, document), write=False)
    return result.observations


def _recovered_wt(
    composition: Composition, anchor: str, anchor_wt: Decimal
) -> dict[str, Decimal]:
    """Invert mole fraction with the molar-mass owner. Anchor fixes the scale.

    Decimal division rounds at the 28th digit, so a recovered weight matches
    the printed weight inside 1e-18 and not to the last digit.
    """

    amounts = composition.as_map()
    scale = anchor_wt / (amounts[anchor] * oxide_molar_mass(anchor))
    return {
        name: amount * oxide_molar_mass(name) * scale
        for name, amount in amounts.items()
    }


def _assert_printed_wt(actual: Decimal, expected: Decimal) -> None:
    assert abs(actual - expected) < Decimal("1e-18")


def test_named_material_mixture_binds_and_refuses(tmp_path: Path) -> None:
    rows = _migrated(tmp_path / "base", _document())

    def row(observation_id: str):
        return rows[f"fixture-source::{observation_id}"]

    record = row("jsc1a_table1")
    assert record.identity.temperature_K == State.of(celsius_to_kelvin("560"))
    assert record.identity.composition is not None
    assert record.identity.composition.is_value
    assert record.identity.composition.value == wt_pct_to_mole_fraction(JSC_1A_WT_PCT)
    assert record.point_conditions["composition"].locator.page == 3
    assert record.point_conditions["composition"].locator.table == "1"

    mixture = row("fig3_mg_jsc1a")
    assert mixture.identity.temperature_K == State.of(celsius_to_kelvin("560"))
    assert mixture.evidence.original_method_class != "derived_composition"
    composition = mixture.identity.composition
    assert composition is not None and composition.is_value
    bound = composition.value
    assert bound is not None
    assert bound.basis == "derived_composition"
    assert "Mg" in bound.as_map()
    assert "MgO" in bound.as_map()
    recovered = _recovered_wt(bound, "Mg", Decimal("26"))
    printed_mgo = Decimal("74") / Decimal("100") * JSC_1A_WT_PCT["MgO"]
    _assert_printed_wt(recovered["Mg"], Decimal("26"))
    _assert_printed_wt(recovered["MgO"], printed_mgo)
    assert abs(recovered["MgO"] - (printed_mgo + _FOLDED_MG_INTO_MGO)) > Decimal("1")
    for name, weight in JSC_1A_WT_PCT.items():
        _assert_printed_wt(recovered[name], Decimal("74") / Decimal("100") * weight)
    assert set(recovered) == set(JSC_1A_WT_PCT) | {"Mg"}
    condition = mixture.point_conditions["composition"]
    assert condition.state.value == bound
    assert condition.locator.page == 4
    assert condition.locator.figure == "3"
    assert condition.inference is not None
    assert condition.inference.relation == "w_mix,j = sum_k f_k * w_k,j"
    assert condition.inference.method is not None
    assert condition.inference.method.state.value == "derived_composition"
    assert condition.inference.references == ("fixture-source::jsc1a_table1",)
    assert "composition_records=fixture-source::jsc1a_table1" in condition.inference.inputs
    assert mixture.derived_from in (None, ())
    lot = [notice for notice in mixture.notices if notice.kind is NoticeKind.LOT_VARIABILITY]
    assert len(lot) == 1
    assert "JSC-1 vs JSC-1A" in lot[0].reason
    assert "JSC-1A" in lot[0].reason
    assert lot[0].source == "fixture-source::jsc1a_table1"

    oxide_mix = row("oxide_plus_jsc1a")
    oxide_bound = oxide_mix.identity.composition.value
    assert "Si" not in oxide_bound.as_map()
    assert "Mg" not in oxide_bound.as_map()
    oxide_recovered = _recovered_wt(oxide_bound, "MgO", printed_mgo)
    _assert_printed_wt(oxide_recovered["MgO"], printed_mgo)
    _assert_printed_wt(
        oxide_recovered["SiO2"],
        Decimal("26") + Decimal("74") / Decimal("100") * JSC_1A_WT_PCT["SiO2"],
    )

    for observation_id, reason in (
        (
            "missing_record",
            "typed-refusal:named_material_without_cited_composition:JSC-1A",
        ),
        (
            "empty_feedstock",
            "typed-refusal:named_material_without_cited_composition:JSC-1A",
        ),
        (
            "open_fractions",
            "typed-refusal:named_material_mixture_mass_fractions_do_not_sum_to_1",
        ),
    ):
        refused = row(observation_id)
        assert refused.identity.temperature_K == State.of(celsius_to_kelvin("560"))
        assert refused.identity.composition is not None
        assert refused.identity.composition.is_unknown
        assert refused.identity.composition.reason == reason
        point = refused.point_conditions["composition"]
        assert point.state.reason == reason
        assert point.locator.page == 4
        assert all(notice.kind is not NoticeKind.LOT_VARIABILITY for notice in refused.notices)
        assert refused.derived_from in (None, ())

    uncited = row("uncited_shape")
    assert uncited.point_conditions["composition"].state.reason == _INVALID_COMPONENTS
    assert uncited.identity.composition is None or uncited.identity.composition.is_unknown
    if uncited.identity.composition is not None and uncited.identity.composition.is_unknown:
        assert not uncited.identity.composition.reason.startswith("typed-refusal:")

    feedstock = row("mls_feedstock")
    feedstock_bound = feedstock.identity.composition.value
    assert "Mg" in feedstock_bound.as_map()
    assert "SiO2" in feedstock_bound.as_map()
    # Hill et al. 2007 Table 1 MLS-1A MgO, the loadable feedstock cell.
    mls_recovered = _recovered_wt(feedstock_bound, "Mg", Decimal("50"))
    printed_mls_mgo = Decimal("1") / Decimal("2") * Decimal("6.19")
    _assert_printed_wt(mls_recovered["Mg"], Decimal("50"))
    _assert_printed_wt(mls_recovered["MgO"], printed_mls_mgo)
    folded_mls = printed_mls_mgo + Decimal("50") * Decimal("40.30") / Decimal("24.31")
    assert abs(mls_recovered["MgO"] - folded_mls) > Decimal("1")
    feed_condition = feedstock.point_conditions["composition"]
    assert feed_condition.inference.references == ("feedstock:lunar_mls_1a",)
    assert "composition_records=feedstock:lunar_mls_1a" in feed_condition.inference.inputs
    assert feedstock.derived_from in (None, ())
    feedstock_issues = validate_observation(feedstock, {}, rows)
    assert not any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and "derived_from" in issue.path
        for issue in feedstock_issues
    )
    assert any(notice.kind is NoticeKind.LOT_VARIABILITY for notice in feedstock.notices)


def test_admitted_cited_record_stays_an_independent_lineage(tmp_path: Path) -> None:
    """A cited composition row is not value ancestry.

    Table 1 is an admitted mass_loss_fraction. Fig. 3 is an admitted
    activity. selected_lineage_level stays true because the citation is
    not Observation.derived_from.
    """

    document = _document()
    kept = []
    for obs in document["species"]["Mg"]["observations"]:
        if obs["observation_id"] == "jsc1a_table1":
            obs["units"] = "1"
            obs["values"] = {
                "quantity": "mass_loss_fraction",
                "method_class": "measured_direct",
                "mass_loss_fraction": "0.10",
            }
            kept.append(obs)
        elif obs["observation_id"] == "fig3_mg_jsc1a":
            obs["values"] = {
                "quantity": "activity",
                "method_class": "measured_direct",
                "activity": "0.20",
            }
            kept.append(obs)
    document["species"]["Mg"]["observations"] = kept
    result = migrate(_write_tree(tmp_path, document), write=False)
    table = result.observations["fixture-source::jsc1a_table1"]
    mixture = result.observations["fixture-source::fig3_mg_jsc1a"]
    assert table.admission.status is AdmissionStatus.ADMITTED
    assert mixture.admission.status is AdmissionStatus.ADMITTED
    candidate_ids = {
        obs.observation_id
        for obs in comparison_candidates(
            ScoreContext(result.works, result.experiments, result.observations)
        )
    }
    assert table.observation_id in candidate_ids
    assert selected_lineage_level(mixture, candidate_ids)
    assert mixture.derived_from in (None, ())
    bound = mixture.identity.composition.value
    assert "Mg" in bound.as_map()
    recovered = _recovered_wt(bound, "Mg", Decimal("26"))
    printed_mgo = Decimal("74") / Decimal("100") * JSC_1A_WT_PCT["MgO"]
    _assert_printed_wt(recovered["MgO"], printed_mgo)
    assert abs(recovered["MgO"] - (printed_mgo + _FOLDED_MG_INTO_MGO)) > Decimal("1")
    assert mixture.point_conditions["composition"].inference.references == (
        "fixture-source::jsc1a_table1",
    )


def test_cited_record_mutation_changes_the_mixture(tmp_path: Path) -> None:
    original = _migrated(tmp_path / "original", _document())["fixture-source::fig3_mg_jsc1a"]
    mutated_document = deepcopy(_document())
    components = mutated_document["species"]["Mg"]["observations"][0]["point_conditions"][
        "composition"
    ]["state"]["value"]["components"]
    components[0][1] = "40.0"
    mutated = _migrated(tmp_path / "mutated", mutated_document)[
        "fixture-source::fig3_mg_jsc1a"
    ]
    original_sio2 = original.identity.composition.value.as_map()["SiO2"]
    mutated_sio2 = mutated.identity.composition.value.as_map()["SiO2"]
    assert mutated_sio2 != original_sio2
    recovered = _recovered_wt(mutated.identity.composition.value, "Mg", Decimal("26"))
    _assert_printed_wt(recovered["SiO2"], Decimal("74") / Decimal("100") * Decimal("40.0"))
    _assert_printed_wt(
        recovered["MgO"], Decimal("74") / Decimal("100") * JSC_1A_WT_PCT["MgO"]
    )
