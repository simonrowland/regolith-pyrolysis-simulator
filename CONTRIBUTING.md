# Contributing — Regolith Pyrolysis Simulator

This is a private research workbench for solar-thermal regolith refining. The
primary contributors are coding agents (Claude Code, Codex, review subagents)
and the project author. This document codifies the workflow those contributors
should follow so the simulator stays scientifically defensible and the codebase
stays in a state where any agent can pick it up cold.

`AGENTS.md` is the operating instructions — the invariants, file map, and
chemistry-engine authority matrix. `CONTRIBUTING.md` (this file) is the
workflow. Read `AGENTS.md` first.

## Before you touch code

1. **Read `AGENTS.md`.** Hard invariants there are load-bearing — mol-native
   ledger, atom conservation per transition, mass balance to 0.000%, Stage 0
   separation, separate O2 outlet bins. If you don't internalise these, you
   will introduce a regression that `tests/test_artifact_guards.py` or
   `tests/test_mass_balance.py` will catch — but the agent that wrote the
   regression will have already burned dispatch tokens.

2. **Read the chemistry-engine policy section of `AGENTS.md`.** The per-intent
   authority matrix (AlphaMELTS / FactSAGE / builtin) is binding. Do not
   silently widen an engine's authority. AlphaMELTS does not emit
   `LedgerTransitionProposal`. FactSAGE without explicit strict gate stays
   diagnostic. No provider mutates `AtomLedger` directly — the kernel commit
   path is the sole writer.

3. **Find the plan of record.** For chemistry-engine work that's
   `docs-private/chemistry-engine-refactor-plan-2026-05-10.md` plus the
   coverage matrix at `docs-private/chemistry-engine-feature-coverage-
   checklist-2026-05-10.md`. For other work, look in `docs-private/` for the
   most recent plan or refactor doc on your topic; if none exists, this work
   may need an `office-hours` interrogation before scoping.

4. **Check `docs-private/RESUME-NOTES-<latest>.md`** if it exists. That's the
   last controller's handoff state — what landed, what's in flight, what's
   blocked. Don't restart work that's already in progress in a sibling
   worktree.

## How the work happens

