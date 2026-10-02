# Melt redox accounting r3 — unified plan (edit of r2)

Design-only. No stack2 product code in this delivery. **EDIT of r2** that folds two
independent UNSOUND reviews (sol; local grok, blind to authoring) and the controller
synthesis in `REQ-melt-redox-accounting-r3-from-regolith-physics-2026-10-01.md`.
See **§12 Dropped or changed from r2** for the explicit diff.

Folds:

- melt-redox-accounting DESIGN r2 (whole-melt direction, corrected constants, Ti divisor,
  dropped 10 wt% cap, withdrawn regime-label Fe/SiO assurance — all retained)
- **Controller synthesis** of sol + grok reviews (agreements + resolved Fe–FeO disagreement
  + openimcc domain finding)
- **Corrected decision ids** (NOTICE 2026-10-01):
  - Q2 alkali couple = **d-057** (was mislabelled d-056 in r2)
  - Q-A..Q-D = **d-058** (was mislabelled d-057 in r2)
  - stirred whole-melt = **d-059** (was mislabelled d-058 in r2)
  - Real **d-056** is regolith-main admission-default — **do not reuse**
- sol r1 / r2 corrections still in force (Kelvin Ellingham, \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\),
  Ti extent, phase references)
- d-052 1 mbar hold remains optimizer input

Seat numerics: `slot-y17` @ green `696299350e98b67f786c334b4b4ccc8856149379` (matches
`origin/work-v064-green` at write time). Ellingham Kelvin, liquid-FeO IW, parent
activity conversion. openimcc / γ numbers cited from review probes are **FLAGGED /
extrapolated** (see §3.5). No invent of extract data or equipment FKs. Ferric-first
(b-645) sequencing remains owned on the shuttle lane; this plan states how Fe³⁺ enters
the **unified solve**.

Both reviewers will re-check r3 under a NOT-FIXED lens before implementation.

North Star unchanged: predict-and-flag, ledger closure, derivation beside every threshold.

---

## 0. What this plan is / is not

| Is | Is not |
|---|---|
| ONE element-ledger state; oxidation states derived | Parallel fO₂ scalars or regime-named “truth” |
| ONE shared-\(\mu_{\mathrm{O_2}}\) (+\(p_{\mathrm{Na}}/p_{\mathrm{K}}\)) titration root on **element-balance residual** | Waterfall of independent couple equalities; interior \(\Delta G=0\) extent search |
| ONE activity model for solve + evaporation rails | Per-rail activity shopping |
| Whole-melt equilibrium per tick (**d-059**) | Separate lance-zone equilibrium volume (LZ-A/B/C **CLOSED**) |
| Equilibrium **targets** vs actual headspace **inventory** / film-written interface | Treating headspace as both instantaneous eq compartment and finite-exchange compartment without split |
| Luna-sized chunks after r3 chunks 7–8 land | Stack2 product commits in this seat |
| Alkali couple as a **live couple inside the solve** | Renaming M2 to M6 whenever Na is dosed |
| Fe–FeO equality as **titration plateau → steep fall near FeO exhaustion** | Asserting equality “preserved” or “lost” while metal+FeO coexist |

**Supersedes** alkali-r2 §4 owner fork (LZ-A default / LZ-B / LZ-C) and r2’s interior
\(\Delta G=0\) U6 acceptance / mixed §4.3 dose oracle. Still-valid r2 content (Kelvin \(K\),
joint Na/K, sites as explanatory, Fe/SiO withdrawal, chunk helpers shape) is folded
below under the unified numbering with corrected decision ids.

---

## 1. STATE — element ledger only; oxidation states derived

### 1.1 Ledger partitions

The **only** persisted redox state is the element (and phase-bucket) ledger:

| Bucket | Holds | Notes |
|---|---|---|
| **Melt** | Element moles in the liquid oxide solution (Si, Ti, Al, Fe, Mg, Ca, Na, K, Cr, Mn, P, O, …) | Speciation / oxidation states are **derived**, not stored as independent truth |
| **Metal** | Condensed metal (Fe⁰ primarily; later alloy out of scope) | Presence opens Fe²⁺/Fe⁰ coexistence when FeO also present |
| **Gas / headspace** | O₂, Na(g), K(g), SiO(g), Mg(g), … **actual inventories** | Coupled to condenser; **not** identically the equilibrium targets |
| **Dose / lance delivery** | Process write of reductant into the system this tick | **Not** a separate equilibrium volume under d-059 |
| **Condenser / sinks** | Captured alkali (and other) vapour | **Explicit** element-balance removal term (surplus alkali destination) |

Derived each tick (and after each authorized writer): Fe³⁺/Fe²⁺/Fe⁰, Ti⁴⁺/Ti³⁺/Ti⁰,
Cr³⁺/Cr²⁺, Na⁺/Na⁰, K⁺/K⁰, Si⁴⁺/Si⁰(/SiO), Mg²⁺/Mg⁰ — from the shared solve + activity
model at the current composition. Never store a second fO₂ that can disagree with the
ledger inverse / buffer identity.

**Committed inventory vs equilibrium partition (sol P1):** r3’s M0–M5 predicates classify
the **committed** species inventory (which can be away from full equilibrium). The unified
solve’s equilibrium partitioning may move an M4-looking inventory toward M2 (e.g.
\(3\mathrm{FeO}\rightleftharpoons\mathrm{Fe_2O_3}+\mathrm{Fe}\)). Preserve r3 absence/bound
contracts for **classification of the committed inventory**; do **not** claim every
first-match predicate survives unrestricted equilibration unchanged. Report both:
committed-regime label and post-solve partition when they diverge.

### 1.2 How lance / dose appears **without** a local equilibrium volume (d-059)

**Lance-zone fork CLOSED.** There is no \(V_L\), no local \(p_{\mathrm{Na}}=0.01\,\mathrm{bar}\)
imposition, and no separate root on a lance sub-volume.

Dose and lance enter the ledger as:

1. **Delivery write** — moles of Na (and/or K) credited to the system this tick
   (condensed reductant mixed into the melt, or vapour entering the **shared** headspace).
2. **Whole-melt titration** — the redox solve uses the **entire** melt + metal as one
   condensed equilibrium zone per tick; headspace contributes **targets and inventory
   constraints**, not a second instantaneous-eq volume (see §4.1).
3. **Equilibrium \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\)** — **outputs** of the solve (boxed
   identity while M2), never recipe constants.
4. **Alkali lost as vapour** — element-balance term: film writes interface → condenser
   (or ballistic escape) removes Na/K from the closed tally; the solve sees that debit
   as an **explicit sink**, not an unspecified clip.

Mixing validity (required statement under d-059):

\[
\tau_{\mathrm{mix}} \ll \Delta t_{\mathrm{tick}},
\qquad
\text{dose reacts within the tick}.
\]

Seat sketch for a stirred bath: \(\tau_{\mathrm{mix}}\sim h/u_{\mathrm{stir}}\) with
\(h\sim0.1\text{–}0.5\,\mathrm{m}\), \(u\sim0.05\text{–}1\,\mathrm{m/s}\) →
\(\tau_{\mathrm{mix}}\sim0.1\text{–}10\,\mathrm{s}\) vs \(\Delta t=3600\,\mathrm{s}\)
(hour tick) — typically **3+ orders** of margin when stirring while lancing.

**Flag** `recipe_breaks_whole_melt_eq` when:

- estimated \(\tau_{\mathrm{mix}} > 0.1\,\Delta t_{\mathrm{tick}}\); or
- instantaneous dose rate exceeds mix-limited capacity so a stratified reductant pocket
  would persist through the tick; or
- operator disables stirring / documents unmixed lance.

