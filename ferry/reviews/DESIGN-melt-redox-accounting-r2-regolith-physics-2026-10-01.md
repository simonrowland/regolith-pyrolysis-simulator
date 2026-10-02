# Melt redox accounting r2 — unified plan (affinities / network modifiers included)

Design-only. No stack2 product code in this delivery. Answers the owner SCOPE CHANGE
(`REQ-melt-redox-accounting-plan-from-regolith-physics-2026-10-01.md`): one reviewable
plan for **systematic melt redox accounting**, of which the alkali couple is a **part**,
not a bolt-on M6.

Folds:

- redox design r3 (M0–M5 iron story, film+integrator, committed interface; chunks 1–6
  landed / 7 in flight as of prior STATUS)
- alkali-couple DESIGN r1 + r2 (sol corrections; AMEND titration; AMEND2 sites; Q-A..Q-D)
- **OWNER RULING d-058** (2026-10-01): stirred whole-melt titration; **lance-zone fork CLOSED**
- sol r1 corrections (Kelvin Ellingham, \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\), Ti extent,
  \(p_{\mathrm{Na}}\) closure, Si/Mg competitors, phase references)
- decided d-056 / d-057 / Q-A..Q-D; d-052 1 mbar hold remains optimizer input

Seat numerics: `slot-z14` @ green `696299350e98b67f786c334b4b4ccc8856149379` (matches
`origin/work-v064-green` at write time). Ellingham Kelvin, liquid-FeO IW, parent
activity conversion. openimcc numbers cited from alkali-r2 seat probes (FLAGGED).
No invent of extract data or equipment FKs. Ferric-first (b-645) sequencing remains
owned on the shuttle lane; this plan states how Fe³⁺ enters the **unified solve**.

Independent second opinion before any implementation.

North Star unchanged: predict-and-flag, ledger closure, derivation beside every threshold.

---

## 0. What this plan is / is not

| Is | Is not |
|---|---|
| ONE element-ledger state; oxidation states derived | Parallel fO₂ scalars or regime-named “truth” |
| ONE shared-\(\mu_{\mathrm{O_2}}\) (+\(p_{\mathrm{Na}}/p_{\mathrm{K}}\)) titration root | Waterfall of independent couple equalities |
| ONE activity model for solve + evaporation rails | Per-rail activity shopping |
| Whole-melt equilibrium per tick (**d-058**) | Separate lance-zone equilibrium volume (LZ-A/B/C **CLOSED**) |
| Luna-sized chunks after r3 chunk 8 | Stack2 product commits in this seat |
| Alkali couple as a **live couple inside the solve** | Renaming M2 to M6 whenever Na is dosed |

**Supersedes** alkali-r2 §4 owner fork (LZ-A default / LZ-B / LZ-C). Still-valid alkali-r2
content (Kelvin \(K\), joint Na/K, sites, openimcc FLAGGED, Fe/SiO withdrawal, chunk
helpers A0–A10 shape) is folded below under the unified numbering.

---

## 1. STATE — element ledger only; oxidation states derived

### 1.1 Ledger partitions

The **only** persisted redox state is the element (and phase-bucket) ledger:

| Bucket | Holds | Notes |
|---|---|---|
| **Melt** | Element moles in the liquid oxide solution (Si, Ti, Al, Fe, Mg, Ca, Na, K, Cr, Mn, P, O, …) | Speciation / oxidation states are **derived**, not stored as independent truth |
| **Metal** | Condensed metal (Fe⁰ primarily; later alloy out of scope) | Presence opens Fe²⁺/Fe⁰ coexistence when FeO also present |
| **Gas / headspace** | O₂, Na(g), K(g), SiO(g), Mg(g), … partial inventories or pressures via the exchange record | Coupled to condenser |
| **Dose / lance delivery** | Process write of reductant into the system this tick | **Not** a separate equilibrium volume under d-058 |
| **Condenser / sinks** | Captured alkali (and other) vapour | Element-balance removal term |

Derived each tick (and after each authorized writer): Fe³⁺/Fe²⁺/Fe⁰, Ti⁴⁺/Ti³⁺/Ti⁰,
Cr³⁺/Cr²⁺, Na⁺/Na⁰, K⁺/K⁰, Si⁴⁺/Si⁰(/SiO), Mg²⁺/Mg⁰ — from the shared solve + activity
model at the current composition. Never store a second fO₂ that can disagree with the
ledger inverse / buffer identity.

### 1.2 How lance / dose appears **without** a local equilibrium volume (d-058)

**Lance-zone fork CLOSED.** There is no \(V_L\), no local \(p_{\mathrm{Na}}=0.01\,\mathrm{bar}\)
imposition, and no separate root on a lance sub-volume.

Dose and lance enter the ledger as:

1. **Delivery write** — moles of Na (and/or K) credited to the system this tick
   (condensed reductant mixed into the melt, or vapour entering the **shared** headspace).
2. **Whole-melt titration** — the redox solve uses the **entire** melt + metal + headspace
   as one equilibrium zone per tick.
3. **Equilibrium \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\)** — outputs of the solve (and/or the
   exchange film driving headspace toward those equilibrium partials), **not** recipe
   constants.
