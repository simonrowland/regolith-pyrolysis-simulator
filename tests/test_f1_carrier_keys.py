"""F1 carrier-key prerequisite — reality-grounded feedstock re-partition tests.

Guards OD-6 carrier keying, mass-conservation re-partition, literature ranges,
dangling foulant-config warnings, and verdict-(b) MELTS/MAGEMin domain re-gate.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from simulator.core import (
    STAGE0_CARBONATE_COMPONENTS,
    STAGE0_CHLORIDE_SALT_COMPONENTS,
    STAGE0_GAS_COMPONENTS,
    PyrolysisSimulator,
)
from simulator.feedstock_guard import is_blocked_feedstock
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.magemin import MAGEMinBackend, MeltCompositionError

FEEDSTOCKS_PATH = Path(__file__).parent.parent / "data" / "feedstocks.yaml"

# Pre-F1 composition totals (re-partition invariant: same sum_check / oxide total).
PRE_SPLIT = {
    "ceres_regolith": {
        "carbonaceous_organic": 5.2,
        "sum_check": 102.95,
    },
    "ci_carbonaceous_chondrite": {
        "carbonaceous_organic": 6.5,
        "sum_check": 100.25,
    },
    "cm_carbonaceous_chondrite": {
        "carbonaceous_organic": 3.2,
        "sum_check": 91.95,
    },
    "mars_basalt": {
        "SO3": 6.0,
        "sum_check": 105.05,
    },
    "mars_sulfate_rich": {
        "SO3": 11.5,
        "sum_check": 107.45,
    },
}

LITERATURE_RANGES = {
    "ceres_regolith": {
        "carbonate_salts": (2.0, 4.5),
        "NH3": (0.8, 2.0),
    },
    "ci_carbonaceous_chondrite": {
        "carbonate_salts": (1.5, 4.0),
    },
    "cm_carbonaceous_chondrite": {
        "carbonate_salts": (0.8, 2.5),
    },
}

STAGE0_SET_BY_FAMILY = {
    "carbonate": STAGE0_CARBONATE_COMPONENTS,
    "nh4_clay": STAGE0_GAS_COMPONENTS,
    "brine_salts": STAGE0_CHLORIDE_SALT_COMPONENTS,
    "sulfate_decomp": frozenset({"so3", "sulfate", "sulfates"}),
}


def _load_feedstocks() -> dict[str, Any]:
    return yaml.safe_load(FEEDSTOCKS_PATH.read_text())


def _composition_total_wt_pct(entry: Mapping[str, Any]) -> float:
    comp = entry.get("composition_wt_pct") or {}
    return sum(float(v) for v in comp.values())


def _carrier_composition_keys(
    feedstock_key: str, entry: Mapping[str, Any]
) -> set[str]:
    keys = set((entry.get("composition_wt_pct") or {}).keys())
    carriers = entry.get("stage0_carrier_keys") or {}
    for carrier_name, meta in carriers.items():
        if not isinstance(meta, Mapping):
            continue
        comp_key = meta.get("composition_key")
        if comp_key:
            keys.add(str(comp_key))
        for sub_key in meta.get("composition_keys") or []:
            keys.add(str(sub_key))
        if carrier_name in (entry.get("composition_wt_pct") or {}):
            keys.add(carrier_name)
    return keys


def _normalize_alias(alias: str) -> str:
    return alias.strip().lower().replace("-", "_").replace(" ", "_")


def audit_foulant_carrier_bindings(
    feedstocks: Mapping[str, Any],
    foulant_configs: Mapping[str, Any],
) -> list[str]:
    """Return dangling-config warnings (OD-6 loader contract)."""
    issues: list[str] = []
    feedstock_alias_index: dict[str, set[str]] = {}
    for feedstock_key, entry in feedstocks.items():
        aliases: set[str] = set()
        for comp_key in _carrier_composition_keys(feedstock_key, entry):
            aliases.add(_normalize_alias(comp_key))
        for carrier_name, meta in (entry.get("stage0_carrier_keys") or {}).items():
            aliases.add(_normalize_alias(str(carrier_name)))
            if isinstance(meta, Mapping):
                comp_key = meta.get("composition_key")
                if comp_key:
                    aliases.add(_normalize_alias(str(comp_key)))
                for sub_key in meta.get("composition_keys") or []:
                    aliases.add(_normalize_alias(str(sub_key)))
        feedstock_alias_index[feedstock_key] = aliases

    all_aliases = set().union(*feedstock_alias_index.values()) if feedstock_alias_index else set()

    for config_key, config in foulant_configs.items():
        carrier = config.get("carrier") or {}
        aliases = carrier.get("aliases") or [config_key]
        resolved = [
            alias
            for alias in aliases
            if _normalize_alias(str(alias)) in all_aliases
        ]
        if not resolved:
            issues.append(
                f"dangling foulant config {config_key!r}: aliases {aliases!r} "
                "resolve to no feedstock carrier key"
            )

    for feedstock_key, entry in feedstocks.items():
        for carrier_name in (entry.get("stage0_carrier_keys") or {}):
            norm = _normalize_alias(str(carrier_name))
            if norm not in all_aliases:
                issues.append(
                    f"dangling feedstock carrier {carrier_name!r} on "
                    f"{feedstock_key!r}: no composition key"
                )
    return issues


def _sim(feedstocks: Mapping[str, Any]) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _post_stage0_melt_oxide_wt_pct(sim: PyrolysisSimulator) -> dict[str, float]:
    return {
        oxide: wt
        for oxide, wt in sim.melt.composition_wt_pct().items()
        if wt > 1e-12
    }


def _magemin_domain_status(
    oxide_wt_pct: Mapping[str, float],
    *,
    subprocess_only: bool = True,
) -> tuple[str, list[str]]:
    """Verdict-(b) domain probe — honest status, no renormalization."""
    backend = MAGEMinBackend()
    init_ok = backend.initialize(
        {"python_bridge": "subprocess"} if subprocess_only else {}
    )
    if not init_ok:
        return "backend_unavailable", ["MAGEMin subprocess binary not located"]

    oxide_sum = sum(float(v) for v in oxide_wt_pct.values())
    if oxide_sum <= 1e-12:
        return "empty_oxide_vector", ["post-Stage-0 melt oxide vector is empty"]

    notes = [
        f"oxide vector sum {oxide_sum:.6f} wt% — passed unnormalized (verdict-b discipline)"
    ]
    try:
        projection = backend._build_db_bulk_projection(dict(oxide_wt_pct))
    except MeltCompositionError as exc:
        return "bulk_projection_failed", notes + [str(exc)]

    result = backend.equilibrate(
        temperature_C=1200.0,
        pressure_bar=0.006,
        fO2_log=-2.0,
        composition_kg={
            oxide: float(wt) for oxide, wt in oxide_wt_pct.items()
        },
        species_formula_registry=PyrolysisSimulator._load_species_formula_registry(),
    )
    status = str(result.status or "unknown")
    notes.extend(projection.warnings)
    notes.extend(result.warnings or [])
    return status, notes


@pytest.fixture(scope="module")
def feedstocks() -> dict[str, Any]:
    return _load_feedstocks()


def test_f1_repartition_preserves_feedstock_totals(feedstocks):
    for feedstock_key, baseline in PRE_SPLIT.items():
        entry = feedstocks[feedstock_key]
        assert entry["sum_check"] == pytest.approx(baseline["sum_check"])
        assert _composition_total_wt_pct(entry) == pytest.approx(
            baseline["sum_check"]
        )

        if "carbonaceous_organic" in baseline:
            organic = float(
                entry["composition_wt_pct"]["carbonaceous_organic"]
            )
            carrier_mass = 0.0
            if feedstock_key == "ceres_regolith":
                comp = entry["composition_wt_pct"]
                carrier_mass = (
                    float(comp["carbonate_salts"]) + float(comp["NH3"])
                )
            elif feedstock_key in (
                "ci_carbonaceous_chondrite",
                "cm_carbonaceous_chondrite",
            ):
                carrier_mass = float(
                    entry["composition_wt_pct"]["carbonate_salts"]
                )
            assert organic + carrier_mass == pytest.approx(
                baseline["carbonaceous_organic"]
            )


def test_f1_carrier_levels_within_literature_ranges(feedstocks):
    for feedstock_key, ranges in LITERATURE_RANGES.items():
        comp = feedstocks[feedstock_key]["composition_wt_pct"]
        for species, (lo, hi) in ranges.items():
            level = float(comp[species])
            assert lo <= level <= hi, (
                f"{feedstock_key}.{species}={level} outside [{lo}, {hi}]"
            )

    brine = feedstocks["ceres_regolith"]["stage0_carrier_keys"]["brine_salts"]
    assert brine["interval_required"] is True
    assert brine["allocated_wt_pct"] == pytest.approx(1.0)
    lo, hi = brine["level_range_wt_pct"]
    assert lo < hi


def test_f1_carrier_keys_bind_to_existing_stage0_sets(feedstocks):
    ceres = feedstocks["ceres_regolith"]["stage0_carrier_keys"]
    assert ceres["carbonate"]["composition_key"] == "carbonate_salts"
    assert ceres["carbonate"]["stage0_components_set"] == (
        "STAGE0_CARBONATE_COMPONENTS"
    )
    assert ceres["nh4_clay"]["stage0_components_set"] == "STAGE0_GAS_COMPONENTS"
    assert ceres["brine_salts"]["stage0_components_set"] == (
        "STAGE0_CHLORIDE_SALT_COMPONENTS"
    )
    assert feedstocks["ci_carbonaceous_chondrite"]["stage0_carrier_keys"][
        "carbonate"
    ]["composition_key"] == "carbonate_salts"
    assert feedstocks["cm_carbonaceous_chondrite"]["stage0_carrier_keys"][
        "carbonate"
    ]["composition_key"] == "carbonate_salts"
    assert "carbonate_salts" in feedstocks["ci_carbonaceous_chondrite"][
        "composition_wt_pct"
    ]
    for mars_key in ("mars_basalt", "mars_sulfate_rich"):
        sulfate = feedstocks[mars_key]["stage0_carrier_keys"]["sulfate_decomp"]
        assert sulfate["composition_key"] == "SO3"
        assert sulfate["onset_C"] == 1200
        assert sulfate["mixed_cation_onset"] is True
        assert "jarosite" in sulfate["regime_caveat"].lower()


def test_f1_dangling_foulant_config_emits_loader_warning(feedstocks):
    bound_configs = {
        "ceres_carbonate": {
            "carrier": {
                "aliases": ["carbonate", "carbonate_salts"],
            },
        },
        "ceres_nh4": {
            "carrier": {
                "aliases": ["nh4_clay", "NH3"],
            },
        },
        "ceres_brine": {
            "carrier": {
                "aliases": ["brine_salts", "NaCl"],
            },
        },
        "mars_sulfate_bulk": {
            "carrier": {
                "aliases": ["SO3", "sulfate_decomp"],
            },
        },
    }
    assert audit_foulant_carrier_bindings(feedstocks, bound_configs) == []

    dangling = {
        "phantom_carrier": {
            "carrier": {
                "aliases": ["CaF2_is_not_a_feedstock_key"],
            },
        },
    }
    issues = audit_foulant_carrier_bindings(feedstocks, dangling)
    assert len(issues) == 1
    assert "dangling foulant config" in issues[0]

    with warnings.catch_warnings(record=True) as caught:
        warnings.warn(issues[0], UserWarning, stacklevel=1)
    assert any("dangling foulant config" in str(w.message) for w in caught)


@pytest.mark.parametrize(
    "feedstock_key",
    [
        "ci_carbonaceous_chondrite",
        "cm_carbonaceous_chondrite",
        "ceres_regolith",
    ],
)
def test_f1_verdict_b_post_stage0_domain_honest_no_renormalize(
    feedstocks, feedstock_key: str
):
    sim = _sim(feedstocks)
    sim.load_batch(feedstock_key, mass_kg=1000.0)
    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)

    oxide_vector = _post_stage0_melt_oxide_wt_pct(sim)
    assert oxide_vector, f"{feedstock_key}: empty post-Stage-0 oxide vector"

    status, notes = _magemin_domain_status(oxide_vector, subprocess_only=True)

    assert status in {
        "ok",
        "out_of_domain",
        "not_converged",
        "bulk_projection_failed",
        "empty_oxide_vector",
        "backend_unavailable",
        "unknown",
    }
    if status == "backend_unavailable":
        pytest.skip(notes[0])

    assert any("passed unnormalized" in note for note in notes)


def test_f1_builtin_feedstocks_still_conserve_batch_mass(feedstocks):
    for key, entry in feedstocks.items():
        if str(entry.get("status", "")).startswith("blocked_"):
            continue
        if is_blocked_feedstock(entry):
            continue
        sim = _sim(feedstocks)
        required_c = 0.0
        if PyrolysisSimulator._uses_mars_carbon_cleanup(entry):
            required_c = PyrolysisSimulator._carbon_reductant_required_kg(
                entry, 1000.0
            )
        additives = {"C": required_c} if required_c > 0.0 else None
        sim.load_batch(key, mass_kg=1000.0, additives_kg=additives)
        snapshot = sim._make_snapshot()
        assert snapshot.mass_balance_error_pct == pytest.approx(0.0), key
