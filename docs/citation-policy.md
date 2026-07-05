# Citation & Provenance Policy

**Status:** binding convention for every physical/chemical number in the codebase.

This project's value is the *truthfulness* of its model, not the prettiness of its outputs. A number
we cannot trace to a source is a number we cannot defend — and an indefensible number is
indistinguishable from a fabricated one. So every grounded coefficient, constant, or fit in the code
carries its provenance with it. This document states what "carries its provenance" means, and why.

## 1. The core rule

**Every grounded value is accompanied, inline at its definition, by a citation sufficient to
reproduce and challenge it.** Not "from NIST" — the specific value, the primary source with a DOI,
the exact table/figure/dataset, and (where they change the meaning of the number) the standard state,
component basis, and the temperature/composition range over which it is valid.

A value without this is not "probably fine, cite later." It is an open liability, and it is treated
as one: it may not back a certification gate (see §3), and it is a defect the moment a reviewer or an
audit finds it uncited.

## 2. What a complete citation contains

For a physical/chemical coefficient, the inline comment (or the sidecar entry, §6) should carry:

- **The value**, in explicit units.
- **The primary source**: author + year + a stable identifier (DOI preferred; NIST-JANAF table id;
  dataset name). Prefer the *primary measurement* over a review that compiled it — cite DeMaria 1971
  (the KEMS measurement), not only Sossi & Fegley 2018 (the review that tabulated it). The review is a
  pointer to the primary, not a substitute for it.
- **The exact locus, to the page.** Name the **page number** *and* the table/figure/equation/row — the
  granularity at which a reader can turn to it and see the number, not go hunting. "Sossi & Fegley 2018,
  Table 2 (pp. 409–410), NaO0.5 row" — not "Sossi & Fegley 2018", and not even "Sossi & Fegley 2018 Table
  2". If the value comes from a figure, the figure number and the axis/curve; if from an equation, the
  equation number and page.
- **A public URL wherever one exists.** The DOI resolver (`https://doi.org/<doi>`) at minimum — it always
  resolves; add an open-access or NASA ADS URL when there is one, and NIST WebBook / dataset URLs for data
  sources. A URL a reader can click beats a citation they must go find. The DOI/URL belongs in the
  bibliography entry (`docs/references/references.yaml`, the `url:` field); the page/table/figure locus
  belongs at the point of use (the registry entry and the inline comment).
- **The locally-held copy.** Where the paper is in the benchmark corpus, cite the OCR'd copy path (with a
  line anchor) — it is the most directly auditable "URL" of all, working offline and pinned to the exact
  text we read.
- **The basis / standard state**, whenever the number's meaning depends on it (§4).
- **The range of validity**: the temperature and composition (and pO2/fO2 where relevant) the source
  measured. A value used outside its measured range is an *extrapolation* and must be labelled so.
- **The uncertainty**: the reported error bar, or the literature scatter if multiple sources disagree
  (§5).

## 3. Three trust tiers: CITED / ASSUMED / UNCERTIFIED

The code tags grounded values by trust tier (see e.g. `simulator/thermal_budget.py`). The tier is part
of the citation, not decoration:

- **CITED** — traceable to a primary source with the specifics above, applied on a basis consistent
  with how the source defined it. Only CITED values may back a certification claim.
- **ASSUMED** — an engineering default or design choice with no direct measurement, carrying a stated
  rationale. Legitimate, but never presented as measured.
- **UNCERTIFIED** — grounded-but-soft: a defensible value whose scatter is *not closed* (independent
  sources not reconciled, or a single moderate-confidence datum). It must say so explicitly, and it is
  **denied from certification gates** so no downstream result can present it as ground truth. Firming
  it up (closing the scatter) is tracked as follow-on work, not silently promoted.

## 4. A right value on the wrong basis is still wrong

This is the rule that a naive "we cited it" misses, and it is worth stating on its own because it has
already bitten us.

An activity coefficient, an equilibrium constant, a vapor pressure — these are defined *relative to a
reference*: a **standard state** (Raoultian pure-liquid vs Henrian infinite-dilution), a **component
basis** (single-cation `NaO0.5` vs the di-cation `Na2O`), and a **concentration measure** (mole
fraction vs weight fraction). A coefficient lifted from a paper and applied on a different basis than
the paper used is *not grounded* — it is a new, wrong number wearing a real citation.

So a citation for such a value must state its basis, and the code must apply it on a basis consistent
end-to-end: the source's standard state must match the reaction's `K` reference, and the mole/cation
normalization the source fit must be the one the code computes.