4. **Alkali lost as vapour** — element-balance term: headspace → condenser (or ballistic
   escape) removes Na/K from the closed tally; the solve and exchange must both see that
   debit.

Mixing validity (required statement under d-058):

\[
\tau_{\mathrm{mix}} \ll \Delta t_{\mathrm{tick}},
\qquad
\text{dose reacts within the tick}.
\]

Seat sketch for a stirred bath: \(\tau_{\mathrm{mix}}\sim h/u_{\mathrm{stir}}\) with
\(h\sim0.1\text{–}0.5\,\mathrm{m}\), \(u\sim0.05\text{–}1\,\mathrm{m/s}\) →
\(\tau_{\mathrm{mix}}\sim0.1\text{–}10\,\mathrm{s}\) vs \(\Delta t=3600\,\mathrm{s}\)
(hour tick) — typically **3+ orders** of margin when stirring while lancing.

**Flag** `recipe_breaks_whole_melt_eq` (and refuse the whole-melt equality publish) when:

- estimated \(\tau_{\mathrm{mix}} > 0.1\,\Delta t_{\mathrm{tick}}\); or
- instantaneous dose rate exceeds mix-limited capacity so a stratified reductant pocket
  would persist through the tick; or
- operator disables stirring / documents unmixed lance.

That flag does **not** reopen a lance-zone equilibrium volume; it marks the tick’s
equality as out-of-domain for the stirred model.

### 1.3 Speciation key (carry-over from r3)

Oxidation-state labels used in reports are a **speciation key** over the ledger, not a
second state. Predict-and-flag: when the activity model cannot support a couple (no
ferric associates, missing \(a_{\mathrm{Ti_2O_3}}\) certification, …), publish the
derived quantity as FLAGGED or absent — never invent inventory.

---

## 2. ONE redox solve — shared oxygen (+ alkali) potential

### 2.1 Formulation

One shared oxygen potential \(\mu_{\mathrm{O_2}}\) (\(\log f\mathrm{O_2}\)) for the whole
melt each tick. When alkali gas/dose is present with alkali oxide in the melt, also an
alkali potential (\(p_{\mathrm{Na}}\), \(p_{\mathrm{K}}\) — or a single combined alkali
potential with joint Na/K constraints per Q-C).

Every couple at equilibrium with that **same** \(\mu_{\mathrm{O_2}}\) at the **current**
composition (titration root):

| Couple | Role in solve |
|---|---|
| Fe³⁺/Fe²⁺ | Kress-type (carries Na₂O/K₂O term); ferric path §3 |
| Fe²⁺/Fe⁰ | IW buffer when metal+FeO coexist |
| Cr | Secondary; simultaneous when live |
| Ti⁴⁺/Ti³⁺/Ti⁰ | JANAF intermediates; no TiO₂→Ti hop (Q-B) |
| Na₂O/Na(g), K₂O/K(g) | Alkali couple (d-056); joint before publish (Q-C) |
| SiO₂/Si, MgO/Mg(g) | In scope for prediction limits; flag if inactive |

**Unknowns:** \(\mu_{\mathrm{O_2}}\) (and \(p_{\mathrm{Na}}/p_{\mathrm{K}}\) when the alkali
couple is live). **Constraints:** element mass balance (O, Na, K, Fe, Ti, Cr, Si, Mg, …)
over melt + metal + gas, including condenser debit this tick.

Solve shape: bracketed 1-D (or low-D with alkali) root on oxygen / alkali balance —
same family as openimcc’s Knudsen oxygen-balance root — then **read every extent** off
the solution. Tolerances: propose \(10^{-10}\) relative on \(\Delta G/RT\),
\(10^{-12}\) mol element closure.

Titration **plateaus** emerge where couples are separated by many log units. Overlapping
couples partition simultaneously. Waterfall is only the well-separated limit (≥3 dex
default for “waterfall agreement” acceptance).

### 2.2 Alkali equalities (sol-corrected; \(p\) from solve)

Dissociation (Na; K analogous), standard states: melt parent oxide activity; gases 1 bar:

\[
K_{\mathrm{Na}}(T)=\frac{(p_{\mathrm{Na}}/\mathrm{bar})^2\,(f\mathrm{O_2}/\mathrm{bar})^{1/2}}{a_{\mathrm{Na_2O}}},
\qquad
f\mathrm{O_2}/\mathrm{bar}
=
\left(
\frac{K_{\mathrm{Na}}(T)\,a_{\mathrm{Na_2O}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2}
\right)^{\!2}.
\]

**Ellingham API is Kelvin.** Seat at \(1150^\circ\mathrm{C}=1423.15\,\mathrm{K}\):

| Quantity | Value |
|---|---:|
| \(\Delta G^\circ_{\mathrm{ox,Na}}\) / kJ mol⁻¹ O₂ | −371.6126 |
| \(\Delta G^\circ_{\mathrm{dissoc}}\) / kJ mol⁻¹ Na₂O | +185.8063 |
| \(K_{\mathrm{Na}}\) | \(1.514901\times10^{-7}\) |
| \(K_{\mathrm{FeO}}\) (liquid-FeO IW) | \(7.286023\times10^{-7}\) |
| \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\) | **4.809570** |

Cross equilibrium \(2\mathrm{Na(g)}+\mathrm{FeO}=\mathrm{Na_2O}+\mathrm{Fe}\):

