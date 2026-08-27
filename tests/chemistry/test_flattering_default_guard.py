"""No default may manufacture confidence out of silence.

SIBLING to ``test_provider_status_vocabulary_guard.py``, not a replacement.
That one forbids a provider re-deriving the kernel status vocabulary as a local
ALLOWLIST. This one forbids a different defect with a different shape: supplying
a *flattering* fallback when a status, grade, confidence or tier is absent.

THE AXIS, AND WHY IT IS THE DATA STRUCTURE RATHER THAN A RULE
-------------------------------------------------------------
Does the default **degrade** or **flatter**?

    degrading   unknown · missing · proxy · low · estimate · unavailable
    flattering  ok · available · sourced · valid · authoritative · complete

A degrading default under-claims: a reader who sees ``"unknown"`` knows less
confidence is warranted, and downstream code that gates on quality will gate
correctly. A flattering default manufactures confidence the code never earned —
silence becomes an affirmative claim, and every consumer downstream inherits it.

The sibling guard's first version got this wrong in an instructive way. It
forbade *any* vocabulary token as a default, which is a **proxy for the property
rather than the property**. Applied at repository scope that rule would have
flagged eleven honest degrading sites against three real ones — inverted
signal-to-noise, and a guard that fires mostly false is one somebody disables.
The author hit it personally: the guard rejected their own honest
``getattr(x, "status", "unavailable")`` fix, and they changed the *code* to
satisfy the *guard*, reading the over-fire as rigour.

Encoding the axis as a **denylist of flattering tokens** removes that failure
mode structurally. ``"unavailable"`` cannot be flagged because it is simply not
in the set. There is no discipline to maintain and no judgement to exercise at
the callsite — the data structure is the rule.

WHY A SIBLING AND NOT A WIDER VERSION OF THE OTHER GUARD
--------------------------------------------------------
The two defects have different *shapes*, not merely different scopes:

    allowlist   status if status in (...) else "ok"     <- sibling guard
    flattering  x.get("status", "ok") / x or "ok"       <- this guard

A vocabulary-membership widen of the allowlist guard would miss the confirmed
``results_store`` and ``alphamelts_volatility`` sites. Keeping them separate lets
each say one true thing precisely.

(An earlier draft of this paragraph cited "condensation" as confirmed and named
``engines/alphamelts/parser.py``. Both were wrong and a review caught them:
condensation is LATENT per the register below, and the parser is CLEAN -- it
degrades to ``'unavailable'``, having been fixed in dd35551a. The correction is
recorded rather than silently applied, because a register that mis-labels its
own examples is how an UNTRIAGED entry gets cited as a bug.)

SCOPE: ``simulator/``, ``web/`` and ``engines/`` — a repository-wide sweep, not
the provider directory. The confirmed sites live outside ``engines/`` entirely.

PROVENANCE: the flattering/degrading inventory comes from a reachability sweep
(``b255-flattering-defaults``) that proved each site rather than grepping it.
Two of four hand-picked candidates did not survive that check, which is the
reason this file trusts the classification and not the grep.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SWEPT_ROOTS = ("simulator", "web", "engines")

# ---------------------------------------------------------------------------
# The axis, as data.
# ---------------------------------------------------------------------------

# Tokens that assert an outcome, a provenance or a confidence the code has not
# established. A default drawn from this set turns "we were not told" into "it
# is fine".
FLATTERING_TOKENS = frozenset({
    "ok",
    "available",
    "sourced",
    "valid",
    "authoritative",
    "complete",
    "verified",
    "measured",
    "confirmed",
    "high",
})

# Recorded ONLY so the negative tests below can assert they are permitted.
# Nothing reads this to decide whether to flag; the denylist above is the
# sole authority. Drawn from real sites in the swept tree.
DEGRADING_TOKENS_SEEN_IN_TREE = frozenset({
    "unknown",
    "missing",
    "proxy",
    "low",
    "estimate",
    "unavailable",
    "not_converged",
    "out_of_domain",
    "missing_mass_balance_trace",
})

# Keys whose value is a quality claim. Wider than "status": the swept tree
# defaults `confidence` to "low" and `tier` to "unknown", so a status-only
# predicate would miss the flattering forms of those.
_QUALITY_KEY_HINTS = (
    "status",
    "grade",
    "confidence",
    "tier",
    "provenance",
    "evidence",
    "authority",
)


def _is_quality_key(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _QUALITY_KEY_HINTS)


def _flattering_default(node: ast.AST) -> tuple[str, str, str] | None:
    """Return (quality_key, flattering_token, spelling) for this node, if any.

    The IDENTITY rather than a line number. Line numbers churn on every edit
    above a site and would make the register a format-change alarm; the identity
    is stable across unrelated edits and still distinguishes one violation from
    another within a file.
    """
    # x.get("status", "ok")
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "get" and len(node.args) == 2:
            key, default = node.args
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _is_quality_key(key.value)
                and isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and default.value in FLATTERING_TOKENS
            ):
                return (key.value, default.value, "get")
        # getattr(x, "status", "ok")
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) == 3:
            _, attr, default = node.args
            if (
                isinstance(attr, ast.Constant)
                and isinstance(attr.value, str)
                and _is_quality_key(attr.value)
                and isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and default.value in FLATTERING_TOKENS
            ):
                return (attr.value, default.value, "getattr")

    # x.get("status") or "ok"  /  value or "ok"
    #
    # The or-fallback spelling. Included because it is the form the .get and
    # getattr checks are blind to, and the correction that named it arrived
    # only after the first inventory -- the shapes a guard misses are the ones
    # nobody thought to write down.
    #
    # ★ IT IS STRICTLY BROADER THAN THE DEFAULT-ARGUMENT FORM, which matters
    # when triaging a flagged site rather than when detecting it.
    #     x.get("status", "ok")     fires only on ABSENCE of the key
    #     x.get("status") or "ok"   fires on absence AND on "" AND on 0
    # So an engine that legitimately reports an empty status, or a numeric 0,
    # gets promoted to "ok" by the or-form where the default form would have
    # passed the real value through. A site in this spelling therefore deserves
    # a higher triage priority than the same token in the default spelling.
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        tail = node.values[-1]
        if (
            isinstance(tail, ast.Constant)
            and isinstance(tail.value, str)
            and tail.value in FLATTERING_TOKENS
        ):
            for earlier in node.values[:-1]:
                key = _quality_key_mentioned(earlier)
                if key is not None:
                    return (key, tail.value, "or")
    return None


def _quality_key_mentioned(node: ast.AST) -> str | None:
    """The quality-ish key this subexpression reads, if any."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if _is_quality_key(sub.value):
                return sub.value
        if isinstance(sub, ast.Attribute) and _is_quality_key(sub.attr):
            return sub.attr
        if isinstance(sub, ast.Name) and _is_quality_key(sub.id):
            return sub.id
    return None


