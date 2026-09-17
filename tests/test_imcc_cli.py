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


def _simulator_imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("simulator"):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("simulator"):
                    found.add(alias.name)
    return found


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


def test_model_half_does_not_import_the_glue():
    """Direction matters: glue -> model is fine, model -> glue would make the
    seam circular and unliftable."""
    for module in ("kernel.py", "adapter.py", "gas.py", "cli.py"):
        assert "simulator.melt_backend.imcc_sf04.backend" not in _simulator_imports(
            _PKG / module
        ), f"{module} imports the glue; the dependency must point the other way"