\[
K_\times(T)=\frac{a_{\mathrm{Na_2O}}\,a_{\mathrm{Fe}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2\,a_{\mathrm{FeO}}}
=\frac{K_{\mathrm{FeO}}(T)}{K_{\mathrm{Na}}(T)}.
\]

**Under d-058**, when M2 is live, rearrange for the equilibrium partial over the whole melt:

\[
\boxed{
p_{\mathrm{Na}}/\mathrm{bar}
=
\sqrt{
\frac{a_{\mathrm{Na_2O}}\,a_{\mathrm{Fe}}}{K_\times(T)\,a_{\mathrm{FeO}}}
}
}
\]

— **derived**, never declared 0.01 bar. Incomplete same-species pair → couple absent
(no equality), not a provisional `min(fO2)`.

Phase / standard-state contract (P3): Ellingham condensed-metal segments require conversion
to gas reference below the boiling point before use with \(p_{\mathrm{Na}}\); K₂O crystalline
refs must convert or refuse (`alkali_phase_reference_unmatched`).

### 2.3 Iron-only limit = r3 regime table M0–M5

When alkali couple is **absent** (no complete same-species Na/K pair) and no other
authorized non-iron couple is live, the unified solve **reduces to** r3’s iron story.
Mapping:

| Regime | Iron-only limit of unified solve | Equality / bound / absence |
|---|---|---|
| **M0** `not_liquid` | Liquidus gate false | **Absence** (no bound) |
| **M1** `no_modelled_redox_couple` | Liquid; FeO≤NOOP; Fe₂O₃≤NOOP; alkali absent | **Absence**, flag `out_of_domain` (b-618 holding pattern for “no couple”) |
| **M2** `fe_feo_buffer` | Metal+FeO > NOOP | **Equality** `IW+2\log_{10}(a_{\mathrm{FeO}})` at **current** \(a_{\mathrm{FeO}}\) (moves under dose). Alkali cross-reaction, if dose present, is **inside** the shared root — it does not rename M2 |
| **M3** `ferrous_free_lower_bound` | FeO≤NOOP, Fe₂O₃>NOOP | **Absence**; flagged **lower bound** (edge), not equality |
| **M4** `fe_saturation_bound` | Fe₂O₃≤NOOP, FeO>NOOP, metal≤NOOP | **Absence**; flagged **lower bound** (R-b number as bound) |
| **M5** `kress91_inverse` | Both FeO and Fe₂O₃ > NOOP | **Equality** from unclamped mole-log inverse |

**Bound / absence semantics, speciation key, predict-and-flag** carry over unchanged from
r3 §1–§3: constructors reject equality on M0/M1/M3/M4; consumers treat absence as one
condition; vapour reads `interface_pO2_bar`, never a bound or melt equality substituted
into SiO.

**Alkali-present enrichment (not a bolt-on M6 rename of M2):**

- While metal+FeO remain → stay **M2**; published iron equality is still IW-scale at
  current \(a_{\mathrm{FeO}}\); joint alkali potentials are additional solve outputs
  (\(p_{\mathrm{Na}}\), capacities).
- When iron couple gone but complete alkali pair live → publish alkali equality from the
  same root (former “M6” **label** may remain as a report tag for “alkali buffer active”,
  but it is not a second solver). First-match iron predicates still win for the iron
  columns of the tick record.
- Incomplete alkali pair → flag `alkali_couple_incomplete`; do not publish alkali equality.

### 2.4 Which r3 chunks stay valid vs superseded

| Chunk | Status under unified solve | Notes |
|---|---|---|
| **1–6** | **Stay** (already landed / reviewed) | Unavailable-buffer return; committed interface; mol predicates; bounds+absence B; metal film — all iron-path load-bearing |
| **7** exponential integrator | **Stay valid unchanged** | Gas-film + finite melt film trajectory; Jacobian of the **same** interface law. Whole-melt titration does not replace transport |
| **8** one tick + refresh | **Stay valid unchanged** | `solve_redox_tick` + post-mutation refresh; melt fields pure functions of ledger |
| **9** one respeciation writer | **Stay, with ordering note** | Exchange after shuttle/char/MRE/thermite before vapour — **hard dependency** for alkali shuttle chunks (Q-D). Not wasted; required |
| **10** R-e moles | **Stay** | Minority inventory > NOOP gate; independent of alkali potential |
| **11** bulk fixed point | **Stay** | Bulk M2 equality uses same activity/fixed-point machinery as surface; unified solve **reuses** this, does not fork a second FeO activity |

**Controller continues chunks 7–8 now.** They are **not** wasted: the unified titration
mutates the ledger (extents); the film+integrator still decides `committed_d_mol` and
`interface_pO2_bar`. Alkali implementation chunks (§7) land **after** chunk 8 unless a
later review proves a specific 7/8 acceptance fixture is broken by the multi-couple root
(none identified at design time).

### 2.5 Fe/SiO assurance — still withdrawn

Dose moves M2 equality by many dex while metal+FeO remain (seat ~7.4 dex waterfall
failure at C3). Regime label alone does **not** de-conflict Fe cleanup from SiO risk.
Replace with coupled shuttle/film + selective-flux acceptance (§7 chunk U8).

---

## 3. ONE activity model

### 3.1 Target: every activity the solve + evaporation rails use

| Activity | Target source | Flag / gap |
|---|---|---|
| \(a_{\mathrm{FeO}}\) | CALPHAD / core fixed point (r3); openimcc ferrous-class where wired | per r3 |
| \(a_{\mathrm{Fe_2O_3}}\) / ferric | **Ferric path below** | Must not silently use ferrous-only openimcc |
| \(a_{\mathrm{Na_2O}}\), \(a_{\mathrm{K_2O}}\) | **openimcc associate** (Q-A), FLAGGED until battery cert | Ladder fallback: constant-\(\gamma\) + parent \(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2\) |
| \(a_{\mathrm{TiO_2}}\) | Engines where available | Flag if absent |
| \(a_{\mathrm{Ti_2O_3}}\) | Ideal \(X\) (Q-B), FLAGGED + JANAF refs | Explicit reference states |
| \(a_{\mathrm{SiO_2}}\), \(a_{\mathrm{MgO}}\), \(a_{\mathrm{Cr_2O_3}}\) | Same ONE model family | Flag / ideal with prediction-limit |
| Pure metals | \(a=1\) outside melt model | Alloy dilution out of scope |

Structural speciation (associates): alkali/alkaline-earth charge-balancing of tetrahedral
Al and Fe³⁺; Na/K–Si network modifiers; **peralkaline step**. These enter **only** through
composition-dependent activities re-evaluated as dose changes the melt — never as a
second energetic benefit double-counted against Ellingham.

### 3.2 openimcc candidate + ferric path

**openimcc `ext-v4`** (NaAlO₂, NaAlSiO₄, …, KAlO₂, …, Na₂SiO₃, …): working source for
\(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) (Q-A). Known limits — flag all four when publishing
step or Fe³⁺ competition:

1. ferrous-only  
2. no metal  
3. no ferric associates  
4. ideal mixing among associates (softens the peralkaline step)

**Ferric path (choose and keep standard states consistent):**

| Option | Mechanism | Why / when |
|---|---|---|
| **F1 (proposed first landing)** | Keep **Kress-type** Fe³⁺/Fe²⁺ relation **with its Na₂O/K₂O term** for the ferric ratio at given \(f\mathrm{O_2}\); openimcc supplies alkali (and ferrous-class) activities for the shared root | Avoids inventing NaFeO₂/KFeO₂ associates before battery evidence; matches today’s executable ferric path; Na₂O term already couples alkali content to Fe³⁺/Fe²⁺ |
| **F2 (later certification)** | Add ferric associates (NaFeO₂, KFeO₂, …) to the associate model when corpus + battery justify | Needed to quantify M⁺-stabilized tetrahedral Fe³⁺ competition that Kress alone under-resolves; lift flags when certified |

**Standard-state consistency:** never mix openimcc parent activities, constant-\(\gamma\)
table, and Ellingham segments without declared conversion. Kress and openimcc must share
the same \(T\) and composition basis on a tick; if not, flag `activity_basis_unmatched`
and refuse equality.

**Fe³⁺-first reduction** (b-645) competes with Na stabilizing Fe³⁺. Under F1: report Fe³⁺
plateau from Kress+alkali term; flag missing ferric-associate feedback. Under F2: competition
becomes model-internal.

### 3.3 Migration order from today’s sources

| Order | Move | Leaves behind |
|---|---|---|
| **M-a** | Wire openimcc parent \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) as primary FLAGGED source for the solve + any alkali evaporation rail | Ad-hoc ideal \(10^{-8}\) floats in process examples |
| **M-b** | Keep CALPHAD / core \(a_{\mathrm{FeO}}\) as M2/IW authority; do not replace with openimcc ferrous until fixed-point identity fixtures pass on both | Dual FeO authorities disagreeing on one tick |
| **M-c** | Point vapour / SiO rails at the **same** activity map the solve used this tick (committed composition snapshot) | Per-rail MELTS/MAGEMin one-shots disagreeing with the root |
| **M-d** | Ferric: F1 Kress+alkali term first; schedule F2 associates behind battery |
| **M-e** | Certify openimcc via KEMS battery (§6); replace FLAGGED with certified table |
| **M-f** | Only then consider retiring constant-\(\gamma\) ladder from production path (keep as debug fallback) |

