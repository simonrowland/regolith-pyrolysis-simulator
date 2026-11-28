
"""Inventory of production caches on paths that can return a flagged value.

A disclosure-bearing row is stored with its law disclosure and read only
through ``accept_stored_memo``. Every other row says why it cannot carry one.
``scan_cache_sites`` is the list the meta-test compares to ``CACHE_SITES``.
A new lru_cache or memo dict in simulator/ or engines/ fails that test until
this table names it.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any

# The controller's cache sweep. ``(`` is escaped so ``ResultStore(`` is literal.
# ``cache = {}`` allows the spaces the production assignments use.
CONTROLLER_CACHE_REGEX = re.compile(
    r"lru_cache|functools\.cache|@cache|_CACHE|_cache\s*[:=]|_memo\s*[:=]|_MEMO|"
    r"cache\s*=\s*\{\}|cache:\s*dict|prediction_cache|held_curve|frozen_curve|"
    r"ResultStore\("
)

# Sites whose cached value can be a flagged vapour-law result.
DISCLOSURE_CARRYING = frozenset({
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
    "store:simulator/battery/score_run.py:residue_prediction_cache",
    "store:simulator/battery/score.py:residue_prediction_cache",
    "store:simulator/diagnostic_helpers/extract_reproduction.py:_MELT_PSAT_CACHE",
    "store:simulator/engine_cache.py:_MEMORY_CACHE",
    "sqlite:simulator/engine_cache.py:engine_outputs",
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
        if rel == "simulator/engine_cache.py":
            for node in tree.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    value = node.value
                    if (
                        node.target.id == "_MEMORY_CACHE"
                        and isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "OrderedDict"
                    ):
                        add(
                            f"store:{rel}:_MEMORY_CACHE",
                            rel,
                            node.lineno,
                            "store",
                            "_MEMORY_CACHE",
                        )
                if isinstance(node, ast.FunctionDef) and node.name == "_initialize_cache":
                    source = ast.get_source_segment(
                        path.read_text(encoding="utf-8"), node
                    ) or ""
                    if (
                        "CREATE TABLE IF NOT EXISTS {_TABLE}" in source
                        or (
                            "_CACHE_TABLE_DEFINITIONS" in source
                            and "CREATE TABLE IF NOT EXISTS {table}" in source
                        )
                    ):
                        add(
                            f"sqlite:{rel}:engine_outputs",
                            rel,
                            node.lineno,
                            "sqlite",
                            "engine_outputs",
                        )
        if rel == "simulator/battery/score.py":
            for node in tree.body:
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name == "_residue_prediction_cache_entry"
                ):
                    # The scorer owns the disclosure-aware cache access path;
                    # score_run owns the mapping instance passed into it.
                    add(
                        f"store:{rel}:residue_prediction_cache",
                        rel,
                        node.lineno,
                        "store",
                        "residue_prediction_cache",
                    )
    return found

CACHE_SITES: dict[str, dict[str, Any]] = {
    'lru:simulator/accounting/formulas.py:tokenizations': {
        "path": 'simulator/accounting/formulas.py',
        "line": 483,
        "kind": 'lru',
        "name": 'tokenizations',
        "carries_disclosure": False,
        "how": 'Per-formula recursive memo of element tokenizations used to detect ambiguous element casing. It is local to one normalization call and does not cache evaluated chemistry results.',
    },
    'lru:simulator/engine_cache.py:_string_bytes': {
        "path": 'simulator/engine_cache.py',
        "line": 133,
        "kind": 'lru',
        "name": '_string_bytes',
        "carries_disclosure": False,
        "how": 'Memoized HOT key string encoding. It returns bytes, not an evaluated vapour-law result.',
    },
    'lru:simulator/engine_cache.py:_key_float_bytes': {
        "path": 'simulator/engine_cache.py',
        "line": 143,
        "kind": 'lru',
        "name": '_key_float_bytes',
        "carries_disclosure": False,
        "how": 'Memoized HOT key float encoding. It returns bytes, not an evaluated vapour-law result.',
    },
    'lru:simulator/engine_cache.py:runtime_identity': {
        "path": 'simulator/engine_cache.py',
        "line": 1666,
        "kind": 'lru',
        "name": 'runtime_identity',
        "carries_disclosure": False,
        "how": 'Memoizes diagnostic engine identity metadata. It does not cache chemistry outputs or skip a law execution.',
    },
    'lru:simulator/policy/default_policy.py:_load_policy_table': {
        "path": 'simulator/policy/default_policy.py',
        "line": 69,
        "kind": 'lru',
        "name": '_load_policy_table',
        "carries_disclosure": False,
        "how": 'LRU cache (maxsize=4) keyed by Path. Policy writes explicitly clear it; otherwise entries leave on LRU eviction.',
    },
    'lru:simulator/reference_data/janaf.py:_formation_enthalpy_rows_kj_per_mol': {
        "path": 'simulator/reference_data/janaf.py',
        "line": 1498,
        "kind": 'lru',
        "name": '_formation_enthalpy_rows_kj_per_mol',
        "carries_disclosure": False,
        "how": 'Unbounded LRU cache keyed by table_id. It has no explicit invalidation and retains immutable parsed rows for the process lifetime.',
    },
    'store:simulator/battery/score_run.py:residue_prediction_cache': {
        "path": 'simulator/battery/score_run.py',
        "line": 508,
        "kind": 'store',
        "name": 'residue_prediction_cache',
        "carries_disclosure": True,
        "how": 'Per score-run residue prediction cache keyed by source, engine, and selected rows. Scorer readers accept stored disclosures before returning predictions.',
    },
    'store:simulator/battery/score.py:residue_prediction_cache': {
        "path": 'simulator/battery/score.py',
        "line": 17316,
        "kind": 'store',
        "name": 'residue_prediction_cache',
        "carries_disclosure": True,
        "how": 'Scorer cache access path over the mapping owned by score_run. Hashimoto, Knudsen, and Sossi cache readers call accept_stored_memo before returning rows.',
    },
    'store:simulator/engine_cache.py:_MEMORY_CACHE': {
        "path": 'simulator/engine_cache.py',
        "line": 769,
        "kind": 'store',
        "name": '_MEMORY_CACHE',
        "carries_disclosure": True,
        "how": 'Process LRU keyed by cache path, engine/version/request digest, and readonly mode. Values for every disclosure-carrying engine include measured or unknown law_disclosures, replayed by accept_stored_memo on hits.',
    },
    'sqlite:simulator/engine_cache.py:engine_outputs': {
        "path": 'simulator/engine_cache.py',
        "line": 1865,
        "kind": 'sqlite',
        "name": 'engine_outputs',
        "carries_disclosure": True,
        "how": 'Persistent HOT SQLite rows keyed by engine/version/request digest. Values for every disclosure-carrying engine include measured or unknown law_disclosures, replayed by accept_stored_memo on hits.',
    },
    'attr:simulator/evaporation.py:_freeze_gate_shared_curve_cache': {
        "path": 'simulator/evaporation.py',
        "line": 3843,
        "kind": 'attr',
        "name": '_freeze_gate_shared_curve_cache',
        "carries_disclosure": True,
        "how": 'Process-wide shared curve. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'class:simulator/optimize/results_store.py:ResultStore': {
        "path": 'simulator/optimize/results_store.py',
        "line": 205,
        "kind": 'class',
        "name": 'ResultStore',
        "carries_disclosure": True,
        "how": 'lookup, fetch, query, and best decode through _row_to_scored_result, which calls accept_stored_memo. A missing disclosure column is a miss.',
    },
    'class:simulator/reduced_real_determinism.py:PT1PersistentEquilibriumStore': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 1530,
        "kind": 'class',
        "name": 'PT1PersistentEquilibriumStore',
        "carries_disclosure": True,
        "how": 'SQLite equilibrium and gate rows. A hit is copied into _entries and returned through _accept_cached_hit, including interpolation.',
    },
    'fn:simulator/optimize/study.py:_scored_result_from_journal_payload': {
        "path": 'simulator/optimize/study.py',
        "line": 2268,
        "kind": 'fn',
        "name": '_scored_result_from_journal_payload',
        "carries_disclosure": True,
        "how": 'Journal replay calls accept_stored_memo on the payload. A missing or unreadable field raises rather than ranking an unflagged row.',
    },
    'lru:simulator/battery/generators/compilation_thermo_fits.py:_generator_commit': {
        "path": 'simulator/battery/generators/compilation_thermo_fits.py',
        "line": 1804,
        "kind": 'lru',
        "name": '_generator_commit',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'store:simulator/battery/compilation_tier.py:fit_evaluation_cache': {
        "path": 'simulator/battery/compilation_tier.py',
        "line": 4889,
        "kind": 'store',
        "name": 'fit_evaluation_cache',
        "carries_disclosure": False,
        "how": 'Per-call cache of accepted thermochemistry fit evaluations against reference curves; it does not evaluate a vapour law.',
    },
    'lru:simulator/battery/generators/janaf.py:_cached_table_property_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3526,
        "kind": 'lru',
        "name": '_cached_table_property_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_element_reference_schedule': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 1136,
        "kind": 'lru',
        "name": '_element_reference_schedule',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_element_reference_state': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 1199,
        "kind": 'lru',
        "name": '_element_reference_state',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_enthalpy_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2939,
        "kind": 'lru',
        "name": '_fusion_enthalpy_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_fusion_entropy_points': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2971,
        "kind": 'lru',
        "name": '_fusion_entropy_points',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_gas_table_ids': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3072,
        "kind": 'lru',
        "name": '_janaf_gas_table_ids',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_pure_solid_table_index': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2666,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_pure_solid_table_temperature_range': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2688,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_temperature_range',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_formula_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1027,
        "kind": 'lru',
        "name": '_formula_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_formula_weight_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1050,
        "kind": 'lru',
        "name": '_formula_weight_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_sibling_formula_index': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1069,
        "kind": 'lru',
        "name": '_sibling_formula_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:_table2_elements': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 813,
        "kind": 'lru',
        "name": '_table2_elements',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_corrections_by_token': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 438,
        "kind": 'lru',
        "name": '_corrections_by_token',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_formula_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 843,
        "kind": 'lru',
        "name": '_formula_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_formula_weight_index_from_298k': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 869,
        "kind": 'lru',
        "name": '_formula_weight_index_from_298k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/generators/usgs_b1452.py:_table2_elements': {
        "path": 'simulator/battery/generators/usgs_b1452.py',
        "line": 915,
        "kind": 'lru',
        "name": '_table2_elements',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/migrate.py:_volatile_closure_component': {
        "path": 'simulator/battery/migrate.py',
        "line": 5436,
        "kind": 'lru',
        "name": '_volatile_closure_component',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/migrate.py:_is_as_printed_provenance_echo_text': {
        "path": 'simulator/battery/migrate.py',
        "line": 1015,
        "kind": 'lru',
        "name": '_is_as_printed_provenance_echo_text',
        "carries_disclosure": False,
        "how": 'Memoizes a deterministic printed-label suffix check; it does not cache a chemistry result.',
    },
    'lru:simulator/battery/score.py:_b1259_tridymite_cristobalite_offset_rows': {
        "path": 'simulator/battery/score.py',
        "line": 4066,
        "kind": 'lru',
        "name": '_b1259_tridymite_cristobalite_offset_rows',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_e15_binding_log10_k': {
        "path": 'simulator/battery/score.py',
        "line": 21705,
        "kind": 'lru',
        "name": '_e15_binding_log10_k',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_iron_saturated_feo_calibration': {
        "path": 'simulator/battery/score.py',
        "line": 4828,
        "kind": 'lru',
        "name": '_iron_saturated_feo_calibration',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_janaf_pure_solid_table_index': {
        "path": 'simulator/battery/score.py',
        "line": 4004,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/battery/score.py:_janaf_pure_solid_table_temperature_range': {
        "path": 'simulator/battery/score.py',
        "line": 4039,
        "kind": 'lru',
        "name": '_janaf_pure_solid_table_temperature_range',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/campaigns.py:_derived_override_field_names': {
        "path": 'simulator/campaigns.py',
        "line": 900,
        "kind": 'lru',
        "name": '_derived_override_field_names',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/campaigns.py:_species_molar_mass_kg_per_mol': {
        "path": 'simulator/campaigns.py',
        "line": 357,
        "kind": 'lru',
        "name": '_species_molar_mass_kg_per_mol',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/chemistry/langmuir_knudsen.py:_vapor_pressure_data': {
        "path": 'simulator/chemistry/langmuir_knudsen.py',
        "line": 317,
        "kind": 'lru',
        "name": '_vapor_pressure_data',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/chemistry/offgas_fo2.py:_load_cea_polynomials_from_text': {
        "path": 'simulator/chemistry/offgas_fo2.py',
        "line": 601,
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
        "line": 223,
        "kind": 'lru',
        "name": '_parse_required_yaml',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/gibbs_battery.py:load_cea_extract': {
        "path": 'simulator/diagnostic_helpers/gibbs_battery.py',
        "line": 622,
        "kind": 'lru',
        "name": 'load_cea_extract',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:_vapor_pressures_payload': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 1027,
        "kind": 'lru',
        "name": '_vapor_pressures_payload',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:cea_by_formula': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 579,
        "kind": 'lru',
        "name": 'cea_by_formula',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:cea_index': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 544,
        "kind": 'lru',
        "name": 'cea_index',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:vapour_rail_catalog': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 1032,
        "kind": 'lru',
        "name": 'vapour_rail_catalog',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/diagnostic_helpers/species_rail_differential.py:vapour_rail_legacy_view': {
        "path": 'simulator/diagnostic_helpers/species_rail_differential.py',
        "line": 1041,
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
        "line": 52,
        "kind": 'lru',
        "name": '_species_formula_registry',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/furnace_materials.py:load_furnace_materials': {
        "path": 'simulator/furnace_materials.py',
        "line": 91,
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
        "line": 1018,
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
        "line": 3139,
        "kind": 'lru',
        "name": '_resolved_engine_identity_for_eval',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/optimize/evaluate.py:_vapor_pressure_provider_code_fingerprint': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 4001,
        "kind": 'lru',
        "name": '_vapor_pressure_provider_code_fingerprint',
        "carries_disclosure": False,
        "how": 'Loaded catalogue or activity registry. It does not evaluate a law, so it has no disclosure to replay.',
    },
    'lru:simulator/optimize/objective.py:_campaign_product_claims_by_id': {
        "path": 'simulator/optimize/objective.py',
        "line": 1770,
        "kind": 'lru',
        "name": '_campaign_product_claims_by_id',
        "carries_disclosure": False,
        "how": 'Caches campaign product-key claims loaded from setpoints; it stores no evaluated vapour-law result or disclosure.',
    },
    'lru:simulator/optimize/objective.py:_c0_auto_apply_phase_names': {
        "path": 'simulator/optimize/objective.py',
        "line": 1835,
        "kind": 'lru',
        "name": '_c0_auto_apply_phase_names',
        "carries_disclosure": False,
        "how": 'Caches the campaign controller phase-name tuple for C0 auto-apply; it stores no evaluated vapour-law result or disclosure.',
    },
    'lru:simulator/optimize/pool.py:_default_feedstocks': {
        "path": 'simulator/optimize/pool.py',
        "line": 955,
        "kind": 'lru',
        "name": '_default_feedstocks',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/recipe.py:_cached_default_allowlist': {
        "path": 'simulator/recipe.py',
        "line": 2130,
        "kind": 'lru',
        "name": '_cached_default_allowlist',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/recipe.py:_default_setpoints': {
        "path": 'simulator/recipe.py',
        "line": 2817,
        "kind": 'lru',
        "name": '_default_setpoints',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/reduced_real_determinism.py:_source_module_digest': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 4755,
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
        "line": 102,
        "kind": 'lru',
        "name": '_thermo_data',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/runner/__init__.py:_resolve_kernel_commit_sha': {
        "path": 'simulator/runner/__init__.py',
        "line": 868,
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
        "line": 878,
        "kind": 'lru',
        "name": '_catalog',
        "carries_disclosure": False,
        "how": 'Pure loader or identity digest. It never evaluates a vapour law, so a cached value cannot carry a disclosure.',
    },
    'lru:simulator/volatile_properties.py:_default_registry': {
        "path": 'simulator/volatile_properties.py',
        "line": 2653,
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
        "line": 1109,
        "kind": 'store',
        "name": '_health_cache',
        "carries_disclosure": False,
        "how": 'AlphaMELTS process-health flag and TTL. Not a vapour-law result.',
    },
    'store:engines/builtin/foulant_disposition.py:_VAPOR_PAYLOAD_CACHE': {
        "path": 'engines/builtin/foulant_disposition.py',
        "line": 167,
        "kind": 'store',
        "name": '_VAPOR_PAYLOAD_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed vapor_pressures.yaml document. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/battery/generators/janaf.py:formation_basis_cache': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2212,
        "kind": 'store',
        "name": 'formation_basis_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/battery/migrate.py:_DEST_CACHE': {
        "path": 'simulator/battery/migrate.py',
        "line": 19413,
        "kind": 'store',
        "name": '_DEST_CACHE',
        "carries_disclosure": False,
        "how": 'Lab-field destination lookup. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:_VOCAB_CACHE': {
        "path": 'simulator/battery/migrate.py',
        "line": 19256,
        "kind": 'store',
        "name": '_VOCAB_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed lab-parameter vocabulary. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/battery/migrate.py:manifest_cache': {
        "path": 'simulator/battery/migrate.py',
        "line": 36256,
        "kind": 'store',
        "name": 'manifest_cache',
        "carries_disclosure": False,
        "how": 'Caches parsed compilation manifests by path for intake role counts; it stores source metadata, not evaluated vapour-law results or disclosures.',
    },
    'store:simulator/battery/migrate.py:equality_fallback_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 35618,
        "kind": 'store',
        "name": 'equality_fallback_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:seen_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 35617,
        "kind": 'store',
        "name": 'seen_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/migrate.py:unique_entries': {
        "path": 'simulator/battery/migrate.py',
        "line": 35616,
        "kind": 'store',
        "name": 'unique_entries',
        "carries_disclosure": False,
        "how": 'Migration dedup of queue entries. Not a vapour-law result.',
    },
    'store:simulator/battery/residue.py:pressure_cache': {
        "path": 'simulator/battery/residue.py',
        "line": 5026,
        "kind": 'store',
        "name": 'pressure_cache',
        "carries_disclosure": True,
        "how": 'OpenIMCC Sossi pressures. _cached_pressure_hit calls accept_stored_memo. No active registry means the row is not stored.',
    },
    'store:simulator/battery/residue.py:pressure_state_cache': {
        "path": 'simulator/battery/residue.py',
        "line": 1784,
        "kind": 'store',
        "name": 'pressure_state_cache',
        "carries_disclosure": True,
        "how": 'Shared residue pressure model. _cached_pressure_hit calls accept_stored_memo. No active registry means the row is not stored.',
    },
    'store:simulator/battery/score.py:_row_metadata_cache': {
        "path": 'simulator/battery/score.py',
        "line": 26382,
        "kind": 'store',
        "name": '_row_metadata_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/battery/validate.py:_cache': {
        "path": 'simulator/battery/validate.py',
        "line": 2288,
        "kind": 'store',
        "name": '_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/chemistry/offgas_fo2.py:_K_cache': {
        "path": 'simulator/chemistry/offgas_fo2.py',
        "line": 1431,
        "kind": 'store',
        "name": '_K_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/core.py:_freeze_gate_held_curve_record': {
        "path": 'simulator/core.py',
        "line": 1508,
        "kind": 'store',
        "name": '_freeze_gate_held_curve_record',
        "carries_disclosure": True,
        "how": 'Held-curve slot owned by the simulator. The freeze-gate reader reads it through accept_stored_memo.',
    },
    'store:simulator/core.py:_freeze_gate_liquid_fraction_cache': {
        "path": 'simulator/core.py',
        "line": 1506,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_cache',
        "carries_disclosure": True,
        "how": 'Core liquidus local slot. _melt_redox_liquidus_gate_record reads it through accept_stored_memo.',
    },
    'store:simulator/core.py:_freeze_gate_liquid_fraction_curve_memo': {
        "path": 'simulator/core.py',
        "line": 1509,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_curve_memo',
        "carries_disclosure": True,
        "how": 'Core curve memo. A hit goes through accept_stored_memo and the rewrite keeps the measured field.',
    },
    'store:simulator/core.py:_memo': {
        "path": 'simulator/core.py',
        "line": 157,
        "kind": 'store',
        "name": '_memo',
        "carries_disclosure": False,
        "how": 'Deepcopy cycle map for refusal state. It never stores a vapour pressure.',
    },
    'store:simulator/core.py:rates_cache': {
        "path": 'simulator/core.py',
        "line": 8721,
        "kind": 'store',
        "name": 'rates_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_LEGACY_VIEW_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1858,
        "kind": 'store',
        "name": '_LEGACY_VIEW_CACHE',
        "carries_disclosure": False,
        "how": 'Schema-v1 projection of the vapour catalog. Dispatch still evaluates the law.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_MELT_PSAT_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1620,
        "kind": 'store',
        "name": '_MELT_PSAT_CACHE',
        "carries_disclosure": True,
        "how": 'Melt provider surface. _read_melt_psat_cache is the only read and calls accept_stored_memo. A row with no law_disclosures is a miss.',
    },
    'store:simulator/diagnostic_helpers/extract_reproduction.py:_VP_PROVIDER_CACHE': {
        "path": 'simulator/diagnostic_helpers/extract_reproduction.py',
        "line": 1619,
        "kind": 'store',
        "name": '_VP_PROVIDER_CACHE',
        "carries_disclosure": False,
        "how": 'Compiled provider object. Dispatch still evaluates the law, so the cached object is not a flagged pressure.',
    },
    'store:simulator/diagnostic_helpers/kems.py:surface_cache': {
        "path": 'simulator/diagnostic_helpers/kems.py',
        "line": 654,
        "kind": 'store',
        "name": 'surface_cache',
        "carries_disclosure": False,
        "how": 'Request-local KEMS surface results keyed by temperature for one comparison case; no persistent memoization.',
    },
    'store:simulator/engine_binding_admission.py:grouped_entries': {
        "path": 'simulator/engine_binding_admission.py',
        "line": 1544,
        "kind": 'store',
        "name": 'grouped_entries',
        "carries_disclosure": False,
        "how": 'Binding-admission grouping of receipts. Not a vapour-law result.',
    },
    'store:simulator/engine_binding_admission.py:receipt_entries': {
        "path": 'simulator/engine_binding_admission.py',
        "line": 1556,
        "kind": 'store',
        "name": 'receipt_entries',
        "carries_disclosure": False,
        "how": 'Binding-admission grouping of receipts. Not a vapour-law result.',
    },
    'store:simulator/evaporation.py:_freeze_gate_held_curve_record': {
        "path": 'simulator/evaporation.py',
        "line": 3668,
        "kind": 'store',
        "name": '_freeze_gate_held_curve_record',
        "carries_disclosure": True,
        "how": 'Same-hour held curve. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'store:simulator/evaporation.py:_freeze_gate_liquid_fraction_cache': {
        "path": 'simulator/evaporation.py',
        "line": 3886,
        "kind": 'store',
        "name": '_freeze_gate_liquid_fraction_cache',
        "carries_disclosure": True,
        "how": 'Evaporation local slot. _freeze_gate_curve_record reads it through accept_stored_memo.',
    },
    'store:simulator/evaporation.py:computing_cache': {
        "path": 'simulator/evaporation.py',
        "line": 3913,
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
    'store:simulator/melt_backend/magemin.py:_MAGEMIN_ENGINE_VERSION_CACHE': {
        "path": 'simulator/melt_backend/magemin.py',
        "line": 210,
        "kind": 'store',
        "name": '_MAGEMIN_ENGINE_VERSION_CACHE',
        "carries_disclosure": False,
        "how": 'MAGEMin --version string keyed by binary inode, size, and mtime. Not a vapour-law result.',
    },
    'store:simulator/melt_backend/vaporock.py:_SESSION_BACKEND_CACHE': {
        "path": 'simulator/melt_backend/vaporock.py',
        "line": 1279,
        "kind": 'store',
        "name": '_SESSION_BACKEND_CACHE',
        "carries_disclosure": False,
        "how": 'Warm VapoRock worker. Equilibrate results are not cached; each call still runs.',
    },
    'store:simulator/melt_backend/vaporock.py:_VAPOROCK_RUNTIME_AVAILABLE_CACHE': {
        "path": 'simulator/melt_backend/vaporock.py',
        "line": 1216,
        "kind": 'store',
        "name": '_VAPOROCK_RUNTIME_AVAILABLE_CACHE',
        "carries_disclosure": False,
        "how": 'Boolean availability flag. Not a vapour-law result.',
    },
    'store:simulator/optimize/evaluate.py:_VAPOROCK_AVAILABLE_CACHE': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 3974,
        "kind": 'store',
        "name": '_VAPOROCK_AVAILABLE_CACHE',
        "carries_disclosure": False,
        "how": 'Boolean availability flag. Not a vapour-law result.',
    },
    'store:simulator/optimize/evaluate.py:per_hour_cache': {
        "path": 'simulator/optimize/evaluate.py',
        "line": 6898,
        "kind": 'store',
        "name": 'per_hour_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/optimize/study.py:prefix_replay_cache': {
        "path": 'simulator/optimize/study.py',
        "line": 718,
        "kind": 'store',
        "name": 'prefix_replay_cache',
        "carries_disclosure": False,
        "how": 'Equality copy of a staged prefix. The value returned to the study is ResultStore.lookup, which re-registers.',
    },
    'store:simulator/reduced_real_determinism.py:physics_bucket_entries': {
        "path": 'simulator/reduced_real_determinism.py',
        "line": 377,
        "kind": 'store',
        "name": 'physics_bucket_entries',
        "carries_disclosure": False,
        "how": 'Hash index from a physics bucket to a PT0 key. The payload and disclosure live on _entries.',
    },
    'store:simulator/runner/__init__.py:per_hour_summary_cache': {
        "path": 'simulator/runner/__init__.py',
        "line": 6205,
        "kind": 'store',
        "name": 'per_hour_summary_cache',
        "carries_disclosure": False,
        "how": 'Local scratch for rates, equilibrium constants, metadata, or validation. It does not store a vapour-law disclosure.',
    },
    'store:simulator/runner/__init__.py:reduced_real_cache': {
        "path": 'simulator/runner/__init__.py',
        "line": 1142,
        "kind": 'store',
        "name": 'reduced_real_cache',
        "carries_disclosure": False,
        "how": 'Configuration handle for the determinism store, not a cached physics row.',
    },
    'store:simulator/session.py:reduced_real_cache': {
        "path": 'simulator/session.py',
        "line": 207,
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
        "line": 448,
        "kind": 'store',
        "name": '_COMPILE_CACHE',
        "carries_disclosure": False,
        "how": 'Compiled catalog object. A hit does not skip evaluate(), which is where a flagged law registers.',
    },
    'store:simulator/vapour_rail/catalog.py:_THERMO_EXTRACT_PARSE_CACHE': {
        "path": 'simulator/vapour_rail/catalog.py',
        "line": 104,
        "kind": 'store',
        "name": '_THERMO_EXTRACT_PARSE_CACHE',
        "carries_disclosure": False,
        "how": 'Parsed thermo extract. A pure loader. It never evaluates a vapour law.',
    },
    'store:simulator/vapour_rail/catalog.py:_THERMO_OBSERVATION_CACHE': {
        "path": 'simulator/vapour_rail/catalog.py',
        "line": 107,
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
    'lru:simulator/battery/compilation_tier.py:_b677_printed_ideal_gas_h298': {
        "path": 'simulator/battery/compilation_tier.py',
        "line": 2796,
        "kind": 'lru',
        "name": '_b677_printed_ideal_gas_h298',
        "carries_disclosure": False,
        "how": 'Boolean check of source heading, formula, JANAF table identity, and printed H−H298 zero. It does not evaluate a vapour law.',
    },
    'lru:simulator/battery/compilation_tier.py:_nasa_cea_9_polynomial': {
        "path": 'simulator/battery/compilation_tier.py',
        "line": 1246,
        "kind": 'lru',
        "name": '_nasa_cea_9_polynomial',
        "carries_disclosure": False,
        "how": 'Builds a NASA-9 thermo polynomial from a source record; pressure is evaluated later by the caller, so this cache has no run disclosure.',
    },
    'lru:simulator/battery/compilation_tier.py:_nasa_cea_9_record_index': {
        "path": 'simulator/battery/compilation_tier.py',
        "line": 1152,
        "kind": 'lru',
        "name": '_nasa_cea_9_record_index',
        "carries_disclosure": False,
        "how": 'Indexes NASA-9 source records by formula and id. It stores source mappings, not evaluated vapour-law results.',
    },
    'lru:simulator/reference_data/nasa_glenn.py:_nasa_glenn_atomic_weights': {
        "path": 'simulator/reference_data/nasa_glenn.py',
        "line": 379,
        "kind": 'lru',
        "name": '_nasa_glenn_atomic_weights',
        "carries_disclosure": False,
        "how": 'Caches source-era atomic weights read from NASA elemental cards; these are source data used for formula mass checks, not evaluated vapour laws.',
    },
    'lru:simulator/reference_data/nasa_glenn.py:load_record_document_by_id': {
        "path": 'simulator/reference_data/nasa_glenn.py',
        "line": 1536,
        "kind": 'lru',
        "name": 'load_record_document_by_id',
        "carries_disclosure": False,
        "how": 'Caches parsed NASA source records by id; it stores source coefficients and identity fields, not evaluated vapour-law results.',
    },
    'lru:simulator/battery/generators/janaf.py:_janaf_liquid_element_table_ids': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3105,
        "kind": 'lru',
        "name": '_janaf_liquid_element_table_ids',
        "carries_disclosure": False,
        "how": 'Indexes neutral elemental liquid JANAF table ids from the manifest. It stores source identities, not evaluated vapour-law results.',
    },
    'lru:simulator/battery/generators/janaf.py:janaf_liquid_element_reference_at_temperature': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 3142,
        "kind": 'lru',
        "name": 'janaf_liquid_element_reference_at_temperature',
        "carries_disclosure": False,
        "how": 'Returns a JANAF elemental Gibbs reference and any table-tail interval; it does not evaluate a vapour law, and callers handle the thermo extrapolation separately.',
    },
    'lru:simulator/battery/generators/janaf.py:janaf_solid_stability_transitions': {
        "path": 'simulator/battery/generators/janaf.py',
        "line": 2866,
        "kind": 'lru',
        "name": 'janaf_solid_stability_transitions',
        "carries_disclosure": False,
        "how": 'Computes JANAF crystal-to-crystal Gibbs crossings from table data; it excludes melting and never evaluates a vapour law.',
    },
    'lru:simulator/battery/generators/usgs_b1259.py:reference_enthalpy_zero_is_ideal_gas_span': {
        "path": 'simulator/battery/generators/usgs_b1259.py',
        "line": 1507,
        "kind": 'lru',
        "name": 'reference_enthalpy_zero_is_ideal_gas_span',
        "carries_disclosure": False,
        "how": 'Checks whether a B1259 record prints zero enthalpy at 298.15 K across its ideal-gas span; it returns a classification boolean, not a vapour-law result.',
    },
    'lru:simulator/battery/migrate.py:_mass_percent_component_stem': {
        "path": 'simulator/battery/migrate.py',
        "line": 998,
        "kind": 'lru',
        "name": '_mass_percent_component_stem',
        "carries_disclosure": False,
        "how": 'Strips a printed mass-percent suffix and returns a boolean. It is string normalization, not a physics or vapour-law result.',
    },
    'lru:simulator/battery/score.py:_catalog_score_rows': {
        "path": 'simulator/battery/score.py',
        "line": 14383,
        "kind": 'lru',
        "name": '_catalog_score_rows',
        "carries_disclosure": False,
        "how": 'Caches compiled species and phase/reaction annotations for scoring eligibility; it does not call evaluate() or produce pressures.',
    },
    'lru:simulator/reference_data/janaf.py:_compiled_table_by_id': {
        "path": 'simulator/reference_data/janaf.py',
        "line": 1393,
        "kind": 'lru',
        "name": '_compiled_table_by_id',
        "carries_disclosure": False,
        "how": 'Loads and validates one compiled JANAF table document. It stores table metadata and printed data, not evaluated vapour-law results.',
    },
    'store:simulator/engine_local_config.py:_BINARY_SHA256_CACHE': {
        "path": 'simulator/engine_local_config.py',
        "line": 28,
        "kind": 'store',
        "name": '_BINARY_SHA256_CACHE',
        "carries_disclosure": False,
        "how": 'Memoizes engine-binary file hashes for local configuration provenance; it stores digests, not chemistry results.',
    },
    'lru:simulator/battery/method_class_bands.py:method_class_bands': {
        "path": 'simulator/battery/method_class_bands.py',
        "line": 15,
        "kind": 'lru',
        "name": 'method_class_bands',
        "carries_disclosure": False,
        "how": 'Loads the method-class applicability table. It returns classification rules, not evaluated vapour-law results.',
    },
    'lru:simulator/chemistry/ellingham_thermo.py:_na2o_janaf_liquid_source_data': {
        "path": 'simulator/chemistry/ellingham_thermo.py',
        "line": 148,
        "kind": 'lru',
        "name": '_na2o_janaf_liquid_source_data',
        "carries_disclosure": False,
        "how": 'Loads Na2O JANAF Gibbs rows and fusion data for Ellingham thermochemistry; it does not evaluate a vapour law.',
    },
    'lru:simulator/condensation.py:_polyatomic_carrier_cea_polynomials': {
        "path": 'simulator/condensation.py',
        "line": 2036,
        "kind": 'lru',
        "name": '_polyatomic_carrier_cea_polynomials',
        "carries_disclosure": False,
        "how": 'Loads NASA CEA carrier polynomials for condensation heat calculations; it stores coefficients, not vapour-law results.',
    },
    'lru:simulator/nucleation.py:nucleation_liquid_data': {
        "path": 'simulator/nucleation.py',
        "line": 127,
        "kind": 'lru',
        "name": 'nucleation_liquid_data',
        "carries_disclosure": False,
        "how": 'Loads the liquid nucleation data document; it stores input records, not evaluated vapour-law results.',
    },
    'lru:simulator/nucleation.py:_sodium_saturation_block': {
        "path": 'simulator/nucleation.py',
        "line": 141,
        "kind": 'lru',
        "name": '_sodium_saturation_block',
        "carries_disclosure": False,
        "how": 'Loads the sodium saturation model block from the configured catalogue; it caches parameters, not evaluated law results.',
    },
    'lru:simulator/reference_data/janaf.py:_formation_gibbs_rows_kj_per_mol': {
        "path": 'simulator/reference_data/janaf.py',
        "line": 1402,
        "kind": 'lru',
        "name": '_formation_gibbs_rows_kj_per_mol',
        "carries_disclosure": False,
        "how": 'Caches checked printed JANAF Gibbs rows and missing nodes in kJ/mol; it stores source data, not a vapour-law result.',
    },
    'lru:simulator/reference_data/janaf.py:_fusion_table_points': {
        "path": 'simulator/reference_data/janaf.py',
        "line": 1837,
        "kind": 'lru',
        "name": '_fusion_table_points',
        "carries_disclosure": False,
        "how": 'Caches checked JANAF fusion-table Gibbs points and digest; it stores source data, not a vapour-law result.',
    },
    'lru:simulator/reference_data/janaf.py:_fusion_missing_gibbs_temperatures': {
        "path": 'simulator/reference_data/janaf.py',
        "line": 1877,
        "kind": 'lru',
        "name": '_fusion_missing_gibbs_temperatures',
        "carries_disclosure": False,
        "how": 'Caches JANAF temperatures whose printed formation-Gibbs cells are missing; it stores table coverage, not law results.',
    },
    'lru:simulator/vapour_rail/wall_saturation.py:_nasa_record_data': {
        "path": 'simulator/vapour_rail/wall_saturation.py',
        "line": 559,
        "kind": 'lru',
        "name": '_nasa_record_data',
        "carries_disclosure": False,
        "how": 'Caches parsed NASA intervals and coefficients for wall Gibbs lookup; the wall-law result and disclosure are assembled later.',
    },
    'lru:simulator/vapour_rail/wall_saturation.py:_janaf_record_data': {
        "path": 'simulator/vapour_rail/wall_saturation.py',
        "line": 631,
        "kind": 'lru',
        "name": '_janaf_record_data',
        "carries_disclosure": False,
        "how": 'Caches checked JANAF Gibbs rows for wall-state lookup; the wall-law result and disclosure are assembled later.',
    },
    'lru:simulator/vapour_rail/wall_saturation.py:_nasa_state_for_source': {
        "path": 'simulator/vapour_rail/wall_saturation.py',
        "line": 668,
        "kind": 'lru',
        "name": '_nasa_state_for_source',
        "carries_disclosure": False,
        "how": 'Caches a source Gibbs state with its authority and published range for wall-law assembly; it does not cache a law result.',
    },
    'lru:simulator/vapour_rail/wall_saturation.py:_janaf_state_for_source': {
        "path": 'simulator/vapour_rail/wall_saturation.py',
        "line": 740,
        "kind": 'lru',
        "name": '_janaf_state_for_source',
        "carries_disclosure": False,
        "how": 'Caches a source Gibbs state with its authority and published range for wall-law assembly; it does not cache a law result.',
    },
}


def _site_tables() -> tuple[
    dict[tuple[str, str], str],
    dict[str, list[tuple[str, str]]],
]:
    """(path, function) -> lru site, and path -> [(name, site)] longest first."""

    by_function: dict[tuple[str, str], str] = {}
    by_path: dict[str, list[tuple[str, str]]] = {}
    for site_id, row in CACHE_SITES.items():
        path = str(row["path"])
        name = str(row["name"])
        by_path.setdefault(path, []).append((name, site_id))
        if row["kind"] == "lru":
            by_function[(path, name)] = site_id
    for names in by_path.values():
        names.sort(key=lambda item: len(item[0]), reverse=True)
    return by_function, by_path


def _classification_of(site_id: str) -> tuple[str, str, str | None]:
    row = CACHE_SITES[site_id]
    if row["carries_disclosure"]:
        return "carrying", str(row["how"]), site_id
    return "inert", str(row["how"]), None


def _def_after(lines: list[str], index: int) -> str | None:
    for row in lines[index:index + 6]:
        match = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(", row)
        if match:
            return match.group(1)
    return None


def _def_containing(lines: list[str], lineno: int) -> str | None:
    tree = ast.parse("\n".join(lines))
    containing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= node.end_lineno
    ]
    if not containing:
        return None
    return min(containing, key=lambda node: node.end_lineno - node.lineno).name


def _token_present(name: str, text: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
        text,
    ) is not None


def scan_controller_cache_lines(
    root: Path | None = None,
) -> list[tuple[str, int, str]]:
    """Every production line the controller cache regex matches."""

    repo = root or _repo_root()
    found: list[tuple[str, int, str]] = []
    for path in _production_python_files(repo):
        rel = str(path.relative_to(repo))
        for lineno, row in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CONTROLLER_CACHE_REGEX.search(row):
                found.append((rel, lineno, row))
    return found


def _extra_cache_line(
    path: str, stripped: str
) -> tuple[str, str, str | None] | None:
    """Regex hits whose text does not name a CACHE_SITES store.

    Each arm is a line the controller regex matches and that cannot carry a
    flagged law, except the shared-curve getattr, which is the carrying read.
    A line that matches none of these stays unclassified.
    """

    if path == "simulator/evaporation.py" and stripped.startswith(
        "shared_curve_cache = getattr("
    ):
        return _classification_of(
            "attr:simulator/evaporation.py:_freeze_gate_shared_curve_cache"
        )
    if path == "simulator/accounting/queries.py" and stripped == "cache = {}":
        return (
            "inert",
            "Accounting prefix totals on a ledger record. Not a vapour-law result.",
            None,
        )
    if path == "simulator/battery/compilation_tier.py" and stripped == "cache = {}":
        return (
            "inert",
            "Shomate enthalpy-form label keyed by the coefficient row. Not a vapour pressure.",
            None,
        )
    if path == "simulator/liner_life.py" and stripped.startswith("cache: dict[float, float]"):
        return (
            "inert",
            "Recession-rate scratch for one liner-life call. Not a vapour-law result.",
            None,
        )
    if path == "simulator/liner_corrosion.py" and (
        "MAGEMIN_CACHE_" in stripped
        or "MAGEMIN_REQUEST_CACHE_VERSION" in stripped
    ):
        return (
            "inert",
            "MAGEMin request version or precision configuration used by the liquid-fraction cache keys, not cached vapour-law data.",
            None,
        )
    if path == "simulator/core.py" and "published_memo = accept_stored_memo(" in stripped:
        return (
            "inert",
            "Local name for the list accept_stored_memo just returned. Not a store.",
            None,
        )
    if path == "simulator/evaporation.py" and stripped == "previous_cache = cache":
        return (
            "inert",
            "Saved previous freeze-gate slot so a failed solve can restore it. The published hit is the carrying cache.",
            None,
        )
    if path == "simulator/reduced_real_determinism.py" and (
        '"_freeze_gate_held_curve_record"' in stripped
    ):
        return (
            "inert",
            "Presence check that a held curve exists. The curve value is read through accept_stored_memo.",
            None,
        )
    if path == "simulator/vapour_rail/catalog.py" and "_legacy_view_cache" in stripped:
        return (
            "inert",
            "Schema-v1 projection of a compiled catalog. A hit does not skip evaluate().",
            None,
        )
    if path == "simulator/vapour_rail/catalog.py" and "_CATALOG_COMPILE_CACHE_MAX" in stripped:
        return (
            "inert",
            "Integer LRU bound. Not a cached vapour pressure.",
            None,
        )
    if path == "simulator/battery/score.py" and any(
        token in stripped
        for token in (
            "_residue_prediction_cache_key",
            "_residue_prediction_cache_entry",
        )
    ):
        return _classification_of(
            "store:simulator/battery/score.py:residue_prediction_cache"
        )
    if path == "simulator/engine_cache.py":
        return (
            "inert",
            "Engine-cache configuration, key encoding, or schema wiring. Cached engine values are stored in _MEMORY_CACHE or engine_outputs.",
            None,
        )
    if path == "simulator/melt_backend/base.py":
        return (
            "inert",
            "HOT projection or notice-code schema. It does not store or return a cached vapour-law result.",
            None,
        )
    if path == "simulator/yaml_cache.py":
        return (
            "inert",
            "Parsed YAML document cache. A pure loader. It never evaluates a vapour law.",
            None,
        )
    if path == "simulator/engine_binding_admission.py" and (
        "GATE_CURVE_MEMO" in stripped or "CROSS_PROCESS_CACHE_ONLY" in stripped
    ):
        return (
            "inert",
            "Binding-admission scope name. Not a memo of a vapour-law result.",
            None,
        )
    if path == "simulator/runner/__init__.py" and "_WALL_DEPOSIT_CUMULATIVE_CACHE" in stripped:
        return (
            "inert",
            "Running sum of snapshot wall-deposit deltas. It does not evaluate a vapour law.",
            None,
        )
    if path == "simulator/runner/__init__.py" and stripped.startswith("cache: dict[str, list[Any]]"):
        return (
            "inert",
            "Parameter of the per-hour summary scratch. It does not store a vapour-law disclosure.",
            None,
        )
    if path == "simulator/battery/score.py" and "observation_lineages" in stripped:
        return (
            "inert",
            "Ancestry index memo passed to lineage resolution. It stores no vapour-law result or disclosure.",
            None,
        )
    if path == "simulator/battery/validate.py" and stripped.startswith(
        "lineage_cache: dict[str, Ancestry]"
    ):
        return (
            "inert",
            "Optional ancestry memo for source-lineage resolution. It stores no vapour-law result or disclosure.",
            None,
        )
    if "reduced_real_cache" in stripped or "REDUCED_REAL_CACHE" in stripped:
        return (
            "inert",
            "Configuration handle or cache-tier label for the determinism store. The physics rows live on _entries.",
            None,
        )
    if any(
        token in stripped
        for token in (
            "CACHE_TIER",
            "cache_tier_ceiling",
            "CACHED_INTERPOLATED",
            "CERTIFIED_CACHE",
            "CACHEABLE_",
            "persistent_cache",
            "SOURCE_CACHE_",
            "DEFAULT_GRIND_CACHE",
            "REGOLITH_DATA_CACHE",
            "inherited_cache",
            "allow_internal_analytical_cache",
            "approximate_cache",
        )
    ):
        return (
            "inert",
            "Cache-tier name, filesystem path, or status allowlist. Not an evaluated vapour pressure.",
            None,
        )
    return None


def classify_controller_cache_line(
    path: str,
    lineno: int,
    text: str,
    *,
    lines: list[str] | None = None,
) -> tuple[str, str, str | None] | None:
    """Classify one regex hit.

    ``carrying`` lines name a ``DISCLOSURE_CARRYING`` site whose reader calls
    ``accept_stored_memo``. ``inert`` lines say why they cannot carry a flagged
    value. ``None`` means the meta-test must fail.
    """

    if path.endswith("disclosure_cache_inventory.py"):
        return (
            "inert",
            "Classification table. Not a runtime cache.",
            None,
        )
    stripped = text.strip()
    if stripped.startswith("#"):
        return ("inert", "Comment. Not a runtime cache.", None)
    if re.search(r"\bimport\b", stripped) and "lru_cache" in stripped:
        return ("inert", "Import of the cache decorator. Not a store.", None)

    if path == "simulator/optimize/physics.py" and re.search(
        r"\bshared_cache\s*(?::|=)", text
    ):
        if lines is None:
            source = (_repo_root() / path).read_text(encoding="utf-8").splitlines()
        else:
            source = lines
        function = _def_containing(source, lineno)
        if function in {
            "_coating_authority_values",
            "_coating_dew_point_warnings",
            "_coating_violation_reasons",
        }:
            return _classification_of(
                "store:simulator/optimize/physics.py:shared_authority_cache"
            )

    by_function, by_path = _site_tables()
    if re.search(r"@\s*(?:functools\.)?(?:lru_cache|cache)\b", stripped):
        if lines is None:
            source = (_repo_root() / path).read_text(encoding="utf-8").splitlines()
        else:
            source = lines
        function = _def_after(source, lineno)
        site_id = by_function.get((path, function or ""))
        if site_id is None:
            return None
        return _classification_of(site_id)
    if "ResultStore(" in text:
        return _classification_of("class:simulator/optimize/results_store.py:ResultStore")

    matches: list[tuple[str, str]] = []
    for name, site_id in by_path.get(path, ()):
        if _token_present(name, text):
            matches.append((name, site_id))
    # A shared helper's parameter is the pressure-cache gate even though the
    # line does not repeat the store's name.
    if path == "simulator/battery/score.py" and re.search(
        r"\bcache\s*:\s*dict\b", text
    ):
        return _classification_of(
            "store:simulator/battery/score.py:residue_prediction_cache"
        )
    if path == "simulator/battery/score_run.py" and re.search(
        r"\bcache\s*:\s*dict\b", text
    ):
        return _classification_of(
            "store:simulator/battery/score_run.py:residue_prediction_cache"
        )
    if path == "simulator/battery/residue.py" and re.search(r"\bcache\s*:\s*dict\b", text):
        return _classification_of("store:simulator/battery/residue.py:pressure_cache")
    if path == "simulator/diagnostic_helpers/kems.py" and re.search(
        r"\bcache\s*:\s*dict\b", text
    ):
        return _classification_of(
            "store:simulator/diagnostic_helpers/kems.py:_surface_cache"
        )
    if not matches:
        return _extra_cache_line(path, stripped)
    carrying = [
        site_id
        for _name, site_id in matches
        if CACHE_SITES[site_id]["carries_disclosure"]
    ]
    if carrying:
        return _classification_of(carrying[0])
    return _classification_of(matches[0][1])


def unclassified_controller_cache_lines(
    root: Path | None = None,
) -> list[str]:
    """Regex hits that are neither disclosure-carrying nor explicitly inert."""

    repo = root or _repo_root()
    missing: list[str] = []
    by_file: dict[str, list[str]] = {}
    for path, _lineno, text in scan_controller_cache_lines(repo):
        by_file.setdefault(path, (_repo_root() / path).read_text(encoding="utf-8").splitlines())
        if classify_controller_cache_line(
            path, _lineno, text, lines=by_file[path]
        ) is None:
            missing.append(f"{path}:{_lineno}:{text.strip()}")
    return missing
