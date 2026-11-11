
"""Inventory of production caches on paths that can return a flagged value.

A disclosure-bearing row is stored with its law disclosure and read only
through ``accept_stored_memo``. Every other row says why it cannot carry one.
``scan_cache_sites`` is the list the meta-test compares to ``CACHE_SITES``.
A new lru_cache or memo dict in simulator/ or engines/ fails that test until
this table names it.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

# Sites whose cached value can be a flagged vapour-law result.
DISCLOSURE_CARRYING = frozenset({
    "store:simulator/reduced_real_determinism.py:_entries",
    "class:simulator/reduced_real_determinism.py:PT1PersistentEquilibriumStore",
    "class:simulator/optimize/results_store.py:ResultStore",
    "fn:simulator/optimize/study.py:_scored_result_from_journal_payload",
    "store:simulator/core.py:_freeze_gate_liquid_fraction_cache",
    "store:simulator/core.py:_freeze_gate_held_curve_record",
    "store:simulator/core.py:_freeze_gate_liquid_fraction_curve_memo",
    "store:simulator/evaporation.py:_freeze_gate_liquid_fraction_cache",
    "store:simulator/evaporation.py:_freeze_gate_held_curve_record",
    "attr:simulator/evaporation.py:_freeze_gate_shared_curve_cache",
    "store:simulator/battery/residue.py:pressure_cache",
    "store:simulator/battery/residue.py:pressure_state_cache",
    "store:simulator/battery/score.py:residue_prediction_cache",
    "store:simulator/diagnostic_helpers/extract_reproduction.py:_MELT_PSAT_CACHE",
})


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _store_name(name: str) -> bool:
    """True for a memo dict, including an upper-case ``_CACHE`` / ``_MEMO`` name.

    The production melt-pressure store is ``_MELT_PSAT_CACHE``. A
    case-sensitive ``_cache`` suffix hid it from the inventory and from the
    meta-test.
    """

    lowered = name.lower()
    return (
        lowered.endswith("_cache")
        or lowered.endswith("_memo")
        or lowered.endswith("_entries")
        or name in {
            "_entries",
            "pressure_cache",
            "pressure_state_cache",
            "residue_prediction_cache",
            "prefix_replay_cache",
            "physics_bucket_entries",
        }
        or "held_curve" in lowered
    )


def _is_store_value(value: ast.AST | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (ast.Dict, ast.List, ast.Set)):
        return True
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.Call):
        func = value.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        return called in {"dict", "list", "set"}
    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _production_python_files(root: Path) -> list[Path]:
    listed = subprocess.check_output(
        ["git", "ls-files", "simulator", "engines"],
        cwd=root,
        text=True,
    )
    return [root / line for line in listed.splitlines() if line.endswith(".py")]


def scan_cache_sites(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every lru_cache and memo/cache store under simulator/ and engines/."""

    repo = root or _repo_root()
    found: dict[str, dict[str, Any]] = {}

    def add(site_id: str, rel: str, line: int, kind: str, name: str) -> None:
        found.setdefault(
            site_id,
            {"path": rel, "line": line, "kind": kind, "name": name},
        )

    for path in _production_python_files(repo):
        rel = str(path.relative_to(repo))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if _decorator_name(decorator) in {"lru_cache", "cache"}:
                        add(f"lru:{rel}:{node.name}", rel, node.lineno, "lru", node.name)
                if node.name == "_scored_result_from_journal_payload":
                    add(f"fn:{rel}:{node.name}", rel, node.lineno, "fn", node.name)
            elif isinstance(node, ast.ClassDef) and node.name in {
                "PT1PersistentEquilibriumStore",
                "ResultStore",
            }:
                add(f"class:{rel}:{node.name}", rel, node.lineno, "class", node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not _is_store_value(node.value):
                    continue
                for target in targets:
                    label = None
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        label = target.attr
                    elif isinstance(target, ast.Name):
                        label = target.id
                    if label and _store_name(label):
                        add(f"store:{rel}:{label}", rel, node.lineno, "store", label)
            elif (
                isinstance(node, ast.Constant)
                and node.value == "_freeze_gate_shared_curve_cache"
                and not rel.endswith("disclosure_cache_inventory.py")
            ):
                add(
                    f"attr:{rel}:_freeze_gate_shared_curve_cache",
                    rel,
                    node.lineno,
                    "attr",
                    node.value,
                )
    return found

CACHE_SITES: dict[str, dict[str, Any]] = {
    'attr:simulator/evaporation.py:_freeze_gate_shared_curve_cache': {
        "path": 'simulator/evaporation.py',
        "line": 2418,
        "kind": 'attr',
        "name": '_freeze_gate_shared_curve_cache',
        "carries_disclosure": True,
        "how": 'Process-wide shared curve. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'class:simulator/optimize/results_store.py:ResultStore': {
        "path": 'simulator/optimize/results_store.py',
        "line": 187,
        "kind": 'class',
        "name": 'ResultStore',
        "carries_disclosure": True,
        "how": 'lookup, fetch, query, and best decode through _row_to_scored_result, which calls accept_stored_memo. A missing disclosure column is a miss.',
    },
    'class:simulator/reduced_real_determinism.py:PT1PersistentEquilibriumStore': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 1645,
        "kind": 'class',
        "name": 'PT1PersistentEquilibriumStore',
        "carries_disclosure": True,
        "how": 'SQLite equilibrium and gate rows. A hit is copied into _entries and returned through _accept_cached_hit, including interpolation.',
    },
    'fn:simulator/optimize/study.py:_scored_result_from_journal_payload': {
        "path": 'simulator/optimize/study.py',
        "line": 2254,
        "kind": 'fn',
        "name": '_scored_result_from_journal_payload',
        "carries_disclosure": True,
        "how": 'Journal replay calls accept_stored_memo on the payload. A missing or unreadable field raises rather than ranking an unflagged row.',
    },
    'lru:simulator/battery/generators/compilation_thermo_fits.py:_generator_commit': {
        "path": 'simulator/battery/generators/compilation_thermo_fits.py',
        "line": 1803,
        "kind": 'lru',
        "name": '_generator_commit',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_cached_table_property_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3158,
        "kind": 'lru',
        "name": '_cached_table_property_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_element_reference_schedule': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 1095,
        "kind": 'lru',
        "name": '_element_reference_schedule',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_element_reference_state': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 1158,
        "kind": 'lru',
        "name": '_element_reference_state',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_enthalpy_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2861,
        "kind": 'lru',
        "name": '_fusion_enthalpy_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_entropy_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2893,
        "kind": 'lru',
        "name": '_fusion_entropy_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_missing_gibbs_temperatures': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2994,
        "kind": 'lru',
        "name": '_fusion_missing_gibbs_temperatures',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_table_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2824,
        "kind": 'lru',
        "name": '_fusion_table_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_gas_table_ids': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3125,
        "kind": 'lru',
        "name": '_janaf_gas_table_ids',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_pure_solid_table_index': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2657,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_pure_solid_table_temperature_range': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2679,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_temperature_range',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_formula_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1195,
        "kind": 'lru',
        "name": '_formula_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_formula_weight_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1218,
        "kind": 'lru',
        "name": '_formula_weight_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_sibling_formula_index': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1237,
        "kind": 'lru',
        "name": '_sibling_formula_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_table2_elements': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 964,
        "kind": 'lru',
        "name": '_table2_elements',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_corrections_by_token': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 429,
        "kind": 'lru',
        "name": '_corrections_by_token',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_formula_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 988,
        "kind": 'lru',
        "name": '_formula_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_formula_weight_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 1014,
        "kind": 'lru',
        "name": '_formula_weight_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_table2_elements': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 1060,
        "kind": 'lru',
        "name": '_table2_elements',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/migrate.py:_mass_percent_component_key': {
        "path": 'simulator/battery/migrate.py',
        "line": 894,
        "kind": 'lru',
        "name": '_mass_percent_component_key',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/migrate.py:_volatile_closure_component': {
        "path": 'simulator/battery/migrate.py',
        "line": 4562,
        "kind": 'lru',
        "name": '_volatile_closure_component',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_b1259_tridymite_cristobalite_offset_rows': {
        "path": 'simulator/battery/score.py',
        "line": 2560,
        "kind": 'lru',
        "name": '_b1259_tridymite_cristobalite_offset_rows',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_e15_binding_log10_k': {
        "path": 'simulator/battery/score.py',
        "line": 15243,
        "kind": 'lru',
        "name": '_e15_binding_log10_k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_iron_saturated_feo_calibration': {
        "path": 'simulator/battery/score.py',
        "line": 2902,
        "kind": 'lru',
        "name": '_iron_saturated_feo_calibration',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_janaf_pure_solid_table_index': {
        "path": 'simulator/battery/score.py',
        "line": 2500,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_janaf_pure_solid_table_temperature_range': {
        "path": 'simulator/battery/score.py',
        "line": 2533,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_temperature_range',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/campaigns.py:_derived_override_field_names': {
        "path": 'simulator/campaigns.py',
        "line": 690,
        "kind": 'lru',
        "name": '_derived_override_field_names',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/campaigns.py:_species_molar_mass_kg_per_mol': {
        "path": 'simulator/campaigns.py',
        "line": 210,
        "kind": 'lru',
        "name": '_species_molar_mass_kg_per_mol',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/chemistry/langmuir_knudsen.py:_vapor_pressure_data': {
        "path": 'simulator/chemistry/langmuir_knudsen.py',
        "line": 299,
        "kind": 'lru',
        "name": '_vapor_pressure_data',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/chemistry/melt_activity.py:_na2o_janaf_liquid_source_data': {
        "path": 'simulator/chemistry/melt_activity.py',
        "line": 712,
        "kind": 'lru',
        "name": '_na2o_janaf_liquid_source_data',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/chemistry/offgas_fo2.py:_load_cea_polynomials_from_text': {
        "path": 'simulator/chemistry/offgas_fo2.py',
        "line": 251,
        "kind": 'lru',
        "name": '_load_cea_polynomials_from_text',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/chemistry/phase_context.py:_cached_grind_cache_lookup': {
        "path": 'simulator/chemistry/phase_context.py',
        "line": 210,
        "kind": 'lru',
        "name": '_cached_grind_cache_lookup',
        "carries_disclosure": False,
        "how": 'Grind-cache file lookup or its epoch stamp. The row is a phase assemblage, not a vapour-law disclosure.',
    },
    'lru:simulator/chemistry/phase_context.py:_max_engine_epoch': {
        "path": 'simulator/chemistry/phase_context.py',
        "line": 349,
        "kind": 'lru',
        "name": '_max_engine_epoch',
        "carries_disclosure": False,
        "how": 'Grind-cache file lookup or its epoch stamp. The row is a phase assemblage, not a vapour-law disclosure.',
    },
    'lru:simulator/config.py:_parse_required_yaml': {
        "path": 'simulator/config.py',
        "line": 187,
        "kind": 'lru',
        "name": '_parse_required_yaml',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/gibbs_battery.py:load_cea_extract': {
        "path": 'simulator/diagnostic_helpers/gibbs_battery.py',
        "line": 621,
        "kind": 'lru',
        "name": 'load_cea_extract',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:_vapor_pressures_payload': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 996,
        "kind": 'lru',
        "name": '_vapor_pressures_payload',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:cea_by_formula': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 548,
        "kind": 'lru',
        "name": 'cea_by_formula',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:cea_index': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 513,
        "kind": 'lru',
        "name": 'cea_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:vapour_rail_catalog': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 1001,
        "kind": 'lru',
        "name": 'vapour_rail_catalog',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:vapour_rail_legacy_view': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 1010,
        "kind": 'lru',
        "name": 'vapour_rail_legacy_view',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/diagnostic_helpers/timeseries_validation_lake.py:_vapor_pressure_data': {
        "path": 'simulator/diagnostic_helpers/timeseries_validation_lake.py',
        "line": 575,
        "kind": 'lru',
        "name": '_vapor_pressure_data',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/evaporation_classes.py:_species_formula_registry': {
        "path": 'simulator/evaporation_classes.py',
        "line": 47,
        "kind": 'lru',
        "name": '_species_formula_registry',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/furnace_materials.py:load_furnace_materials': {
        "path": 'simulator/furnace_materials.py',
        "line": 77,
        "kind": 'lru',
        "name": 'load_furnace_materials',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/material_densities.py:material_density_data': {
        "path": 'simulator/material_densities.py',
        "line": 21,
        "kind": 'lru',
        "name": 'material_density_data',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/melt_backend/openimcc_bridge.py:_load_pack': {
        "path": 'simulator/melt_backend/openimcc_bridge.py',
        "line": 860,
        "kind": 'lru',
        "name": '_load_pack',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/melt_backend/pure_phase_janaf_score.py:bands_for_table_id': {
        "path": 'simulator/melt_backend/pure_phase_janaf_score.py',
        "line": 306,
        "kind": 'lru',
        "name": 'bands_for_table_id',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/optimize/evaluate.py:_resolved_engine_identity_for_eval': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 2845,
        "kind": 'lru',
        "name": '_resolved_engine_identity_for_eval',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/optimize/evaluate.py:_vapor_pressure_provider_code_fingerprint': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 3596,
        "kind": 'lru',
        "name": '_vapor_pressure_provider_code_fingerprint',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/optimize/pool.py:_default_feedstocks': {
        "path": 'simulator/optimize/pool.py',
        "line": 981,
        "kind": 'lru',
        "name": '_default_feedstocks',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/recipe.py:_cached_default_allowlist': {
        "path": 'simulator/recipe.py',
        "line": 2073,
        "kind": 'lru',
        "name": '_cached_default_allowlist',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/recipe.py:_default_setpoints': {
        "path": 'simulator/recipe.py',
        "line": 2760,
        "kind": 'lru',
        "name": '_default_setpoints',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/reduced_real_determinism.py:_source_module_digest': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 4888,
        "kind": 'lru',
        "name": '_source_module_digest',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/reference_data/pankratz_1984_usbm_b677_loader.py:load_manifest': {
        "path": 'simulator/reference_data/pankratz_1984_usbm_b677_loader.py',
        "line": 26,
        "kind": 'lru',
        "name": 'load_manifest',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/refractory_vaporization.py:_thermo_data': {
        "path": 'simulator/refractory_vaporization.py',
        "line": 101,
        "kind": 'lru',
        "name": '_thermo_data',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/runner/__init__.py:_resolve_kernel_commit_sha': {
        "path": 'simulator/runner/__init__.py',
        "line": 763,
        "kind": 'lru',
        "name": '_resolve_kernel_commit_sha',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/vapour_rail/melt_activity_resolver.py:load': {
        "path": 'simulator/vapour_rail/melt_activity_resolver.py',
        "line": 686,
        "kind": 'lru',
        "name": 'load',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/vapour_rail/trace_acquisition.py:load_trace_acquisition': {
        "path": 'simulator/vapour_rail/trace_acquisition.py',
        "line": 52,
        "kind": 'lru',
        "name": 'load_trace_acquisition',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/volatile_properties.py:_catalog': {
        "path": 'simulator/volatile_properties.py',
        "line": 877,
        "kind": 'lru',
        "name": '_catalog',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/volatile_properties.py:_default_registry': {
        "path": 'simulator/volatile_properties.py',
        "line": 2652,
        "kind": 'lru',
        "name": '_default_registry',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/wall_advisor.py:_load_wall_materials_cached': {
        "path": 'simulator/wall_advisor.py',
        "line": 258,
        "kind": 'lru',
        "name": '_load_wall_materials_cached',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'store:engines/alphamelts/thermoengine.py:_health_cache': {
        "path": 'engines/alphamelts/thermoengine.py',
        "line": 1078,
        "kind": 'store',
        "name": '_health_cache',
        "carries_disclosure": False,
        "how": 'AlphaMELTS process-health flag and TTL. Not a vapour-law result.',
    },
    'store:engines/builtin/foulant_disposition.py:_VAPOR_PAYLOAD_CACHE': {
        "path": 'engines/builtin/foulant_disposition.py',
        "line": 162,
        "kind": 'store',
        "name": '_VAPOR_PAYLOAD_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed vapor_pressures.yaml document. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/battery/generators/janaf.py:formation_basis_cache': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2171,
        "kind": 'store',
        "name": 'formation_basis_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/battery/migrate.py:_DEST_CACHE': {
        "path": 'simulator/battery/migrate.py',
        "line": 15311,
        "kind": 'store',
        "name": '_DEST_CACHE',
        "carries_disclosure": False,
        "how": 'Lab-field destination lookup. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:_VOCAB_CACHE': {
        "path": 'simulator/battery/migrate.py',
        "line": 15155,
        "kind": 'store',
        "name": '_VOCAB_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed lab-parameter vocabulary. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/battery/migrate.py:equality_fallback_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 27322,
        "kind": 'store',
        "name": 'equality_fallback_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:seen_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 27321,
        "kind": 'store',
        "name": 'seen_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:unique_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 27320,
        "kind": 'store',
        "name": 'unique_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/residue.py:pressure_cache': {
        "path": 'simulator/battery/residue.py',
        "line": 3615,
        "kind": 'store',
        "name": 'pressure_cache',
        "carries_disclosure": True,
        "how": 'OpenIMCC Sossi pressures. _cached_pressure_hit calls accept_stored_memo. No active registry means the row is not stored.',
    },
    'store:simulator/battery/residue.py:pressure_state_cache': {
        "path": 'simulator/battery/residue.py',
        "line": 1433,
        "kind": 'store',
        "name": 'pressure_state_cache',
        "carries_disclosure": True,
        "how": 'Shared residue pressure model. _cached_pressure_hit calls accept_stored_memo. No active registry means the row is not stored.',
    },
    'store:simulator/battery/score.py:_row_metadata_cache': {
        "path": 'simulator/battery/score.py',
        "line": 19856,
        "kind": 'store',
        "name": '_row_metadata_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/battery/score.py:residue_prediction_cache': {
        "path": 'simulator/battery/score.py',
        "line": 14124,
        "kind": 'store',
        "name": 'residue_prediction_cache',
        "carries_disclosure": True,
        "how": 'Sossi cohort rows carry law_disclosures. _cached_sossi_prediction is the only read of those rows and calls accept_stored_memo. A Sossi row with no field is dropped and recomputed. Hashimoto and Knudsen entries in this dict are result objects with no law_disclosures field.',
    },
    'store:simulator/battery/validate.py:_cache': {
        "path": 'simulator/battery/validate.py',
        "line": 1948,
        "kind": 'store',
        "name": '_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/chemistry/offgas_fo2.py:_K_cache': {
        "path": 'simulator/chemistry/offgas_fo2.py',
        "line": 915,
        "kind": 'store',
        "name": '_K_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/core.py:_freeze_gate_held_curve_record': {
        "path": 'simulator/core.py',
        "line": 1393,
        "kind": 'store',
        "name": '_freeze_gate_held_curve_record',
        "carries_disclosure": True,
        "how": 'Held-curve slot owned by the simulator. The freeze-gate reader reads it through accept_stored_memo.',
    },
    'store:simulator/core.py:_freeze_gate_liquid_fraction_cache': {
        "path": 'simulator/core.py',
        "line": 1391,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_cache',
        "carries_disclosure": True,
        "how": 'Core liquidus local slot. _melt_redox_liquidus_gate_record reads it through accept_stored_memo.',
    },
    'store:simulator/core.py:_freeze_gate_liquid_fraction_curve_memo': {
        "path": 'simulator/core.py',
        "line": 1394,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_curve_memo',
        "carries_disclosure": True,
        "how": 'Core curve memo. A hit goes through accept_stored_memo and the rewrite keeps the measured field.',
    },
    'store:simulator/core.py:_memo': {
        "path": 'simulator/core.py',
        "line": 151,
        "kind": 'store',
        "name": '_memo',
        "carries_disclosure": False,
        "how": 'Deepcopy cycle map for refusal state. It never stores a vapour pressure.',
    },
    'store:simulator/core.py:published_cache': {
        "path": 'simulator/core.py',
        "line": 5302,
        "kind": 'store',
        "name": 'published_cache',
        "carries_disclosure": False,
        "how": 'Local name for the list accept_stored_memo just returned. Not a store.',
    },
    'store:simulator/core.py:rates_cache': {
        "path": 'simulator/core.py',
        "line": 7508,
        "kind": 'store',
        "name": 'rates_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_LEGACY_VIEW_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1916,
        "kind": 'store',
        "name": '_LEGACY_VIEW_CACHE',
        "carries_disclosure": False,
        "how": 'Schema-v1 projection of the vapour catalog. Dispatch still evaluates the law.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_MELT_PSAT_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1603,
        "kind": 'store',
        "name": '_MELT_PSAT_CACHE',
        "carries_disclosure": True,
        "how": 'Melt provider surface. _read_melt_psat_cache is the only read and calls accept_stored_memo. A row with no law_disclosures is a miss.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_VP_PROVIDER_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1598,
        "kind": 'store',
        "name": '_VP_PROVIDER_CACHE',
        "carries_disclosure": False,
        "how": 'Compiled provider object. Dispatch still evaluates the law, so the cached object is not a flagged pressure.',
    },
    'store:simulator/diagnostic_helpers/kems.py:surface_cache': {
        "path": 'simulator/diagnostic_helpers/kems.py',
        "line": 653,
        "kind": 'store',
        "name": 'surface_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/engine_binding_admission.py:grouped_entries': {
        "path": 'simulator/engine_binding_admission.py',
        "line": 1536,
        "kind": 'store',
        "name": 'grouped_entries',
        "carries_disclosure": False,
        "how": 'Binding-admission grouping of receipts. Not a vapour-law result.',
    },
    'store:simulator/engine_binding_admission.py:receipt_entries': {
        "path": 'simulator/engine_binding_admission.py',
        "line": 1548,
        "kind": 'store',
        "name": 'receipt_entries',
        "carries_disclosure": False,
        "how": 'Binding-admission grouping of receipts. Not a vapour-law result.',
    },
    'store:simulator/evaporation.py:_freeze_gate_held_curve_record': {
        "path": 'simulator/evaporation.py',
        "line": 2223,
        "kind": 'store',
        "name": '_freeze_gate_held_curve_record',
        "carries_disclosure": True,
        "how": 'Same-hour held curve. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'store:simulator/evaporation.py:_freeze_gate_liquid_fraction_cache': {
        "path": 'simulator/evaporation.py',
        "line": 2647,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_cache',
        "carries_disclosure": True,
        "how": 'Evaporation local slot. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'store:simulator/evaporation.py:computing_cache': {
        "path": 'simulator/evaporation.py',
        "line": 2495,
        "kind": 'store',
        "name": 'computing_cache',
        "carries_disclosure": False,
        "how": 'In-progress freeze-gate placeholder. It is replaced before a hit is published.',
    },
    'store:simulator/liner_life.py:monotonicity_evidence_cache': {
        "path": 'simulator/liner_life.py',
        "line": 736,
        "kind": 'store',
        "name": 'monotonicity_evidence_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/melt_backend/vaporock.py:_SESSION_BACKEND_CACHE': {
        "path": 'simulator/melt_backend/vaporock.py',
        "line": 1270,
        "kind": 'store',
        "name": '_SESSION_BACKEND_CACHE',
        "carries_disclosure": False,
        "how": 'Warm VapoRock worker. Equilibrate results are not cached; each call still runs.',
    },
    'store:simulator/melt_backend/vaporock.py:_VAPOROCK_RUNTIME_AVAILABLE_CACHE': {
        "path": 'simulator/melt_backend/vaporock.py',
        "line": 1207,
        "kind": 'store',
        "name": '_VAPOROCK_RUNTIME_AVAILABLE_CACHE',
        "carries_disclosure": False,
        "how": 'Boolean availability flag. Not a vapour-law result.',
    },
    'store:simulator/optimize/evaluate.py:_VAPOROCK_AVAILABLE_CACHE': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 3569,
        "kind": 'store',
        "name": '_VAPOROCK_AVAILABLE_CACHE',
        "carries_disclosure": False,
        "how": 'Boolean availability flag. Not a vapour-law result.',
    },
    'store:simulator/optimize/evaluate.py:per_hour_cache': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 6320,
        "kind": 'store',
        "name": 'per_hour_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/optimize/study.py:prefix_replay_cache': {
        "path": 'simulator/optimize/study.py',
        "line": 712,
        "kind": 'store',
        "name": 'prefix_replay_cache',
        "carries_disclosure": False,
        "how": 'Equality copy of a staged prefix. The value returned to the study is ResultStore.lookup, which re-registers.',
    },
    'store:simulator/reduced_real_determinism.py:_entries': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 438,
        "kind": 'store',
        "name": '_entries',
        "carries_disclosure": True,
        "how": 'PT0 memory rows. The only read is _accept_cached_hit, which calls accept_stored_memo. The dict is _entries.',
    },
    'store:simulator/reduced_real_determinism.py:physics_bucket_entries': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 439,
        "kind": 'store',
        "name": 'physics_bucket_entries',
        "carries_disclosure": False,
        "how": 'Hash index from a physics bucket to a PT0 key. The payload and disclosure live on _entries.',
    },
    'store:simulator/runner/__init__.py:per_hour_summary_cache': {
        "path": 'simulator/runner/__init__.py',
        "line": 5994,
        "kind": 'store',
        "name": 'per_hour_summary_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/runner/__init__.py:reduced_real_cache': {
        "path": 'simulator/runner/__init__.py',
        "line": 1037,
        "kind": 'store',
        "name": 'reduced_real_cache',
        "carries_disclosure": False,
        "how": 'Configuration handle for the determinism store, not a cached physics row.',
    },
    'store:simulator/session.py:reduced_real_cache': {
        "path": 'simulator/session.py',
        "line": 215,
        "kind": 'store',
        "name": 'reduced_real_cache',
        "carries_disclosure": False,
        "how": 'Configuration handle for the determinism store, not a cached physics row.',
    },
    'store:simulator/subsolidus_bakeout.py:_CATALOG_DOMAIN_CACHE': {
        "path": 'simulator/subsolidus_bakeout.py',
        "line": 54,
        "kind": 'store',
        "name": '_CATALOG_DOMAIN_CACHE',
        "carries_disclosure": False,
        "how": 'S2/SO2 temperature-domain bounds from the species catalog. Not an evaluated pressure.',
    },
    'store:simulator/vapour_rail/catalog.py:_COMPILE_CACHE': {
        "path": 'simulator/vapour_rail/catalog.py',
        "line": 411,
        "kind": 'store',
        "name": '_COMPILE_CACHE',
        "carries_disclosure": False,
        "how": 'Compiled catalog object. A hit does not skip evaluate(), which is where a flagged law registers.',
    },
    'store:simulator/vapour_rail/catalog.py:_THERMO_EXTRACT_PARSE_CACHE': {
        "path": 'simulator/vapour_rail/catalog.py',
        "line": 97,
        "kind": 'store',
        "name": '_THERMO_EXTRACT_PARSE_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed thermo extract. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/vapour_rail/catalog.py:_THERMO_OBSERVATION_CACHE': {
        "path": 'simulator/vapour_rail/catalog.py',
        "line": 100,
        "kind": 'store',
        "name": '_THERMO_OBSERVATION_CACHE',
        "carries_disclosure": False,
        "how": 'Thermo observation rows used at compile time. Not an evaluated pressure.',
    },
    'store:simulator/vapour_rail/u0_manifest.py:_U0_MANIFEST_CACHE': {
        "path": 'simulator/vapour_rail/u0_manifest.py',
        "line": 1025,
        "kind": 'store',
        "name": '_U0_MANIFEST_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed U0 manifest fixture. A pure loader. It never evaluates a vapour law.',
    },
}