MAGEMin/MELTS: diagnostic / cross-check only until migration M-c proves single-map
discipline.

### 3.4 Structural sites / peralkaline step (folded)

Na⁺/K⁺ first fill charge-balancing sites of tetrahedral Al³⁺ (and Fe³⁺ when present).
Beyond Na+K ~ Al (corrected for Ca/Mg: preference K > Na > Ca > Mg), further alkali is a
network modifier at much higher activity — a **step** in \(a_{\mathrm{Na_2O}}\) vs dose.
Shuttle \(\Delta G\to 0\) arrives early because of that step. **10 wt% Na₂O stoichiometric
cap remains dropped.** Site saturation is explanatory, not a clip.

Declared 100 kg mare fixture (`lunar_mare_low_ti`): undosed (Na+K)/Al = 0.057;
peralkaline boundary at **+128.6 mol Na₂O** (~5.91 kg Na) while FeO still remains.
openimcc \(a_{\mathrm{Na_2O}}\) rises ~6 orders undosed → deep peralkaline (alkali-r2 §2.3
table; FLAGGED).

---

## 4. EXCHANGE AND KINETICS — film + whole-melt (d-058)

### 4.1 Gas exchange (r3) — unchanged skeleton

- Finite two-film law (gas film \(k_g\) + melt-side inventory film \(k_m\)).
- Error-controlled exponential hour integrator (chunk 7).
- Exchange regimes E0–E5; zero commit publishes gas; nonzero commit publishes accepted
  endpoint root.