**d-059 predict-and-flag (changed from r2):** out-of-domain mixing must **PREDICT** the
whole-melt \(p_{\mathrm{Na}}\) / \(f\mathrm{O_2}\) **with the flag**, not withhold the
equality publish. Lance-zone equilibrium is closed, so the whole-melt number *is* the
prediction. Absence remains correct for M0/M1/M3/M4 and incomplete alkali pairs (no
couple); it is the wrong outcome for a couple evaluable under a violated mixing assumption.

### 1.3 Speciation key (carry-over from r3)

Oxidation-state labels used in reports are a **speciation key** over the ledger, not a
second state. Predict-and-flag: when the activity model cannot support a couple (no
ferric associates, missing \(a_{\mathrm{Ti_2O_3}}\) certification, out-of-domain T, …),
publish the derived quantity as FLAGGED or typed-unavailable — never invent inventory.

---

## 2. ONE redox solve — shared oxygen (+ alkali) potential

### 2.1 Formulation — element-balance residual; extent is the unknown

One shared oxygen potential \(\mu_{\mathrm{O_2}}\) (\(\log f\mathrm{O_2}\)) for the whole
melt each tick. When alkali gas/dose is present with alkali oxide in the melt, also
alkali potentials (\(p_{\mathrm{Na}}\), \(p_{\mathrm{K}}\) — or joint Na/K constraints per
Q-C / **d-058**).

**While metal and FeO are both above NOOP (M2 live):**

- The **monotonic unknown** is the condensed **extent** \(\xi\) (dose consumed /
  FeO reduced / oxygen transferred among Fe states).
- \(f\mathrm{O_2}\) and \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\) are **outputs**:
  \(f\mathrm{O_2}=\mathrm{IW}(a_{\mathrm{FeO}}(\xi))\);
  \(p_{\mathrm{Na}}\) from the boxed identity (§2.2).
- There is **no second root** for \(p_{\mathrm{Na}}\) during M2, and **no interior
  \(\Delta G_\times=0\) search**: once \(p_{\mathrm{Na}}\) follows the boxed identity,
  \(\Delta G_\times(\xi)/RT=\ln(Q_\times/K_\times)=0\) at **every** trial extent
  (sol verified for FeO inventories 99…0.001 mol; residuals \(\sim10^{-16}\)).

**Determining residual (openimcc-style element balance vs extent):**

\[
R_{\mathrm{Na}}(\xi)
=
D_{\mathrm{Na}}
- 2\,n_{\mathrm{Na_2O}}(\xi)
- \underbrace{\frac{p_{\mathrm{Na}}^{\mathrm{eq}}(\xi)\,V}{RT}}_{\text{eq target inventory, diagnostic}}
- n_{\mathrm{Na,out}}(\xi)
\;=\;0
\]

with independent \(R_{\mathrm{K}}\) when K is live; oxygen / Fe complementarity
constraints; and the declared headspace policy binding \(n_{\mathrm{Na,out}}\) /
\(\sum p_i\le P_g\) (see §4.1). The Knudsen bridge’s effusion-flux oxygen balance is
**not** this residual (fixed supplied activities there); uniqueness here requires
monotonic \(a_{\mathrm{FeO}}(\xi)\) (observed in both γ and openimcc sweeps in the reviews)
plus explicit phase complementarity and inventory bounds.

**Residual contract (must be written in the implementer API, not only prose):**

| Item | Requirement |
|---|---|
| Residual vector | \(R_{\mathrm{Na}}\), \(R_{\mathrm{K}}\) (if live), oxygen / Fe complementarity; ferric oxygen from Kress as **flagged post-step** at fixed \(f\mathrm{O_2}(\xi)\), not a second Gibbs unknown |
| Bracket | On **extent** (not stiff \(\mu_{\mathrm{O_2}}\) across a buffer plateau); feasible interval from dose / FeO / headspace-cap endpoints |
| Endpoint inequalities | Stop when FeO → NOOP, dose exhausted, or headspace policy binds; publish which binder won |
| Failure behaviour | Typed refusal (missing/invalid input) or FLAGGED prediction (out-of-domain physics); rollback unsolved numerical candidates; never silent zero / never certified equality on failure |
| After iron exhausted | One alkali residual with \(p\) capped by the same headspace policy; K₂O refused until a liquid (or converted) reference exists |

Every couple at equilibrium with that **same** \(\mu_{\mathrm{O_2}}\) at the **current**
composition:

| Couple | Role in solve |
|---|---|
| Fe³⁺/Fe²⁺ | Kress-type (carries Na₂O/K₂O term); ferric path §3; FLAGGED below Kress floor (~1200°C) |
| Fe²⁺/Fe⁰ | IW buffer when metal+FeO coexist |
| Cr | Secondary; simultaneous when live |
| Ti⁴⁺/Ti³⁺(/TiO/Ti₃O₅)/Ti⁰ | JANAF intermediates; no TiO₂→Ti hop (Q-B / **d-058**); see §2.6 |
| Na₂O/Na(g), K₂O/K(g) | Alkali couple (**d-057**); joint before publish (Q-C / **d-058**) |
| SiO₂/Si, MgO/Mg(g) | Competitors tied to sign of \(\Delta G\) with stated references (§2.6) |

Tolerances: relative pin on \(K_{\mathrm{Na}}\) uses the **executable** value
(\(1.51490112\times10^{-7}\) at 1423.15 K — see §5); scale-aware element closure
(§5) instead of unconditional \(10^{-12}\) mol absolute.

Titration **plateaus** emerge where couples are separated by many log units **and**
capacities allow (§2.5). Overlapping couples partition simultaneously. Waterfall is
only the well-separated limit (≥3 dex default for “waterfall agreement” acceptance —
necessary but not sufficient; stoichiometry and capacities also matter).

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
| \(K_{\mathrm{Na}}\) (executable) | \(1.51490112\times10^{-7}\) |
| \(K_{\mathrm{Na}}\) (rounded display) | \(1.514901\times10^{-7}\) |
| \(K_{\mathrm{FeO}}\) (liquid-FeO IW) | \(7.28602284\times10^{-7}\) |
| \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\) | **4.809570** |

Cross equilibrium \(2\mathrm{Na(g)}+\mathrm{FeO}=\mathrm{Na_2O}+\mathrm{Fe}\):