def flattering_defaults(tree: ast.AST) -> Counter[tuple[str, str, str]]:
    """Multiset of violation IDENTITIES.

    A Counter, not a set: a file carrying two identical violations (same key,
    same token, same spelling) on different lines must count as two, or fixing
    one would free a slot the other could silently occupy -- the fungibility
    hole this design exists to close.
    """
    hits: Counter[tuple[str, str, str]] = Counter()
    for node in ast.walk(tree):
        identity = _flattering_default(node)
        if identity is not None:
            hits[identity] += 1
    return hits


# ---------------------------------------------------------------------------
# BASELINE — a debt register, not an exemption list.
# ---------------------------------------------------------------------------
#
# The sweep found 27 sites across 16 files already in the tree. A guard that
# simply failed all of them would be reverted on the first CI run and would
# have protected nothing. So this guard is a RATCHET: the baseline records what
# exists as of 2026-08-26, and the assertion is that no file may EXCEED its
# recorded count. New flattering defaults are blocked; existing ones are tracked
# debt.
#
# THE BASELINE MUST ONLY EVER SHRINK. Raising a number to make a red go away
# converts this file from a guard into a rubber stamp -- which is precisely the
# failure the sibling guard's history warns about. If a legitimate change needs
# a higher count, the right move is to fix the site instead.
#
# It keys on the violation IDENTITY -- (quality_key, flattering_token, spelling)
# -- and NOT on a line number or a bare count.
#
# Line numbers churn on every edit above a site, which would make the guard a
# format-change alarm and therefore a nuisance. A bare per-file COUNT has a
# worse problem: it makes the allowance FUNGIBLE WITHIN THE FILE. Fix the site
# on line 47 of a file baselined at 2 and a brand-new violation on line 900
# slides into the freed slot silently.
#
# An earlier draft closed that by also failing on UNDER-count. A review pointed
# out that this reintroduced, on the COUNT axis, exactly the failure this file's
# docstring argues against on the TOKEN axis: every legitimate fix goes red
# until someone edits the register, so the guard fires on correct work and
# earns itself a deletion. It also made one shared register the merge-conflict
# point for two controllers on different branches.
#
# Identity keying dissolves that tension. Fixing line 47 removes ITS identity;
# line 900's new violation is red on its own merits with no freed allowance. A
# shrunken baseline is simply a subset, so improvement is never red.
#
# It is a Counter and not a set because a file may carry two IDENTICAL
# violations (same key, same token, same spelling) on different lines --
# simulator/optimize/evaluate.py carries three. Under a set, fixing one would
# free a slot the others could occupy, reopening the fungibility hole.
#
# ★ KNOWN REACHABILITY (b255-flattering-defaults, reachability proved per site):
#   CONFIRMED  simulator/optimize/results_store.py -- BOTH halves of one
#              self-contradiction: status defaults to "available" AND
#              output_status to "authoritative" while authoritative=False, on a
#              legacy coating payload ALREADY COMMITTED to the repo. Owned by
#              regolith-main (b-254 lane).
#   CONFIRMED  simulator/diagnostic_helpers/alphamelts_volatility.py -- an
#              activities-only mapping emerges status="ok" with
#              backend_status_reason=None. Sibling getattr shares the shape.
#   LATENT     simulator/condensation.py -- every current CARRIER_GAS_PROPERTIES
#              key has a provenance row, so the default cannot fire unless a
#              carrier is added without one; the adjacent notice fail-closes.
#   LATENT*    simulator/core.py -- THREE sites, and the file-grain count hides
#              that they differ. L6633 and L8653 are latent (their callers always
#              set the key / the dataclass field always exists). ★ L7227 is
#              MORE FIREABLE than either: it reads
#              getattr(kernel_result,'status','') or diagnostic.get('status','')
#              or 'ok' -- IntentResult.status is required with no default, but an
#              EMPTY STRING still falls through to 'ok'. Calling the whole file
#              LATENT is a lump; triage L7227 on its own.
#   UNTRIAGED  every other entry below. Presence here means the SHAPE is
#              present, NOT that it fires. Do not cite an untriaged entry as a
#              bug without proving reachability first -- two of four hand-picked
#              candidates did not survive that check.
KNOWN_FLATTERING_DEFAULTS: dict[str, dict[tuple[str, str, str], int]] = {
    # file -> {(quality_key, flattering_token, spelling): count}
    "engines/builtin/melt_effect_adjustment.py": {('_last_backend_status', 'ok', 'getattr'): 1, ('backend_status', 'ok', 'or'): 1},
    "simulator/condensation.py": {('status', 'sourced', 'get'): 1},
    "simulator/core.py": {('status', 'ok', 'get'): 1, ('status', 'ok', 'getattr'): 1, ('status', 'ok', 'or'): 1},
    "simulator/diagnostic_helpers/alphamelts_volatility.py": {('status', 'ok', 'get'): 1, ('status', 'ok', 'getattr'): 1},
    "simulator/extraction.py": {('status', 'ok', 'or'): 1},
    "simulator/optimize/determinism.py": {('output_status', 'authoritative', 'getattr'): 1, ('status', 'available', 'getattr'): 1},
    "simulator/optimize/evaluate.py": {('status', 'ok', 'getattr'): 3},
    "simulator/optimize/objective.py": {('output_status', 'authoritative', 'get'): 1},
    "simulator/optimize/physics.py": {('output_status', 'authoritative', 'get'): 1},
    "simulator/optimize/results_store.py": {('output_status', 'authoritative', 'get'): 1, ('status', 'available', 'get'): 1},
    "simulator/optimize/study.py": {('output_status', 'authoritative', 'getattr'): 1, ('status', 'available', 'getattr'): 1},
    "simulator/reduced_real_determinism.py": {('status', 'ok', 'getattr'): 2},
    "simulator/run_executor.py": {('_last_backend_status', 'ok', 'getattr'): 2},
    "simulator/runner/__init__.py": {('output_status', 'authoritative', 'get'): 1},
    "simulator/vapour_rail/kinetics_anchors.py": {('status', 'measured', 'or'): 1},
    "web/routes.py": {('status', 'available', 'or'): 1},
}