- SiO / surface release reads **only** `interface_pO2_bar` (committed-interface contract).
- Hard vacuum (E0) still wins over E3–E5.

Alkali vapour exchange uses the **same** headspace and film machinery: \(p_{\mathrm{Na}}\)
equilibrium from the melt solve is the melt-side target the alkali film drives toward;
condenser removal is a headspace sink (element-balance term). **Passive alkali film is
later** (Q-D); first landing is shuttle chemistry + O₂ film as today.

### 4.2 Whole-melt limit — lance-zone fork CLOSED

| Closed option | Disposition |
|---|---|
| LZ-A local \(V_L\), imposed \(p_{\mathrm{Na}}=0.01\) | **CLOSED** — not implemented |
| LZ-B whole-melt single \(p_{\mathrm{Na}}\) | **SELECTED by d-058** (this is the plan) |
| LZ-C continuous delivery setpoint as local pressure | **CLOSED** as equilibrium volume; delivery remains a **dose-rate write** into the whole-melt ledger |

Validity condition and `recipe_breaks_whole_melt_eq` — §1.2.

### 4.3 Worked SiO / process numbers under d-058 closure

All at \(1150^\circ\mathrm{C}\), \(P_g=1\,\mathrm{mbar}\) (d-052), seat \(K_{\mathrm{Na}}\),
\(K_\times\). Formal SiO amp vs 1 mbar \(=\sqrt{P_g/f\mathrm{O_2}}\) at fixed silica
activity — **not** an achievable flux multiplier.

**While M2 holds**, \(f\mathrm{O_2}\) is set by IW at **current** \(a_{\mathrm{FeO}}\);
SiO amp tracks FeO depletion. Equilibrium \(p_{\mathrm{Na}}\) is **read from** \(K_\times\),
not declared:

| Stage (illustrative) | \(a_{\mathrm{Na_2O}}\) | \(a_{\mathrm{FeO}}\) | \(p_{\mathrm{Na}}^{\mathrm{eq}}\) / bar | \(f\mathrm{O_2}\) / bar | Formal SiO amp |
|---|---:|---:|---:|---:|---:|
| openimcc undosed, \(a_{\mathrm{FeO}}\sim0.17\) | \(1.45\times10^{-14}\) | 0.17 | \(1.33\times10^{-7}\) | \(1.53\times10^{-14}\) | \(2.55\times10^{5}\) |
| ~1 kg Na (γ-table \(a\)) | \(1.03\times10^{-9}\) | 0.15 | \(3.78\times10^{-5}\) | \(1.19\times10^{-14}\) | \(2.89\times10^{5}\) |
| ~4 kg Na | \(1.04\times10^{-8}\) | 0.11 | \(1.40\times10^{-4}\) | \(6.42\times10^{-15}\) | \(3.95\times10^{5}\) |
| near FeO exhaust | \(6.01\times10^{-8}\) | \(10^{-3}\) | \(3.53\times10^{-3}\) | \(5.31\times10^{-19}\) | \(4.34\times10^{7}\) |
| deep mutual-eq style | \(2.77\times10^{-8}\) | \(6.92\times10^{-5}\) | \(9.13\times10^{-3}\) | \(2.54\times10^{-21}\) | \(6.27\times10^{8}\) |

Notes:

- At deep reduction, \(p_{\mathrm{Na}}^{\mathrm{eq}}\sim9\times10^{-3}\) bar **exceeds**
  \(P_g=10^{-3}\) bar. Under the 1 mbar hold without raising bulk \(P_g\), headspace cannot
  host that partial as a pure-Na gas: either \(P_g\) rises, mole fraction is capped with
  other gases, or condenser + delivery kinetics keep the **actual** interface \(p_{\mathrm{Na}}\)
  below the unconstrained equilibrium. The solve must publish both the **unconstrained
  equilibrium partial** and the **exchange-limited interface partial**, and flag
  `alkali_p_headspace_capped` when they diverge.
- **Superseded contrast:** declared \(p_{\mathrm{Na}}=0.01\), \(a=10^{-8}\) gave
  \(f\mathrm{O_2}=2.295\times10^{-22}\), SiO amp \(2.09\times10^{9}\). That line is **not**
  a process claim under d-058.

**Pure M6 (iron couple gone),** \(p_{\mathrm{Na}}\) from solve with headspace cap at \(P_g\):

| \(a_{\mathrm{Na_2O}}\) | \(p_{\mathrm{Na}}=P_g=10^{-3}\) | \(f\mathrm{O_2}\) / bar | Formal SiO amp |
|---:|---:|---:|---:|
| \(1.05\times10^{-8}\) | \(10^{-3}\) | \(2.53\times10^{-18}\) | \(1.99\times10^{7}\) |
| \(6\times10^{-8}\) | \(10^{-3}\) | \(8.26\times10^{-17}\) | \(3.48\times10^{6}\) |

