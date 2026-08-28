"""Ratcheted conformance harness for the multi-carrier vapour rail.

Layer 2 answers whether every demanded carrier is either executable through the
real helper paths or named in the typed gap ledger.  Layer 3 pins external
residuals without mistaking internal self-consistency for validation.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import yaml

from engines.builtin.foulant_disposition import chi_escape_salt
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.langmuir_knudsen import grounded_alpha
from simulator.diagnostic_helpers.extract_reproduction import (
    evaluate_all,
    rollup_species_error_bars,
)
from simulator.state import EvaporationFlux
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    FluxActivationContext,
    FluxDiagnosticUpperBound,
    FluxEligible,
    FluxRefusal,
    PressureRefusal,
    PressureUpperBound,
    PressureValue,
)
from simulator.vapour_rail.catalog import (
    HotTrainInapplicable,
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.engine_crosscheck import (
    _run_rail_cell,
    load_rail_provider,
)
from simulator.vapour_rail.instrumentation import serialize_vapour_answer
from simulator.vapour_rail.request import VapourResolveState
from tests.chemistry.conftest import _build_sim, _load_yaml


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MANIFEST_PATH = Path(
    os.environ.get(
        "RAIL_DEMAND_MANIFEST_PATH", DATA / "vapour_rail_demand_manifest.yaml"
    )
)
GAPS_PATH = Path(
    os.environ.get(
        "RAIL_COVERAGE_GAPS_PATH", DATA / "vapour_rail_coverage_gaps.yaml"
    )
)
PINS_PATH = Path(
    os.environ.get(
        "RAIL_VALIDATION_PINS_PATH", DATA / "vapour_rail_validation_pins.yaml"
    )
)
ENGINE_REPORT_PATH = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-08-03-vapour-rail-engine-crosscheck"
    / "engine_crosscheck_report.json"
)
ORACLE_MESSAGE = (
    "investigate \N{EM DASH} do NOT tune the systematic path to match the incumbent."
)
REASON_TYPES = {
    "thermo_missing",
    "activity_missing",
    "acquisition-pending",
    "negligibility-proven-with-margin",
}
UNRESOLVED_CARRIER = "__carrier_discovery__"
TIER_ORDER = {"T1": 1, "T2": 2, "T3": 3, "T4": 4}
TIER_VALIDATIONS = {"T2": "vaporock", "T3": "mass_spec", "T4": "ratio"}


def _yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(payload, dict), f"{path}: expected a YAML mapping"
    return payload


@lru_cache(maxsize=1)
def _git_tracked_files() -> frozenset[str]:
    """Repo-relative paths present in the git index (tracked or staged).

    Fresh-clone-green for promotion evidence requires every primary
    ``evidence_refs`` path to survive ``git clone`` — presence on this
    laptop is not enough (docs-private/ is gitignored and machine-local).
    """
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return frozenset(
        path for path in proc.stdout.decode("utf-8").split("\0") if path
    )


def _assert_evidence_ref_present_and_tracked(ref: str, *, context: str) -> None:
    """Primary evidence must exist on disk AND be git-tracked.

    URL / DOI refs are external identifiers and are accepted without a local
    file. Fragment anchors (``path#section``) resolve against the path only.
    Optional ``internal_notes_refs`` are not checked here — they may point at
    machine-local private narratives.
    """
    assert isinstance(ref, str) and ref.strip(), f"{context}: empty evidence ref"
    if "://" in ref or ref.startswith("doi:"):
        return
    rel = Path(ref.split("#", 1)[0]).as_posix()
    evidence_path = ROOT / rel
    assert evidence_path.is_file(), f"{context}: missing evidence {ref}"
    assert rel in _git_tracked_files(), (
        f"{context}: evidence {ref!r} exists on disk but is not git-tracked "
        f"(fresh clone would be RED). Distil a publishable artifact under a "
        f"tracked root (e.g. validation-data/pin-evidence/); do not point "
        f"evidence_refs at docs-private/ or other untracked paths."
    )


MANIFEST = _yaml(MANIFEST_PATH)
GAPS = _yaml(GAPS_PATH)
PINS = _yaml(PINS_PATH)
CATALOG_PAYLOAD = _yaml(DATA / "vapor_pressures.yaml")
CATALOG = compile_vapour_rail_catalog(CATALOG_PAYLOAD)
CATALOG_RULES = {
    rule.species_id: rule
    for rule in CATALOG.request_rules
    if rule.origin == "catalog"
}
CATALOG_RAW_ROWS = {
    str(species_id): row
    for family in CATALOG_PAYLOAD["families"].values()
    for species_id, row in family["physical_properties"]["species"].items()
}


def _pair_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["element"]), str(row["carrier"])


def _ready_catalog_species(row: dict[str, Any]) -> str | None:
    for species_id in row.get("catalog_species_ids", []):
        rule = CATALOG_RULES.get(str(species_id))
        species = CATALOG.species.get(str(species_id))
        if (
            rule is not None
            and species is not None
            and species.evaluator is not None
            and rule.has_pressure_evaluator
            and rule.has_alpha
            and rule.has_route
            and rule.has_formula
        ):
            return str(species_id)
    return None


def _status_only_catalog_species(row: dict[str, Any]) -> str | None:
    """Return only t-583 receipts that cover this exact demand element."""

    for species_id in row.get("catalog_species_ids", []):
        species = CATALOG.species.get(str(species_id))
        if species is None or species.evaluator is None:
            continue
        metadata = species.code_metadata
        element = str(row["element"])
        existing_receipt = metadata.raw.get("t583_existing_executable_composed")
        if (
            isinstance(existing_receipt, dict)
            and existing_receipt.get("status") == "existing_evaluator_wiring_receipt"
            and element in {str(value) for value in existing_receipt.get("coverage_elements", [])}
        ):
            return str(species_id)
        raw_row = CATALOG_RAW_ROWS[str(species_id)]
        nested = raw_row.get("t583_composition")
        coverage = nested if isinstance(nested, dict) else raw_row
        alpha = species.vaporisation_coefficients.evaporation_alpha
        if (
            element in {str(value) for value in coverage.get("coverage_elements", [])}
            and metadata.hot_train_applicability == "not_applicable"
            and metadata.raw.get("t583_status_only_composed") is True
            and alpha.get("status") == "no_data"
            and alpha.get("policy") == "refuse_nonzero_flux"
        ):
            return str(species_id)
    return None


DEMAND_BY_KEY = {_pair_key(row): row for row in MANIFEST["pairs"]}
GAP_BY_KEY = {
    _pair_key(row): row
    for row in GAPS["entries"]
    if row["carrier"] != UNRESOLVED_CARRIER
}
DISCOVERY_GAPS = tuple(
    row for row in GAPS["entries"] if row["carrier"] == UNRESOLVED_CARRIER
)
STRUCTURAL_BY_KEY = {
    key: species_id
    for key, row in DEMAND_BY_KEY.items()
    if (species_id := _ready_catalog_species(row)) is not None
}
LIVE_BY_KEY = {
    key: species_id
    for key, species_id in STRUCTURAL_BY_KEY.items()
    if CATALOG.species[species_id].code_metadata.source_account
    == "process.cleaned_melt"
    and CATALOG.species[
        species_id
    ].vaporisation_coefficients.evaporation_alpha.get("status")
    != "diagnostic_upper_bound"
}
STATUS_ONLY_BY_KEY = {
    key: species_id
    for key, row in DEMAND_BY_KEY.items()
    if (species_id := _status_only_catalog_species(row)) is not None
}
STRUCTURAL_PAIRS = tuple(sorted(STRUCTURAL_BY_KEY))
STRUCTURAL_SPECIES = tuple(sorted(set(STRUCTURAL_BY_KEY.values())))
LIVE_PAIRS = tuple(sorted(LIVE_BY_KEY))
LIVE_SPECIES = tuple(sorted(set(LIVE_BY_KEY.values())))


@pytest.fixture(scope="module", autouse=True)
def harness_ledgers_remain_byte_identical():
    paths = (MANIFEST_PATH, GAPS_PATH, PINS_PATH)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    yield
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    assert after == before, (
        "golden-neutrality violation: conformance execution modified a harness ledger"
    )


def _family_row(species_id: str) -> dict[str, Any]:
    compiled = CATALOG.species[species_id]
    return CATALOG_PAYLOAD["families"][compiled.family_id]["physical_properties"][
        "species"
    ][species_id]


def _selected_reaction(species_id: str) -> dict[str, Any]:
    reaction_id = CATALOG.species[species_id].source_reaction_id
    matches = [
        reaction
        for reaction in _family_row(species_id).get("source_reactions", [])
        if reaction.get("id") == reaction_id
    ]
    assert len(matches) == 1
    return matches[0]


def _reaction_formula_atoms(formula: str) -> dict[str, float]:
    """Parse the decimal pseudo-formulas used by source-reaction declarations."""

    import re

    formula = re.sub(r"\([^()]+\)$", "", formula)
    token = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")
    matches = list(token.finditer(formula))
    assert matches and "".join(match.group(0) for match in matches) == formula
    atoms: dict[str, float] = {}
    for match in matches:
        atoms[match.group(1)] = atoms.get(match.group(1), 0.0) + float(
            match.group(2) or 1.0
        )
    return atoms


def _signed_reaction_terms(species_id: str) -> tuple[tuple[float, str], ...]:
    if CATALOG.species[species_id].source_reaction_id is None:
        return ()
    reaction = _selected_reaction(species_id)
    return tuple(
        (sign * float(term["stoichiometry"]), str(term["formula"]))
        for sign, side in ((-1.0, "reactants"), (1.0, "products"))
        for term in reaction[side]
    )


def _expected_po2_exponent(species_id: str) -> float:
    terms = _signed_reaction_terms(species_id)
    if not terms:
        return 0.0
    reaction = _selected_reaction(species_id)
    vapor_formula = CATALOG.species[species_id].formula
    nu_vapor = sum(
        float(term["stoichiometry"])
        for term in reaction["products"]
        if term["formula"] == vapor_formula
    )
    nu_o2 = sum(
        coefficient for coefficient, formula_id in terms if formula_id == "O2"
    )
    assert nu_vapor > 0.0
    return -nu_o2 / nu_vapor


def _expected_activity_exponent(species_id: str) -> float | None:
    import re

    def phase_neutral(formula: str) -> str:
        return re.sub(r"\([^()]+\)$", "", formula)

    declaration = CATALOG.species[species_id].source_reaction_activity
    if declaration is None:
        return None
    reaction = _selected_reaction(species_id)
    vapor_formula = CATALOG.species[species_id].formula
    nu_vapor = sum(
        float(term["stoichiometry"])
        for term in reaction["products"]
        if term["formula"] == vapor_formula
    )
    nu_activity = -sum(
        float(term["stoichiometry"])
        for term in reaction["reactants"]
        if phase_neutral(str(term["formula"]))
        == phase_neutral(declaration.component_id)
    )
    assert nu_vapor > 0.0 and nu_activity < 0.0
    return -nu_activity / nu_vapor


EXPECTED_PO2_EXPONENTS = {
    species_id: _expected_po2_exponent(species_id)
    for species_id in STRUCTURAL_SPECIES
}
EXPECTED_ACTIVITY_EXPONENTS = {
    species_id: expected
    for species_id in STRUCTURAL_SPECIES
    if (expected := _expected_activity_exponent(species_id)) is not None
}
ZERO_OXYGEN_SPECIES = tuple(
    species_id
    for species_id, expected in EXPECTED_PO2_EXPONENTS.items()
    if expected == 0.0
)


def _evaluate(species_id: str, temperature_K: float, *, activity: float = 0.37,
              pO2_bar: float = 1.0e-9):
    evaluator = CATALOG.evaluator_for(species_id)
    return evaluator.evaluate(
        temperature_K,
        source_activity=activity if evaluator.activity_exponent else None,
        pO2_bar=pO2_bar if evaluator.pO2_exponent else None,
    )


def _resolve_one(species_id: str, temperature_K: float):
    rule = CATALOG_RULES[species_id]
    ledger = {rule.source_account: {parent: 1.0 for parent in rule.parent_species_ids}}
    state_kwargs: dict[str, Any] = {
        "temperature_K": temperature_K,
        "process_phase": "stage0",
        "stage": "stage0",
        "fO2_bar": 1.0e-9,
        "source_reaction_fO2_bar": 1.0e-9,
    }
    if CATALOG.evaluator_for(species_id).activity_exponent:
        activity_declaration = CATALOG.species[species_id].source_reaction_activity
        assert activity_declaration is not None
        state_kwargs.update(
            source_reaction_activities={species_id: 0.37},
            source_reaction_activity_provider="rail_conformance_fixture",
            source_reaction_activity_evidence_refs={
                species_id: f"test_rail_conformance:{species_id}"
            },
            source_reaction_activity_standard_states={
                species_id: activity_declaration.standard_state
            },
        )
    batch = CATALOG.resolve_batch(
        ledger,
        VapourResolveState(**state_kwargs),
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )
    return batch, batch.channel(species_id)


def _assert_two_way_pin(name: str, observed: float, pinned: float) -> None:
    margin = float(PINS["policy"]["margin_dex"])
    assert observed <= pinned + 1.0e-12, (
        f"{name}: residual regression {observed:.12g} dex exceeds pin "
        f"{pinned:.12g} dex"
    )
    assert observed >= pinned - margin - 1.0e-12, (
        f"{name}: stale/loosened pin {pinned:.12g} dex; current residual is "
        f"{observed:.12g} dex. Tighten the pin. A loosening is an explicit, "
        "owner-reviewable validation-pin data diff."
    )


# T3 mass_spec ratchets KEMS/Langmuir observables (psat, rate, Langmuir alpha).
# activity_coefficient / transition_point / gibbs_table are different measurements.
MASS_SPEC_OBSERVATION_TYPES = frozenset({"psat_series", "rate_series", "alpha"})
RESIDUAL_BASELINES_PATH = (
    ROOT / "tests" / "chemistry" / "extract_store_reproduction_residual_baselines.yaml"
)
NA2O_ACTIVITY_PIN_KEY = "ms2000_044_na2o_activity_xsio2_0709_t1673:activity"


@lru_cache(maxsize=1)
def _residual_baseline_points() -> tuple[dict[str, Any], ...]:
    payload = yaml.safe_load(RESIDUAL_BASELINES_PATH.read_text(encoding="utf-8")) or {}
    points = payload.get("points") or []
    assert isinstance(points, list), f"{RESIDUAL_BASELINES_PATH}: expected points list"
    return tuple(points)


def _mass_spec_covered_species(
    rollup: dict[str, dict[str, Any]],
    live_species: set[str],
) -> set[str]:
    return {
        species_id
        for species_id in live_species & set(rollup)
        if rollup[species_id]["max_residual_dex"] is not None
    }


def _mass_spec_expected_species(
    *,
    pins: dict[str, Any],
    live_species: set[str],
    residual_pins: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    covered_species: set[str],
) -> set[str]:
    """LIVE T3 mass_spec declarations backed by a mass-spec-type residual pin.

    An activity / boiling-point residual pin must not mint a species here.
    ``covered_species`` is the live mass-spec-type record set: a declared pin
    whose store record is gone (inadmissible figure, typed skip) does not stay
    expected. Lost-record detection is ``_assert_mass_spec_pin_record_parity``.
    """

    declared = {
        species_id
        for species_id, row in pins["species"].items()
        if species_id in live_species and "mass_spec" in row["validations"]
    }
    typed_pin_species = {
        str(pin["species"])
        for pin in residual_pins
        if pin.get("observation_type") in MASS_SPEC_OBSERVATION_TYPES
        and str(pin["species"]) in live_species
    }
    return declared & typed_pin_species & covered_species


def _assert_mass_spec_pin_record_parity(
    expected_species: set[str],
    covered_species: set[str],
) -> None:
    assert expected_species, "no live rail species declare mass-spec validation pins"
    assert expected_species == covered_species, (
        "mass-spec validation coverage drift: "
        f"pins_without_records={sorted(expected_species - covered_species)}, "
        f"records_without_pins={sorted(covered_species - expected_species)}"
    )


@pytest.fixture(scope="module")
def conformance_sim():
    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


@pytest.fixture(scope="module")
def stage0_committed_carriers(conformance_sim) -> set[str]:
    vapor_data = _load_yaml("vapor_pressures.yaml")
    feedstocks = _load_yaml("feedstocks.yaml")
    setpoints = _load_yaml("setpoints.yaml")

    chloride_feedstocks = deepcopy(feedstocks)
    chloride_row = deepcopy(chloride_feedstocks["lunar_mare_low_ti"])
    composition = dict(chloride_row["composition_wt_pct"])
    for species in ("Cl", "KCl", "NaCl"):
        composition.pop(species, None)
    composition.update({"KCl": 0.3, "NaCl": 0.3})
    chloride_row["composition_wt_pct"] = composition
    chloride_row["sum_check"] = sum(float(value) for value in composition.values())
    chloride_feedstocks["mc4b_stage0_chlorides"] = chloride_row

    ceres_feed = feedstocks["ceres_regolith"]
    ceres_carbon_kg = conformance_sim._carbon_reductant_required_kg(
        ceres_feed, 1000.0
    )
    sulfate_feed = feedstocks["mars_sulfate_rich"]
    sulfate_carbon_kg = conformance_sim._carbon_reductant_required_kg(
        sulfate_feed, 1000.0
    )
    sims = (
        _build_sim(
            "mc4b_stage0_chlorides",
            vapor_data,
            chloride_feedstocks,
            setpoints,
        ),
        _build_sim(
            "ceres_regolith",
            vapor_data,
            feedstocks,
            setpoints,
            additives_kg={"C": ceres_carbon_kg},
        ),
        _build_sim(
            "mars_sulfate_rich",
            vapor_data,
            feedstocks,
            setpoints,
            additives_kg={"C": sulfate_carbon_kg},
        ),
    )
    chloride_debit_kg = {"KCl": 0.0, "NaCl": 0.0}
    chloride_products: dict[str, set[str]] = {"KCl": set(), "NaCl": set()}
    for transition in sims[0].atom_ledger.transitions:
        if transition.reason != "stage0_foulant_volatilization":
            continue
        for lot in transition.debits:
            if lot.account != "process.stage0_foulant":
                continue
            for source_species in chloride_debit_kg:
                chloride_debit_kg[source_species] += float(
                    lot.species_kg.get(source_species, 0.0)
                )
        credited = {
            species
            for lot in transition.credits
            if lot.account == "terminal.offgas"
            for species in lot.species_kg
        }
        if any(
            float(lot.species_kg.get("KCl", 0.0)) > 0.0
            for lot in transition.debits
        ):
            chloride_products["KCl"].update(credited)
        if any(
            float(lot.species_kg.get("NaCl", 0.0)) > 0.0
            for lot in transition.debits
        ):
            chloride_products["NaCl"].update(credited)
    # C6-style independent source bound: monomer+dimer channels may split the
    # normalized raw-feed reservoir, but their summed debit can never exceed
    # the one externally supplied source mass.
    raw_chloride_kg = sims[0].inventory.raw_components_kg
    assert chloride_debit_kg["KCl"] <= raw_chloride_kg["KCl"] + 1.0e-10
    assert chloride_debit_kg["NaCl"] <= raw_chloride_kg["NaCl"] + 1.0e-10
    assert chloride_products == {
        "KCl": {"KCl", "K2Cl2"},
        "NaCl": {"NaCl", "Na2Cl2"},
    }
    committed: set[str] = set()
    for sim in sims:
        for transition in sim.atom_ledger.transitions:
            for lot in transition.credits:
                if lot.account == "terminal.offgas":
                    committed.update(lot.species_kg)
    return committed


def _covered_pairs(stage0_committed_carriers: set[str]) -> set[tuple[str, str]]:
    covered = set(LIVE_BY_KEY)
    covered.update(STATUS_ONLY_BY_KEY)
    covered.update(
        key
        for key, species_id in STRUCTURAL_BY_KEY.items()
        if species_id in stage0_committed_carriers
    )
    return covered


def test_demand_manifest_is_fresh() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_rail_demand_manifest.py"),
            "--check",
            "--output",
            str(MANIFEST_PATH),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_harness_ledgers_are_not_simulation_runtime_inputs() -> None:
    forbidden = {
        "vapour_rail_demand_manifest.yaml",
        "vapour_rail_coverage_gaps.yaml",
        "vapour_rail_validation_pins.yaml",
    }
    runtime_files = [
        *sorted((ROOT / "simulator").rglob("*.py")),
        *sorted((ROOT / "engines").rglob("*.py")),
        ROOT / "app.py",
        ROOT / "regolith-pyrolysis-run.py",
    ]
    consumers = {
        str(path.relative_to(ROOT)): sorted(name for name in forbidden if name in text)
        for path in runtime_files
        if (text := path.read_text(encoding="utf-8"))
        and any(name in text for name in forbidden)
    }
    assert not consumers, (
        "golden-neutrality violation: conformance ledgers entered the simulation "
        f"run path: {consumers}"
    )


def test_demand_manifest_counts_are_truthful() -> None:
    pairs = MANIFEST["pairs"]
    assert len(pairs) == len(DEMAND_BY_KEY), "duplicate demand pair"
    assert MANIFEST["elements"] == sorted(set(MANIFEST["elements"]))
    assert MANIFEST["carriers"] == sorted({row["carrier"] for row in pairs})
    assert pairs == sorted(pairs, key=_pair_key)
    assert MANIFEST["counts"] == {
        "elements": len(set(MANIFEST["elements"])),
        "carriers": len({row["carrier"] for row in pairs}),
        "pairs": len(pairs),
        "catalog_linked_pairs": sum(
            bool(row["catalog_species_ids"]) for row in pairs
        ),
    }
    for row in pairs:
        parsed_atoms = _reaction_formula_atoms(row["formula"])
        assert row["atoms"] == {
            element: parsed_atoms[element] for element in sorted(parsed_atoms)
        }
        assert row["element"] in parsed_atoms


def test_demand_manifest_ratchet_rejects_source_census_shrinkage() -> None:
    ratchet = PINS["demand_manifest_ratchet"]
    count_keys = {
        "minimum_elements": "elements",
        "minimum_carriers": "carriers",
        "minimum_pairs": "pairs",
        "minimum_catalog_linked_pairs": "catalog_linked_pairs",
    }
    for floor_key, count_key in count_keys.items():
        observed = int(MANIFEST["counts"][count_key])
        floor = int(ratchet[floor_key])
        assert observed >= floor, (
            f"demand-manifest ratchet regressed: {count_key}={observed} < "
            f"{floor_key}={floor}"
        )


def test_gap_ledger_schema_and_reason_types() -> None:
    assert set(GAPS["reason_types"]) == REASON_TYPES
    entries = GAPS["entries"]
    keys = [_pair_key(row) for row in entries]
    assert len(keys) == len(set(keys)), "duplicate coverage-gap entry"
    for row in entries:
        assert row["reason"] in REASON_TYPES
        assert isinstance(row.get("missing"), str) and row["missing"].strip()
        if row["carrier"] == UNRESOLVED_CARRIER:
            assert row["contract"] == "carrier-discovery"
            assert row["reason"] == "acquisition-pending"
            continue
        demanded = DEMAND_BY_KEY.get(_pair_key(row))
        assert demanded is not None, f"coverage gap is not demanded: {_pair_key(row)}"
        if row["reason"] == "thermo_missing":
            assert row["contract"] == "C2-C5"
            assert not demanded["thermo_available"], (
                f"{_pair_key(row)}: thermo_missing is stale; thermo is available"
            )
        if row["reason"] == "acquisition-pending":
            key = _pair_key(row)
            if row["contract"] == "C1-C5":
                assert demanded["thermo_available"], (
                    f"{key}: acquisition-pending cannot mask missing thermo"
                )
                assert key not in STRUCTURAL_BY_KEY, (
                    f"{key}: acquisition-pending C1-C5 gap is stale; C1-C4 are ready"
                )
            elif row["contract"] == "C2-C5":
                assert demanded["thermo_available"], (
                    f"{key}: acquisition-pending cannot mask missing thermo"
                )
                assert row.get("disposition") == "NEEDS-BASE", (
                    f"{key}: acquisition-pending C2-C5 is reserved for an explicit "
                    "NEEDS-BASE reservoir gap"
                )
                assert key not in STRUCTURAL_BY_KEY, (
                    f"{key}: acquisition-pending C2-C5 gap is stale; C2-C4 are ready"
                )
            elif row["contract"] == "C5":
                assert key in STRUCTURAL_BY_KEY and key not in LIVE_BY_KEY, (
                    f"{key}: acquisition-pending C5 gap does not match live capabilities"
                )
            else:
                pytest.fail(
                    f"{key}: acquisition-pending has unsupported contract "
                    f"{row['contract']!r}"
                )
        if row["reason"] == "activity_missing":
            assert demanded["thermo_available"], (
                f"{_pair_key(row)}: activity_missing cannot mask missing thermo"
            )
            assert "C4" in row["contract"] and _pair_key(row) not in LIVE_BY_KEY, (
                f"{_pair_key(row)}: activity_missing is stale or names the wrong contract"
            )
            declared_activity_terms = [
                CATALOG.species[species_id].source_reaction_activity
                for species_id in demanded["catalog_species_ids"]
                if species_id in CATALOG.species
            ]
            assert any(term is not None for term in declared_activity_terms), (
                f"{_pair_key(row)}: activity_missing lacks an activity-dependent "
                "reaction declaration"
            )
        if row["reason"] == "negligibility-proven-with-margin":
            margin = row.get("negligibility_margin_dex")
            observed = row.get("observed_upper_bound_dex")
            threshold = row.get("acceptance_threshold_dex")
            refs = row.get("evidence_refs")
            assert isinstance(margin, (int, float)) and margin > 0.0
            assert isinstance(observed, (int, float)) and math.isfinite(observed)
            assert isinstance(threshold, (int, float)) and math.isfinite(threshold)
            assert threshold - observed == pytest.approx(margin), (
                f"{_pair_key(row)}: negligibility margin is stale"
            )
            assert isinstance(refs, list) and refs and all(
                isinstance(ref, str) and ref.strip() for ref in refs
            )
            for ref in refs:
                if "://" in ref or ref.startswith("doi:"):
                    continue
                assert (ROOT / ref.split("#", 1)[0]).is_file(), (
                    f"{_pair_key(row)}: negligibility evidence is stale: {ref}"
                )


def test_feedstock_element_without_catalog_carrier_requires_typed_gap() -> None:
    catalog_elements = {
        row["element"] for row in MANIFEST["pairs"] if row["catalog_species_ids"]
    }
    gap_elements = {row["element"] for row in GAPS["entries"]}
    missing = sorted(set(MANIFEST["elements"]) - catalog_elements - gap_elements)
    assert not missing, (
        "feedstock elements have neither a catalog carrier nor a typed "
        f"coverage gap: {missing}"
    )


def test_every_uncovered_demand_pair_is_listed(stage0_committed_carriers) -> None:
    listed = set(GAP_BY_KEY)
    expected = set(DEMAND_BY_KEY) - _covered_pairs(stage0_committed_carriers)
    missing = sorted(expected - listed)
    assert not missing, f"uncovered demanded pairs missing from gap ledger: {missing}"
    unexpected = sorted(listed - set(DEMAND_BY_KEY))
    assert not unexpected, f"C1-C5 ledger entries are not demanded: {unexpected}"


def test_covered_pairs_are_not_stale_in_gap_ledger(stage0_committed_carriers) -> None:
    listed = set(GAP_BY_KEY)
    stale = sorted(_covered_pairs(stage0_committed_carriers) & listed)
    assert not stale, f"covered pairs remain stale in gap ledger: {stale}"
    demand_elements = {row["element"] for row in MANIFEST["pairs"]}
    stale_discovery = sorted(
        row["element"]
        for row in DISCOVERY_GAPS
        if row["element"] not in MANIFEST["elements"]
        or row["element"] in demand_elements
    )
    assert not stale_discovery, (
        "carrier-discovery gaps are stale once an element is absent or has a "
        f"known demanded carrier: {stale_discovery}"
    )


def test_partial_structural_pairs_name_only_the_remaining_contract_gap(
    stage0_committed_carriers,
) -> None:
    partial = set(STRUCTURAL_BY_KEY) & set(GAP_BY_KEY)
    for key in partial:
        row = GAP_BY_KEY[key]
        assert row["contract"] == "C5"
        assert row["reason"] == "acquisition-pending"

    for species_id in sorted({STRUCTURAL_BY_KEY[key] for key in partial}):
        species = CATALOG.species[species_id]
        if species.code_metadata.source_account == "process.stage0_foulant":
            assert species_id not in stage0_committed_carriers, (
                f"{species_id}: C5 gap is stale because an end-to-end Stage-0 "
                "batch committed its source debit and offgas credit"
            )
        else:
            assert species.code_metadata.source_account == "process.cleaned_melt"
            alpha = species.vaporisation_coefficients.evaporation_alpha
            assert alpha.get("status") == "diagnostic_upper_bound"
            assert CATALOG_RAW_ROWS[species_id].get("flux_dormant") is True


def test_stage0_coverage_requires_committed_runtime_debit_credit(
    stage0_committed_carriers,
) -> None:
    assert {
        "KCl",
        "K2Cl2",
        "NaCl",
        "Na2Cl2",
        "N2",
        "NH3",
        "SO2",
    } <= stage0_committed_carriers
    assert "MgCl2" not in stage0_committed_carriers


def test_live_parametrization_has_pinned_non_vacuity_floor() -> None:
    floor = int(PINS["conformance"]["live_pair_floor"])
    assert len(LIVE_PAIRS) >= floor, (
        f"live pair count {len(LIVE_PAIRS)} fell below pinned floor {floor}; "
        "the gap ledger may be swallowing executable cases"
    )


def test_validation_tiers_and_promotions_have_external_evidence() -> None:
    default_target = PINS["policy"]["target_tier"]
    assert default_target in TIER_ORDER
    assert PINS["species"], "validation pin species ledger must be non-empty"
    missing_live_pins = sorted(set(LIVE_SPECIES) - set(PINS["species"]))
    assert not missing_live_pins, (
        f"live rail species lack validation pins: {missing_live_pins}"
    )
    oracle = PINS["systematic_sio_oracle"]
    assert oracle["status"] in {"evaluator_gap", "ready"}
    oracle_refs = oracle.get("evidence_refs")
    assert isinstance(oracle_refs, list) and oracle_refs
    for ref in oracle_refs:
        _assert_evidence_ref_present_and_tracked(
            ref, context="SiO oracle"
        )
    for species_id, row in sorted(PINS["species"].items()):
        tier = row["tier"]
        target = row["target_tier"]
        assert tier in TIER_ORDER and target in TIER_ORDER
        assert target == default_target
        assert TIER_ORDER[tier] <= TIER_ORDER[target]
        assert tier != "T4", (
            f"{species_id}: T4 promotion is RED until an executable external "
            "ratio-residual ratchet is implemented"
        )
        validations = row.get("validations")
        assert isinstance(validations, dict) and validations
        if tier == "T1":
            assert "structural" in validations
        required_validation = TIER_VALIDATIONS.get(tier)
        if required_validation is not None:
            # Tiers classify the strongest available external evidence; they
            # are not cumulative because an engine may not answer a species
            # that has direct mass-spec coverage (Mn is the concrete case).
            assert required_validation in validations, (
                f"{species_id}: {tier} promotion lacks {required_validation} evidence"
            )
        for required_tier, validation_name in TIER_VALIDATIONS.items():
            if validation_name in validations:
                assert TIER_ORDER[tier] >= TIER_ORDER[required_tier], (
                    f"{species_id}: {validation_name} evidence is hidden by {tier} demotion"
                )
        for validation_name, validation in validations.items():
            assert validation_name in {"structural", *TIER_VALIDATIONS.values()}
            if validation_name != "structural":
                pinned = validation.get("pinned_residual_dex")
                assert isinstance(pinned, (int, float)) and math.isfinite(pinned)
                assert pinned >= 0.0
            refs = validation.get("evidence_refs")
            assert isinstance(refs, list) and refs and all(
                isinstance(ref, str) and ref.strip() for ref in refs
            ), f"{species_id}:{validation_name}: promotion evidence refs required"
            for ref in refs:
                _assert_evidence_ref_present_and_tracked(
                    ref, context=f"{species_id}:{validation_name}"
                )
            # Supplementary private notes are optional and unguarded: they must
            # never be the sole promotion evidence (that is what evidence_refs is for).
            notes = validation.get("internal_notes_refs")
            if notes is not None:
                assert isinstance(notes, list) and all(
                    isinstance(n, str) and n.strip() for n in notes
                ), f"{species_id}:{validation_name}: internal_notes_refs must be a string list"


@pytest.mark.parametrize("element,carrier", STRUCTURAL_PAIRS)
def test_c1_keys_resolve_through_real_consumers(
    element: str, carrier: str, conformance_sim
) -> None:
    species_id = STRUCTURAL_BY_KEY[(element, carrier)]
    compiled = CATALOG.species[species_id]
    rule = CATALOG_RULES[species_id]

    assert CATALOG.evaluator_for(species_id) is compiled.evaluator
    if compiled.code_metadata.hot_train_applicability in {"applicable", "always"}:
        assert CATALOG.evaluator_for_hot_train(species_id) is compiled.evaluator
    else:
        with pytest.raises(HotTrainInapplicable):
            CATALOG.evaluator_for_hot_train(species_id)
    assert all(
        (rule.has_pressure_evaluator, rule.has_alpha, rule.has_route, rule.has_formula)
    )
    legacy = CATALOG.legacy_view()
    projection = compiled.code_metadata.compatibility_projection
    assert species_id in legacy[projection]
    assert species_id in conformance_sim.species_formula_registry
    formula = resolve_species_formula(
        species_id, conformance_sim.species_formula_registry
    )
    assert formula.molar_mass_kg_per_mol() > 0.0

    midpoint = sum(compiled.valid_temperature_K) / 2.0
    if compiled.code_metadata.source_account == "process.stage0_foulant":
        direct = _evaluate(species_id, midpoint)
        assert direct.pressure_pa > 0.0
        split = chi_escape_salt(species_id, midpoint - 273.15, 1.0e-3)
        assert 0.0 <= split.escaped_frac <= 1.0
    else:
        batch, answer = _resolve_one(species_id, midpoint)
        assert species_id in batch.requested_species_ids
        if answer.extra.get("alpha_authority_status") == "diagnostic_upper_bound":
            assert isinstance(answer.pressure, PressureUpperBound)
            assert isinstance(answer.flux, FluxDiagnosticUpperBound)
            assert species_id not in batch.flux_active_species_ids
        else:
            assert isinstance(answer.pressure, PressureValue)
            assert isinstance(answer.flux, FluxEligible)
            assert species_id in batch.flux_active_species_ids
        rendered = serialize_vapour_answer(answer)
        assert rendered["species_id"] == species_id
        assert rendered["pressure"]["pa"] > 0.0


@pytest.mark.parametrize(
    "species_id",
    ("Ca", "Ti", "CaO_gas", "TiO", "TiO2_gas", "Ca2"),
)
def test_ca_ti_alpha_ceiling_uses_canonical_catalog_receipt(
    species_id: str,
) -> None:
    compiled = CATALOG.species[species_id]
    alpha = compiled.vaporisation_coefficients.evaporation_alpha
    midpoint = sum(compiled.valid_temperature_K) / 2.0

    _, answer = _resolve_one(species_id, midpoint)
    rendered = serialize_vapour_answer(answer)

    assert alpha["status"] == "analytical_upper_bound"
    assert alpha["tag"] == "hkl_ideal_upper_bound_status_bearing"
    assert rendered["extra"]["alpha_authority_status"] == (
        "analytical_upper_bound"
    )
    assert rendered["extra"]["alpha_inventory_policy"] == (
        "inventory_eligible_analytical_upper_bound_noncertifying"
    )


@pytest.mark.parametrize("species_id", STRUCTURAL_SPECIES)
def test_c2_pressure_is_finite_over_required_grid(species_id: str) -> None:
    evaluator = CATALOG.evaluator_for(species_id)
    for temperature_K in (1200.0, 1400.0, 1600.0, 1800.0, 2000.0, 2300.0):
        for pO2_bar in (1.0e-6, 1.0e-9, 1.0e-12):
            result = _evaluate(species_id, temperature_K, pO2_bar=pO2_bar)
            assert math.isfinite(result.pressure_pa) and result.pressure_pa > 0.0
            low, high = evaluator.valid_temperature_K
            assert result.out_of_range is not (low <= temperature_K <= high)
            if result.out_of_range:
                assert result.status and result.acquisition_flag


@pytest.mark.parametrize("species_id", STRUCTURAL_SPECIES)
def test_c2_out_of_domain_value_is_typed_diagnostic_flux(species_id: str) -> None:
    # Runtime contract b-142: the anti-cliff continuation is normally the best
    # available status-bearing point estimate. Rows whose source polynomials are
    # narrower than the outer continuation declare a typed refusal instead.
    if CATALOG.species[species_id].code_metadata.source_account == "process.stage0_foulant":
        direct = _evaluate(species_id, 2300.0)
        split = chi_escape_salt(species_id, 2300.0 - 273.15, 1.0e-3)
        assert direct.out_of_range and direct.status and direct.pressure_pa > 0.0
        assert split.escaped_frac > 0.0 and split.warning
    else:
        evaluator = CATALOG.evaluator_for(species_id)
        out_of_range_temperature_K = evaluator.valid_temperature_K[1] + 1.0
        batch, answer = _resolve_one(species_id, out_of_range_temperature_K)
        typed_refusal = (
            CATALOG.species[species_id].code_metadata.raw.get(
                "out_of_domain_disposition"
            )
            == "typed_refusal"
        )
        if typed_refusal:
            assert isinstance(answer.pressure, PressureRefusal)
            assert answer.pressure.code == "outside_declared_evaluator_domain"
            assert isinstance(answer.flux, FluxRefusal)
            assert species_id not in batch.flux_active_species_ids
        elif answer.extra.get("alpha_authority_status") == "diagnostic_upper_bound":
            assert isinstance(answer.pressure, PressureUpperBound)
            assert isinstance(answer.flux, FluxDiagnosticUpperBound)
            assert species_id not in batch.flux_active_species_ids
        else:
            assert isinstance(answer.pressure, PressureValue)
            assert isinstance(answer.flux, FluxEligible)
            assert species_id in batch.flux_active_species_ids
        if not typed_refusal:
            assert answer.extra["out_of_range"] is True
            assert answer.extra["status"]
            assert answer.extra["acquisition_flag"]
            assert answer.pressure.pa > 0.0


@pytest.mark.parametrize("species_id", STRUCTURAL_SPECIES)
def test_c3_po2_exponent_is_stoichiometric_and_numerically_active(
    species_id: str,
) -> None:
    signed_terms = _signed_reaction_terms(species_id)

    atom_balance: dict[str, float] = {}
    for coefficient, formula_id in signed_terms:
        for atom, count in _reaction_formula_atoms(formula_id).items():
            atom_balance[atom] = atom_balance.get(atom, 0.0) + coefficient * count
    assert all(abs(value) <= 1.0e-12 for value in atom_balance.values())

    # Premise: per mol carrier, oxygen released by M_xO_y -> carrier + q O2 is
    # q = (x-y)/2. Algebra: K = p_v^nu_v * pO2^nu_o2 / a, hence
    # d ln(P_v)/d ln(pO2) = -nu_o2/nu_v = -q. Unit check: all coefficients
    # and the logarithmic derivative are dimensionless. Sanity: more O2
    # suppresses an oxygen-releasing metal-vapour reaction, so slope is negative.
    expected_exponent = EXPECTED_PO2_EXPONENTS[species_id]
    evaluator = CATALOG.evaluator_for(species_id)
    assert evaluator.pO2_exponent == pytest.approx(expected_exponent, abs=1.0e-12)

    temperature_K = sum(evaluator.valid_temperature_K) / 2.0
    p1, p2 = 1.0e-10, 1.0e-8
    activity = 0.37 if species_id in EXPECTED_ACTIVITY_EXPONENTS else None
    pressure1 = evaluator.evaluate(
        temperature_K, source_activity=activity, pO2_bar=p1
    ).pressure_pa
    pressure2 = evaluator.evaluate(
        temperature_K, source_activity=activity, pO2_bar=p2
    ).pressure_pa
    numerical = math.log(pressure2 / pressure1) / math.log(p2 / p1)
    assert numerical == pytest.approx(expected_exponent, rel=1.0e-11, abs=1.0e-12)


@pytest.mark.parametrize("species_id", ZERO_OXYGEN_SPECIES)
def test_c3_y_zero_limiting_case_is_unity(species_id: str) -> None:
    # Premise: y=0 means no O2 reaction term. Algebra: (pO2/p_ref)^0 = 1.
    # Unit check: the pressure ratio is dimensionless. Sanity: changing pO2
    # cannot move a reaction with no oxygen coefficient.
    evaluator = CATALOG.evaluator_for(species_id)
    assert evaluator.pO2_exponent == 0.0
    temperature_K = sum(evaluator.valid_temperature_K) / 2.0
    low = _evaluate(species_id, temperature_K, pO2_bar=1.0e-12).pressure_pa
    high = _evaluate(species_id, temperature_K, pO2_bar=1.0e-6).pressure_pa
    assert high / low == PINS["internal_sensibility"]["y_zero_ratio"]


def test_c4_each_activity_unity_reversion_moves_its_own_answer() -> None:
    baseline_activity = 0.37
    activities = {
        species_id: baseline_activity for species_id in EXPECTED_ACTIVITY_EXPONENTS
    }
    original = dict(activities)
    try:
        for species_id, expected_exponent in EXPECTED_ACTIVITY_EXPONENTS.items():
            evaluator = CATALOG.evaluator_for(species_id)
            assert evaluator.activity_exponent == pytest.approx(
                expected_exponent, abs=1.0e-12
            )
            temperature_K = sum(evaluator.valid_temperature_K) / 2.0
            baseline = _evaluate(
                species_id, temperature_K, activity=activities[species_id]
            ).pressure_pa
            activities[species_id] = 1.0
            mutated = _evaluate(
                species_id, temperature_K, activity=activities[species_id]
            ).pressure_pa
            # Premise: P = P_ref * a^n. Algebra: P(a=1)/P(a=a0)
            # = (1/a0)^n. Unit check: pressure ratio and activity are
            # dimensionless. Sanity: n>0 and a0<1 must increase pressure.
            expected_ratio = (1.0 / baseline_activity) ** expected_exponent
            assert mutated / baseline == pytest.approx(expected_ratio, rel=1.0e-12)
            assert mutated > baseline
            activities[species_id] = baseline_activity
    finally:
        activities.clear()
        activities.update(original)
    assert activities == original


@pytest.mark.parametrize("species_id", LIVE_SPECIES)
def test_c5_debit_route_alpha_and_source_metadata_are_executable(
    species_id: str, conformance_sim
) -> None:
    species = CATALOG.species[species_id]
    rule = CATALOG_RULES[species_id]
    assert rule.source_account == species.code_metadata.source_account
    assert rule.parent_species_ids
    assert species.fiat_routing.process_or_terminal_destination
    if species.code_metadata.source_account == "process.cleaned_melt":
        legacy_row = (
            conformance_sim.vapor_pressures.get("metals", {}).get(species_id)
            or conformance_sim.vapor_pressures.get("oxide_vapors", {}).get(species_id)
            or {}
        )
        parent = str(legacy_row["parent_oxide"])
        owner = next(
            element
            for (element, _carrier), live_species_id in LIVE_BY_KEY.items()
            if live_species_id == species_id
        )
        rate_kg_hr = 1.0e-7
        carrier_formula = resolve_species_formula(
            species_id, conformance_sim.species_formula_registry
        )
        parent_formula = resolve_species_formula(
            parent, conformance_sim.species_formula_registry
        )
        carrier_mol = rate_kg_hr / carrier_formula.molar_mass_kg_per_mol()
        expected_parent_mol = (
            carrier_mol
            * carrier_formula.elements[owner]
            / parent_formula.elements[owner]
        )
        expected_owner_atoms = carrier_mol * carrier_formula.elements[owner]
        parent_inventory = conformance_sim.atom_ledger.mol_by_species(
            "process.cleaned_melt"
        )
        if parent not in parent_inventory:
            parent_buffer_kg = (
                2.0
                * expected_parent_mol
                * parent_formula.molar_mass_kg_per_mol()
            )
            conformance_sim.record.additives_kg[parent] = (
                conformance_sim.record.additives_kg.get(parent, 0.0)
                + parent_buffer_kg
            )
            conformance_sim.atom_ledger.load_external(
                "process.cleaned_melt",
                {parent: parent_buffer_kg},
                source=f"{species_id} conformance parent buffer",
                material_origin="feedstock",
            )
        oxygen_consumed_kg = max(
            0.0,
            -float(legacy_row.get("stoich_O2_per_vapor", 0.0)) * rate_kg_hr,
        )
        if oxygen_consumed_kg > 0.0:
            oxygen_buffer_kg = 2.0 * oxygen_consumed_kg
            conformance_sim.record.additives_kg["O2"] = (
                conformance_sim.record.additives_kg.get("O2", 0.0)
                + oxygen_buffer_kg
            )
            conformance_sim.atom_ledger.load_external(
                "process.overhead_gas",
                {"O2": oxygen_buffer_kg},
                source=f"{species_id} conformance reactant buffer",
                material_origin="feedstock",
            )
        before_parent = conformance_sim.atom_ledger.mol_by_species(
            "process.cleaned_melt"
        )[parent]
        before_owner = conformance_sim.atom_ledger.atom_moles_by_account(
            "process.cleaned_melt"
        )[owner]
        _credited, transition = conformance_sim._credit_evaporation_transition(
            species_id,
            rate_kg_hr,
            rate_kg_hr,
            legacy_row,
            apply_evaporative_redox_source_terms=False,
            return_transition=True,
        )
        assert transition is not None
        after_parent = conformance_sim.atom_ledger.mol_by_species(
            "process.cleaned_melt"
        ).get(parent, 0.0)
        after_owner = conformance_sim.atom_ledger.atom_moles_by_account(
            "process.cleaned_melt"
        )[owner]
        assert before_parent - after_parent == pytest.approx(
            expected_parent_mol, rel=1.0e-10
        )
        assert before_owner - after_owner == pytest.approx(
            expected_owner_atoms, rel=1.0e-10
        )
        assert abs(conformance_sim._make_snapshot().mass_balance_error_pct) <= 5.0e-12

        alpha_value, alpha_diagnostic = grounded_alpha(
            species_id, sum(species.valid_temperature_K) / 2.0
        )
        assert math.isfinite(alpha_value) and 0.0 < alpha_value <= 1.0
        assert alpha_diagnostic
        provider, _payload = load_rail_provider()
        report = json.loads(ENGINE_REPORT_PATH.read_text(encoding="utf-8"))
        composition_mol = deepcopy(report["composition"]["composition_mol"])
        if parent not in composition_mol:
            composition_mol[parent] = 2.0 * expected_parent_mol
        cell = _run_rail_cell(
            provider,
            composition_mol=composition_mol,
            temperature_K=sum(species.valid_temperature_K) / 2.0,
            fo2_log10_bar=-9.0,
            pressure_bar=float(report["domain"]["pressure_bar"]),
            process_phase="stage0",
        )
        assert cell["status"] in {"ok", "non_authoritative"}
        assert float(cell["pressures_Pa"][species_id]) > 0.0
        assert isinstance(cell["sources"][species_id], str) and cell["sources"][
            species_id
        ]
    else:
        pytest.fail(
            f"{species_id}: C5-ready carrier lacks an executable inventory-debit "
            "transition; retain a typed evaluator_gap until runtime plumbing exists"
        )


@pytest.mark.parametrize(
    "species_id", tuple(PINS["internal_sensibility"]["pure_component_species"])
)
def test_t1_pure_component_clausius_clapeyron_floor(species_id: str) -> None:
    evaluator = CATALOG.evaluator_for(species_id)
    low, high = evaluator.valid_temperature_K
    t1 = low + 0.35 * (high - low)
    t2 = low + 0.65 * (high - low)
    p1 = _evaluate(species_id, t1).pressure_pa
    p2 = _evaluate(species_id, t2).pressure_pa
    assert p2 > p1 > 0.0
    slope = math.log(p2 / p1) / ((1.0 / t2) - (1.0 / t1))
    delta_h_kj_mol = -8.31446261815324 * slope / 1000.0
    minimum, maximum = PINS["internal_sensibility"]["delta_h_vap_kJ_mol"]
    assert minimum <= delta_h_kj_mol <= maximum
    # T1 is necessary internal consistency only. It NEVER validates a species:
    # the same model supplies both pressures, so passing can be self-agreement.


@pytest.fixture(scope="module")
def mass_spec_evaluations() -> list[Any]:
    return [
        ev
        for ev in evaluate_all()
        if ev.observation.obs_type in MASS_SPEC_OBSERVATION_TYPES
    ]


@pytest.fixture(scope="module")
def mass_spec_rollup(mass_spec_evaluations: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        row["species"]: row
        for row in rollup_species_error_bars(mass_spec_evaluations)
    }


def test_t3_na2o_activity_pin_remains_admitted() -> None:
    matches = [
        pin
        for pin in _residual_baseline_points()
        if pin.get("key") == NA2O_ACTIVITY_PIN_KEY
    ]
    assert matches, (
        f"Na2O activity pin {NA2O_ACTIVITY_PIN_KEY} was deleted; "
        "d-006 binary-melt admission must stay in the residual ledger"
    )
    pin = matches[0]
    assert pin["species"] == "Na2O"
    assert pin["observation_type"] == "activity_coefficient"
    assert pin["observation_type"] not in MASS_SPEC_OBSERVATION_TYPES


def test_t3_guard_still_fires_when_mass_spec_pin_loses_its_record() -> None:
    expected_species = {"Fe", "K", "Mg", "SiO"}
    covered_species = {"K", "Mg", "SiO"}
    with pytest.raises(AssertionError, match=r"pins_without_records=\['Fe'\]"):
        _assert_mass_spec_pin_record_parity(expected_species, covered_species)


def test_t3_activity_residual_pin_does_not_mint_mass_spec_expected_species() -> None:
    pins = {
        "species": {
            "Na": {"validations": {"mass_spec": {"pinned_residual_dex": 1.0}}},
            "Fe": {"validations": {"mass_spec": {"pinned_residual_dex": 1.0}}},
        }
    }
    residual_pins = (
        {
            "key": NA2O_ACTIVITY_PIN_KEY,
            "observation_type": "activity_coefficient",
            "species": "Na2O",
        },
        {
            "key": "synthetic_na_activity:activity",
            "observation_type": "activity_coefficient",
            "species": "Na",
        },
        {
            "key": "synthetic_fe_psat:T=1800",
            "observation_type": "psat_series",
            "species": "Fe",
        },
    )
    expected = _mass_spec_expected_species(
        pins=pins,
        live_species={"Na", "Fe"},
        residual_pins=residual_pins,
        covered_species={"Na", "Fe"},
    )
    assert "Na" not in expected, (
        "activity residual pin minted Na as a mass-spec expected species"
    )
    assert expected == {"Fe"}


def test_t3_unfixed_expected_set_names_na_without_mass_spec_record(
    mass_spec_rollup: dict[str, dict[str, Any]],
) -> None:
    """Red-by-revert: every PINS mass_spec key vs mass-spec-type records.

    The assertion must name pins_without_records=['Na'] — Na has a T3
    mass_spec declaration but no live mass-spec-type store record.
    """

    unfixed_expected = {
        species_id
        for species_id, row in PINS["species"].items()
        if species_id in LIVE_SPECIES and "mass_spec" in row["validations"]
    }
    covered = _mass_spec_covered_species(mass_spec_rollup, set(LIVE_SPECIES))
    with pytest.raises(AssertionError, match=r"pins_without_records=\['Na'\]"):
        _assert_mass_spec_pin_record_parity(unfixed_expected, covered)


def test_t3_expected_species_come_from_mass_spec_type_pins_only(
    mass_spec_rollup: dict[str, dict[str, Any]],
) -> None:
    covered = _mass_spec_covered_species(mass_spec_rollup, set(LIVE_SPECIES))
    expected = _mass_spec_expected_species(
        pins=PINS,
        live_species=set(LIVE_SPECIES),
        residual_pins=_residual_baseline_points(),
        covered_species=covered,
    )
    assert "Na" not in expected, (
        "Na entered expected_species without a mass-spec-type store record; "
        "an activity residual pin must not mint mass-spec coverage"
    )
    _assert_mass_spec_pin_record_parity(expected, covered)


def test_t3_mass_spec_residual_pins_are_two_way(
    mass_spec_rollup: dict[str, dict[str, Any]]
) -> None:
    covered = _mass_spec_covered_species(mass_spec_rollup, set(LIVE_SPECIES))
    expected_species = _mass_spec_expected_species(
        pins=PINS,
        live_species=set(LIVE_SPECIES),
        residual_pins=_residual_baseline_points(),
        covered_species=covered,
    )
    _assert_mass_spec_pin_record_parity(expected_species, covered)
    for species_id in sorted(expected_species):
        row = PINS["species"][species_id]
        observed = float(mass_spec_rollup[species_id]["max_residual_dex"])
        pinned = float(
            row["validations"]["mass_spec"]["pinned_residual_dex"]
        )
        _assert_two_way_pin(f"mass_spec:{species_id}", observed, pinned)


@pytest.fixture(scope="module")
def engine_residuals() -> dict[str, float]:
    report = json.loads(ENGINE_REPORT_PATH.read_text(encoding="utf-8"))
    rows = [
        row
        for row in report["rows"]
        if row["species"] in LIVE_SPECIES and row["coverage"] == "matched_point"
    ]
    external_species = {row["species"] for row in rows}
    provider, _payload = load_rail_provider()
    cells: dict[tuple[float, float], dict[str, Any]] = {}
    for row in rows:
        key = (float(row["temperature_K"]), float(row["fo2_log10_bar"]))
        if key not in cells:
            cells[key] = _run_rail_cell(
                provider,
                composition_mol=report["composition"]["composition_mol"],
                temperature_K=key[0],
                fo2_log10_bar=key[1],
                pressure_bar=float(report["domain"]["pressure_bar"]),
            )
    residuals: dict[str, list[float]] = {
        species_id: [] for species_id in external_species
    }
    for row in rows:
        key = (float(row["temperature_K"]), float(row["fo2_log10_bar"]))
        actual = float(cells[key]["pressures_Pa"][row["species"]])
        expected = float(row["vaporock"]["pressure_Pa"])
        residuals[row["species"]].append(abs(math.log10(actual / expected)))
    return {species_id: max(values) for species_id, values in residuals.items()}


def test_t2_vaporock_residual_pins_are_two_way(
    engine_residuals: dict[str, float]
) -> None:
    expected_species = {
        species_id
        for species_id, row in PINS["species"].items()
        if species_id in LIVE_SPECIES and "vaporock" in row["validations"]
    }
    externally_covered_species = set(engine_residuals)
    assert expected_species, "no live rail species declare VapoRock validation pins"
    assert expected_species == externally_covered_species, (
        "VapoRock validation coverage drift: "
        f"pins_without_records={sorted(expected_species - externally_covered_species)}, "
        f"records_without_pins={sorted(externally_covered_species - expected_species)}"
    )
    for species_id in sorted(expected_species):
        row = PINS["species"][species_id]
        observed = engine_residuals[species_id]
        pinned = float(
            row["validations"]["vaporock"]["pinned_residual_dex"]
        )
        _assert_two_way_pin(f"vaporock:{species_id}", observed, pinned)


@pytest.mark.xfail(
    condition=PINS["systematic_sio_oracle"]["status"] == "evaluator_gap",
    strict=True,
    reason=(
        "evaluator_gap: absolute systematic SiO path lacks the condensed SiO2 "
        "reservoir term required by DESIGN-BRIEF section 4"
    ),
)
def test_sio_systematic_oracle_is_not_silently_substituted() -> None:
    oracle = PINS["systematic_sio_oracle"]
    assert oracle["required_failure_message"] == ORACLE_MESSAGE
    assert oracle["status"] in {"evaluator_gap", "ready"}
    if oracle["status"] != "ready":
        pytest.fail(f"delta=unavailable dex; {oracle['missing']} {ORACLE_MESSAGE}")

    oracle_species_id = str(oracle["oracle_species_id"])
    systematic_species_id = str(oracle["systematic_species_id"])
    oracle_family_id = str(oracle["oracle_family_id"])
    assert systematic_species_id not in {"", "None", oracle_species_id}, (
        "systematic SiO path must be independent of the incumbent oracle"
    )
    assert CATALOG.species[oracle_species_id].family_id == oracle_family_id
    assert CATALOG.species[systematic_species_id].family_id != oracle_family_id

    uncertainty_dex = float(oracle["uncertainty_dex"])
    assert uncertainty_dex > 0.0
    for point in oracle["comparison_grid"]:
        temperature_K = float(point["temperature_K"])
        pO2_bar = float(point["pO2_bar"])
        activity = float(point["activity"])
        incumbent_pressure = _evaluate(
            oracle_species_id,
            temperature_K,
            activity=activity,
            pO2_bar=pO2_bar,
        ).pressure_pa
        systematic_pressure = _evaluate(
            systematic_species_id,
            temperature_K,
            activity=activity,
            pO2_bar=pO2_bar,
        ).pressure_pa
        delta_dex = math.log10(systematic_pressure / incumbent_pressure)
        assert abs(delta_dex) <= uncertainty_dex, (
            f"SiO oracle disagreement at T={temperature_K:g} K, "
            f"pO2={pO2_bar:.12g} bar, activity={activity:.12g}: "
            f"delta={delta_dex:+.12g} dex exceeds ±{uncertainty_dex:.12g} dex; "
            f"{ORACLE_MESSAGE}"
        )


def _flux(species_kg_hr: dict[str, float]) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=species_kg_hr)
    flux.update_totals()
    return flux


@pytest.mark.parametrize(
    "requested_rates",
    (
        {"Si": 0.001},
        {"SiO": 0.002},
        {"Si": 0.001, "SiO": 0.002},
        {"SiO": 0.002, "Si": 0.001},
        {"Si": 0.002, "SiO": 0.001},
        {"Si": 1.0e-7, "SiO": 3.0e-7},
        {
            "Si": 1.0e-7,
            "SiO": 2.0e-7,
            "Si2": 3.0e-7,
            "Si3": 4.0e-7,
            "SiO2_gas": 5.0e-7,
        },
    ),
    ids=(
        "si_singleton",
        "sio_singleton",
        "sio_dominant",
        "sio_dominant_reversed_order",
        "si_dominant",
        "scaled_down",
        "mc4b_five_carrier_sum_once",
    ),
)
def test_c6_shared_si_reservoir_conserves_independent_carrier_sum(
    requested_rates: dict[str, float],
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    before_si = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Si"]
    before_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )

    smoothed = sim._apply_analytic_evaporation_depletion(
        _flux(requested_rates)
    )
    assert set(smoothed.species_kg_hr) == set(requested_rates)
    # Independent expectation from applied carrier masses, not ledger deltas.
    # Premise: each Si_xO_y carrier debits x Si atoms from the one shared SiO2
    # parent and releases (2x-y)/2 O2 per carrier molecule. Algebra:
    # debit_Si=sum(n_i*x_i); release_O2=sum(n_i*(2x_i-y_i)/2).
    # Unit check: kg / (kg mol^-1) = mol. Sanity: the MC-4b Si/SiO/Si2/Si3/
    # SiO2(g) case adds every carrier independently, then performs one grouped
    # reservoir debit rather than debiting the full SiO2 parent five times.
    carrier_formulas = {
        species_id: resolve_species_formula(
            species_id, sim.species_formula_registry
        )
        for species_id in smoothed.species_kg_hr
    }
    carrier_mol = {
        species_id: rate_kg_hr
        / carrier_formulas[species_id].molar_mass_kg_per_mol()
        for species_id, rate_kg_hr in smoothed.species_kg_hr.items()
    }
    expected_si_debit_mol = sum(
        carrier_mol[species_id] * carrier_formulas[species_id].elements["Si"]
        for species_id in carrier_mol
    )
    expected_o2_release_mol = sum(
        carrier_mol[species_id]
        * (
            2.0 * carrier_formulas[species_id].elements["Si"]
            - carrier_formulas[species_id].elements.get("O", 0.0)
        )
        / 2.0
        for species_id in carrier_mol
    )

    sim._configure_condensation_operating_conditions(smoothed)
    sim._route_to_condensation(smoothed)
    sim._update_melt_composition(smoothed)

    after_si = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Si"]
    after_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )
    assert before_si - after_si == pytest.approx(expected_si_debit_mol, rel=1.0e-11)
    assert after_o2 - before_o2 == pytest.approx(expected_o2_release_mol, rel=1.0e-11)
    assert abs(sim._make_snapshot().mass_balance_error_pct) <= 5.0e-12


def test_c6_shared_ti_reservoir_conserves_three_carrier_sum_once() -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    requested_rates = {"Ti": 1.0e-7, "TiO": 2.0e-7, "TiO2_gas": 3.0e-7}
    before_ti = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Ti"]
    before_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )

    smoothed = sim._apply_analytic_evaporation_depletion(_flux(requested_rates))
    assert set(smoothed.species_kg_hr) == set(requested_rates)
    # Premise: Ti, TiO, and TiO2(g) each carry one Ti atom; relative to the
    # TiO2 parent they release 1, 1/2, and 0 O2 per carrier mole. Algebra:
    # debit_Ti=sum(n_i), release_O2=n_Ti+n_TiO/2. Unit check: kg divided by
    # kg/mol gives mol. Sanity: three per-carrier transitions must sum to one
    # shared parent debit, never debit the TiO2 reservoir three times.
    carrier_mol = {
        species_id: rate_kg_hr
        / resolve_species_formula(
            species_id, sim.species_formula_registry
        ).molar_mass_kg_per_mol()
        for species_id, rate_kg_hr in smoothed.species_kg_hr.items()
    }
    expected_ti_debit_mol = sum(carrier_mol.values())
    expected_o2_release_mol = carrier_mol["Ti"] + 0.5 * carrier_mol["TiO"]

    sim._configure_condensation_operating_conditions(smoothed)
    sim._route_to_condensation(smoothed)
    sim._update_melt_composition(smoothed)

    after_ti = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Ti"]
    after_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )
    assert before_ti - after_ti == pytest.approx(expected_ti_debit_mol, rel=1.0e-11)
    assert after_o2 - before_o2 == pytest.approx(expected_o2_release_mol, rel=1.0e-11)
    transition_reasons = {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert {
        "evaporate_Ti",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
    } <= transition_reasons
    assert abs(sim._make_snapshot().mass_balance_error_pct) <= 5.0e-12


def test_c6_shared_al_reservoir_conserves_seven_carrier_sum_once() -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    requested_rates = {
        "Al": 1.0e-8,
        "Al2": 2.0e-8,
        "Al2O": 3.0e-8,
        "Al2O2": 4.0e-8,
        "Al2O3_gas": 5.0e-8,
        "AlO": 6.0e-8,
        "AlO2": 7.0e-8,
    }
    before_al = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Al"]
    before_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )

    releasing_rates = {
        species_id: rate
        for species_id, rate in requested_rates.items()
        if species_id != "AlO2"
    }
    smoothed_releasing = sim._apply_analytic_evaporation_depletion(
        _flux(releasing_rates)
    )
    assert set(smoothed_releasing.species_kg_hr) == set(releasing_rates)
    sim._configure_condensation_operating_conditions(smoothed_releasing)
    sim._route_to_condensation(smoothed_releasing)
    sim._update_melt_composition(smoothed_releasing)

    # AlO2 consumes O2, so evaluate it after the co-carriers have populated
    # the physical overhead O2 reservoir rather than inventing external oxygen.
    smoothed_oxidizing = sim._apply_analytic_evaporation_depletion(
        _flux({"AlO2": requested_rates["AlO2"]})
    )
    assert set(smoothed_oxidizing.species_kg_hr) == {"AlO2"}
    sim._configure_condensation_operating_conditions(smoothed_oxidizing)
    sim._route_to_condensation(smoothed_oxidizing)
    sim._update_melt_composition(smoothed_oxidizing)
    applied_rates = dict(smoothed_releasing.species_kg_hr)
    applied_rates.update(smoothed_oxidizing.species_kg_hr)
    # Premise: all seven carriers draw from one Al2O3 reservoir. Al atoms per
    # carrier are 1,2,2,2,2,1,1; O2 released per carrier is
    # 3/4,3/2,1,1/2,0,1/4,-1/4. Algebra: sum each applied carrier mole times
    # its coefficient exactly once. Unit check: kg/(kg/mol)=mol. Sanity:
    # AlO2 consumes O2 while every other oxidized carrier releases no more
    # oxygen than its shared Al2O3 parent contains.
    carrier_mol = {
        species_id: rate_kg_hr
        / resolve_species_formula(
            species_id, sim.species_formula_registry
        ).molar_mass_kg_per_mol()
        for species_id, rate_kg_hr in applied_rates.items()
    }
    al_atoms_per_carrier = {
        "Al": 1.0,
        "Al2": 2.0,
        "Al2O": 2.0,
        "Al2O2": 2.0,
        "Al2O3_gas": 2.0,
        "AlO": 1.0,
        "AlO2": 1.0,
    }
    o2_per_carrier = {
        "Al": 0.75,
        "Al2": 1.5,
        "Al2O": 1.0,
        "Al2O2": 0.5,
        "Al2O3_gas": 0.0,
        "AlO": 0.25,
        "AlO2": -0.25,
    }
    expected_al_debit_mol = sum(
        carrier_mol[species_id] * al_atoms_per_carrier[species_id]
        for species_id in carrier_mol
    )
    expected_o2_release_mol = sum(
        carrier_mol[species_id] * o2_per_carrier[species_id]
        for species_id in carrier_mol
    )

    after_al = sim.atom_ledger.atom_moles_by_account("process.cleaned_melt")["Al"]
    after_o2 = sum(
        sim.atom_ledger.mol_by_species(account).get("O2", 0.0)
        for account in ("process.overhead_gas", "reservoir.fo2_buffer")
    )
    assert before_al - after_al == pytest.approx(expected_al_debit_mol, rel=1.0e-11)
    assert after_o2 - before_o2 == pytest.approx(expected_o2_release_mol, rel=1.0e-11)
    transition_reasons = {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert {f"evaporate_{species_id}" for species_id in requested_rates} <= (
        transition_reasons
    )
    assert abs(sim._make_snapshot().mass_balance_error_pct) <= 5.0e-12