\[
K_\times(T)=\frac{a_{\mathrm{Na_2O}}\,a_{\mathrm{Fe}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2\,a_{\mathrm{FeO}}}
=\frac{K_{\mathrm{FeO}}(T)}{K_{\mathrm{Na}}(T)}.
\]

**Under d-059**, when M2 is live, rearrange for the equilibrium partial over the whole melt:

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

### 2.3 Phase / standard-state contract (tightened)

| Species / helper | Seat fact at 1423.15 K | Action |
|---|---|---|
| Na seat | Active segment `4 Na(g)+O₂→2 Na₂O(l)` on (1405.2, 1800) K; \(K_{\mathrm{Na}}\) already gas-metal / liquid-oxide | OK for Na at C3 |
| Na₂O below 1405.2 K | Solid alpha | Convert with stated \(\Delta G\) or refuse (`alkali_phase_reference_unmatched`) |
| K₂O | `4 K(g)+O₂→2 K₂O(cr)` — **no liquid segment** at 1423 K | Convert with stated \(\Delta G\) or refuse; joint Na/K equality **cannot publish** for a K dose until conversion exists |
| Fe Ellingham `"Fe"` | `2 Fe(γ)+O₂→2 FeO(s)`; \(\log_{10}f\mathrm{O_2}=-13.23\) at unit \(a\) — **0.96 dex below** liquid-FeO IW (−12.275) | Helpers must call `feo_iw_log10_fO2_bar` for \(K_{\mathrm{FeO}}\); refuse silent `ellingham("Fe")` as liquid-IW |
| Si | `Si(s)+O₂→SiO₂(II)` solid | Competitor \(\Delta G\) uses solid-oxide reference; flag says so |
| Mg | `MgO(s)` | Same |

Parent-oxide conversion \(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2\) alone does **not** convert
a crystalline reference into a liquid reference.

### 2.4 Iron-only limit = r3 regime table M0–M5 (committed inventory)

When alkali couple is **absent** (no complete same-species Na/K pair) and no other
authorized non-iron couple is live, the unified solve’s **committed-inventory
classification** **reduces to** r3’s iron story. Mapping:

| Regime | Iron-only limit of unified solve | Equality / bound / absence |
|---|---|---|
| **M0** `not_liquid` | Liquidus gate false | **Absence** (no bound) |
| **M1** `no_modelled_redox_couple` | Liquid; FeO≤NOOP; Fe₂O₃≤NOOP; alkali absent | **Absence**, flag `out_of_domain` (b-618) |
| **M2** `fe_feo_buffer` | Metal+FeO > NOOP | **Equality** `IW+2\log_{10}(a_{\mathrm{FeO}})` at **current** \(a_{\mathrm{FeO}}\) (moves under dose / depletion). Alkali cross-reaction, if dose present, is **inside** the shared root — it does not rename M2 |
| **M3** `ferrous_free_lower_bound` | FeO≤NOOP, Fe₂O₃>NOOP | **Absence**; flagged **lower bound** (edge), not equality |
| **M4** `fe_saturation_bound` | Fe₂O₃≤NOOP, FeO>NOOP, metal≤NOOP | **Absence**; flagged **lower bound** (R-b number as bound) |
| **M5** `kress91_inverse` | Both FeO and Fe₂O₃ > NOOP | **Equality** from unclamped mole-log inverse |

**Bound / absence semantics, speciation key, predict-and-flag** carry over from r3 §1–§3
for the committed inventory: constructors reject equality on M0/M1/M3/M4; consumers treat
absence as one condition; vapour reads `interface_pO2_bar`, never a bound or melt
equality substituted into SiO.

**Equilibrium partitioning note:** unrestricted equilibration of an M4-looking inventory
may produce metal+ferric (→ M2 after solve). That is a model-consistency outcome, not an
experimental disproportionation claim. Product absence (e.g. zero initial K₂O) needs
**reaction endpoint / complementarity** treatment; it cannot universally disable the
equilibrium reaction.

**Alkali-present enrichment (not a bolt-on M6 rename of M2):**

- While metal+FeO remain → stay **M2**; published iron equality is still IW-scale at
  current \(a_{\mathrm{FeO}}\); joint alkali potentials are additional solve **outputs**.
- When iron couple gone but complete alkali pair live → publish alkali equality from the
  same root (former “M6” **label** may remain as a report tag; not a second solver).
- Incomplete alkali pair → flag `alkali_couple_incomplete`; do not publish alkali equality.

### 2.5 Fe–FeO equality along the titration — plateau then steep fall (resolved disagreement)

**sol:** continued reduction can move the Fe–FeO equality onto the alkali line “while FeO
and metal coexist” (example: 100 mol FeO, 250 mol Na → 0.069 mol FeO left,
\(f\mathrm{O_2}\sim1.8\times10^{-21}\) bar).

**grok:** under d-059, \(p_{\mathrm{Na}}\) is an OUTPUT; with either named activity model
\(a_{\mathrm{FeO}}\) stays \(\mathcal{O}(0.05)\) until most FeO moles are gone, so the
equality stays near \(\mathrm{IW}+2\log_{10}a_{\mathrm{FeO}}\) (openimcc path: +0.03 dex at
the peralkaline dose, −2.0 dex at 10.5 kg with 9.6 of 236 mol FeO left). sol’s ~7.8 dex
shift is the **fixed** \(p_{\mathrm{Na}}=0.01\) bar calculation.

**Controller resolution — both are the same titration curve:**

The Fe–FeO equality is a **PLATEAU** while FeO is abundant and falls **steeply only as
FeO approaches exhaustion** (sol’s cited state has ~0.07% of the FeO left). r3 must
**show the curve** and quantify how much of the Fe inventory can be extracted before the
interface \(p\mathrm{O_2}\) falls far enough to raise SiO release by a given factor — a
**coating budget** — rather than asserting either “preserved” or “lost”.

Illustrative coating-budget framing (process numbers are FLAGGED / model-dependent;
acceptance uses one-call activity pairs, not the superseded r2 §4.3 mixed oracle):

| Region | \(a_{\mathrm{FeO}}\) behaviour | Fe–FeO equality | SiO / coating note |
|---|---|---|---|
| Early / mid dose (most FeO still present) | \(\mathcal{O}(0.05\text{–}0.2)\) | **Plateau** near undosed IW+\(2\log a_{\mathrm{FeO}}\) (dex change ≪ 1 under openimcc / γ paths) | Formal SiO amp stays \(\sim10^{5}\)–\(10^{6}\) order at \(P_g=1\) mbar |
| Near FeO exhaustion | Falls toward \(10^{-3}\)…\(10^{-5}\) and below | **Steep fall** (many dex) | Formal amp rises sharply; coating budget spent here |
| Imposed \(p_{\mathrm{Na}}=0.01\) (superseded) | Forced \(a_{\mathrm{FeO}}\sim2\times10^{-5}\) | ~7–8 dex shift while inventing that partial as input | **Not** a process claim under d-059 |

U7 / U8 acceptance: plot \(\log f\mathrm{O_2}(\xi)\), \(a_{\mathrm{FeO}}(\xi)\),
\(p_{\mathrm{Na}}^{\mathrm{eq}}(\xi)\), and cumulative Fe extracted vs SiO release factor;
bound “Fe moles extractable before SiO amp rises by \(X\)” — no regime-label assurance.

### 2.6 Competitors — Si / Mg / Ti intermediates

Keep the no-direct-Ti-hop rule and corrected \(\xi_{\max}=\min(n_{\mathrm{Na}}/2,\,n_{\mathrm{TiO_2}}/2)\).

**Ti intermediates:** TiO and Ti₃O₅ cannot be dismissed universally. Seat JANAF
linear-interpolation signs at 1423.15 K (sol): \(\Delta G^\circ(\mathrm{Ti_3O_5}\to\mathrm{TiO_2}+\mathrm{Ti_2O_3})=+13.665\,\mathrm{kJ/mol}\);
TiO is ~30.36 kJ/mol Ti below Ti₂O₃+Ti at the same O/Ti. **Include** the relevant
reactions in the solve **or** explicitly limit the titration-region prediction and flag
`ti_intermediate_omitted`. Naming JANAF tables without a reaction treatment is insufficient.

**Si / Mg:** tie competitor flags to the **sign of \(\Delta G\)** with the **solid-oxide
references** stated in the flag text (prediction-limit signs, not certified margins).

- \(4\mathrm{Na}+\mathrm{SiO_2}\to 2\mathrm{Na_2O}+\mathrm{Si}\): quiet at typical §4
  partials under solid SiO₂ ref; can flip under extreme \(a_{\mathrm{Na_2O}}/p_{\mathrm{Na}}\).
- \(2\mathrm{Na}+\mathrm{MgO}\to\mathrm{Na_2O}+\mathrm{Mg(g)}\): **favorable** even at
  1 mbar with product removal (seat ~−47 to −101 kJ depending on \(p_{\mathrm{Mg}}\) /
  activities — sol/grok). A warning without competition in the balance can overallocate
  dose to Fe/Ti.

U5 acceptance: flag fires when \(\Delta G<0\) under stated refs **or** when reactions are
omitted from a prediction that claims the Ti/Cr/alkali region; not only when source omits
the reaction name.

### 2.7 Which r3 chunks stay valid vs superseded

| Chunk | Status under unified solve | Notes |
|---|---|---|
| **1–6** | **Stay** (already landed / reviewed) | Unavailable-buffer return; committed interface; mol predicates; bounds+absence B; metal film — iron-path load-bearing |
| **7** exponential integrator | **Stay; land first** | Gas-film + finite melt film; Jacobian of the **same** interface law. Whole-melt titration does not replace transport |
| **8** one tick + refresh | **Stay; land with / right after 7** | `solve_redox_tick` + post-mutation refresh; melt fields pure functions of ledger |
| **9** one respeciation writer | **Stay; hard predecessor of unified chemistry writer** | Exchange after shuttle/char/MRE/thermite before vapour — required before U2+ alkali writer activates |
| **10** R-e moles | **Stay** | Minority inventory > NOOP gate |
| **11** bulk fixed point | **Stay; predecessor of U2** | U2 “reuses the fixed point” is false until 11 has landed; do not land U2 on pre-chunk-11 \(a_{\mathrm{FeO}}\) |

**Controller continues chunks 7–8 now.** They are **not** wasted: the unified titration
mutates the ledger (extents); the film+integrator still decides `committed_d_mol` and
`interface_pO2_bar`. Alkali / unified U-chunks land **after** chunk 8, with chunk 9 before
the unified writer and chunk 11 before U2 (see §7).

### 2.8 Fe/SiO assurance — still withdrawn

Dose can eventually move M2 equality by many dex, but **only near FeO exhaustion** under
d-059 with named activity models (§2.5). Regime label alone does **not** de-conflict Fe
cleanup from SiO risk. Replace with coupled shuttle/film + **coating-budget** acceptance
(§7 chunk U8). Do not use superseded r2 ~\(10^{8}\) formal amp as a bound.

---

## 3. ONE activity model

### 3.1 Target: every activity the solve + evaporation rails use

| Activity | Target source | Flag / gap |
|---|---|---|
| \(a_{\mathrm{FeO}}\) | CALPHAD / core fixed point (r3); openimcc ferrous-class where wired | per r3; one-call with alkali \(a\) for acceptance tables |
| \(a_{\mathrm{Fe_2O_3}}\) / ferric | **Ferric path below** | Must not silently use ferrous-only openimcc |
| \(a_{\mathrm{Na_2O}}\), \(a_{\mathrm{K_2O}}\) | **openimcc associate** (Q-A / **d-058**), FLAGGED — **and T-domain extrapolated at C3** (§3.5) | Ladder fallback: constant-\(\gamma\) + parent \(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2\) (also out of γ point domain at 1423 K) |
| \(a_{\mathrm{TiO_2}}\) | Engines where available | Flag if absent |
| \(a_{\mathrm{Ti_2O_3}}\) | Ideal \(X\) (Q-B / **d-058**), FLAGGED + JANAF refs | Explicit reference states |
| \(a_{\mathrm{SiO_2}}\), \(a_{\mathrm{MgO}}\), \(a_{\mathrm{Cr_2O_3}}\) | Same ONE model family | Flag / ideal with prediction-limit |
| Pure metals | \(a=1\) outside melt model | Alloy dilution out of scope |

Structural speciation (associates): alkali/alkaline-earth charge-balancing of tetrahedral
Al and Fe³⁺; Na/K–Si network modifiers; **peralkaline activity rise** (explanatory, not a
stop — §3.4). These enter **only** through composition-dependent activities re-evaluated
as dose changes the melt — never as a second energetic benefit double-counted against
Ellingham.

### 3.2 openimcc candidate + ferric path

**openimcc `ext-v4`** (NaAlO₂, NaAlSiO₄, …, KAlO₂, …, Na₂SiO₃, …): working source for
\(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) (Q-A / **d-058**). Flag when publishing step-shape
or Fe³⁺ competition:

1. ferrous-only  
2. no metal  
3. no ferric associates  
4. ideal mixing among associates (smooths any peralkaline feature)  
5. **NEW (top alkali-couple certification item):** complex rows declared on
   **[1700, 3000] K**; C3 cool-Na window (~1423 K) is **outside domain** (§3.5)

**Ferric path (choose and keep standard states consistent):**

| Option | Mechanism | Why / when |
|---|---|---|
| **F1 (proposed first landing)** | Keep **Kress-type** Fe³⁺/Fe²⁺ relation **with its Na₂O/K₂O term** for the ferric ratio at given \(f\mathrm{O_2}\); openimcc supplies alkali (and ferrous-class) activities for the shared root | Explicitly **flagged empirical approximation**, not a proven common Gibbs model (sol P2). Avoids inventing NaFeO₂/KFeO₂ associates before battery evidence. Kress Na₂O coefficient 5.854 couples alkali content to Fe³⁺/Fe²⁺; ferrous projection supplies no ferric-site feedback to alkali \(a\) |
| **F2 (later certification)** | Add ferric associates (NaFeO₂, KFeO₂, …) when corpus + battery justify | Needed to quantify M⁺-stabilized tetrahedral Fe³⁺ competition; lift flags when certified |

**Standard-state consistency:** never mix openimcc parent activities, constant-\(\gamma\)
table, and Ellingham segments without declared conversion. Kress and openimcc must share
the same \(T\) and composition basis on a tick; if not, flag `activity_basis_unmatched`
and refuse equality. Propagate activity uncertainty: at fixed \(p_{\mathrm{Na}}\), one dex
in \(a_{\mathrm{Na_2O}}\) → **two dex in \(f\mathrm{O_2}\)** and **one dex in formal SiO
pressure**.

**Fe³⁺-first reduction** (b-645) competes with Na stabilizing Fe³⁺. Under F1: report Fe³⁺
plateau from Kress+alkali term; flag missing ferric-associate feedback **and** Kress
floor (~1200°C) when drawing a ferric plateau at 1150°C. Under F2: competition becomes
model-internal.

### 3.3 Migration order from today’s sources

| Order | Move | Leaves behind |
|---|---|---|
| **M-a** | Wire openimcc parent \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) as primary FLAGGED source for the solve + any alkali evaporation rail; **record 1700 K domain miss as the flag** | Ad-hoc ideal \(10^{-8}\) floats; silent extrapolation looking certified |
| **M-b** | Keep CALPHAD / core \(a_{\mathrm{FeO}}\) as M2/IW authority; do not replace with openimcc ferrous until fixed-point identity fixtures pass on both | Dual FeO authorities disagreeing on one tick |
| **M-c** | **Chunked (§7 U-Mc):** point vapour / SiO rails at the **same** activity map the solve used this tick (committed composition snapshot) | Per-rail MELTS/MAGEMin one-shots disagreeing with the root |
| **M-d** | Ferric: F1 Kress+alkali term first; schedule F2 associates behind battery |
| **M-e** | Certify openimcc via real admitted \(a_{\mathrm{Na_2O}}\) series (§6); replace FLAGGED with certified table — **unavailable until series exist** |
| **M-f** | Only then consider retiring constant-\(\gamma\) ladder from production path (keep as debug fallback) |