def _swept_sources() -> list[Path]:
    """Every swept source, refusing if a declared root has gone missing.

    The earlier version skipped a non-existent root with ``continue`` and only
    asserted that the TOTAL was non-empty. A review named the consequence: rename
    or move one of three roots and the guard silently sweeps two, finds nothing
    new in them, and reports green. Coverage shrinks and the report still says
    clean -- the "reports clean AND reports confidence" failure this whole file
    exists to prevent, reproduced in the guard's own scaffolding.

    So a missing declared root is now a hard error, and each root must yield at
    least one file.
    """
    missing = [r for r in SWEPT_ROOTS if not (REPO_ROOT / r).is_dir()]
    assert not missing, (
        f"declared sweep root(s) missing: {missing}. Coverage would shrink "
        "silently -- update SWEPT_ROOTS deliberately, do not let the sweep "
        "quietly cover less than it claims."
    )
    found: list[Path] = []
    for root in SWEPT_ROOTS:
        in_root = [
            p
            for p in (REPO_ROOT / root).rglob("*.py")
            if "__pycache__" not in str(p)
        ]
        assert in_root, f"sweep root {root!r} exists but yielded no .py files"
        found.extend(in_root)
    return sorted(found)


# ---------------------------------------------------------------------------
# The guard's own tests come FIRST: a matcher that cannot see the defect makes
# every sweep result below vacuous, and a matcher that flags honest code gets
# the whole file deleted by the next person it annoys.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ('x = str(value.get("status", "ok"))', "ok"),
        ("x = str(getattr(result, 'status', 'ok'))", "ok"),
        ('x = str(item.get("status", "available"))', "available"),
        ("x = provenance.get('status', 'sourced')", "sourced"),
        ("x = payload.get('confidence', 'high')", "high"),
        ("x = record.get('status') or 'ok'", "ok"),
    ],
)
def test_matcher_catches_each_flattering_spelling(source: str, expected: str) -> None:
    """Every spelling the sweep found, verbatim, must be seen.

    The `.get`, `getattr` and or-fallback forms are three separate code paths in
    the matcher. The or-fallback was added last, after a correction; without a
    case per spelling a refactor can silently drop one and leave the repository
    sweep passing on a tree that still has the defect.
    """
    hits = flattering_defaults(ast.parse(source))
    tokens = {token for (_key, token, _spelling) in hits}
    assert tokens == {expected}, f"missed or mis-typed: {source} -> {dict(hits)}"