Headspace Na inventory at 1423 K is tiny vs melt dose
(\(n=pV/RT\): at \(V=1\,\mathrm{m^3}\), \(p=10^{-3}\) bar → \(8.45\times10^{-3}\) mol).
Alkali lost to condenser is still an element-balance term because cumulative hours remove
moles even when instantaneous gas inventory is small.

### 4.4 Mixing / dose rate numbers for the validity flag

| Parameter | Seat / design value |
|---|---|
| Tick \(\Delta t\) | 3600 s (hour) |
| \(\tau_{\mathrm{mix}}\) well-stirred | ~0.1–10 s (§1.2) |
| Flag threshold | \(\tau_{\mathrm{mix}}>0.1\Delta t\) (=360 s) or documented unmixed lance |
| Dose reacts within tick | Required; else `recipe_breaks_whole_melt_eq` |

---

## 5. ACCOUNTING INVARIANTS

1. **Element closure** \(10^{-12}\) mol per element per tick on every accepted titration /
   shuttle / exchange commit (same order as alkali-r2 / r3 noop discipline;
   `OXYGEN_RESERVOIR_NOOP_MOL=1e-15` remains the inventory presence floor, not the closure tol).
2. **One writer per quantity** — r3 chunk 9: one respeciation writer; passive exchange
   once per hour; melt equality/bound fields are pure functions of the post-commit ledger
   (refresh without re-rooting exchange).
3. **Committed-interface contract** — every release rail (SiO, surface O₂, alkali vapour
   when filmed) reads the stored `interface_pO2_bar` / committed alkali interface fields;
   never re-opens a diagnostic root; never substitutes melt equality or a bound.
4. **Flags and refusals — three categories:**
   - **Absence** — no equality (M0/M1/M3/M4; incomplete alkali pair; unavailable activity).
   - **Bound** — one-sided limit stored as bound, never floated as equality (M3/M4).
   - **FLAGGED number** — equality or activity published with certification/authority tier
     (openimcc uncertified; ideal Ti₂O₃; out-of-band Kress; `alkali_p_headspace_capped`;
     `recipe_breaks_whole_melt_eq`).
5. **Kelvin into Ellingham** on every production path.
6. **No TiO₂→Ti hop**; Ti 4a \(\xi_{\max}=\min(n_{\mathrm{Na}}/2,\,n_{\mathrm{TiO_2}}/2)\).
7. **Joint Na/K** before shared alkali equality (Q-C); no `min(fO2)` publish path.
8. **No Fe/SiO de-confliction claim** from M2 label alone (withdrawn).
9. **d-058** — no code path may introduce a separate lance-zone equilibrium volume.

---

## 6. EVIDENCE — battery rows that score each piece

Headline claims of this plan are scoreable; no invent of extract numbers.

| Claim piece | Battery / corpus hook | Pass idea |
|---|---|---|
| \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) vs composition, **peralkaline step** | Shornikov / Stolyarova / Zaitsev KEMS series (e.g. Zaitsev Na₂O–SiO₂ via `kems-019` / Tsaplin–Zaitsev basalt-bench rows; Stolyarova 1992 `kems-016` Na₂O–B₂O₃–SiO₂ / Na₂O–K₂O–SiO₂; Shornikov/Stolyarova CAS-adjacent alkali as landed) | Model vs measured \(a\) across metaluminous→peralkaline; certify Q-A |
| Fe³⁺/Fe²⁺ vs \(f\mathrm{O_2}\) **and alkali content** | Kress calibration set + alkali-bearing ferric redox rows in corpus | F1 residual; F2 only after associate evidence |
| Metal saturation / IW buffer | Existing r3 M2 fixtures; liquid-FeO IW seat identity | Fixed-point `IW+2log a_FeO` within 1e-6 |
| Vapour / SiO vs committed interface | r3 chunk 3 fixtures; zero-commit gas; nonzero endpoint | No diagnostic re-entry; amp tracks interface not equality |
| Titration plateaus on mare fixture | Declared `lunar_mare_low_ti` 100 kg synthetic fixture + openimcc/γ activities | Fe³⁺ plateau, Fe²⁺/Fe⁰ plateau, Ti/Cr/alkali region; closure 1e-12; waterfall only where ≥3 dex separation |
| Whole-melt \(p_{\mathrm{Na}}\) closure | Seat identity: \(p_{\mathrm{Na}}=\sqrt{a_{\mathrm{Na_2O}}/(K_\times a_{\mathrm{FeO}})}\) under M2; condenser debit changes Na tally | Mutation: impose 0.01 bar lance pressure → fail d-058 acceptance |
| Kelvin \(K_{\mathrm{Na}}\) | Production-path Ellingham at 1423.15 K | \(K_{\mathrm{Na}}=1.514901\times10^{-7}\) within 1e-6 rel; C-as-K mutation rejected |
| Selective Fe vs SiO | Coupled shuttle/film fixture (U8) | Bounds on Fe reduction flux vs SiO release; no regime-label assurance |