MAGEMin/MELTS: diagnostic / cross-check only until migration M-c proves single-map
discipline.

### 3.4 Structural sites / peralkaline rise — **not a stop**

Na⁺/K⁺ first fill charge-balancing sites of tetrahedral Al³⁺ (and Fe³⁺ when present).
Beyond Na+K ~ Al (preference order K > Na > Ca > Mg; Ca-subtracted “boundary” is already
passed in undosed mare), further alkali is a network modifier at higher activity — a
**rise** in \(a_{\mathrm{Na_2O}}\) vs dose. **10 wt% Na₂O stoichiometric cap remains
dropped.** Site saturation is explanatory, **not a clip and not a shuttle stop**.

**Changed from r2:** the peralkaline “step” is **not** a stop. On M2, \(\Delta G_\times=0\)
at every extent once \(p_{\mathrm{Na}}\) follows the boxed identity; the extent runs until
**FeO**, the **dose**, or a **headspace policy** binds. openimcc’s \(a_{\mathrm{Na_2O}}\)
rises **smoothly** (ideal mixing of associates gives no sharp thermodynamic step). Remove
the interior \(\Delta G=0\) acceptance (former U6).

Declared 100 kg mare fixture (`lunar_mare_low_ti`): undosed (Na+K)/Al ≈ 0.057;
\((\mathrm{Na{+}K})=\mathrm{Al}\) crossing near **+128.6 mol Na₂O** (~5.91 kg Na) after
renormalization of the yaml (sums to 97.15 wt%) — while FeO still remains
(~105–108 mol of ~236). As-stored 100 kg crossing ≈ 124.9 mol Na₂O (5.74 kg Na). Either
way FeO remains. openimcc \(a_{\mathrm{Na_2O}}\) rises ~5–6 orders undosed → deep dose
(FLAGGED / extrapolated at 1423 K).

