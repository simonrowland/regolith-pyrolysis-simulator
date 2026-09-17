"""IMCC-SF04 CLI and package-split tests.

Two things are pinned here:

1. The CLI contract -- exit 0 solved, exit 2 typed refusal, exit 1 usage error.
   The refusal code matters: a caller scripting against this needs to tell
   "the model declined" apart from "the invocation was wrong".
2. The MODEL/GLUE split itself. ``adapter.py`` + ``kernel.py`` + ``gas.py`` +
   ``cli.py`` must not import simulator policy. That is the property that makes
   the package extractable, and it is invisible to every other test -- nothing
   fails if someone re-adds a ``simulator.backend_names`` import to the model
   half, it just quietly re-welds the seam.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from simulator.melt_backend.imcc_sf04 import cli

PACK = Path("data/melt_activity/imcc/imcc-sf04-v1.0.2.json")

BASALT = [
    "--oxide", "SiO2=45.4",
    "--oxide", "MgO=8.1",
    "--oxide", "FeO=10.9",
    "--oxide", "CaO=11.4",
    "--oxide", "Al2O3=14.2",
    "--oxide", "TiO2=3.2",
    "--oxide", "Na2O=0.4",
    "--oxide", "K2O=0.1",
]

# The published pack declares [1700, 3000] K. 900 K is far outside it and no
# --allow-extrapolation is passed, so the model must refuse rather than answer.
T_IN_DOMAIN = "1800"
T_OUT_OF_DOMAIN = "900"


def _solve_argv(temperature: str, *extra: str) -> list[str]:
    return [
        "solve",
        "--pack", str(PACK),
        "--temperature", temperature,
        "--basis-type", "wt",
        *BASALT,
        *extra,
    ]


def test_describe_reports_the_pack_identity(capsys):
    assert cli.main(["describe", "--pack", str(PACK)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "IMCC-SF04" in out
    assert "1.0.2" in out


def test_describe_collapses_the_repetitive_per_row_domain_basis(capsys):
    """domain_basis is one long string PER REACTION; printing it verbatim is a
    screenful of duplicates. The published pack has 38 rows and 2 distinct
    values, so the summary must be far shorter than the raw join."""
    assert cli.main(["describe", "--pack", str(PACK)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "distinct over" in out
    # The raw join would repeat the long ADOPTED string ~34 times.
    assert out.count("sf04-exercised-ADOPTED") <= 2


def test_solve_in_domain_exits_ok_and_emits_activities(capsys):
    assert cli.main(_solve_argv(T_IN_DOMAIN, "--json")) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["temperature_K"] == pytest.approx(1800.0)
    assert payload["parent_oxides"][0] == "SiO2"
    # One activity/gamma per parent oxide, all finite and non-negative.
    n = len(payload["parent_oxides"])
    assert len(payload["parent_activity"]) == n
    assert len(payload["parent_gamma"]) == n
    assert all(a >= 0.0 for a in payload["parent_activity"])
    assert payload["D"] > 1.0  # an associated solution, not ideal mixing


def test_out_of_domain_is_a_typed_refusal_not_a_crash(capsys):
    """Exit 2 with a machine-readable code. A refusal is a RESULT."""
    assert cli.main(_solve_argv(T_OUT_OF_DOMAIN, "--json")) == cli.EXIT_REFUSED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused"
    assert payload["code"] == "imcc_T_outside_datapack_domain"
    assert "900" in payload["reason"]


def test_extrapolation_flag_turns_the_refusal_into_a_flagged_answer(capsys):
    """The same point answers when extrapolation is explicitly allowed, and the
    answer carries the flag. Silent extrapolation would be the real defect."""
    assert cli.main(_solve_argv(T_OUT_OF_DOMAIN, "--allow-extrapolation", "--json")) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["extrapolated"] is True


def test_usage_errors_exit_one_not_two(capsys):
    """Exit 1 is 'you called it wrong'; exit 2 is reserved for the model
    declining. Collapsing them would make the CLI unscriptable."""
    assert cli.main(["solve", "--pack", str(PACK), "--temperature", "1800"]) == cli.EXIT_USAGE
    assert "no composition given" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ("SiO2", "NAME=VALUE"),
        ("=45", "empty name"),
        ("SiO2=lots", "not numeric"),
    ],
)
def test_oxide_parsing_refuses_malformed_pairs(bad, fragment):
    with pytest.raises(ValueError) as excinfo:
        cli._parse_oxides([bad])
    assert fragment in str(excinfo.value)


def test_oxide_parsing_refuses_a_repeated_oxide():
    """A repeated --oxide is ambiguous; last-wins would silently discard input."""
    with pytest.raises(ValueError, match="more than once"):
        cli._parse_oxides(["SiO2=40", "SiO2=50"])


# The package these modules live in, used to resolve relative imports. A
# relative import records only the tail -- `from ...accounting.formulas import X`
# has node.module == "accounting.formulas" -- so a scanner that tests
# `startswith("simulator")` on the raw value sees nothing at all. Resolving
# against the package first is what makes the relative and absolute spellings
# compare equal, which is the whole point of these guards.
_PKG_PARTS = ("simulator", "melt_backend", "imcc_sf04")


def _resolve(module: str | None, level: int) -> str:
    """Absolute dotted name for one import, relative or not.

    level 0 is already absolute. level 1 is this package, level 2 its parent,
    and so on -- the same arithmetic importlib does:
        from .backend        (level 1) -> simulator.melt_backend.imcc_sf04.backend
        from ...accounting.x (level 3) -> simulator.accounting.x
    """
    if level == 0:
        return module or ""
    base = ".".join(_PKG_PARTS[: len(_PKG_PARTS) - (level - 1)])
    return f"{base}.{module}" if module else base


def _import_statements(tree: ast.AST, *, import_time_only: bool):
    """Yield import nodes, optionally only those that RUN on import.

    When import_time_only, descends through every statement container except
    function bodies. A module-level `try: import x except ImportError:` or a
    `class C: import x` executes the moment the module loads, so excluding them
    -- as a plain `tree.body` scan does -- leaves the guard blind in exactly the
    place someone writes a graceful-degradation import.
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif import_time_only and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue  # a function body runs when called, not on import
        else:
            yield from _import_statements(node, import_time_only=import_time_only)


