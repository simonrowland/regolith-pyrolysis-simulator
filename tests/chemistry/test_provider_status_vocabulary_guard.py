"""No engine provider may re-derive the kernel status vocabulary locally.

WHY THIS FILE EXISTS, AND WHY ITS DELETION IS THE POINT
------------------------------------------------------
`590b1cda` gave the intent-result status vocabulary a single validated owner
(`INTENT_RESULT_STATUSES` / `IntentResultStatusError` in
`simulator/chemistry/kernel/dto.py`). It also **deleted a source-grep test** that
asserted AlphaMELTS carried no ``else 'ok'`` remap, replacing it with a
structural check that the frozenset contains every production token.

That trade looked like an upgrade -- a crude string grep swapped for a real
set-equality assertion -- and it was the opposite. A later not-fixed review put
it plainly: the deleted grep *was the only test that could have been pointed at
a sibling remap*, and set equality over the vocabulary proves nothing about
whether each producer actually passes its raw token to the owner. The MAGEMin
provider kept its own four-token allowlist mapping everything else to ``'ok'``
for the entire life of that commit, and every test stayed green.

So this file restores the property that was lost, generalised from one engine to
all of them: **an engine provider must hand its adapter's status to the owner,
not decide for itself what the owner would have said.**

WHAT THIS IS AND IS NOT
-----------------------
This is a STRUCTURAL test, and structural evidence is usually the weak kind. It
is the right kind here because the property being defended is itself structural:
"no local copy of the vocabulary exists." A behavioural test can only demonstrate
the providers that exist today with the tokens someone thought to try; the
failure mode is a *new* provider, or a *new* branch in an old one, and only a
source-level sweep sees that coming.

VALIDATED AGAINST THE REAL DEFECT, NOT ONLY A SYNTHETIC ONE
-----------------------------------------------------------
``test_the_guard_actually_catches_the_historical_defect`` below feeds the matcher a
hand-written copy of the pre-fix expression. That proves the matcher works on a string
*this file's author wrote*, which is weaker than it looks -- a matcher over-fitted to
that exact phrasing would still pass.

So the guard was additionally run against the genuine article: the committed HEAD copy
of the three engine providers, via ``git show HEAD:<path>``. Result at the time of
writing:

    HEAD engines/magemin/provider.py    FLAGGED  line 247
                                        ('ok', 'not_converged', 'out_of_domain', 'unavailable')
    HEAD engines/alphamelts/provider.py clean
    HEAD engines/vaporock/provider.py   clean

It caught code written months earlier that had survived two independent reviews, at the
right line, with the right tuple. The two clean results matter as much: AlphaMELTS and
VapoRock had a DIFFERENT defect (an absent-status default of ``'ok'``), which this guard
correctly does not flag. A guard that flagged all three would have been telling us
nothing.

It is deliberately paired with, and does not replace:
  - the per-provider mechanism tests (an unrecognised token raises at the DTO);
  - the consequence test in `test_evaporation_freeze_gate.py`, which proves the
    resulting exception is not caught and reclassified into permissive physics.
Mechanism, consequence, and class. Any one of the three alone has a documented
hole.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.chemistry.kernel import INTENT_RESULT_STATUSES

ENGINES_ROOT = Path(__file__).resolve().parents[2] / "engines"

# A provider is entitled to ORIGINATE a status ("I am unavailable", "that intent
# is unsupported"). What it may not do is take an EXTERNAL token and decide
# locally which values are acceptable. The tell for the latter is a membership
# test of a status-ish name against a literal collection of status strings.
_STATUS_NAME_HINTS = ("status", "backend_status", "kernel_status")

# ★ THE AXIS IS DEGRADE-vs-FLATTER, NOT "IS IT A VOCABULARY TOKEN".
#
# The first version of this guard flagged any vocabulary member used as a
# default, which meant `getattr(x, 'status', 'unavailable')` was treated exactly
# as harshly as `getattr(x, 'status', 'ok')`. Those are opposites. A default to
# unavailable UNDER-CLAIMS -- a reader who sees it knows less confidence is
# warranted, and nothing is manufactured. A default to ok invents success out of
# silence. Only the second is the defect, and the original rule was a proxy for
# the real property rather than the property itself.
#
# That over-breadth was not theoretical: it fired on the CORRECT fix for the
# bug this file exists to catch, and the fix was reshaped to appease it. A
# guard that flags honest code is one somebody eventually deletes -- which this
# file's own header warns about.
#
# The two sets are declared separately and asserted to PARTITION
# INTENT_RESULT_STATUSES exactly (see
# test_every_status_token_is_classified_on_the_degrade_flatter_axis). A curated
# flattering list would rot silently the moment an affirmative token was added
# to the vocabulary: the guard would keep reporting clean on the very shape it
# exists to catch. With the partition assert, a new token fails this file until
# somebody classifies it, so the default for an unclassified token is a red
# test rather than an unflagged defect.
# ★ RELATIONSHIP TO test_flattering_default_guard.py (4b8a8047), WHICH OVERLAPS
# THIS CHECK ON PURPOSE. That sibling owns the flattering-default shape
# REPOSITORY-WIDE (simulator/, web/, engines/), across three call shapes, with a
# broader denylist than this vocabulary contains -- available, sourced, valid,
# authoritative, complete, verified, measured, confirmed, high. It supersedes
# nothing here by accident: its scope strictly contains this file's engines/
# sweep for this one shape.
#
# The obvious tidy-up is therefore to DELETE the default check from this file
# and let the sibling own it alone. That is exactly the trade 590b1cda made --
# a crude check swapped for a better-looking one -- and this file's header
# documents how that went: the deleted grep was the only thing that could have
# caught the sibling remap, and MAGEMin carried the defect for the whole life of
# that commit with every test green. So the duplication stays, deliberately.
# What does NOT stay is the false-positive liability, which is the actual
# complaint: a guard that flags honest code gets disabled, and then the
# duplication is worth nothing.
_FLATTERING_STATUS_DEFAULTS: frozenset[str] = frozenset({"ok"})

_DEGRADING_STATUS_DEFAULTS: frozenset[str] = frozenset(
    {
        "non_authoritative",
        "not_attempted",
        "not_converged",
        "out_of_domain",
        "refused",
        "unavailable",
        "unsupported",
    }
)


def _provider_sources() -> list[Path]:
    found = sorted(ENGINES_ROOT.rglob("provider.py"))
    assert found, f"no engine providers discovered under {ENGINES_ROOT}"
    return found


def _literal_status_collections(tree: ast.AST) -> list[tuple[int, tuple[str, ...]]]:
    """Membership tests of a status-ish value against a literal status set."""
    hits: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        left = node.left
        subject = ""
        if isinstance(left, ast.Name):
            subject = left.id
        elif isinstance(left, ast.Attribute):
            subject = left.attr
        if not any(hint in subject.lower() for hint in _STATUS_NAME_HINTS):
            continue
        for comparator in node.comparators:
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            values = tuple(
                elt.value
                for elt in comparator.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
            # Only a collection made ENTIRELY of vocabulary members is a
            # re-derivation of the vocabulary. A provider comparing against
            # its own private markers is doing something else.
            if values and all(v in INTENT_RESULT_STATUSES for v in values):
                hits.append((node.lineno, values))
    return hits



def _manufactured_status_defaults(tree: ast.AST) -> list[tuple[int, str]]:
    """``getattr(obj, 'status', '<vocabulary token>')`` -- inventing an answer.

    The SECOND defect shape, distinct from the allowlist above and missed by it.
    Both round-2 reviewers flagged the gap independently: the class guard pinned
    the historical MAGEMin allowlist but not the AlphaMELTS/VapoRock shape, where
    a MISSING status was defaulted to a real vocabulary token -- usually ``'ok'``.

    What keeps this from over-firing is the DEGRADE-vs-FLATTER axis, not the
    mere presence of a vocabulary token. A ``None`` default is correct and stays
    legal, because the caller must then decide explicitly what silence means
    (the fixed providers use ``getattr(x, 'status', None) or 'unavailable'``).
    A DEGRADING string default is legal too: ``'unavailable'`` under-claims, and
    a reader who sees it knows less confidence is warranted.

    Only a FLATTERING default is the defect -- it decides, at the seam, that an
    object which never declared a status is reporting success, manufacturing
    confidence out of silence. See ``_FLATTERING_STATUS_DEFAULTS``.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "getattr"):
            continue
        if len(node.args) != 3:
            continue
        attr, default = node.args[1], node.args[2]
        if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
            continue
        if not any(hint in attr.value.lower() for hint in _STATUS_NAME_HINTS):
            continue
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            if default.value in _FLATTERING_STATUS_DEFAULTS:
                hits.append((node.lineno, default.value))

    # The Mapping form of the same defect: ``result.get('status', 'ok')``.
    # Caught separately because it is a METHOD CALL on an arbitrary receiver,
    # not a getattr builtin, so the walk above cannot see it. This blind spot
    # was real, not hypothetical -- MAGEMin's parity helper carried BOTH forms
    # side by side in one function, and only the getattr half was flagged on
    # the first run of this guard.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
            continue
        if len(node.args) != 2:
            continue
        key, default = node.args
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        if not any(hint in key.value.lower() for hint in _STATUS_NAME_HINTS):
            continue
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            if default.value in _FLATTERING_STATUS_DEFAULTS:
                hits.append((node.lineno, default.value))

    return sorted(set(hits))