@pytest.mark.parametrize("token", sorted(DEGRADING_TOKENS_SEEN_IN_TREE))
def test_matcher_permits_every_degrading_default(token: str) -> None:
    """Honest under-claiming defaults must pass, in all three spellings.

    This is the case whose absence produced the sibling guard's over-firing.
    Eleven real sites in this tree default to a degrading token; a guard that
    flags them is worse than no guard, because it also reports confidence while
    being wrong. Pinning the permission makes the axis itself falsifiable.
    """
    for source in (
        f'x = record.get("status", "{token}")',
        f'x = getattr(result, "status", "{token}")',
        f'x = record.get("status") or "{token}"',
    ):
        assert not flattering_defaults(ast.parse(source)), (
            f"guard wrongly flags an honest degrading default: {source}"
        )


def test_matcher_ignores_non_quality_keys() -> None:
    """A default of "ok" on something that is not a quality claim is not this bug."""
    assert not flattering_defaults(ast.parse('x = cfg.get("button_label", "ok")'))
    assert not flattering_defaults(ast.parse('x = cfg.get("mode", "complete")'))


# ---------------------------------------------------------------------------
# The sweep.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    _swept_sources(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_new_flattering_default_in_swept_tree(source: Path) -> None:
    """No file may gain a flattering-default IDENTITY beyond its register.

    ``hits <= baseline`` componentwise is exactly "no new kind, and no more of
    any kind". A file absent from the register has an empty baseline, so any hit
    is red -- the common case for new code.

    Improvement is deliberately NOT red. A shrunken result is a subset, so fixing
    a site passes without anyone editing the register first. That is the whole
    reason this keys on identity rather than a count: an earlier draft failed on
    under-count to stop a fixed slot being silently reoccupied, which made every
    legitimate fix go red and turned the guard into the kind of nuisance its own
    docstring warns gets deleted.

    FALSIFIABILITY: goes red on a new identity in any of the three spellings, and
    on a second copy of an existing identity. Verified by injecting both.
    """
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - a parse failure is its own bug
        pytest.fail(f"{source} does not parse: {exc}")

    rel = str(source.relative_to(REPO_ROOT))
    hits = flattering_defaults(tree)
    baseline = Counter(KNOWN_FLATTERING_DEFAULTS.get(rel, {}))

    excess = hits - baseline
    assert not excess, (
        f"{rel} manufactures confidence from silence: {dict(excess)} beyond the "
        f"register. A missing status/grade/confidence is the ABSENCE of a claim: "
        "default to a degrading token (unknown, missing, unavailable) so the "
        "uncertainty travels, never to an affirmative one. Do NOT add it to the "
        "register to silence this."
    )