def _imported_modules(module_path: Path, *, import_time_only: bool = False) -> set[str]:
    """Resolved absolute module names imported by this file."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in _import_statements(tree, import_time_only=import_time_only):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve(node.module, node.level)
            if resolved.startswith("simulator"):
                found.add(resolved)
        else:
            for alias in node.names:
                if alias.name.startswith("simulator"):
                    found.add(alias.name)
    return found


def _simulator_imports(module_path: Path) -> set[str]:
    """Every simulator module this file imports, however deeply nested."""
    return _imported_modules(module_path)


# The model half may depend on its own package and on scalar_boundary -- a
# 30-line stdlib-only leaf that travels with the package on extraction.
_MODEL_ALLOWED = {
    "simulator.melt_backend.imcc_sf04",
    "simulator.melt_backend.imcc_sf04.adapter",
    "simulator.melt_backend.imcc_sf04.kernel",
    "simulator.melt_backend.imcc_sf04.gas",
    "simulator.scalar_boundary",
}

_PKG = Path("simulator/melt_backend/imcc_sf04")


@pytest.mark.parametrize("module", ["kernel.py", "adapter.py", "gas.py", "cli.py"])
def test_model_half_stays_free_of_simulator_policy(module):
    """The extraction seam. If this fails, someone re-welded the model half to
    simulator policy (backend naming, fidelity vocabulary, MeltBackend) and the
    package is no longer liftable without dragging that in."""
    offending = sorted(_simulator_imports(_PKG / module) - _MODEL_ALLOWED)
    assert not offending, (
        f"{module} imports simulator policy: {offending}. Policy belongs in "
        "backend.py; the model half must stay extractable."
    )


def test_glue_half_is_where_the_policy_lives():
    """The complement: backend.py SHOULD carry the policy imports. Without this
    the test above could be satisfied by deleting the glue entirely."""
    imports = _simulator_imports(_PKG / "backend.py")
    assert "simulator.melt_backend.base" in imports
    assert "simulator.backend_names" in imports


_GLUE_ABS = "simulator.melt_backend.imcc_sf04.backend"
_PKG_ABS = "simulator.melt_backend.imcc_sf04"


def _glue_reaches(module_path: Path) -> list[str]:
    """Every way a model module could reach the glue, relative forms included.

    Relative imports are resolved against the package before comparing, so
    `from .backend import X`, `from ..imcc_sf04.backend import X` and the
    absolute spelling all collapse to the same name. A scanner that compared
    the raw `node.module` string saw nothing for any multi-level relative form
    -- the seam could re-weld with the guard still green.

    `from <pkg> import backend` binds a SUBMODULE rather than a name, so each
    alias is also tested as a potential submodule of the imported package.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    reaches: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _GLUE_ABS:
                    reaches.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(node.module, node.level)
            dots = "." * node.level
            if resolved == _GLUE_ABS:
                reaches.append(f"from {dots}{node.module or ''} import ...")
                continue
            for alias in node.names:
                if f"{resolved}.{alias.name}" == _GLUE_ABS:
                    reaches.append(f"from {dots}{node.module or ''} import {alias.name}")
    return reaches


