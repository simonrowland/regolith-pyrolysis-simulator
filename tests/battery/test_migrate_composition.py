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
    _catalogue_composition_located_from_values,
    _sample_from_plain,
    migrate,
    oxide_molar_mass,
    sample_from_equipment,
    to_plain,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import as_decimal
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree


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
    "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_table1_fresnel_pyrolysis_log::T=1747.15:row=mls-1a:h=7bb710cb23de",
    "kems-038-matchett-2006::matchett_2006_table3_fresnel_pyrolysis_log::T=1747.15:row=mls-1a:h=23d3c0f9125e",
    "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction",
}


def test_mass_percent_composition_keeps_printed_map_and_derives_moles() -> None:
    raw = {
        "initial_composition": {
            "locator": {"table": "2", "page": 14},
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["MgO", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == {"SiO2": "60", "MgO": "40"}
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert sample.initial_composition.inference is not None
    assert sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"
    assert dict(sample.initial_composition.state.value.components) == dict(
        wt_pct_to_mole_fraction({"SiO2": Decimal("60"), "MgO": Decimal("40")}).components
    )


def test_typed_printed_composition_is_readable_and_gets_canonical_sibling() -> None:
    raw = {
        "printed_composition": {
            "locator": {"table": "2", "page": 14},
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["MgO", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == {"SiO2": "60", "MgO": "40"}
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_value
    assert sample.initial_composition.inference is not None
    assert sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"


def test_sample_catalogue_composition_is_typed_proxy_not_printed() -> None:
    values = {
        "composition_from_sample_catalog": True,
        "composition_source": (
            "Lunar Sample Compendium entry 10017. "
            "Selection rule: first complete named whole-sample analysis."
        ),
        "sample_oxide_composition_wt_pct": {"SiO2": 50, "CaO": 50},
    }

    located = _catalogue_composition_located_from_values(values, None)
    assert located is not None
    composition = located.state.value
    assert composition.basis == "sample_catalog_proxy"
    assert composition.proxy_flag == "composition_from_sample_catalog"
    assert composition.proxy_source.startswith("Lunar Sample Compendium")
    assert composition.analysis_selection_rule == (
        "first complete named whole-sample analysis"
    )

    sample = sample_from_equipment({}, values=values)
    assert sample.printed_composition is None
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.value.proxy_flag == (
        "composition_from_sample_catalog"
    )


def test_ambiguous_mass_percent_initial_preserves_print_and_withholds_canonical() -> None:
    raw = {
        "initial_composition": {
            "locator": {"table": "2", "page": 14},
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["Cl", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == raw["initial_composition"]["state"]["value"]
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_unknown


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
    t0, later = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(
            "fixture-source::markova_1984_table2_residual_melt_VI_lherzolite::"
        )
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
    ilmenite, mls = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::table1::")
    ]
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
    later = max(
        rows,
        key=lambda obs: Decimal(
            re.search(
                r"::T=([0-9.]+):",
                str(obs.get("observation_id") or ""),
            ).group(1)
        )
        if re.search(r"::T=([0-9.]+):", str(obs.get("observation_id") or ""))
        else Decimal("-1"),
    )
    assert later is not t0
    later_printed = _printed_wt_map(later)
    assert later_printed
    assert later_printed != printed, "ramp residual composition was collapsed into one map"


def test_markova_1984_mass_loss_identity_uses_its_own_panel_initial_charge() -> None:
    """Cumulative mass loss uses each panel's own T=0 charge as X0."""

    store = _OBS_ROOTS[0] / "kems-026-markova-1984.yaml"
    doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    panels = {
        "VI_lherzolite": {"SiO2": "44.02", "MgO": "36.44"},
        "V_alumina_basalt": {"SiO2": "52.32", "Al2O3": "19.13"},
    }

    for panel, signature in panels.items():
        prefix = f"kems-026-markova-1984::markova_1984_table2_residual_melt_{panel}"
        rows = [
            obs
            for obs in (doc.get("observations") or [])
            if str(obs.get("observation_id") or "").startswith(prefix)
            and _quantity(obs) == "mass_loss_fraction"
        ]
        assert len(rows) == 12, (panel, len(rows))
        t0 = next(
            obs
            for obs in rows
            if str(obs.get("observation_id") or "").endswith("::point:0")
        )
        charge = _printed_wt_map(t0)
        for oxide, expected in signature.items():
            assert charge[oxide] == as_decimal(expected), (panel, oxide, charge)
        expected_x0 = dict(wt_pct_to_mole_fraction(charge).components)
        for row in rows:
            identity_x0 = _identity_composition_components(row)
            assert identity_x0, (panel, row.get("observation_id"))
            assert identity_x0.keys() == expected_x0.keys(), (panel, row.get("observation_id"))
            assert all(
                abs(identity_x0[oxide] - value) < as_decimal("1e-12")
                for oxide, value in expected_x0.items()
            ), (panel, row.get("observation_id"), identity_x0)


def test_markova_1983_rows_use_each_points_own_printed_composition(
    tmp_path: Path,
) -> None:
    source_path = (
        REPO_ROOT
        / "data"
        / "literature"
        / "extracts"
        / "kems-025-markova-1983.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    expected_by_temperature: dict[Decimal, dict[str, Decimal]] = {}
    for block in (source.get("species") or {}).values():
        for source_observation in block.get("observations") or []:
            values = source_observation.get("values") or {}
            for point in values.get("points") or []:
                printed = point.get("composition_wt_pct")
                if not isinstance(printed, dict):
                    continue
                temperature_k = as_decimal(point["T_C"]) + as_decimal("273.15")
                expected_by_temperature[temperature_k] = {
                    str(oxide): as_decimal(value)
                    for oxide, value in printed.items()
                }
    assert len(expected_by_temperature) == 25

    result = _migrate_real_extract(tmp_path, source_path.name, write=False)
    rows = [
        to_plain(observation)
        for observation in result.observations.values()
        if observation.point_conditions
        and "printed_composition" in observation.point_conditions
    ]
    assert len(rows) == 25
    assert {
        row["identity"]["quantity"]["value"] for row in rows
    } == {"p_partial"}

    mismatches: list[str] = []
    for row in rows:
        identity = row["identity"]
        temperature_k = as_decimal(identity["temperature_K"]["value"])
        expected_wt = expected_by_temperature[temperature_k]
        printed_wt = _printed_wt_map(row)
        if printed_wt != expected_wt:
            mismatches.append(f"{row['observation_id']}: printed map differs from source")
            continue
        expected_x = dict(wt_pct_to_mole_fraction(expected_wt).components)
        actual_x = _identity_composition_components(row)
        if actual_x.keys() != expected_x.keys() or any(
            abs(actual_x[oxide] - value) >= as_decimal("1e-12")
            for oxide, value in expected_x.items()
        ):
            mismatches.append(
                f"{row['observation_id']}: identity does not match its printed map"
            )
    assert not mismatches, f"{len(mismatches)} composition mismatches: {mismatches}"


def test_markova_table2_migrates_both_quantities_and_printed_charge(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "kems-026-markova-1984.yaml",
        use_repository_index_row=True,
    )
    prefix = "kems-026-markova-1984::markova_1984_table2_residual_melt_"
    rows = {
        observation_id: to_plain(observation)
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(prefix)
    }
    mass_loss_rows = {
        observation_id: row
        for observation_id, row in rows.items()
        if _quantity(row) == "mass_loss_fraction"
    }
    assert len(mass_loss_rows) == 24
    assert {
        row["identity"]["species"]["formula"] for row in mass_loss_rows.values()
    } == {"unknown"}
    assert sum(
        row["admission"]["status"] == "admitted"
        for row in mass_loss_rows.values()
    ) == 22
    zero_rows = [
        row
        for row in mass_loss_rows.values()
        if Decimal(row["value"]["point"]) == Decimal("0")
    ]
    assert len(zero_rows) == 2
    for row in zero_rows:
        assert row["admission"]["status"] == "pending"
        assert row["identity"]["temperature_K"]["tag"] == "unknown"
    assert {
        Decimal(row["value"]["point"])
        for row in mass_loss_rows.values()
    } == {
        Decimal(value)
        for value in (
            "0",
            "0.0146", "0.0416", "0.0974", "0.218", "0.3835", "0.6521",
            "0.8605", "0.8925", "0.9181", "0.9551", "0.9834", "0.0117",
            "0.0439", "0.1441", "0.3717", "0.5353", "0.6601", "0.6935",
            "0.7133", "0.7619", "0.8368", "0.9799",
        )
    }

    residue = {
        observation_id: row
        for observation_id, row in rows.items()
        if _quantity(row) == "residue_component_composition"
    }
    assert len(residue) == 110
    printed_cells = {
        ("VI", "1748.15", "MgO"): Decimal("36.92"),
        ("VI", "2156.15", "SiO2"): Decimal("13.6"),
        ("VI", "2311.15", "MgO"): Decimal("0.0"),
        ("V", "1992.15", "MgO"): Decimal("8.11"),
    }
    for (panel, temperature, oxide), expected in printed_cells.items():
        matching = [
            row
            for observation_id, row in residue.items()
            if row["experiment_id"].endswith(f"::{panel}")
            and row["identity"]["species"]["formula"] == oxide
            and row["identity"]["temperature_K"]["value"] == temperature
        ]
        assert len(matching) == 1
        assert Decimal(matching[0]["value"]["point"]) == expected

    charges = {
        "VI": {
            "SiO2": Decimal("44.02"), "Al2O3": Decimal("4.56"),
            "FeO": Decimal("8.04"), "MgO": Decimal("36.44"),
            "CaO": Decimal("6.95"),
        },
        "V": {
            "SiO2": Decimal("52.32"), "Al2O3": Decimal("19.13"),
            "FeO": Decimal("9.37"), "MgO": Decimal("6.25"),
            "CaO": Decimal("12.93"),
        },
    }
    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/kems-026-markova-1984.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_values = {
        observation["values"]["composition_id"]: observation["values"]
        for observation in extract["species"]["SiO2"]["observations"]
        if observation["values"].get("quantity") == "residue_composition_vs_time"
    }
    for panel, charge in charges.items():
        series = source_values[panel]["series"]
        panel_rows = [
            row
            for row in mass_loss_rows.values()
            if row["experiment_id"].endswith(f"::{panel}")
        ]
        assert len(panel_rows) == len(series)
        for row, source_point in zip(panel_rows, series):
            expected = (
                charge
                if source_point.get("T_C_is_initial_composition")
                else {
                    oxide: as_decimal(source_point[f"{oxide}_wt_pct"])
                    for oxide in charge
                }
            )
            assert _printed_wt_map(row) == expected

    for panel, charge in charges.items():
        row = next(
            value for value in residue.values()
            if value["experiment_id"].endswith(f"::{panel}")
        )
        identity = _identity_composition_components(row)
        expected_moles = dict(wt_pct_to_mole_fraction(charge).components)
        assert identity == expected_moles
        sample = result.experiments[row["experiment_id"]].sample
        assert sample.printed_composition is not None
        assert sample.printed_composition.state.value == {
            oxide: str(value) for oxide, value in charge.items()
        }
        assert sample.initial_composition is not None
        assert dict(sample.initial_composition.state.value.components) == expected_moles


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


_PLANTE_CHARGE_WT = {"K2O": "43.94", "SiO2": "56.06"}
# The regen replaced bare ordinal experiment ids with descriptive slugs.
_PLANTE_EXPERIMENT_ID = "10.6028/nbs.sp.561v1::experiment::k2o-sio2-effusion-series"


def _plante_evolving_extract() -> dict:
    """Two Table 2 rows of one K2Si2O5 charge; residual wt% changes, charge does not."""

    charge = {"K2O": 43.94, "SiO2": 56.06}
    sample = {
        "form": "crystalline K2Si2O5",
        "printed_composition": charge,
        "locator": {
            "published_page": 276,
            "table": "2",
            "note": "Series 1104 first row; K2Si2O5 charge as loaded",
        },
    }
    equipment = {
        "cell_material": {
            "value": "platinum Knudsen cell",
            "locator": {"published_page": 267, "figure": "2"},
        },
        "sample": sample,
    }

    def row(oid: str, t_k: float, k2o: float, sio2: float) -> dict:
        return {
            "observation_id": oid,
            "type": "activity_coefficient",
            "locator": {
                "table": "2",
                "published_page": 276,
                "note": f"{oid} residual melt",
            },
            "phase": "K2O-SiO2_binary_silicate_melt",
            "regime": "kems_effusion",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_vapor_reference_eq7",
                "method_class": "measured_direct",
                "activity": 1.0e-16,
                "composition_wt_pct": {"K2O": k2o, "SiO2": sio2},
                "T_K_as_published": t_k,
                "series": 1104,
            },
            "equipment": equipment,
        }

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
            "K2O": {
                "observations": [
                    row("plante_s1104_start", 1302.0, 43.94, 56.06),
                    row("plante_s1104_later", 1396.0, 40.36, 59.64),
                ]
            }
        },
    }


def test_migrate_plante_shaped_charge_survives_residual_evolution(tmp_path: Path) -> None:
    """Evolving composition_wt_pct is the residual; Sample keeps the loaded charge.

    Fail-proof: vocabulary maps composition_wt_pct → Sample.printed_composition,
    so two residuals conflict and the merge wipes Sample to {}.
    """

    root = _write_min_tree(tmp_path, _plante_evolving_extract())
    result = migrate(root, write=False)
    start = result.observations["fixture-source::plante_s1104_start"]
    later = result.observations["fixture-source::plante_s1104_later"]
    assert start.experiment_id == later.experiment_id
    experiment = result.experiments[start.experiment_id]
    printed = experiment.sample.printed_composition
    assert printed is not None and printed.state.is_value, (
        "Sample.printed_composition was wiped by residual composition_wt_pct"
    )
    wt = printed.state.value
    assert as_decimal(wt["K2O"]) == as_decimal("43.94")
    assert as_decimal(wt["SiO2"]) == as_decimal("56.06")
    assert experiment.sample.form is not None and experiment.sample.form.state.is_value
    assert experiment.sample.form.state.value == "crystalline K2Si2O5"
    assert experiment.sample.mass_kg is None
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert initial.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert initial.inference is not None
    assert initial.inference.relation == "wt_pct_to_mole_fraction"
    start_x = dict(start.identity.composition.value.components)
    later_x = dict(later.identity.composition.value.components)
    assert start_x != later_x


def test_registry_charge_repeated_by_row_keeps_derived_initial_composition(
    tmp_path: Path,
) -> None:
    raw = _plante_evolving_extract()
    charge = dict(_PLANTE_CHARGE_WT)
    raw["experiments"] = [
        {
            "experiment_id": "k2o-sio2-effusion-series",
            "method": "knudsen_effusion",
            "sample": {
                "printed_composition": {
                    "state": {"tag": "value", "value": charge},
                    "locator": {"table": "2", "published_page": 276},
                }
            },
        }
    ]
    for observation in raw["species"]["K2O"]["observations"]:
        observation["experiment"] = "k2o-sio2-effusion-series"

    result = migrate(_write_min_tree(tmp_path, raw), write=False)

    experiment_id = result.observations[
        "fixture-source::plante_s1104_start"
    ].experiment_id
    experiment = result.experiments[experiment_id]
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert initial.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert initial.inference is not None
    assert initial.inference.relation == "wt_pct_to_mole_fraction"
    expected = wt_pct_to_mole_fraction(
        {key: as_decimal(value) for key, value in _PLANTE_CHARGE_WT.items()}
    )
    assert dict(initial.state.value.components) == dict(expected.components)


def test_plante_1979_one_k2si2o5_charge_not_eight_series_experiments() -> None:
    """Table 2 is one K2Si2O5 charge; series are analysis groups, not reloads.

    Paper p.271: crystalline K2Si2O5 starting material. p.270: composition
    from the known starting composition plus integrated ion current.
    Table 3 ranges chain 43.9–6.8 wt% K2O with no reload jump. p.272:
    'The data were broken up into eight series for analysis.'
    Fail-proof: the store currently files 383 observations on one
    experiment whose Sample is {}.
    """

    work_path = REPO_ROOT / "data" / "literature" / "works" / "10.6028_nbs.sp.561v1.yaml"
    store = _OBS_ROOTS[0] / "kems-042-plante-1979.yaml"
    assert work_path.is_file() and store.is_file()
    work_doc = yaml.safe_load(work_path.read_text(encoding="utf-8"))
    plante_experiments = [
        row
        for row in (work_doc.get("experiments") or [])
        if isinstance(row, dict)
        and str(row.get("experiment_id") or "").startswith("10.6028/nbs.sp.561v1::")
    ]
    assert [row.get("experiment_id") for row in plante_experiments] == [
        _PLANTE_EXPERIMENT_ID
    ], [row.get("experiment_id") for row in plante_experiments]
    experiment = plante_experiments[0]
    sample = experiment.get("sample") or {}
    printed = sample.get("printed_composition") or {}
    printed_state = printed.get("state") if isinstance(printed, dict) else None
    assert isinstance(printed_state, dict) and printed_state.get("tag") == "value", (
        f"sample keys: {sorted(sample)}"
    )
    wt = printed_state.get("value") or {}
    assert as_decimal(wt["K2O"]) == as_decimal(_PLANTE_CHARGE_WT["K2O"])
    assert as_decimal(wt["SiO2"]) == as_decimal(_PLANTE_CHARGE_WT["SiO2"])
    locator = printed.get("locator") or {}
    assert str(locator.get("table")) == "2"
    assert locator.get("published_page") == 276
    initial = sample.get("initial_composition") or {}
    initial_state = initial.get("state") if isinstance(initial, dict) else None
    assert isinstance(initial_state, dict) and initial_state.get("tag") == "value"
    initial_value = initial_state.get("value") or {}
    assert initial_value.get("amount_basis") == "mole_fraction"
    inference = initial.get("inference") or {}
    assert inference.get("relation") == "wt_pct_to_mole_fraction"
    converted = wt_pct_to_mole_fraction(
        {k: as_decimal(v) for k, v in _PLANTE_CHARGE_WT.items()}
    )
    got = {
        str(item[0]): as_decimal(item[1])
        for item in (initial_value.get("components") or ())
    }
    expected = dict(converted.components)
    for oxide, amount in expected.items():
        assert abs(got[oxide] - amount) < as_decimal("1e-12"), oxide
    assert sample.get("mass_kg") is None, (
        "paper does not state a loaded mass; absence must not become a measured zero"
    )
    form = sample.get("form") or {}
    form_state = form.get("state") if isinstance(form, dict) else None
    assert isinstance(form_state, dict) and form_state.get("value") == "crystalline K2Si2O5"
    obs_doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    rows = [
        obs
        for obs in (obs_doc.get("observations") or [])
        if isinstance(obs, dict)
    ]
    assert len(rows) == 383
    assert {obs.get("experiment_id") for obs in rows} == {_PLANTE_EXPERIMENT_ID}
    first = next(
        obs
        for obs in rows
        if str(obs.get("observation_id") or "").endswith(
            "plante1979_table2_k2o_s1104_000_1302K"
        )
    )
    later = next(
        obs
        for obs in rows
        if str(obs.get("observation_id") or "").endswith(
            "plante1979_table2_k2o_s1104_027_1396K"
        )
    )
    assert _identity_composition_components(first)
    assert _identity_composition_components(later)
    assert _identity_composition_components(first) != _identity_composition_components(
        later
    )