### 3.5 Domain finding — top alkali-couple certification item

**openimcc `ext-v4` complex (associate) rows are declared on [1700, 3000] K.** At the C3
cool-Na window (~1423 K), `evaluate()` refuses (`ImccTOutsideDatapackDomainError`). The
\(a_{\mathrm{Na_2O}}\) values quoted in r2 / alkali-r2 were produced only with
**extrapolation forced**. At the domain edge (1700 K), undosed \(a_{\mathrm{Na_2O}}\) is
~**73× higher**. The shipped \(\gamma(\mathrm{NaO_{0.5}})\) table is a **point domain**
(1673, 1673) K; seat-temperature γ activities are
`out_of_gamma_domain_status_bearing_non_authoritative`.

**Consequence:** **NO activity source we have is in-domain for alkali activity at the C3
shuttle temperature.** Predict and flag (**extrapolated**), and record this as the
**top certification item** for the alkali couple (U4 / U11). A number coming out of
forced extrapolation must not look certified.

---

## 4. EXCHANGE AND KINETICS — film + whole-melt (d-059)

### 4.1 Gas exchange — equilibrium target vs inventory (split)

- Finite two-film law (gas film \(k_g\) + melt-side inventory film \(k_m\)) — r3 skeleton.
- Error-controlled exponential hour integrator (chunk 7).
- Exchange regimes E0–E5; zero commit publishes gas; nonzero commit publishes accepted
  endpoint root.
- SiO / surface release reads **only** `interface_pO2_bar` (committed-interface contract).
- Hard vacuum (E0) still wins over E3–E5.

**Required split (both reviews; controller):**

| Quantity | Writer / meaning |
|---|---|
| \(p_{\mathrm{Na}}^{\mathrm{eq}}\), \(p_{\mathrm{K}}^{\mathrm{eq}}\) | Solve outputs (boxed identity on M2; alkali residual after iron) — **equilibrium targets** |
| Actual headspace inventory \(n_i\), partials \(p_i\) | Ledgers; \(\sum p_i\le P_g\) under the **one declared headspace / outlet policy** |
| Interface partials + condenser debit | **Film only** (r3 machinery) — the **only** writer of interface partials and condenser debit |
| Surplus alkali | Goes to the **condenser** (explicit sink); clipping pressure without a destination is forbidden |

Stirring establishes a whole-melt approximation; it does **not** establish instantaneous
gas–melt equilibrium. Passive alkali film remains **later** (Q-D / **d-058**); first
landing is shuttle chemistry + O₂ film as today. Deferring the passive alkali film does
**not** supply an unspecified alkali sink — condenser / outlet policy must be named.

Counterexample scale (sol): 100 mol FeO + 250 mol Na + 1 m³ headspace at 1423.15 K —
even complete FeO reduction leaves ~50 mol Na requiring ~5.9 bar as gas; at 1 mbar the
headspace holds only ~0.0085 mol. Surplus **must** hit the condenser (or raise \(P_g\) /
throttle delivery per owner policy — still open in §9).

### 4.2 Whole-melt limit — lance-zone fork CLOSED

| Closed option | Disposition |
|---|---|
| LZ-A local \(V_L\), imposed \(p_{\mathrm{Na}}=0.01\) | **CLOSED** — not implemented |
| LZ-B whole-melt single \(p_{\mathrm{Na}}\) | **SELECTED by d-059** (this is the plan) |
| LZ-C continuous delivery setpoint as local pressure | **CLOSED** as equilibrium volume; delivery remains a **dose-rate write** into the whole-melt ledger |

Validity condition and `recipe_breaks_whole_melt_eq` — §1.2 (**predict-and-flag**, not
withhold).

### 4.3 Worked process numbers under d-059 — curve / coating-budget framing

All at \(1150^\circ\mathrm{C}\), \(P_g=1\,\mathrm{mbar}\) (d-052), seat \(K_{\mathrm{Na}}\),
\(K_\times\). Formal SiO amp vs 1 mbar \(=\sqrt{P_g/f\mathrm{O_2}}\) at fixed silica
activity — **not** an achievable flux multiplier. **All alkali \(a\) at 1423 K are
extrapolated / out-of-domain (§3.5) — FLAGGED.**

**Acceptance rule (changed from r2):** every tabulated \((a_{\mathrm{Na_2O}}, a_{\mathrm{FeO}})\)
pair must come from **one activity-model call** at that dose. Do not mix γ-table
\(a_{\mathrm{Na_2O}}\) with an unrelated \(a_{\mathrm{FeO}}\). U3/U7 oracles reproduce a
**sweep** that prints both activities from one call, plus \(p_{\mathrm{Na}}^{\mathrm{eq}}\)
from \(K_\times\) and \(f\mathrm{O_2}\) from IW — not the superseded r2 mixed table.

**Illustrative openimcc-path sketch** (extrapolation forced at 1423.15 K; renormalized
mare; FLAGGED — numbers from grok review probes, not invented extracts):

| Dose | \(a_{\mathrm{Na_2O}}\) | \(a_{\mathrm{FeO}}\) | \(p_{\mathrm{Na}}^{\mathrm{eq}}\) / bar | \(f\mathrm{O_2}\) / bar | Notes |
|---|---:|---:|---:|---:|---|
| 0 | \(1.45\times10^{-14}\) | ~0.0675 | \(\sim2\times10^{-7}\) | \(\sim2\times10^{-15}\) | Plateau region |
| ~5.91 kg (peralkaline crossing) | \(\sim2\times10^{-9}\) | ~0.07 | \(\sim8\times10^{-5}\) | ~same order as undosed | **Not a stop**; FeO still ~100+ mol |
| ~10.5 kg | \(\sim9\times10^{-9}\) | ~0.007 | \(\sim5\times10^{-4}\) | \(\sim2\times10^{-17}\) | ~2 dex drop; still above 1 mbar \(p\) |
| Near FeO exhaust | rises further | → small | rises; may exceed \(P_g\) | steep fall | Coating budget spent here |

**γ-table path** (also out of point domain at 1423 K): \(a_{\mathrm{FeO}}\) falls faster with
dose in the review sweep; still a plateau then steep fall near exhaustion — same
**curve shape**, different quantitative coating budget. Acceptance must name which model.

**Headspace cap:** when \(p_{\mathrm{Na}}^{\mathrm{eq}}>P_g\), publish both unconstrained
equilibrium partial and exchange-limited interface partial; flag
`alkali_p_headspace_capped`. **Superseded:** declared \(p_{\mathrm{Na}}=0.01\),
\(a=10^{-8}\) → \(f\mathrm{O_2}=2.295\times10^{-22}\), SiO amp \(2.09\times10^{9}\) —
**not** a process claim under d-059.

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
| On flag | **Predict** whole-melt \(p_{\mathrm{Na}}\) / \(f\mathrm{O_2}\) **and** set `recipe_breaks_whole_melt_eq` (d-059) |
| Dose reacts within tick | Required statement; else same flag + prediction |