@pytest.mark.parametrize(
    "source", _provider_sources(), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_provider_does_not_redefine_the_status_vocabulary(source: Path) -> None:
    """No provider may gate an external status against its own literal set.

    FALSIFIABILITY: goes red the moment any engine provider reintroduces a
    line of the shape that caused this bug --

        kernel_status = backend_status if backend_status in (
            'ok', 'not_converged', 'out_of_domain', 'unavailable'
        ) else 'ok'

    -- because the comparator is a literal tuple drawn entirely from
    INTENT_RESULT_STATUSES and the subject name contains "status". Verified
    against the historical defect: the pre-fix MAGEMin provider trips this.

    It does NOT fire on a provider originating its own status (assignment, not
    membership), nor on a comparison against non-vocabulary markers.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    hits = _literal_status_collections(tree)
    assert not hits, (
        f"{source} re-derives the kernel status vocabulary locally at "
        f"line(s) {[ln for ln, _ in hits]}: {[vals for _, vals in hits]}. "
        "Pass the adapter's token to IntentResult and let "
        "INTENT_RESULT_STATUSES validate it; a local allowlist silently "
        "converts an unrecognised engine answer into an accepted one."
    )


def test_the_guard_actually_catches_the_historical_defect() -> None:
    """The guard is worthless if it cannot see the bug it was written for.

    Rather than trusting that the pattern-matcher works, feed it the exact
    pre-fix MAGEMin expression and require a hit. Without this, a refactor of
    ``_literal_status_collections`` that quietly stops matching would leave
    every provider test above passing vacuously -- the same failure this whole
    file exists to prevent.
    """
    historical_defect = (
        "backend_status = diagnostics.backend_status\n"
        "kernel_status = backend_status if backend_status in (\n"
        "    'ok', 'not_converged', 'out_of_domain', 'unavailable'\n"
        ") else 'ok'\n"
    )
    hits = _literal_status_collections(ast.parse(historical_defect))
    assert hits, "guard failed to detect the known pre-fix MAGEMin remap"
    assert hits[0][1] == ("ok", "not_converged", "out_of_domain", "unavailable")


def test_guard_does_not_fire_on_a_provider_originating_its_own_status() -> None:
    """A provider saying 'I am unavailable' is legitimate and must stay legal.

    Guards against over-fitting the check into something that forbids normal
    provider behaviour and gets disabled by the next person it annoys.
    """
    legitimate = (
        "if not backend.is_available():\n"
        "    return IntentResult(intent=i, status='unavailable')\n"
        "if request.intent not in _INTENTS:\n"
        "    return IntentResult(intent=i, status='unsupported')\n"
    )
    assert not _literal_status_collections(ast.parse(legitimate))

@pytest.mark.parametrize(
    "source", _provider_sources(), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_provider_does_not_manufacture_a_status_from_silence(source: Path) -> None:
    """No provider may default a missing status to a FLATTERING token.

    The companion to the allowlist check. Round-1 and round-2 reviews together
    established that the status-laundering class has TWO shapes, and the first
    version of this guard only saw one of them:

        allowlist  : status if status in ('ok', ...) else 'ok'   <- MAGEMin
        default    : getattr(result, 'status', 'ok')             <- AlphaMELTS

    Both end at the same place -- an unearned 'ok' reaching a consumer that
    treats it as an engine answer.

    FALSIFIABILITY: goes red if any provider reverts to a string vocabulary
    default. Verified against committed HEAD, where the AlphaMELTS provider and
    parser both trip this and the fixed working tree does not.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    hits = _manufactured_status_defaults(tree)
    assert not hits, (
        f"{source} manufactures a status from silence at line(s) "
        f"{[(ln, d) for ln, d in hits]}. An absent status is the ABSENCE of an "
        "answer: default to None and decide explicitly what silence means "
        "(the fixed providers use `or 'unavailable'`), or to a DEGRADING token "
        "that under-claims -- never to one that reports success."
    )


def test_the_default_guard_catches_the_real_alphamelts_shape() -> None:
    """Same self-test discipline as the allowlist guard, for the second shape."""
    historical_defect = "backend_status = str(getattr(equilibrium_result, 'status', 'ok'))\n"
    hits = _manufactured_status_defaults(ast.parse(historical_defect))
    assert hits and hits[0][1] == "ok"


def test_the_default_guard_allows_the_corrected_form() -> None:
    """`getattr(x, 'status', None) or 'unavailable'` must stay legal.

    Guards against a check so aggressive it forbids the fix it exists to
    encourage -- which is how guards get deleted.
    """
    corrected = "backend_status = str(getattr(equilibrium, 'status', None) or 'unavailable')\n"
    assert not _manufactured_status_defaults(ast.parse(corrected))


def test_the_default_guard_catches_the_mapping_form_too() -> None:
    """``result.get('status', 'ok')`` is the same defect through a dict.

    Added after the guard's first run found only the getattr half of a helper
    that carried both forms. A guard that sees one spelling of a defect and not
    the other reports clean on a file that still has it -- which is worse than
    no guard, because it also reports confidence.
    """
    mapping_form = "return str(result.get('status', 'ok'))\n"
    hits = _manufactured_status_defaults(ast.parse(mapping_form))
    assert hits and hits[0][1] == "ok"

    corrected = "return str(result.get('status') or 'unavailable')\n"
    assert not _manufactured_status_defaults(ast.parse(corrected))


def test_every_status_token_is_classified_on_the_degrade_flatter_axis() -> None:
    """The two sets must PARTITION the vocabulary, or the guard rots silently.

    This is the load-bearing test for the whole degrade-vs-flatter refinement.
    A hand-curated flattering list has one failure mode: somebody adds an
    affirmative token to INTENT_RESULT_STATUSES, nobody adds it here, and the
    guard goes on reporting clean on exactly the shape it exists to catch. That
    failure is silent, which is the worst kind -- the guard keeps emitting
    confidence while its coverage shrinks.

    Asserting the partition makes an unclassified token a RED TEST rather than
    an unflagged defect. Whoever adds the token has to say which side it falls
    on, which is the one moment they are certain to know.
    """
    assert _FLATTERING_STATUS_DEFAULTS | _DEGRADING_STATUS_DEFAULTS == set(
        INTENT_RESULT_STATUSES
    ), (
        "status vocabulary changed without classifying the new token(s): "
        f"{set(INTENT_RESULT_STATUSES) ^ (_FLATTERING_STATUS_DEFAULTS | _DEGRADING_STATUS_DEFAULTS)}. "
        "Decide whether each DEGRADES (under-claims, honest) or FLATTERS "
        "(manufactures confidence from silence) and add it to the right set."
    )
    assert not (_FLATTERING_STATUS_DEFAULTS & _DEGRADING_STATUS_DEFAULTS)


def test_the_default_guard_allows_a_degrading_string_default() -> None:
    """``getattr(x, 'status', 'unavailable')`` is HONEST and must not be flagged.

    The over-broad case. The guard used to flag any vocabulary member as a
    default, so a degrading default was flagged exactly as hard as ``'ok'`` --
    and this is not hypothetical: it fired on the CORRECT fix for the very bug
    this file was written to catch, and the fix was reshaped to
    ``getattr(..., None) or 'unavailable'`` to appease it. That produced a
    uniform idiom, which has some value, but a guard that flags honest code is
    one somebody eventually deletes, and this file's own header says so.

    Distinguish the two carefully, because they look identical structurally and
    differ only in which direction they lie:

        getattr(x, 'status', 'ok')            -> invents success from silence
        getattr(x, 'status', 'unavailable')   -> under-claims; a reader is warned

    Both call shapes are covered, since the mapping form is the one the guard
    originally missed.
    """
    for degrading in sorted(_DEGRADING_STATUS_DEFAULTS):
        attr_form = f"s = str(getattr(equilibrium, 'status', {degrading!r}))\n"
        assert not _manufactured_status_defaults(ast.parse(attr_form)), (
            f"degrading default {degrading!r} was flagged as manufactured "
            "confidence; only flattering defaults are the defect"
        )
        mapping_form = f"s = str(result.get('status', {degrading!r}))\n"
        assert not _manufactured_status_defaults(ast.parse(mapping_form)), (
            f"degrading default {degrading!r} flagged in the mapping form"
        )

    # ...and the flattering one is still caught, in BOTH shapes, so this test
    # cannot pass by the matcher simply having stopped working.
    for flattering in sorted(_FLATTERING_STATUS_DEFAULTS):
        attr_form = f"s = str(getattr(equilibrium, 'status', {flattering!r}))\n"
        assert _manufactured_status_defaults(ast.parse(attr_form)), (
            f"flattering default {flattering!r} was NOT flagged"
        )
        mapping_form = f"s = str(result.get('status', {flattering!r}))\n"
        assert _manufactured_status_defaults(ast.parse(mapping_form)), (
            f"flattering default {flattering!r} not flagged in the mapping form"
        )
