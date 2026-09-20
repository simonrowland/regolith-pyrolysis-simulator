"""Bulk-property rows carry a composition map, not a single-species formula."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.battery.enums import AmountBasis
from simulator.battery.migrate import (
    REPO_ROOT,
    migrate,
    oxide_molar_mass,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import as_decimal
from tests.battery.test_migrate import _write_min_tree


_OBS_ROOTS = (
    REPO_ROOT / "data" / "literature" / "extracts-v2",
    REPO_ROOT / "data" / "literature" / "observations-v2",
)
_BULK_QUANTITIES = {
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
    "evolved_gas_yield",
}
_SAMPLE_CODE_FORMULA = re.compile(r"(?i)^(MLS[-_]?|MS)\d")
_MARKOVA_VI_PREFIX = (
    "kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite"
)
_MLS_BULK_IDS = {
    "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_table1_fresnel_pyrolysis_log::point:11",
    "kems-038-matchett-2006::matchett_2006_table3_fresnel_pyrolysis_log::point:11",
    "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction",
}


def _iter_store_observations() -> list[dict]:
    rows: list[dict] = []
    for folder in _OBS_ROOTS:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.yaml")):
            raw = path.read_bytes()
            if b"mass_loss_fraction" not in raw and b"yield_fraction" not in raw and b"o2_yield" not in raw:
                continue
            doc = yaml.safe_load(raw.decode("utf-8"))
            if not isinstance(doc, dict):
                continue
            for obs in doc.get("observations") or []:
                if isinstance(obs, dict):
                    rows.append(obs)
    return rows


def _quantity(obs: dict) -> str | None:
    ident = obs.get("identity") or {}
    q = ident.get("quantity") or {}
    if isinstance(q, dict) and q.get("tag") == "value":
        return q.get("value")
    return None


def _formula(obs: dict) -> str:
    ident = obs.get("identity") or {}
    species = ident.get("species") or {}
    return str(species.get("formula") or "")


def _state_value(payload: object) -> object:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state") if "state" in payload else payload
    if not isinstance(state, dict) or state.get("tag") != "value":
        return None
    return state.get("value")


def _printed_wt_map(obs: dict) -> dict[str, Decimal]:
    pc = obs.get("point_conditions") or {}
    raw = _state_value(pc.get("printed_composition"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Decimal] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = as_decimal(value)
        except (TypeError, ValueError):
            continue
    return out


def _identity_composition_components(obs: dict) -> dict[str, Decimal]:
    ident = obs.get("identity") or {}
    raw = _state_value(ident.get("composition"))
    if not isinstance(raw, dict):
        return {}
    components = raw.get("components") or ()
    out: dict[str, Decimal] = {}
    for item in components:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out[str(item[0])] = as_decimal(item[1])
    return out


def _markova_vi_extract() -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-19", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "SiO2": {
                "observations": [
                    {
                        "observation_id": "markova_1984_table2_residual_melt_VI_lherzolite",
                        "type": "rate_series",
                        "locator": {"table": "2", "published_page": 510, "pdf_page_index": 2},
                        "phase": "residual_silicate_melt_composition_VI_lherzolite",
                        "regime": "kems_effusion",
                        "units": "wt percent as published",
                        "values": {
                            "quantity": "residual_melt_composition_vs_T_and_mass_loss",
                            "method_class": "measured_direct",
                            "composition_id": "VI",
                            "composition_name": "lherzolite",
                            "points": [
                                {
                                    "T_C": 0,
                                    "mass_loss_pct": 0.0,
                                    "SiO2": 44.02,
                                    "Al2O3": 4.56,
                                    "FeO": 8.04,
                                    "MgO": 36.44,
                                    "CaO": 6.95,
                                    "T_C_is_initial_composition": True,
                                },
                                {
                                    "T_C": 2207,
                                    "mass_loss_pct": 95.51,
                                    "SiO2": 0.0,
                                    "Al2O3": 79.28,
                                    "FeO": 0.08,
                                    "MgO": 0.0,
                                    "CaO": 21.64,
                                    "T_K": 2480.15,
                                    "T_C_is_initial_composition": False,
                                },
                            ],
                        },
                    }
                ]
            }
        },
    }


def test_migrate_lifts_markova_shaped_oxide_map(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path, _markova_vi_extract())
    result = migrate(root, write=False)
    t0 = result.observations[
        "fixture-source::markova_1984_table2_residual_melt_VI_lherzolite::point:0"
    ]
    later = result.observations[
        "fixture-source::markova_1984_table2_residual_melt_VI_lherzolite::point:1"
    ]
    assert t0.identity.species.formula == "unknown"
    printed = t0.point_conditions["printed_composition"].state.value
    assert as_decimal(printed["SiO2"]) == as_decimal("44.02")
    assert as_decimal(printed["MgO"]) == as_decimal("36.44")
    ident = t0.identity.composition
    assert ident is not None and ident.is_value
    assert ident.value.amount_basis is AmountBasis.MOLE_FRACTION
    experiment = result.experiments[t0.experiment_id]
    assert experiment.sample.printed_composition is not None
    assert as_decimal(experiment.sample.printed_composition.state.value["SiO2"]) == as_decimal(
        "44.02"
    )
    assert experiment.sample.initial_composition is not None
    assert experiment.sample.initial_composition.inference is not None
    assert experiment.sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"
    later_printed = later.point_conditions["printed_composition"].state.value
    assert as_decimal(later_printed["SiO2"]) == as_decimal("0.0")
    assert later_printed != printed


def test_migrate_keeps_ilmenite_and_drops_sample_code(tmp_path: Path) -> None:
    extract = {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-19", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "MLS-1A": {
                "observations": [
                    {
                        "observation_id": "table1",
                        "type": "rate_series",
                        "locator": {"table": "1", "page": 3},
                        "phase": "mixed_simulants",
                        "regime": "solar_fresnel_vacuum_pyrolysis",
                        "units": "%",
                        "values": {
                            "quantity": "vacuum_pyrolysis_experiment_summary",
                            "method_class": "measured_direct",
                            "tests": [
                                {
                                    "test": "2b",
                                    "sample": "FeTiO3",
                                    "mass_loss_pct": 16.0,
                                    "Tmax_C": 800,
                                },
                                {
                                    "test": "11",
                                    "sample": "MLS-1a",
                                    "mass_loss_pct": 10.1,
                                    "Tmax_C": 1474,
                                },
                            ],
                        },
                    }
                ]
            }
        },
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    ilmenite = result.observations["fixture-source::table1::point:0"]
    mls = result.observations["fixture-source::table1::point:1"]
    assert ilmenite.identity.species.formula == "FeTiO3"
    assert mls.identity.species.formula == "unknown"


def test_markova_vi_lherzolite_carries_printed_five_oxide_composition() -> None:
    """Table 2 T=0 recast is a 5-oxide lherzolite, not species SiO2.

    Fail-proof: the current store files the series under SiO2 with
    identity.composition unknown, so this is red until the oxide map is
    lifted onto printed_composition / mole-fraction Composition.
    """

    store = _OBS_ROOTS[0] / "kems-026-markova-1984.yaml"
    assert store.is_file(), "migrated Markova extract-v2 is required"
    doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    rows = [
        obs
        for obs in (doc.get("observations") or [])
        if str(obs.get("observation_id") or "").startswith(_MARKOVA_VI_PREFIX)
        and _quantity(obs) == "mass_loss_fraction"
    ]
    assert rows, "Markova VI residual-melt mass-loss points missing from store"
    t0 = next(
        (obs for obs in rows if str(obs.get("observation_id") or "").endswith("::point:0")),
        None,
    )
    assert t0 is not None, "Markova VI T=0 point missing"
    assert _formula(t0) != "SiO2", (
        "bulk lherzolite mass-loss is still asserted as species SiO2"
    )
    printed = _printed_wt_map(t0)
    assert printed, "T=0 printed wt% map is missing from point_conditions"
    assert set(printed) >= {"SiO2", "Al2O3", "FeO", "MgO", "CaO"}
    assert as_decimal(printed["SiO2"]) == as_decimal("44.02")
    assert as_decimal(printed["MgO"]) == as_decimal("36.44")
    total = sum(printed.values())
    assert as_decimal("99.5") <= total <= as_decimal("100.5"), total
    mole = _identity_composition_components(t0)
    assert mole, "initial mole-fraction Composition is missing on identity"
    converted = wt_pct_to_mole_fraction(printed)
    converted_map = dict(converted.components)
    for oxide, x in mole.items():
        assert abs(x - converted_map[oxide]) < as_decimal("1e-12"), oxide
    m_sio2 = oxide_molar_mass("SiO2")
    m_mgo = oxide_molar_mass("MgO")
    assert m_sio2 == as_decimal(str(ATOMIC_WEIGHTS_G_PER_MOL["Si"] + 2 * ATOMIC_WEIGHTS_G_PER_MOL["O"]))
    expected_ratio = (printed["SiO2"] / m_sio2) / (printed["MgO"] / m_mgo)
    got_ratio = mole["SiO2"] / mole["MgO"]
    assert abs(got_ratio - expected_ratio) < as_decimal("1e-12")
    later = next(
        (obs for obs in rows if str(obs.get("observation_id") or "").endswith("::point:10")),
        None,
    )
    assert later is not None
    later_printed = _printed_wt_map(later)
    assert later_printed
    assert later_printed != printed, "ramp residual composition was collapsed into one map"


def test_bulk_property_rows_do_not_use_sample_code_as_formula() -> None:
    """MLS-* / MS[0-9] are sample labels, not chemical formulas."""

    bad: list[str] = []
    for obs in _iter_store_observations():
        if _quantity(obs) not in _BULK_QUANTITIES:
            continue
        formula = _formula(obs)
        if formula and _SAMPLE_CODE_FORMULA.match(formula):
            bad.append(f"{obs.get('observation_id')} formula={formula}")
    assert not bad, bad[:20]


def test_mls_1a_case_duplicate_is_one_identity() -> None:
    """MLS-1a and MLS-1A are one material; bulk rows share one species formula."""

    formulas: set[str] = set()
    found: list[str] = []
    for obs in _iter_store_observations():
        oid = str(obs.get("observation_id") or "")
        if oid not in _MLS_BULK_IDS:
            continue
        if _quantity(obs) not in _BULK_QUANTITIES:
            continue
        found.append(oid)
        formulas.add(_formula(obs))
    assert set(found) == _MLS_BULK_IDS, found
    assert len(formulas) == 1, formulas
    formula = next(iter(formulas))
    assert formula, "MLS bulk rows have empty species.formula"
    assert not _SAMPLE_CODE_FORMULA.match(formula), formula