---

## 5. ACCOUNTING INVARIANTS

1. **Element closure — scale-aware.** Replace unconditional \(10^{-12}\) mol absolute
   (fails binary64 spacing at \(10^{4}\)–\(10^{6}\) mol inventories) with a scale-aware
   criterion, e.g. \(\max(10^{-12},\,N\cdot\varepsilon_{\mathrm{mach}}\cdot\kappa)\) mol per
   element per tick on every accepted titration / shuttle / exchange commit, plus a
   separate accumulated-conservation test. `OXYGEN_RESERVOIR_NOOP_MOL=1e-15` remains the
   inventory presence floor, not the closure tol. Relative tolerance on \(\Delta G/RT\)
   needs an absolute residual floor near zero.
2. **One writer per quantity** — r3 chunk 9: one respeciation writer; passive exchange
   once per hour; melt equality/bound fields are pure functions of the post-commit ledger
   (refresh without re-rooting exchange). Film is the only writer of interface partials
   and condenser debit (§4.1).
3. **Committed-interface contract** — every release rail (SiO, surface O₂, alkali vapour
   when filmed) reads the stored `interface_pO2_bar` / committed alkali interface fields;
   never re-opens a diagnostic root; never substitutes melt equality or a bound.
4. **Flags and refusals — two layers:**
   - **Mandate categories** (keep): **missing input** / **invalid input** /
     **out-of-domain physics**. Typed unavailable for missing/invalid; continued
     **FLAGGED prediction** for physical domain violations (including
     `recipe_breaks_whole_melt_eq` under d-059); rollback of unsolved numerical
     candidates. None may become a silent zero or a certified equality.
   - **Result representation** (keep): **Absence** / **Bound** / **FLAGGED number**
     (openimcc uncertified; T-extrapolated; ideal Ti₂O₃; out-of-band Kress;
     `alkali_p_headspace_capped`; `activity_basis_unmatched`;
     `alkali_phase_reference_unmatched`; `ti_intermediate_omitted`).
5. **Kelvin into Ellingham** on every production path; refuse `ellingham("Fe")` as
   liquid-IW (§2.3).
6. **No TiO₂→Ti hop**; Ti 4a \(\xi_{\max}=\min(n_{\mathrm{Na}}/2,\,n_{\mathrm{TiO_2}}/2)\).
7. **Joint Na/K** before shared alkali equality (Q-C / **d-058**); no `min(fO2)` publish
   path; refuse K₂O(cr) without stated conversion.
8. **No Fe/SiO de-confliction claim** from M2 label alone (withdrawn); use coating budget.
9. **d-059** — no code path may introduce a separate lance-zone equilibrium volume.
10. **\(K_{\mathrm{Na}}\) pin** — acceptance uses executable
    \(1.51490112\times10^{-7}\) within a relative tol that the production path passes
    (sketch: \(10^{-6}\); do **not** require \(10^{-8}\) rel against the rounded
    \(1.514901\times10^{-7}\) display anchor — that rejects the correct implementation).

---

## 6. EVIDENCE — identity / synthetic / empirical split

Headline claims are scoreable only where admitted observations exist. **No invent of
extract numbers.** Certification stays **unavailable** until real series are admitted.

### 6.1 What cannot score \(a_{\mathrm{Na_2O}}\) today

| Named hook in r2 | Checkout fact | Disposition |
|---|---|---|
| `kems-019` as Zaitsev Na₂O–SiO₂ | **Miller–Armatys 2013 review**; all extracted rows refused (`compilation_only_not_measurement`, etc.) | **Cannot score** |
| `kems-016` Stolyarova | All refused / unmapped Na₂O chemical-potential quantity | **Cannot score** |
| Tsaplin 12 / Yamaguchi 42 Na₂O activities | In bench; **held from scoring** pending authoritative oxide-basis result; bench scores \(a_{\mathrm{SiO_2}}\) on those binaries | **Held** — not a live \(a_{\mathrm{Na_2O}}\) series |
| Mare titration (Fe³⁺ / Fe²⁺/Fe⁰ plateaus, SiO amp) | **Synthetic fixture** | Scores numerics / identities only — not empirical lancing validation |

Binary Na₂O–SiO₂ data also cannot validate Al/Fe³⁺ charge-site competition even when
admitted.

### 6.2 Separated test layers

| Layer | What it scores | Examples |
|---|---|---|
| **Thermodynamic identity** | Production-path constants and rearrangements | Kelvin \(K_{\mathrm{Na}}\) executable pin; \(K_\times\) direction; boxed \(p_{\mathrm{Na}}\) identity; liquid-FeO IW vs `ellingham("Fe")` mutation; C-as-K mutation |
| **Synthetic numerical** | Closure, plateaus where the **model** predicts them, d-059 mutations | Mare fixture sweep with one-call activities; element-balance residual uniqueness; freeze \(p_{\mathrm{Na}}=0.01\) → fail; omit active phase / duplicate writer / lose capped Na inventory; scale-aware closure at \(10^{4}\)–\(10^{6}\) mol |
| **Empirical validation** | Model vs admitted observations | **Unavailable** for \(a_{\mathrm{Na_2O}}\) / peralkaline certification until real series land; IW / vapour committed-interface remain on existing r3 fixtures |

### 6.3 Battery hooks that remain live

| Claim piece | Hook | Pass idea |
|---|---|---|
| Metal saturation / IW buffer | Existing r3 M2 fixtures; liquid-FeO IW seat identity | Fixed-point `IW+2log a_FeO` within stated tol |
| Vapour / SiO vs committed interface | r3 chunk 3 fixtures; zero-commit gas; nonzero endpoint | No diagnostic re-entry; amp tracks interface not equality |
| Whole-melt \(p_{\mathrm{Na}}\) closure | Seat identity under M2; condenser debit changes Na tally | Mutation: impose 0.01 bar lance pressure → fail d-059 acceptance |
| Kelvin \(K_{\mathrm{Na}}\) | Production-path Ellingham at 1423.15 K | Executable value within \(10^{-6}\) rel; C-as-K rejected |
| Selective Fe vs SiO / coating budget | Coupled shuttle/film fixture (U8) | Bounds on Fe moles extractable vs SiO release factor; no regime-label assurance |
| openimcc T-domain | U4 | Publishing at 1423 K **must** carry extrapolated / out-of-domain flag |
| \(a_{\mathrm{Na_2O}}\) certification | U11 — **blocked** until admitted series | Do not lift Q-A flag on refused/held rows |

---

## 7. PLAN — luna-sized chunks after r3 chunks 7–8

**Schedule (controller):**

1. **r3 chunks 7–8 stay and land first** (continue now; not wasted).
2. **Chunk 9** precedes the unified chemistry writer (hard predecessor of U2+ / U10).
3. **Chunk 11** precedes U2.
4. **U-Mc (M-c activity-map chunk)** — missing in r2; required so “one map for solve +
   rails” has an acceptance test (lands with or immediately after U4).
5. Unified U-chunks after the above predecessors; default Q-D / **d-058**: shuttle
   chemistry first; passive alkali film later.

Chunk IDs use **U** (unified). A0-style helpers absorbed.

**U0. Thermodynamic helpers.** Kelvin Ellingham→\(K_{\mathrm{Na}}/K_{\mathrm{K}}\);
parent activity conversion; corrected \(K_\times\); phase-reference checks (Na₂O(s)
below 1405.2 K; K₂O(cr); refuse `ellingham("Fe")` as \(K_{\mathrm{FeO}}\));
**\(p_{\mathrm{Na}}\) from solve** helper (M2 rearrange + pure-alkali branch).  
Acceptance: \(K_{\mathrm{Na}}(1423.15\,\mathrm{K})=1.51490112\times10^{-7}\) within
\(10^{-6}\) rel; C-as-K rejected; imposing declared 0.01 bar in the helper fails d-059
unit test; K₂O without conversion → refuse.