The simulator is large enough that work is usually staged through the
[goal-flight](https://github.com/simonrowland/goal-flight) controller pattern.
The skill is installed at `~/.claude/skills/goal-flight`. Workflow for a
substantial change:

1. **`/goal-flight init <topic>`** — audits the repo, scaffolds AGENTS.md
   patches if needed, builds the RAG corpus at `docs-private/rag/` if the
   project meets the size threshold (it does; the corpus is already present).
2. **`/goal-flight decompose-plan <plan-file>`** — breaks the plan into
   numbered `\goal` chunks with SCOPE / CHECKLIST / ACCEPTANCE / FORBIDDEN.
   Adversarial review pass (Claude + codex) before the queue is finalised.
3. **`/goal-flight ask-questions`** — surfaces chunk-level ambiguities before
   dispatch. Address these inline, not mid-execute.
4. **`/goal-flight execute`** — runs the per-chunk dispatch loop. Sequential
   by default; `--parallel N` for `[parallel-safe:<group>]`-tagged chunks
   that touch disjoint modules. Embedded self-review per chunk; milestone
   codex+claude review every 5 commits (configurable via `--review-every`).

For trivial one-shot changes (typo, version bump, single-constant rename,
known-good test addition) — skip goal-flight. Direct edit + commit is the
right tool. Goal-flight's overhead doesn't pay for chunks under ~30 LoC
with no cross-module coupling.

## Code style

- **Python**: PEP-8 with these project-specific rules:
  - Imports are top-of-module unless lazy imports are required by an explicit
    contract documented in `docs-private/rag/patterns/`. The pattern slice
    `lazy-import-rule.md` (if present) is canonical when lazy imports apply.
  - Type hints on every public function in `simulator/`. Optional on private
    helpers and test fixtures.
  - Dataclasses for state structures (see `simulator/state.py`). Frozen
    dataclasses for anything immutable per the binding-spec.
  - Errors are first-class. Domain errors (`UnbalancedTransitionError`,
    `MeltStateError`, etc.) are defined alongside the module that raises
    them and re-exported via the module's `__init__.py` if cross-module.
  - No catching `Exception:` broadly. If you don't know which exception
    matters, the binding-spec or the patterns slice tells you.

- **Tests**: `pytest`. Tests live under `tests/`. Mirror the source layout:
  `simulator/foo.py` → `tests/test_foo.py`. Integration tests can live at
  the top level (`tests/test_mass_balance.py`, `tests/test_artifact_guards.py`).
  These two are load-bearing — they enforce invariants documented in
  `AGENTS.md`. Do not skip or weaken them without an explicit decision
  recorded in `docs-private/rag/decisions.md` (oldest-first append).

- **Web layer** (`web/`, `app.py`): Flask + Socket.IO. Routes are thin —
  business logic lives in `simulator/`. Socket events are documented in
  `web/events.py`'s module docstring.

## Where things go

| Kind of change | Destination |
|---|---|
| Simulation engine logic | `simulator/<area>.py` |
| State / dataclasses | `simulator/state.py` |
| Mol-native ledger | `simulator/accounting/` |
| Chemistry-engine adapters | `simulator/melt_backend/` (AlphaMELTS, FactSAGE) |
| Decision routing | `simulator/decision_tree.py` |
| Web routes / events | `web/routes.py`, `web/events.py` |
| Dashboard front-end | `web/static/` |
| Process docs (public) | `docs/` |
| Plans, reviews, RAG corpus | `docs-private/` (gitignored) |
| Tests | `tests/test_<module>.py` |
| Engine binaries / data | `engines/` (managed by `install-engines.py`) |

If you're not sure where something goes, the `file-map.md` slice in
`docs-private/rag/` is the source of truth — it's regenerated from the
current code at corpus-build time.

## Testing

Before committing any non-trivial change, run at minimum:

```bash
# Fast invariant guards — under 5 s, always run.
uv run pytest tests/test_artifact_guards.py tests/test_mass_balance.py -q

# The module(s) you touched.
uv run pytest tests/test_<module>.py -q
```

For changes that span multiple modules, run the full suite:

```bash
uv run pytest -q
```

The goal-flight controller embeds a self-review step in every executor
dispatch — agents are expected to run the relevant subset and self-fix
P0/P1 findings BEFORE reporting a chunk done. Surface P2/P3 as TODO
comments in the diff or as goal-queue follow-ups; do not commit them
silently.

## Commits

- **One chunk per commit.** Subject line: imperative mood, < 70 chars,
  ends with `(chunk N/M)` suffix when running goal-flight, or just the
  imperative for one-off changes.
- **Body** (optional but encouraged for substantive changes): the *why*,
  not the *what*. Diff shows the what. The why is: what plan-of-record
  is this chunk consuming, what invariant is it preserving, what was
  the surprise that made it harder than expected.
- **Trailers**: `Co-Authored-By:` for agent-pair work; `Refs:` to
  `docs-private/...` plan files.
- **Never** amend a published commit (`git commit --amend` after `git push`
  rewrites history other worktrees depend on). If a fix is needed, make
  a new commit.

## Code review

Solo project; review happens via:

- **Embedded self-review** inside every executor dispatch (mandatory).
- **Milestone reviews** every 5 commits via `goal-flight execute`'s
  `--review-every K` mechanism. Two reviewers run in parallel — typically
  one Claude (concern: chemistry/accounting correctness) and one codex
  (concern: code quality + consistency). Findings dedupe; P0/P1 must
  resolve before the next milestone.
- **Pre-landing pass** via gstack's `/review <range>` skill when shipping
  a substantive change.

Outside agents reviewing this repo: prefer concern-split reviewers (chemistry
correctness + code quality) over model-diversity reviewers (Claude + codex on
the same concern), per `~/Repos/goal-flight/reference/pattern.md`'s
"more load-bearing for 12-hour refactor runs" guidance.

## Working with `docs-private/`

`docs-private/` is gitignored. It holds: plan-of-record files, review
artifacts, RESUME-NOTES, the RAG corpus, lessons-learned, and the
goal-queue files goal-flight emits. None of this is shared via git;
it's local working state for the controller.

If you find yourself wanting to commit something from `docs-private/`,
ask why. Plans of record can be moved to `docs/` if they're stable
references. Most things should stay private.

## When you find a bug

1. Reproduce it with a failing test FIRST (add the test to `tests/`
   in the appropriate file). The test failure pins the behavior.
2. Find the root cause. Don't patch around it. Read the binding-spec
   slice for the affected intent (in `docs-private/rag/binding-spec/`)
   to confirm the expected behavior.
3. Fix it. Re-run the test. Run the invariant guards.
4. Commit with the *why* in the body — what was the surprise, what was
   the wrong assumption.

If the bug exposes a missing invariant: add the invariant to the AGENTS.md
hard-invariants list. If it exposes a missing test: add a guard test that
would have caught it.

## Questions / ambiguity

If a chunk's SCOPE is unclear, an invariant is ambiguous, or the chemistry-
engine authority for an intent isn't obvious — STOP and surface the question.
The asking-discipline in `~/Repos/goal-flight/SKILL.md` applies: interrupt
the user only for decisions that affect the north star (code quality +
first-principles scientific integrity), not for trivia. But when in doubt
on scientific correctness, ask. A wrong invariant baked in is more expensive
than a 30-second clarification.

## North star

Quoting AGENTS.md indirectly: this is a process-modeling workbench, not
flight hardware. Optimize for scientific defensibility (every claim
traceable to a citation, model, or first-principles derivation) over
shipping velocity. Mass balance closes to 0.000% per transition. Every
provider's authority is bounded. The simulator runs locally and tells
the truth about regolith.