def test_baseline_has_no_stale_entries() -> None:
    """Every baselined path must still exist, or the register is fiction."""
    missing = [
        rel for rel in KNOWN_FLATTERING_DEFAULTS if not (REPO_ROOT / rel).exists()
    ]
    assert not missing, (
        f"baseline names files that no longer exist: {missing}. A debt register "
        "that outlives its debts stops being checkable."
    )

def test_or_form_is_documented_as_broader_than_the_default_form() -> None:
    """Both spellings are caught, and the docstring records why one is worse.

    Not a behavioural difference in the MATCHER -- both are flagged -- but the
    distinction drives triage: the or-form promotes an empty or zero value to a
    flattering token, whereas the default form only fires when the key is
    absent entirely. Pinned so the reasoning is not lost the next time someone
    wonders why two spellings are listed separately.
    """
    default_form = 'x = item.get("status", "ok")'
    or_form = 'x = item.get("status") or "ok"'
    assert flattering_defaults(ast.parse(default_form))
    assert flattering_defaults(ast.parse(or_form))

    source = Path(__file__).read_text(encoding="utf-8")
    assert "STRICTLY BROADER THAN THE DEFAULT-ARGUMENT FORM" in source, (
        "the triage rationale for the or-form was removed; restore it or the "
        "two spellings look interchangeable"
    )