@pytest.mark.parametrize(
    "module", ["kernel.py", "adapter.py", "gas.py", "cli.py", "bench.py"]
)
def test_model_half_does_not_import_the_glue(module):
    """Direction matters: glue -> model is fine, model -> glue would make the
    seam circular and unliftable."""
    reaches = _glue_reaches(_PKG / module)
    assert not reaches, (
        f"{module} imports the glue ({reaches}); the dependency must point the "
        "other way or the package cannot be lifted out."
    )


def test_the_glue_direction_check_catches_relative_and_facade_imports(tmp_path):
    """Pin the detector itself.

    The previous check passed on three of these four, so without this test a
    later simplification back to a substring match would look green.
    """
    shapes = [
        f"import {_GLUE_ABS}\n",
        f"from {_GLUE_ABS} import ImccSf04Backend\n",
        "from .backend import ImccSf04Backend\n",
        "from . import backend\n",
        f"from {_PKG_ABS} import backend\n",
        # Multi-level relatives. These resolve to the same module and were
        # invisible to the pre-resolver scanner, which compared the raw
        # node.module ("imcc_sf04.backend") against the absolute name.
        "from ..imcc_sf04.backend import ImccSf04Backend\n",
        "from ...melt_backend.imcc_sf04.backend import ImccSf04Backend\n",
        "from ..imcc_sf04 import backend\n",
    ]
    for source in shapes:
        probe = tmp_path / "probe.py"
        probe.write_text(source, encoding="utf-8")
        assert _glue_reaches(probe), f"detector missed: {source.strip()!r}"

    # ...and does not fire on the legitimate direction or a lookalike name.
    for benign in (
        "from .kernel import solve\n",
        "from . import kernel\n",
        "from simulator.melt_backend.base import MeltBackend\n",
        "import backend_utils\n",
    ):
        probe = tmp_path / "benign.py"
        probe.write_text(benign, encoding="utf-8")
        assert not _glue_reaches(probe), f"false positive on: {benign.strip()!r}"


# --- bench.py: the seam has a second tier ------------------------------------
#
# bench.py (the empirical residual runner) does not fit the flat rule above, and
# forcing it to would be the wrong fix. Its activity / activity_coefficient path
# is pure model; only the `partial_pressure` observable reaches for the
# simulator's shared analytical gas layer, and it does so through a DEFERRED
# import inside a branch that already turns any exception -- ImportError included
# -- into a typed refusal. So the package still lifts: a standalone checkout
# scores activities and refuses gas points, which is the honest outcome.
#
# That makes the guard two-tier. Module level must stay clean, because that is
# what decides whether the module imports at all without simulator installed;
# deferred reaches are a closed list, because a third one added later would very
# likely NOT be refusal-guarded and nothing else would notice.
# The behavioural half of this contract -- actually blocking those modules and
# checking the runner degrades instead of crashing -- lives in
# tests/test_imcc_bench.py::test_bench_still_runs_without_the_simulator_gas_layer.
_BENCH_DEFERRED_ALLOWED = {
    # Formula parsing for the single-cation basis conversion. Reached only from
    # _single_cation_gas_activities, which is called only from the gas branch.
    "simulator.accounting.formulas",
    # The shared analytical gas layer. Reusing it is deliberate rather than lazy:
    # re-deriving the basis conversion here is exactly how a residual goes
    # silently wrong while still looking plausible.
    "simulator.diagnostic_helpers.alphamelts_volatility",
}


