import ast
import re
import subprocess
from pathlib import Path

from simulator.accounting import load_species_formulas


def test_internal_analytical_implementation_has_no_legacy_stub_symbols():
    repo = Path(__file__).parent.parent
    forbidden = {
        "StubBackend",
        "stub_backend_cls",
        "_stub_backend",
        "_stub_equilibrium",
        "_backend_allows_stub_fallback",
    }
    violations = []
    for root in (
        repo / "simulator",
        repo / "engines",
        repo / "scripts",
        repo / "web",
    ):
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo).as_posix()
            if relative.startswith("simulator/optimize/"):
                continue
            tree = ast.parse(path.read_text(), filename=relative)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names.append(node.name)
                elif isinstance(node, ast.arg):
                    names.append(node.arg)
                elif isinstance(node, ast.alias):
                    names.extend((node.name, node.asname))
                for name in names:
                    if name in forbidden:
                        violations.append(f"{relative}:{node.lineno}: {name}")

    assert not violations, "\n".join(violations)


def test_simulator_has_no_forbidden_internal_kg_mutations():
    repo = Path(__file__).parent.parent
    forbidden = [
        r"oxygen_cumulative_kg \+=",
        r"O2_vented_cumulative_kg \+=",
        r"train\.stages\[6\].*O2",
        r"stages\[6\]\.collected_kg\['O2'\]",
        r"composition_kg\[[^\]]+\]\s*=",
        r"collected_kg\[[^\]]+\]\s*=",
        r"shuttle_.*inventory_kg\s*-=",
        r"thermite_Mg_inventory_kg\s*-=",
        r"terminal\.oxygen_stored",
        r"terminal\.oxygen_vented_to_vacuum",
    ]

    result = subprocess.run(
        ["rg", "-n", "|".join(forbidden), "simulator"],
        cwd=repo,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout


def test_forbidden_internal_kg_mutation_patterns_match_samples():
    samples = {
        r"oxygen_cumulative_kg \+=": "self.oxygen_cumulative_kg += 1.0",
        r"O2_vented_cumulative_kg \+=": "self.O2_vented_cumulative_kg += vented",
        r"composition_kg\[[^\]]+\]\s*=": "self.melt.composition_kg['FeO'] = 0",
        r"collected_kg\[[^\]]+\]\s*=": "stage.collected_kg['O2'] = 1",
        r"terminal\.oxygen_stored": "terminal.oxygen_stored",
    }

    for pattern, sample in samples.items():
        assert re.search(pattern, sample), pattern


def test_species_catalog_loads_case_sensitive_species_ids():
    repo = Path(__file__).parent.parent

    formulas = load_species_formulas(repo / "data" / "species_catalog.yaml")

    assert dict(formulas["Co"].elements) == {"Co": 1.0}
    assert dict(formulas["CO"].elements) == {"C": 1.0, "O": 1.0}


def test_no_direct_melt_regime_membership_comparisons_outside_helper():
    repo = Path(__file__).parent.parent
    violations = []
    for root in (repo / "simulator", repo / "engines" / "builtin"):
        for path in root.rglob("*.py"):
            if path.relative_to(repo).as_posix() == "simulator/melt_regime.py":
                continue
            violations.extend(_melt_regime_membership_ast_violations(path, repo))

    assert not violations, "\n".join(violations)


def _melt_regime_membership_ast_violations(
    path: Path,
    repo: Path,
) -> list[str]:
    return _melt_regime_membership_ast_violations_from_source(
        path.read_text(),
        path.relative_to(repo).as_posix(),
    )


def _melt_regime_membership_ast_violations_from_source(
    source: str,
    label: str,
) -> list[str]:
    tree = ast.parse(source, filename=label)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left_chain = [node.left, *node.comparators[:-1]]
        for left, op, right in zip(left_chain, node.ops, node.comparators):
            if _forbidden_liquid_fraction_compare(left, op, right):
                violations.append(
                    f"{label}:{node.lineno}: {ast.unparse(node)}"
                )
                break
            if _forbidden_solidus_compare(left, op, right):
                violations.append(
                    f"{label}:{node.lineno}: {ast.unparse(node)}"
                )
                break
    return violations


def _forbidden_liquid_fraction_compare(
    left: ast.expr,
    op: ast.cmpop,
    right: ast.expr,
) -> bool:
    if not isinstance(op, (ast.Eq, ast.LtE, ast.GtE)):
        return False
    return (
        _expr_mentions_liquid_fraction(left)
        and _expr_is_zero_or_epsilon_boundary(right)
    ) or (
        _expr_is_zero_or_epsilon_boundary(left)
        and _expr_mentions_liquid_fraction(right)
    )


def _forbidden_solidus_compare(
    left: ast.expr,
    op: ast.cmpop,
    right: ast.expr,
) -> bool:
    if not isinstance(op, (ast.Eq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        return False
    return (
        _expr_mentions_temperature_boundary(left)
        and _expr_mentions_solidus_boundary(right)
    ) or (
        _expr_mentions_solidus_boundary(left)
        and _expr_mentions_temperature_boundary(right)
    )


def _expr_mentions_liquid_fraction(expr: ast.expr) -> bool:
    expr = _unwrap_float_call(expr)
    if isinstance(expr, ast.Name):
        return expr.id == "liquid_fraction"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "liquid_fraction"
    if isinstance(expr, ast.Subscript):
        return _subscript_key(expr.slice) == "liquid_fraction"
    if isinstance(expr, ast.Call):
        return _call_reads_liquid_fraction(expr) or any(
            _expr_mentions_liquid_fraction(child)
            for child in ast.iter_child_nodes(expr)
            if isinstance(child, ast.expr)
        )
    return any(
        _expr_mentions_liquid_fraction(child)
        for child in ast.iter_child_nodes(expr)
        if isinstance(child, ast.expr)
    )


def _expr_mentions_temperature_boundary(expr: ast.expr) -> bool:
    expr = _unwrap_float_call(expr)
    if isinstance(expr, ast.Name):
        return expr.id in {"temperature_C", "temperature_K", "T_K"}
    if isinstance(expr, ast.Attribute):
        return expr.attr in {"temperature_C", "temperature_K", "T_K"}
    return any(
        _expr_mentions_temperature_boundary(child)
        for child in ast.iter_child_nodes(expr)
        if isinstance(child, ast.expr)
    )


def _expr_mentions_solidus_boundary(expr: ast.expr) -> bool:
    expr = _unwrap_float_call(expr)
    if isinstance(expr, ast.Name):
        return expr.id in {"solidus_T_C", "solidus_K"}
    if isinstance(expr, ast.Attribute):
        return expr.attr in {"solidus_T_C", "solidus_K"}
    return any(
        _expr_mentions_solidus_boundary(child)
        for child in ast.iter_child_nodes(expr)
        if isinstance(child, ast.expr)
    )


def _expr_is_zero_or_epsilon_boundary(expr: ast.expr) -> bool:
    expr = _unwrap_float_call(expr)
    if isinstance(expr, ast.Constant):
        return (
            isinstance(expr.value, (int, float))
            and not isinstance(expr.value, bool)
            and float(expr.value) == 0.0
        )
    if isinstance(expr, ast.Name):
        return expr.id in {"_FREEZE_GATE_EPSILON", "MELT_REGIME_EPSILON"}
    return False


def _unwrap_float_call(expr: ast.expr) -> ast.expr:
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "float"
        and len(expr.args) == 1
        and not expr.keywords
    ):
        return expr.args[0]
    return expr


def _call_reads_liquid_fraction(expr: ast.Call) -> bool:
    if (
        isinstance(expr.func, ast.Name)
        and expr.func.id == "getattr"
        and len(expr.args) >= 2
    ):
        return _string_constant(expr.args[1]) == "liquid_fraction"
    if (
        isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "get"
        and expr.args
    ):
        return _string_constant(expr.args[0]) == "liquid_fraction"
    return False


def _subscript_key(expr: ast.expr) -> str | None:
    return _string_constant(expr)


def _string_constant(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def test_melt_regime_ast_guard_detects_executable_comparisons_not_strings():
    source = '''
legacy_predicate = "liquid_fraction == 0.0"
comment = """
if liquid_fraction == 0.0:
    pass
"""
if liquid_fraction == 0.0:
    pass
if result.liquid_fraction <= 0:
    pass
if controls["liquid_fraction"] == 0.0:
    pass
if getattr(result, "liquid_fraction") == 0.0:
    pass
if controls.get("liquid_fraction") == 0.0:
    pass
if temperature_C <= solidus_T_C:
    pass
if (float(T_K) - 273.15) > solidus_T_C:
    pass
'''

    violations = _melt_regime_membership_ast_violations_from_source(
        source,
        "sample.py",
    )

    assert len(violations) == 7
    assert all("legacy_predicate" not in violation for violation in violations)


def test_ok_equilibrium_result_constructors_do_not_hardcode_liquid_fraction():
    repo = Path(__file__).parent.parent
    violations = []

    for root in (repo / "simulator", repo / "engines"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func_name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else None
                )
                if func_name != "EquilibriumResult":
                    continue
                keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                status = keywords.get("status")
                if (
                    not isinstance(status, ast.Constant)
                    or status.value != "ok"
                ):
                    continue
                liquid_fraction = keywords.get("liquid_fraction")
                phase_assemblage_available = keywords.get(
                    "phase_assemblage_available"
                )
                vapor_only = (
                    isinstance(liquid_fraction, ast.Constant)
                    and liquid_fraction.value is None
                    and isinstance(phase_assemblage_available, ast.Constant)
                    and phase_assemblage_available.value is False
                )
                hardcoded_fraction = (
                    liquid_fraction is None
                    or (
                        isinstance(liquid_fraction, ast.Constant)
                        and liquid_fraction.value is not None
                    )
                )
                if hardcoded_fraction and not vapor_only:
                    violations.append(
                        f"{path.relative_to(repo)}:{node.lineno}"
                    )

    assert not violations, "\n".join(violations)


def test_data_yaml_survives_latin1_misdecode():
    """data/*.yaml must contain no UTF-8 bytes in the C1 range (0x80-0x9F).

    docs-private/grind/prepare-profiles.sh runs the profile generator under
    LC_ALL=en_US.ISO8859-1 + PYTHONUTF8=0 (alphamelts subprocess workaround);
    characters like em-dash decode to C1 control chars there and YAML
    check_printable rejects the whole file (2026-06-12: 252/252 cells
    excluded). ASCII-or-Latin-1-printable punctuation only in data yamls.
    """
    repo = Path(__file__).parent.parent
    offenders = []
    for path in sorted((repo / "data").glob("*.yaml")):
        raw = path.read_bytes()
        for i, byte in enumerate(raw):
            if 0x80 <= byte <= 0x9F:
                line = raw[:i].count(b"\n") + 1
                offenders.append(f"{path.name}:{line} byte=0x{byte:02X}")
                break
    assert not offenders, (
        "C1-range UTF-8 bytes in data yamls (break ISO8859-1 profile "
        f"generation): {offenders}"
    )