**U1. Element-ledger state + derived oxidation map.** Ledger in → speciation key out;
dose write API without lance volume; committed-regime vs post-solve partition reporting.  
Acceptance: dose credits Na moles to melt/headspace tallies only; no \(V_L\) field.
Mutation: add lance-zone moles as equilibrium state → rejected.

**U2. Shared-\(\mu_{\mathrm{O_2}}\) titration root (iron-only first) — after chunk 11.**
Bracketed **extent / oxygen-balance** root reproducing M0–M5 committed-inventory limit;
bound/absence semantics for classification unchanged.  
Acceptance: r3 discriminating fixtures still green; document where unrestricted
equilibration moves M4→M2 (model-consistency fixture, not experimental claim).

**U3. Alkali potentials inside the same root.** Complete same-species pair; joint Na/K;
M2 keeps IW formula at **current** \(a_{\mathrm{FeO}}\); publish \(p_{\mathrm{Na}}^{\mathrm{eq}}\)
from §2.2; **element-balance residual** selects extent (no interior \(\Delta G=0\)).  
Acceptance: mare/dose **one-call** sweeps reproduce \(p_{\mathrm{Na}}\) within tol;
Na₂O+K-only → `alkali_couple_incomplete`. Mutation: `min(fO2)` publish path → fail;
freeze \(p_{\mathrm{Na}}=0.01\) → fail d-059.

**U4. openimcc activity wire (M-a) + F1 ferric path.** FLAGGED associate \(a_{\mathrm{Na_2O}}\);
**must record [1700,3000] K domain miss / extrapolated at C3** as the flag; Kress+Na₂O/K₂O
ferric term as flagged empirical approximation; basis audit.  
Acceptance: peralkaline **rise** monotonic in expected direction on mare fixture (not a
stop); `activity_basis_unmatched` when bases diverge; 1423 K publish carries
extrapolated flag.

**U-Mc. Activity-map discipline (M-c).** Point every vapour / SiO rail at the committed
activity map the solve used this tick.  
Acceptance: mutation that swaps a rail to a one-shot MELTS/MAGEMin disagreeing with the
root → fail; one-map fixture green.

**U5. Ti path + competitors.** 4a then optional 4b; \(\xi_{\max}\) with /2; TiO/Ti₃O₅
included or `ti_intermediate_omitted` limits the region; Si/Mg flags tied to sign of
\(\Delta G\) with solid-oxide refs in the flag text.  
Acceptance: old Ti hop rejected; Mg-favorable case fires competitor handling; omitted
intermediates cannot silently claim the full titration region.

**U6. Bounded extents inside the unified solve (absorbs former shuttle root).**  
Acceptance: residual / complementarity / endpoint inequalities (§2.1); typed failures;
solubility warning ≠ stop; **no** interior \(\Delta G=0\) acceptance; remove second
shuttle root if it only rediscovers equalities.

**U7. Titration-curve / coating-budget acceptance on mare fixture.** \(\log f\mathrm{O_2}\),
\(a_{\mathrm{FeO}}\), \(p_{\mathrm{Na}}^{\mathrm{eq}}\) vs added Na from **one activity call**
per point; Fe³⁺ plateau only where model+Kress domain predict it (flag below ~1200°C);
Fe–FeO **plateau then steep fall near exhaustion**; element closure scale-aware at every
point; waterfall agreement only where couples ≥3 dex **and** capacities allow;
quantify Fe moles extractable before SiO amp rises by declared factors.  
Mutation: freeze \(p_{\mathrm{Na}}=0.01\) → d-059 fail; mix activities across models in one
row → fail; omit condenser sink under cap → fail.

**U8. Coupled shuttle/film + selective-flux / coating budget (Q-D / d-058).** Until green:
no Fe/SiO assurance in reports.  
Acceptance: selective Fe reduction vs SiO release under whole-melt \(p_{\mathrm{Na}}\) +
finite M2 film within declared coating-budget bounds; headspace-cap flag exercised;
**not** the superseded \(10^{8}\) amp line.

**U9. Consumer absence widen + refresh metadata.** Alkali activity/\(p\) unavailable;
align with r3 chunk 5/8 absence-guard cleanup; Mandate missing/invalid vs out-of-domain.

**U10. One-writer / chunk-9 integration.** Prove shuttle-before-exchange ordering with
alkali debit affecting this hour’s exchange; no second ferric writer. (Chunk 9 must
already exist.)

**U11. openimcc certification path.** Battery score **only** with admitted numeric
\(a_{\mathrm{Na_2O}}\) series; lift Q-A flag when pass criteria met. **Blocked** on
kems-019/016 refused rows and held Tsaplin/Yamaguchi Na₂O activities — do not lift on
those. Top item remains C3 T-domain / extrapolation certification.

**U12. (Later) F2 ferric associates + passive alkali film.** Not first landing.

Studio SiO CPU remains out of VPS scope (r3 post-chunk Studio measurement still applies
after 7/8).

### Runnable mutation sketch (U0 / d-059)

```python
import math
from simulator.chemistry.ellingham_thermo import ellingham_delta_g_kj_per_mol_o2
from simulator.fe_redox import feo_iw_log10_fO2_bar

R = 8.314462618
K_NA_EXECUTABLE = 1.51490112e-7  # production path at 1423.15 K

def K_Na(T_K: float) -> float:
    dG_ox = ellingham_delta_g_kj_per_mol_o2("Na", T_K)  # Kelvin
    return math.exp(-(-0.5 * dG_ox * 1000.0) / (R * T_K))

def K_x(T_K: float) -> float:
    # liquid-FeO IW — never ellingham("Fe") solid oxide as K_FeO
    iw = feo_iw_log10_fO2_bar(T_K, a_feo=1.0)
    K_FeO = math.sqrt(10 ** iw)
    return K_FeO / K_Na(T_K)

def p_Na_whole_melt_M2(a_Na2O: float, a_FeO: float, T_K: float, a_Fe: float = 1.0) -> float:
    """d-059: equilibrium p_Na over whole melt — derived, not declared."""
    return math.sqrt(a_Na2O * a_Fe / (K_x(T_K) * a_FeO))

def acceptance():
    T_K = 1423.15
    assert abs(K_Na(T_K) / K_NA_EXECUTABLE - 1.0) < 1e-6
    p = p_Na_whole_melt_M2(1e-8, 2.079188e-5, T_K)
    assert abs(p / 0.01 - 1.0) < 1e-3  # identity: solve recovers former point
    p2 = p_Na_whole_melt_M2(1.45e-14, 0.0675, T_K)  # one-call-style a_FeO
    assert p2 < 1e-5

def mutation_impose_lance_pressure():
    declared = 0.01
    T_K = 1423.15
    a, a_feo = 1.45e-14, 0.0675
    p_eq = p_Na_whole_melt_M2(a, a_feo, T_K)
    assert abs(declared / p_eq - 1.0) < 0.1  # should FAIL

acceptance()
try:
    mutation_impose_lance_pressure()
    raise AssertionError("declared lance p_Na mutation survived")
except AssertionError as e:
    if "survived" in str(e):
        raise
print("d-059 mutation rejected; acceptance OK")
```

---

## 8. Mapping to tickets / rulings