def _module_level_simulator_imports(module_path: Path) -> set[str]:
    """Imports that execute when the module loads.

    Only function bodies are exempt: those run when called. Everything else at
    module scope -- ``try``/``except``, ``if``, ``with``, a class body -- runs on
    import and can therefore stop an extracted checkout from loading at all.
    """
    return _imported_modules(module_path, import_time_only=True)


def test_bench_imports_no_simulator_policy_at_module_level():
    """bench.py must still IMPORT in a checkout with no simulator policy."""
    offending = sorted(_module_level_simulator_imports(_PKG / "bench.py") - _MODEL_ALLOWED)
    assert not offending, (
        f"bench.py imports simulator policy at module level: {offending}. "
        "That makes the package unimportable once extracted; if the gas layer "
        "is genuinely needed, defer the import into the refusal-guarded branch."
    )


def test_bench_deferred_simulator_reaches_are_a_closed_list():
    """A new deferred reach is almost certainly NOT refusal-guarded.

    Widening this set is a real decision -- it means proving the new call site
    degrades to a typed refusal when simulator policy is absent -- so it should
    cost a deliberate edit here rather than passing silently.
    """
    all_imports = _simulator_imports(_PKG / "bench.py")
    deferred = all_imports - _module_level_simulator_imports(_PKG / "bench.py")
    unexpected = sorted(deferred - _BENCH_DEFERRED_ALLOWED - _MODEL_ALLOWED)
    assert not unexpected, (
        f"bench.py gained undeclared deferred simulator reaches: {unexpected}. "
        "Each must sit inside a branch that converts ImportError into a typed "
        "refusal, then be listed in _BENCH_DEFERRED_ALLOWED with why."
    )


# --- exit-code contract: argparse must not squat on the refusal code ---------

# An alkali fraction above the pack's 0.5 bound; refused unless explicitly
# allowed. See tests/test_imcc_adapter.py::test_allow_out_of_envelope_labels_result.
_OUT_OF_ENVELOPE = ["--oxide", "Na2O=70.0", "--oxide", "SiO2=30.0"]


@pytest.mark.parametrize(
    "argv, why",
    [
        (["solve"], "required --pack/--temperature missing"),
        (["solve", "--nonesuch"], "unknown flag"),
        ([], "no subcommand"),
        (["solve", "--pack", str(PACK), "--temperature", "notanumber"], "bad type"),
    ],
)
def test_argparse_usage_errors_do_not_return_the_refusal_code(argv, why):
    """argparse exits 2 by default -- the same code this CLI reserves for a
    typed refusal. Unmapped, a caller scripting `rc == 2` would read "you
    called it wrong" as "the model declined", and main() would raise
    SystemExit instead of returning at all."""
    assert cli.main(argv) == cli.EXIT_USAGE, why


def test_help_still_exits_zero():
    """The remap above must not swallow --help's successful exit."""
    assert cli.main(["--help"]) == cli.EXIT_OK


def test_json_is_accepted_before_or_after_the_subcommand(capsys):
    """The parent-parser + SUPPRESS pattern is load-bearing: dropping SUPPRESS
    would let the subparser default clobber a leading --json, and every other
    test here passes --json trailing so nothing would notice."""
    argv = ["--pack", str(PACK), "--temperature", T_IN_DOMAIN, "--basis-type", "wt", *BASALT]
    assert cli.main(["--json", "solve", *argv]) == cli.EXIT_OK
    leading = json.loads(capsys.readouterr().out)
    assert cli.main(["solve", *argv, "--json"]) == cli.EXIT_OK
    trailing = json.loads(capsys.readouterr().out)
    assert leading == trailing


