"""One owner for backend_status precedence, and one for the crash-point predicate.

These are anti-drift ratchets, not behaviour documentation. The defect they pin
was not a wrong line of code -- it was FOUR copies of the same rule, two of which
had quietly transposed `unavailable` and `out_of_domain`, and one of which had
dropped a clause from a predicate. Nothing failed, because nothing compared the
copies. So the tests compare the copies.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

from simulator.chemistry.kernel.dto import (
    BACKEND_STATUS_PRECEDENCE,
    select_backend_status,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The three tokens whose relative order is the rule under test. A module that
# spells all three in one literal is stating a precedence, and precedence has
# exactly one owner.
RANKED_TOKENS = frozenset(BACKEND_STATUS_PRECEDENCE)

# Sites allowed to spell the ordering. The kernel dto owns it; run_executor and
# melt_effect_adjustment import it. Nothing else may restate it.
PRECEDENCE_OWNER = "simulator/chemistry/kernel/dto.py"

# The crash-point rule and the boolean derived from it. Both are scanned,
# because a copy of either reopens the disagreement this file exists to stop.
CRASH_POINT_RULE_NAMES = frozenset({"carrier_has_crash_point", "crash_point_from_carrier"})

# The two key names ARE the rule's signature. Name-scanning cannot catch a
# renamed copy -- a review proved that by injecting one under another name --
# so the shape is matched instead. Verified against the tree: exactly one
# function mentions both keys, and it is the owner, so this fires on no
# correct code today.
CRASH_POINT_KEYS = frozenset({"out_of_domain_crash_point", "crash_point"})
CRASH_POINT_OWNER = "simulator/optimize/backend_status.py"


def _python_sources() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for root in ("simulator", "engines", "web"):
        base = REPO_ROOT / root
        if base.exists():
            paths.extend(sorted(base.rglob("*.py")))
    return paths


def test_precedence_ordering_has_exactly_one_owner() -> None:
    """No production module may restate the ranked ordering as a literal.

    Counterfactual: before the unification this failed with three extra sites --
    simulator/optimize/evaluate.py, simulator/optimize/objective.py (both on a
    TRANSPOSED order) and simulator/run_executor.py.
    """
    offenders: list[str] = []
    for path in _python_sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == PRECEDENCE_OWNER:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        # Container literals are the OBVIOUS restatement, and they are not the
        # only one: an if/elif chain returning the tokens in order states the
        # same rule with no Tuple in sight. A NOT-FIXED review injected exactly
        # that and this test stayed green.
        #
        # The first attempt at closing that hole flagged any function MENTIONING
        # all three tokens. It fired on five functions of correct code --
        # magemin.equilibrate, vaporock.equilibrate, alphamelts._run_liquidus_finder
        # and a calibration report -- which all SET a status per engine outcome
        # rather than RANKING statuses against each other. A guard that fires on
        # correct work gets deleted, so the net is cast on the ranking SHAPE
        # instead: a token that is both TESTED and RETURNED in the same function
        # is being ranked, not assigned. Setters return a token they never test;
        # membership frozensets test tokens they never return.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            returned = {
                sub.value.value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Return)
                and isinstance(sub.value, ast.Constant)
                and isinstance(sub.value.value, str)
            }
            tested = {
                const.value
                for branch in ast.walk(node)
                if isinstance(branch, ast.If)
                for const in ast.walk(branch.test)
                if isinstance(const, ast.Constant) and isinstance(const.value, str)
            }
            ranked_here = RANKED_TOKENS & returned & tested
            if ranked_here == RANKED_TOKENS:
                offenders.append(f"{rel}:{node.lineno} ({node.name}) [if/elif rank]")

            for sub in ast.walk(node):
                if not isinstance(sub, (ast.Tuple, ast.List)):
                    continue
                values = [
                    element.value
                    for element in sub.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                ]
                if RANKED_TOKENS.issubset(set(values)):
                    offenders.append(f"{rel}:{sub.lineno} ({node.name}) [literal]")

        # module-level container literals, outside any function
        func_lines = {
            ln
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for ln in range(fn.lineno, (fn.end_lineno or fn.lineno) + 1)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List)) or node.lineno in func_lines:
                continue
            values = [
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if RANKED_TOKENS.issubset(set(values)):
                offenders.append(f"{rel}:{node.lineno} [module literal]")
    assert offenders == [], (
        "backend_status precedence is restated outside its owner "
        f"({PRECEDENCE_OWNER}); import BACKEND_STATUS_PRECEDENCE instead: {offenders}"
    )


def test_unavailable_outranks_out_of_domain() -> None:
    """The direction that matters operationally.

    `out_of_domain` is a verdict about the RECIPE -- physically infeasible, so
    the optimizer prunes the candidate permanently. `unavailable` is a verdict
    about the TOOLING -- the engine was missing, so the candidate deserves a
    retry. Ranking them the other way converts a broken install into a physics
    conclusion about the process being designed.
    """
    assert select_backend_status(["out_of_domain", "unavailable"]) == "unavailable"
    assert select_backend_status(["unavailable", "out_of_domain"]) == "unavailable"
    assert BACKEND_STATUS_PRECEDENCE.index("unavailable") < BACKEND_STATUS_PRECEDENCE.index(
        "out_of_domain"
    )


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["ok", "not_converged"], "not_converged"),
        (["ok", "out_of_domain", "not_converged"], "out_of_domain"),
        (["ok", "out_of_domain", "unavailable"], "unavailable"),
        (["ok", "ok"], "ok"),
        ([], None),
        ([None, None], None),
        (["ok", None, "unavailable"], "unavailable"),
    ],
)
def test_selection_ranks_and_ignores_none(statuses, expected) -> None:
    assert select_backend_status(statuses) == expected


def test_selection_is_order_independent() -> None:
    """Ranking must not depend on the order the carriers happened to be walked in."""
    import itertools

    for permutation in itertools.permutations(["ok", "not_converged", "out_of_domain", "unavailable"]):
        assert select_backend_status(list(permutation)) == "unavailable"


def test_crash_point_predicate_has_exactly_one_definition() -> None:
    """Counterfactual: before the fix there were two, and they disagreed."""
    definitions: list[str] = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Scan BOTH names. The first draft of this test scanned only
            # carrier_has_crash_point -- the derived boolean -- and not
            # crash_point_from_carrier, which is the actual rule. A NOT-FIXED
            # review pointed out it was therefore decorative for the thing it
            # most needed to protect.
            if node.name.lstrip("_") in CRASH_POINT_RULE_NAMES:
                definitions.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} ({node.name})"
                )
    assert len(definitions) == len(CRASH_POINT_RULE_NAMES), (
        "the crash-point rule and its derived predicate must have exactly one "
        f"definition each, found: {definitions}"
    )


def test_no_module_restates_the_crash_point_rule_under_another_name() -> None:
    """A renamed copy is still a copy, and the name scan cannot see it.

    `test_crash_point_predicate_has_exactly_one_definition` matches on function
    NAMES, so a NOT-FIXED review evaded it in one line by calling the old
    presence-based loop `_carrier_holds_crash_evidence`. It stayed green. Any
    name-based check has that hole by construction.

    So this matches the SHAPE instead: the pair of key names is the rule's
    signature, and a function that reaches for both of them is deciding what
    counts as crash evidence rather than asking the owner. Verified against the
    tree when written -- exactly one function mentions both keys and it is the
    owner -- so this fires on no correct code.
    """
    offenders: list[str] = []
    for path in _python_sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == CRASH_POINT_OWNER:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {
                sub.value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            }
            if CRASH_POINT_KEYS.issubset(names):
                offenders.append(f"{rel}:{node.lineno} ({node.name})")
    assert offenders == [], (
        "the crash-point rule is restated outside its owner "
        f"({CRASH_POINT_OWNER}); call crash_point_from_carrier instead: {offenders}"
    )


def test_empty_crash_point_is_not_evidence_of_out_of_domain() -> None:
    """An empty placeholder must not synthesise a physics verdict.

    Pinned on BOTH optimizer modules, because the drift was that one of them
    had this and the other did not. `evaluate.py` was covered by
    test_empty_crash_point_placeholder_does_not_mark_backend_out_of_domain;
    `objective.py` was not, which is how it drifted.
    """
    # NB: `from simulator.optimize import evaluate` binds the public evaluate()
    # FUNCTION, which the package re-exports over the submodule of the same
    # name. import_module reaches the module itself.
    evaluate_module = importlib.import_module("simulator.optimize.evaluate")
    objective_module = importlib.import_module("simulator.optimize.objective")

    placeholder = {"backend_status": "ok", "crash_point": {}}
    populated = {"backend_status": "ok", "crash_point": {"temperature_C": 1400.0}}

    for module in (evaluate_module, objective_module):
        assert module._carrier_has_crash_point(placeholder) is False
        assert module._carrier_has_crash_point(populated) is True

    # ★ THE WALKER NO LONGER SYNTHESISES A STATUS FROM CRASH CONTENT, and this
    # assertion was deliberately changed when that moved. It previously read
    #     assert "out_of_domain" in _backend_statuses_from_carrier(populated)
    # which pinned the walker as the thing that turns crash evidence into a
    # verdict. That is precisely what made the optimizer's gate dead code: the
    # gate asks "may crash evidence promote this status?" of a status the
    # walker had ALREADY promoted, so its first clause fired and the gate never
    # ran. Only `unavailable` escaped, and only because it outranks the
    # synthesised token.
    #
    # The walker now reports what the engine SAID. Whether crash evidence
    # amounts to an out-of-domain verdict is decided in one gated place. So the
    # populated carrier must NOT produce a status here -- the predicate above
    # is what still sees it.
    for module in (evaluate_module, objective_module):
        assert "out_of_domain" not in module._backend_statuses_from_carrier(placeholder)
        assert "out_of_domain" not in module._backend_statuses_from_carrier(populated)
        assert module._backend_statuses_from_carrier(populated) == ("ok",)


def test_both_optimizer_modules_share_the_owner_objects() -> None:
    """Identity, not equality -- two equal copies are exactly what drifted."""
    # NB: `from simulator.optimize import evaluate` binds the public evaluate()
    # FUNCTION, which the package re-exports over the submodule of the same
    # name. import_module reaches the module itself.
    evaluate_module = importlib.import_module("simulator.optimize.evaluate")
    objective_module = importlib.import_module("simulator.optimize.objective")

    for name in (
        "_carrier_has_crash_point",
        "_backend_statuses_from_carrier",
        "_latest_backend_status_from_sequence",
        "_latest_backend_status",
        "_select_backend_status",
    ):
        assert getattr(evaluate_module, name) is getattr(objective_module, name), (
            f"{name} is not shared between evaluate and objective"
        )


def test_runner_aggregation_agrees_with_the_owner() -> None:
    """The runner assembles candidates; the owner ranks them. Same answer."""
    from simulator.run_executor import _aggregate_backend_status

    cases = [
        (["out_of_domain"], "unavailable"),
        (["unavailable"], "out_of_domain"),
        (["ok", "not_converged"], "ok"),
        ([], "ok"),
    ]
    for history, latest in cases:
        assert _aggregate_backend_status(history, latest) == select_backend_status(
            [*history, latest]
        )


def test_runner_aggregation_survives_a_non_iterable_history() -> None:
    from simulator.run_executor import _aggregate_backend_status

    assert _aggregate_backend_status(7, "ok") == "ok"


def test_runner_does_not_overrule_the_owner_with_a_typed_failure() -> None:
    """A typed failure is a CANDIDATE for the ranking, not a replacement for it.

    Both runner call sites used to compute the owner's answer and then discard
    it whenever a typed exception carried a status:

        backend_status = _aggregate_backend_status(history, latest)
        if honest is not None:
            backend_status = honest

    so a run whose history said `unavailable` reported `out_of_domain` after a
    domain-typed exception and `not_converged` after a timeout -- engine absence
    becoming a permanent physics claim about the recipe, which is the exact
    transposition the owner's derivation says must not happen.
    """
    from simulator.run_executor import _aggregate_backend_status

    # the defect: absence must survive a typed failure of lower precedence
    assert _aggregate_backend_status(
        ["unavailable"], "ok", exception_status="out_of_domain"
    ) == "unavailable"
    assert _aggregate_backend_status(
        ["unavailable"], "ok", exception_status="not_converged"
    ) == "unavailable"

    # ...without muting typed failures over an unremarkable history
    assert _aggregate_backend_status(
        ["ok"], "ok", exception_status="not_converged"
    ) == "not_converged"

    # ...and an UNRANKED token still wins, via the owner's last-value fallback.
    # This is why the candidate is appended last rather than first.
    assert _aggregate_backend_status(["ok"], "ok", exception_status="refused") == "refused"

    # no typed failure at all changes nothing
    assert _aggregate_backend_status(["ok"], "ok", exception_status=None) == "ok"


def test_no_call_site_overrides_the_aggregate_after_selecting_it() -> None:
    """Neither runner call site may assign over the owner's answer.

    ★ THIS IS STRUCTURAL BECAUSE THE BEHAVIOURAL TEST CANNOT REACH BOTH SITES.
    `run_executor` aggregates in two places -- the primary path and the
    degraded-envelope path -- and a bare failing session only exercises the
    second. Verified the hard way: reverting the PRIMARY site to its old
    post-selection override left every test green, including the behavioural
    b-264 test in test_run_executor.py, which only went red when the
    DEGRADED-ENVELOPE site was reverted. So one of the two sites had no
    behavioural coverage at all and a regression there would have landed
    silently.

    Rather than hunt for a fixture that reaches the primary path, the shape is
    matched. The predicate had to be narrowed once: a first version flagged any
    assignment to `backend_status` whose value tree CONTAINED a call to
    `_backend_status_from_honest_exception`, and that fired on the FIXED code,
    because the fix passes exactly that call as a keyword argument inside the
    aggregating call. A guard that flags the correct shape is worse than no
    guard.

    So it inspects the assigned value DIRECTLY rather than walking it: a bare
    rebind (`backend_status = honest_backend_status`) or an assignment straight
    from the honest-exception call is an override. The same call nested as an
    ARGUMENT is the fix, and is left alone.
    """
    source = (REPO_ROOT / "simulator" / "run_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if "backend_status" not in targets:
            continue
        value = node.value
        # `backend_status = _backend_status_from_honest_exception(...)` -- the
        # assigned value IS the override. Nested-as-an-argument is the fix.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_backend_status_from_honest_exception"
        ):
            offenders.append(f"run_executor.py:{node.lineno} (direct override)")
        # `backend_status = honest_backend_status` -- the original shape
        if isinstance(value, ast.Name) and value.id.endswith("backend_status"):
            offenders.append(f"run_executor.py:{node.lineno} (rebind {value.id})")

    assert offenders == [], (
        "a runner call site assigns over the aggregated backend_status; the "
        f"typed failure must be passed INTO the selection instead: {offenders}"
    )


def test_the_operator_facing_view_agrees_with_the_engine() -> None:
    """The web badge must not issue a clean bill on evidence the engine aborts on.

    ★ THE FOURTH CONSUMER, and the one that showed the census was the wrong
    question. `web/routes._latest_backend_status` was an independent resolver:
    it returned the FIRST token it found, applied no precedence, and walked a
    different carrier set -- it never looked at `backend_diagnostics`, which the
    optimizer's walker does, while it did look at `result_blob`, which the
    optimizer's does not. Neither set contained the other.

    So one body of evidence produced three answers: the runner aborted the
    study, the optimizer pruned the candidate, and this view -- which feeds the
    operator's backend badge AND `certification_allowed` -- reported `ok`.

    Note what that means for the anti-drift guards above: they hunt for
    RESTATEMENTS of the rule, and this function restated nothing. It ignored
    the rule. A divergent ANSWER needs no divergent COPY, so "how many places
    restate this rule" is not the same question as "how many places answer it".
    That is why this test is behavioural rather than another AST sweep.
    """
    web_routes = importlib.import_module("web.routes")

    # ★ EXPLICIT EXPECTATIONS, NOT AGREEMENT WITH A HELPER THIS FIX ADDED.
    # A first draft compared web's answer against web's OWN new candidate
    # helper. Against the pre-fix module that test died on AttributeError --
    # red, but for the wrong reason: it would have passed for anyone who
    # reverted the behaviour while keeping the helper. The expected values are
    # written out instead, so the test fails on the DEFECT and not on a missing
    # symbol.
    cases = [
        # the reproduction: the disagreeing token sits where only the owner looks.
        # Pre-fix this returned 'ok' -- a clean bill on evidence the runner
        # aborts the study on and the optimizer prunes the candidate on.
        (
            {
                "per_hour": [{"backend_status": "ok"}],
                "backend_diagnostics": {"backend_status": "unavailable"},
            },
            "unavailable",
        ),
        # ranking, not order-of-arrival
        (
            {"per_hour": [{"backend_status": "unavailable"}, {"backend_status": "ok"}]},
            "unavailable",
        ),
        # a carrier key only the web side knows about must still be ranked
        (
            {"result_blob": {"backend_status": "unavailable"}, "backend_status": "ok"},
            "unavailable",
        ),
        # the ordinary healthy case is unchanged
        ({"per_hour": [{"backend_status": "ok"}]}, "ok"),
    ]

    for carrier, expected in cases:
        assert web_routes._latest_backend_status(carrier) == expected, carrier