| ID | This design |
|---|---|
| **d-059** | Whole-melt titration; lance-zone fork **CLOSED**; \(p_{\mathrm{Na}}/p_{\mathrm{K}}\) from solve; mixing validity + **predict-and-flag** (not withhold); SiO/process numbers as coating-budget curve §4.3 |
| **d-057** | Alkali couple after iron / whenever dose+oxide live — **inside** unified solve |
| **d-058** / Q-A..Q-D | openimcc FLAGGED (+ T-domain); ideal Ti₂O₃ FLAGGED+JANAF; joint Na/K; shuttle-first / zero-commit gas; Fe/SiO withdrawn |
| **d-056** | **regolith-main admission-default — do not reuse** in this plan |
| d-052 | 1 mbar hold remains optimizer input; headspace may cap \(p_{\mathrm{Na}}\) |
| b-646 | ΔG-limited shuttle; no 10 wt%/0.75; no Ti skip; bounded extents in unified solve |
| b-645 | Ferric-first sequencing (shuttle lane); F1/F2 in activity §3 |
| b-618 | M1 / no-couple absence; unified solve does not invent a couple |
| r3 chunks 7–8 | Continue / land **first**; not wasted |
| r3 chunk 9 | Hard predecessor of unified writer |
| r3 chunk 11 | Predecessor of U2 |
| alkali-r2 LZ-A/B/C | **Superseded / CLOSED** by d-059 |

---

## 9. Open questions **after** d-059

Lance-zone equilibrium volume options are **not** open.

Remaining (narrow; do not block design review):

1. **Headspace / outlet policy when \(p_{\mathrm{Na}}^{\mathrm{eq}}>P_g\) or delivery exceeds
   removal capacity:** raise \(P_g\), dilute with other gases, condenser-limited interface,
   and/or **throttle delivery** — pick at implementation of U3/U8 with a single flagged
   default (`alkali_p_headspace_capped` + exchange-limited interface + explicit condenser
   sink recommended; owner priority between throttle vs pressure rise is a policy choice
   — sol owner question).
2. **F2 ferric-associate schedule** — after real \(a_{\mathrm{Na_2O}}\) battery certification,
   not before.
3. **Passive alkali film** — explicitly later (Q-D / **d-058**); not first landing.
4. **Mixing-time estimator inputs** (geometry, stir rate) — process/equipment fields;
   until present, use conservative flag on unmixed recipes only (**still predict**).
5. **Stated \(\Delta G\) conversions** for K₂O(cr)→liquid-ref and Na₂O(s) below 1405.2 K —
   convert or refuse; needed before joint K publish.
6. **C3 alkali activity in-domain source** — top certification item (§3.5); no silent
   workaround.

No LZ-A/B/C choice remains for the owner.

---

## 10. Dropped or changed vs alkali-r2 / r1 (still in force from r2)

| Prior item | Disposition |
|---|---|
| Lance-zone fork LZ-A default / LZ-B / LZ-C | **CLOSED by d-059**; whole-melt only |
| Declared lance \(p_{\mathrm{Na}}=0.01\) in process examples | **Replaced** by \(p_{\mathrm{Na}}\) from solve |
| Bolt-on M6 as separate solver | **Absorbed** into unified shared-\(\mu_{\mathrm{O_2}}\) root |
| Waterfall thermodynamics | **Simultaneous** titration; waterfall = well-separated limit only |
| Fe/SiO assurance | **Still withdrawn** (U8 coating budget) |
| 10 wt% Na₂O / 0.75 accessibility stops | **Still dropped** |
| Celsius-as-Kelvin / inverted \(K_\times\) / Ti hop / \(p\to0\Rightarrow f\mathrm{O_2}\to0\) | **Still corrected** (sol) |
| Dual activity shopping across rails | **Forbidden**; migration M-a..M-f + U-Mc |

---

## 11. Review disposition

Author design for regolith-physics: **melt redox accounting r3** (edit of r2). Answers
controller REQ synthesis fold list; folds sol + grok UNSOUND reviews; uses corrected
decision ids **d-057 / d-058 / d-059**. Seat: `slot-y17` @
`696299350e98b67f786c334b4b4ccc8856149379`. Both reviewers re-check under NOT-FIXED lens
before implementation. No stack2 product commits in this seat.

— regolith-empirical, 2026-10-01 ~21:00 ET

---

## 12. Dropped or changed from r2

Explicit edit list (controller synthesis + reviews). Everything not listed here is
intended to carry forward from r2 with **decision-id renumbering** applied throughout.

| r2 item | r3 disposition |
|---|---|
| Decision ids d-056 / d-057 / d-058 for alkali / Q-A..Q-D / whole-melt | **Renumbered:** alkali couple = **d-057**; Q-A..Q-D = **d-058**; whole-melt = **d-059**. Real **d-056** = regolith-main admission-default — unused here |
| Peralkaline “step” as early shuttle \(\Delta G\to0\) arrival / soft stop narrative | **Dropped as stop.** Rise is explanatory; extent runs until FeO, dose, or headspace policy binds; openimcc smooth |
| U6 acceptance: interior \(\Delta G=0\) root | **Removed.** Residual is identically zero once \(p_{\mathrm{Na}}\) is the boxed output; U6 = bounded extents + complementarity inside the unified solve |
| Unknowns framed as \(\mu_{\mathrm{O_2}}\) (+ \(p_{\mathrm{Na}}\)) with vague “low-D” root | **Extent** is the monotonic unknown on M2; \(f\mathrm{O_2}\) and \(p_{\mathrm{Na}}\) are outputs; residual vector / brackets / endpoints / failures written (§2.1) |
| Headspace inside “one equilibrium zone” without split from finite films | **Split:** eq targets vs actual inventory; film only writer of interface + condenser debit; \(\sum p_i\le P_g\); explicit condenser sink |
| `recipe_breaks_whole_melt_eq` refuses equality publish | **Predict-and-flag** whole-melt values (d-059); do not withhold |
| §4.3 mixed activity pairs / “~7.4 dex while FeO remains” / deep \(10^{8}\) amp as oracle | **Superseded** by plateau→steep-fall curve + coating-budget framing; one-call activity pairs only; imposed-\(p=0.01\) line remains superseded |
| “Fe–FeO equality preserved vs lost” framing | **Replaced** by titration curve / coating budget (§2.5) |
| Evidence table treating kems-019 / kems-016 / Tsaplin–Yamaguchi Na₂O as scoring hooks | **Split:** those cannot score \(a_{\mathrm{Na_2O}}\) today; identity / synthetic / empirical layers; certification unavailable until admitted series |
| Absence/bound/flag only | **Plus** Mandate missing / invalid / out-of-domain-physics categories |
| Phase contract naming K₂O + condensed metal mainly | **Tightened:** Na₂O(s) below 1405.2 K; K₂O(cr) no liquid segment; Si/Mg solid refs; `ellingham("Fe")` 0.96 dex below liquid-IW |
| Competitors: flag-when-omitted only | **Tie to sign of \(\Delta G\)**; include or limit TiO / Ti₃O₅ |
| Schedule: U2 after chunk 8; chunk 9 only at U10; M-c not chunked | **7–8 first; chunk 9 before unified writer; chunk 11 before U2; add U-Mc** |
| U0 \(10^{-8}\) rel pin on rounded \(K_{\mathrm{Na}}\); \(10^{-12}\) mol absolute closure | **Executable \(K_{\mathrm{Na}}\) + \(10^{-6}\) rel; scale-aware closure** |
| openimcc flags: ferrous-only / no metal / no ferric associates / ideal mixing | **Plus T-domain [1700,3000] K gap at C3** as **top alkali-couple certification item** (predict+flag extrapolated) |
| F1 implied as consistent thermodynamic model | **F1 = flagged empirical approximation**; uncertainty propagation stated |
| M0–M5 “unchanged from unrestricted equilibrium” | **Committed-inventory classification** preserved; equilibrium partition may differ (M4→M2 note) |
| Seat `slot-z14` | This delivery seat **`slot-y17`** (same green tip) |

End of r3 edit list.