**Worked example — the melt activity coefficients (CF-3).** Sossi & Fegley 2018 Table 2 tabulate
alkali-oxide activity coefficients on the **single-cation `NaO0.5`/`KO0.5` basis**, **Raoultian**
(their Eqn 24: activity → 1 for the pure oxide), for the reaction `NaO0.5(l) = Na(g) + 1/4 O2` (their
Eqn 25) — which makes the sodium partial pressure **linear** in the activity coefficient,
`p_Na ∝ gamma_NaO0.5 * X_NaO0.5`. The *value* `gamma_NaO0.5 ≈ 1.0e-3` is independently confirmed by
Sossi 2019 KEMS on ferrobasalt and by DeMaria 1971 KEMS on Apollo 12022 lunar basalt. A first pass that
used that value on the di-cation `Na2O` basis with a square-root exponent and a weight-fraction proxy
was *wrong by construction* — it recovered only `sqrt(gamma)` of the suppression — even though the
number and the DOI were correct. The citation is not complete until the basis is stated and the code
honours it. (Full trace: `docs-private/deep-research/cf3-alkali-activity-synthesis.md`.)

## 5. Uncertainty is a deliverable, not a footnote

The reproduction error bar *is* the product (see `docs/model-limitations.md`). Where a value carries a
measured uncertainty or the literature scatters, record it. It feeds the decomposed error budget that
turns the README's "comparative estimates, not validated predictions" from a disclaimer into a
measured claim. A value with unclosed scatter is UNCERTIFIED (§3) until the scatter is reconciled.

When our model disagrees with the data, the answer is to **investigate — never retune a coefficient to
mask the gap.** A citation is a commitment to a source, not a knob to tune until the output looks
right. Curve-fitting a "grounded" value to make numbers match intuition is the corruption this policy
exists to prevent.

## 6. Ground-truth tests cite too — and can't cheat

Physics/chemistry tests assert against the *external* value, not the simulator's own past output (a
refactor-parity test locks in whatever error already exists). The literature-sourced ground-truth
values live in a sidecar kept **separate from the runtime fallback** (e.g.
`data/vapor_pressures.yaml::pure_component_antoine`, checked by `tests/test_physics_ground_truth.py`),
so a test cannot pass by parroting the number it is meant to check. The sidecar entry carries the same
citation as the code.

## 7. The provenance chain

Three layers, cheapest first:

1. **Inline citation** at the value's definition — the minimum, always present. This is the commitment.
2. **The benchmark literature corpus** (`docs-private/deep-research/literature/<paper>/`) — the OCR'd
   primary paper with the actual table, held so the number can be re-checked against its source at any
   time.
3. **The fact-forest** (curated provenance pages) — the synthesized, cross-referenced write-up of a
   value and its competing sources. This is the richest layer and is built as a deliberate follow-on;
   the inline citation and the corpus paper are what must exist *now*.

## 8. Comparative provenance: what we use, *why*, and what the alternatives lack

Citing the source we chose is necessary but not sufficient. A number is only *defensible* when the
record also answers **why this source and not the others** — because that is the question a reviewer,
an auditor, or a future maintainer actually asks. So for every value that has competing literature, the
provenance records:

- **The chosen source** and the one-line reason it wins (right basis, primary not review, correct
  feedstock/composition, closest temperature range, tightest uncertainty, most recent supersession).
- **The alternatives considered**, each with *what it lacks* — the specific X/Y/Z that disqualified or
  down-ranked it: "wrong component basis (Na2O di-cation, not NaO0.5)", "wrong feedstock (gasifier
  slag, not silicate melt)", "review compilation, not the primary measurement", "measured only to
  1500 K, we run to 2170 K", "no reported uncertainty", "superseded by …".

This turns provenance from "here is a citation" into "here is the decision, and here is the evidence
that it was the right one" — which is the thing that lets someone trust or challenge it quickly.

## 9. Easily auditable — including by agents

Provenance that can only be reconstructed by reading scattered comments is not auditable at scale, and
it is invisible to an agent asked to check it. So the comparative provenance lives in **one structured,
machine-readable registry**: [`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml). Each entry
carries the fields of §2 and §8 as data (id, value, units, tier, basis, range, uncertainty, chosen
source with DOI + locus + corpus path, the alternatives-and-what-they-lack, and the `code_sites` that
consume it).

The registry is the single source of truth; the inline comment at each `code_site` points back to its
registry `id`. An audit is then one command, runnable by a human or an agent:
`scripts/audit_chemistry_provenance.py` — which checks that (a) every registry value matches the number
at its code site, (b) every grounded chemistry coefficient in the code has a registry entry, and (c)
reports the counts per trust tier and the open gaps. "Is this number grounded, and why this one?" is
answerable by reading one YAML entry, not by archaeology.

The registry is human-readable too (commented YAML), and is the source from which the public
chemistry-provenance narrative in the docs is generated — so the machine view and the human view never
drift.

## 10. In short

- Cite the primary, at the value, with the specifics — value, DOI, table, basis, range, uncertainty.
- Tag the trust tier honestly; keep UNCERTIFIED values out of certification gates.
- State the basis and apply it consistently — a right value on the wrong basis is wrong.
- Uncertainty is the deliverable; investigate disagreements, never retune to hide them.
- Tests assert against the source via a separate sidecar, never against our own output.