---

## 7. PLAN — luna-sized chunks after r3 chunk 8

Controller **continues r3 chunks 7–8** (not wasted). Unified-accounting chunks below are
ordered **after chunk 8**. Integrate with chunk 9 ordering (exchange after shuttle).
Default Q-D: shuttle chemistry first; passive alkali film later.

Chunk IDs use **U** (unified) to avoid confusion with alkali-r2 A-chunks; A0-style helpers
are absorbed.

**U0. Thermodynamic helpers (absorb A0).** Kelvin Ellingham→\(K_{\mathrm{Na}}/K_{\mathrm{K}}\);
parent activity conversion; corrected \(K_\times\); phase-reference checks;
**\(p_{\mathrm{Na}}\) from solve** helper (M2 rearrange + pure-alkali branch).  
Acceptance: \(K_{\mathrm{Na}}(1423.15\,\mathrm{K})=1.514901\times10^{-7}\) within 1e-8 rel;
C-as-K mutation rejected; imposing declared 0.01 bar in the helper fails d-058 unit test.

**U1. Element-ledger state + derived oxidation map.** Document/implement the derivation
API: ledger in → speciation key out; dose write API without lance volume.  
Acceptance: dose credits Na moles to melt/headspace tallies only; no \(V_L\) field in
tick record. Mutation: add lance-zone moles as equilibrium state → rejected.

**U2. Shared-\(\mu_{\mathrm{O_2}}\) titration root (iron-only first).** Bracketed root
reproducing M0–M5 iron-only limit; bound/absence semantics unchanged.  
Acceptance: r3 discriminating fixtures still green; waterfall-vs-simultaneous fixture
shows ≥7 dex error when freezing initial IW under FeO depletion (C3 failure mode).

**U3. Alkali potentials inside the same root (absorb A1–A3).** Complete same-species
pair; joint Na/K; M2 keeps IW formula at **current** \(a_{\mathrm{FeO}}\); publish
\(p_{\mathrm{Na}}^{\mathrm{eq}}\) from §2.2.  
Acceptance: mare/dose tables reproduce seat \(p_{\mathrm{Na}}\) within tolerance;
Na₂O+K-only → `alkali_couple_incomplete`. Mutation: `min(fO2)` publish path → fail.

**U4. openimcc activity wire (M-a) + F1 ferric path.** FLAGGED associate \(a_{\mathrm{Na_2O}}\);
Kress+Na₂O/K₂O ferric term; basis audit.  
Acceptance: peralkaline sweep monotonic in the expected direction on mare fixture;
`activity_basis_unmatched` when bases diverge.

**U5. Ti path + competitors (absorb A5/A4 Ti parts).** 4a then optional 4b;
\(\xi_{\max}\) with /2; Si/Mg prediction-limit flags.  
Acceptance: old Ti hop rejected by explicit Ti₂O₃ assert; Si/Mg flags fire when omitted.

**U6. Bounded shuttle root; retire 10 wt%/0.75 (absorb A4).**  
Acceptance: interior \(\Delta G=0\) root; typed failures; solubility warning ≠ stop.

**U7. Titration-curve acceptance on mare fixture (REQ §7).** \(\log f\mathrm{O_2}\) vs
added Na: Fe³⁺ plateau, Fe²⁺/Fe⁰ plateau, Ti/Cr/alkali region; element closure
\(10^{-12}\) mol at every point; waterfall agreement only where couples ≥3 dex apart;
**\(p_{\mathrm{Na}}(\xi)\) from solve** plotted beside \(f\mathrm{O_2}\).  
Mutation: freeze \(p_{\mathrm{Na}}=0.01\) → d-058 acceptance fails.

**U8. Coupled shuttle/film + selective-flux (absorb A8; Q-D).** Until green: no Fe/SiO
assurance in reports.  
Acceptance: selective Fe reduction vs SiO release under whole-melt \(p_{\mathrm{Na}}\) +
finite M2 film within declared bounds; headspace-cap flag exercised.

**U9. Consumer absence widen + refresh metadata.** Alkali activity/\(p\) unavailable;
align with r3 chunk 5/8 absence-guard cleanup.

**U10. One-writer / chunk-9 integration.** Prove shuttle-before-exchange ordering with
alkali debit affecting this hour’s exchange; no second ferric writer.

**U11. openimcc certification path (absorb A10).** Battery score peralkaline step; lift
Q-A flag when pass criteria met.

**U12. (Later) F2 ferric associates + passive alkali film.** Not first landing.

Studio SiO CPU remains out of VPS scope (r3 post-chunk Studio measurement still applies
after 7/8).

### Runnable mutation sketch (U0 / d-058)