def test_out_of_envelope_refuses_then_answers_with_the_flag_visible(capsys):
    """Composition envelope, the sibling of the temperature-domain case above.

    The flag half is the point: the mandate's posture is predict AND flag, so
    an allowed out-of-envelope answer must carry its notice. Text mode was
    printing a clean-looking result with nothing to distinguish it.
    """
    argv = ["solve", "--pack", str(PACK), "--temperature", T_IN_DOMAIN,
            "--basis-type", "wt", *_OUT_OF_ENVELOPE]

    assert cli.main([*argv, "--json"]) == cli.EXIT_REFUSED
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "imcc_composition_outside_validated_envelope"

    assert cli.main([*argv, "--allow-out-of-envelope", "--json"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["labels"]["envelope_status"] == "outside_validated"

    assert cli.main([*argv, "--allow-out-of-envelope"]) == cli.EXIT_OK
    assert "OUTSIDE VALIDATED COMPOSITION ENVELOPE" in capsys.readouterr().out


def test_import_time_scanner_sees_every_construct_that_runs_on_import(tmp_path):
    """Pin the import-time predicate itself.

    A scan of ``tree.body`` alone misses all of these, and they are not exotic
    -- a module-level ``try: import x except ImportError:`` is the standard way
    someone writes an optional dependency, and it is exactly the construct that
    would re-weld the seam while reading as defensive coding. It runs on import,
    so in an extracted checkout it raises before the module finishes loading.

    The function-body case is the control: if it were also reported, the
    scanner would be indistinguishable from the full walk and the two-tier
    guard would collapse into one tier.
    """
    target = "simulator.accounting.formulas"
    runs_on_import = {
        "bare": f"from {target} import f\n",
        "try/except": f"try:\n    from {target} import f\nexcept ImportError:\n    f = None\n",
        "if": f"if True:\n    from {target} import f\n",
        "with": f"import contextlib\nwith contextlib.suppress(ImportError):\n    from {target} import f\n",
        "class body": f"class C:\n    from {target} import f\n",
        "nested": f"try:\n    if True:\n        from {target} import f\nexcept ImportError:\n    pass\n",
        # Multi-level relative: resolves to the same module, and the raw
        # node.module is "accounting.formulas", which no startswith("simulator")
        # test would ever match.
        "relative": "from ...accounting.formulas import f\n",
    }
    for label, source in runs_on_import.items():
        probe = tmp_path / "probe.py"
        probe.write_text(source, encoding="utf-8")
        assert target in _module_level_simulator_imports(probe), (
            f"{label!r} executes on import but the scanner did not see it"
        )

    deferred_only = {
        "function body": f"def go():\n    from {target} import f\n    return f\n",
        "method body": f"class C:\n    def go(self):\n        from {target} import f\n",
    }
    for label, source in deferred_only.items():
        probe = tmp_path / "probe.py"
        probe.write_text(source, encoding="utf-8")
        assert target not in _module_level_simulator_imports(probe), (
            f"{label!r} runs when called, not on import; reporting it collapses "
            "the two-tier guard into one"
        )
        assert target in _simulator_imports(probe), (
            f"{label!r} must still be visible to the full walk as a deferred reach"
        )


def test_version_exits_zero_and_does_not_invent_a_number(capsys):
    """--version shares the SystemExit(0) path with --help, so the remap must
    pass it through. The string matters too: running from a checkout there is
    no installed distribution, and a fabricated version on a scientific result
    is worse than an honest unknown because it looks reproducible."""
    assert cli.main(["--version"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "imcc" in out
    assert cli._resolve_version() in out


def test_help_documents_the_exit_code_contract(capsys):
    """The codes are the reason to script this at all. If they live only in the
    module docstring, a caller has to read source to find them."""
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "exit codes:" in out
    assert "typed refusal" in out
    assert "--oxide SiO2=" in out, "the repeated --oxide form needs an example"