```python
import math
from simulator.chemistry.ellingham_thermo import ellingham_delta_g_kj_per_mol_o2
from simulator.fe_redox import feo_iw_log10_fO2_bar

R = 8.314462618

def K_Na(T_K: float) -> float:
    dG_ox = ellingham_delta_g_kj_per_mol_o2("Na", T_K)  # Kelvin
    return math.exp(-(-0.5 * dG_ox * 1000.0) / (R * T_K))

def K_x(T_K: float) -> float:
    iw = feo_iw_log10_fO2_bar(T_K, a_feo=1.0)
    K_FeO = math.sqrt(10 ** iw)
    return K_FeO / K_Na(T_K)

def p_Na_whole_melt_M2(a_Na2O: float, a_FeO: float, T_K: float, a_Fe: float = 1.0) -> float:
    """d-058: equilibrium p_Na over whole melt — derived, not declared."""
    return math.sqrt(a_Na2O * a_Fe / (K_x(T_K) * a_FeO))

def acceptance():
    T_K = 1423.15
    assert abs(K_Na(T_K) / 1.514901e-7 - 1.0) < 1e-6
    # Mutual-eq style point from alkali-r2 (a_FeO at former declared p=0.01 line)
    p = p_Na_whole_melt_M2(1e-8, 2.079188e-5, T_K)
    assert abs(p / 0.01 - 1.0) < 1e-3  # identity: solve recovers former point
    # Undosed-ish: p_Na is NOT 0.01
    p2 = p_Na_whole_melt_M2(1.45e-14, 0.17, T_K)
    assert p2 < 1e-5

def mutation_impose_lance_pressure():
    # Forbidden under d-058: treat 0.01 bar as input rather than output
    declared = 0.01
    T_K = 1423.15
    a, a_feo = 1.45e-14, 0.17
    p_eq = p_Na_whole_melt_M2(a, a_feo, T_K)
    assert abs(declared / p_eq - 1.0) < 0.1  # should FAIL

acceptance()
try:
    mutation_impose_lance_pressure()
    raise AssertionError("declared lance p_Na mutation survived")
except AssertionError as e:
    if "survived" in str(e):
        raise
print("d-058 mutation rejected; acceptance OK")
```

---

## 8. Mapping to tickets / rulings

| ID | This design |
|---|---|
| **d-058** | Whole-melt titration; lance-zone fork **CLOSED**; \(p_{\mathrm{Na}}/p_{\mathrm{K}}\) from solve; mixing validity + flag; SiO numbers re-done §4.3 |
| d-056 | Alkali couple after iron / whenever dose+oxide live — **inside** unified solve |
| d-057 / Q-A..Q-D | openimcc FLAGGED; ideal Ti₂O₃ FLAGGED+JANAF; joint Na/K; shuttle-first / zero-commit gas; Fe/SiO withdrawn |
| d-052 | 1 mbar hold remains optimizer input; headspace may cap \(p_{\mathrm{Na}}\) |
| b-646 | ΔG-limited shuttle; no 10 wt%/0.75; no Ti skip; bounded root |
| b-645 | Ferric-first sequencing (shuttle lane); F1/F2 in activity §3 |
| b-618 | M1 / no-couple absence; unified solve does not invent a couple |
| r3 chunks 7–8 | Continue now; not wasted |
| r3 chunk 9 | Hard dependency for U-chunks (exchange after shuttle) |
| alkali-r2 LZ-A/B/C | **Superseded / CLOSED** by d-058 |

---

## 9. Open questions **after** d-058

Lance-zone equilibrium volume options are **not** open.

Remaining (narrow; do not block design review):

1. **Headspace cap policy when \(p_{\mathrm{Na}}^{\mathrm{eq}}>P_g\):** raise \(P_g\), dilute
   with other gases, or condenser-limited interface — pick at implementation of U3/U8
   with a single flagged default (`alkali_p_headspace_capped` + exchange-limited interface
   recommended default).
2. **F2 ferric-associate schedule** — after Q-A battery certification, not before.
3. **Passive alkali film** — explicitly later (Q-D); not first landing.
4. **Mixing-time estimator inputs** (geometry, stir rate) — process/equipment fields;
   until present, use conservative flag on unmixed recipes only.

No LZ-A/B/C choice remains for the owner.

---

## 10. Dropped or changed vs alkali-r2 / r1

| Prior item | This r2 disposition |
|---|---|
| Lance-zone fork LZ-A default / LZ-B / LZ-C | **CLOSED by d-058**; whole-melt only |
| Declared lance \(p_{\mathrm{Na}}=0.01\) in process examples | **Replaced** by \(p_{\mathrm{Na}}\) from solve; §4.3 tables |
| Bolt-on M6 as separate solver | **Absorbed** into unified shared-\(\mu_{\mathrm{O_2}}\) root; M2 not renamed under dose |
| Waterfall thermodynamics | **Simultaneous** titration; waterfall = well-separated limit only |
| Fe/SiO assurance | **Still withdrawn** (U8) |
| 10 wt% Na₂O / 0.75 accessibility stops | **Still dropped** |
| Celsius-as-Kelvin / inverted \(K_\times\) / Ti hop / \(p\to0\Rightarrow f\mathrm{O_2}\to0\) | **Still corrected** (sol) |
| Dual activity shopping across rails | **Forbidden**; migration M-a..M-f |

---

## 11. Review disposition

Author design for regolith-physics: **melt redox accounting r2**. Answers REQ sections
1–7. Folds d-058 whole-melt closure with re-done SiO/process numbers and explicit
lance-zone **CLOSED**. Seat: `slot-z14` @ `696299350e98b67f786c334b4b4ccc8856149379`.
Independent second opinion before implementation. No stack2 product commits in this seat.

— regolith-empirical, 2026-10-01 ~20:35 ET
