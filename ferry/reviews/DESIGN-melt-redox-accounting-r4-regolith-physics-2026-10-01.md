# Melt redox accounting r4 — unified plan (edit of r3)

Design-only. No stack2 product code in this delivery. **EDIT of r3** that folds two
independent REVISE confirms (sol-r3-confirm; grok-r3-confirm) and the controller
synthesis in `REQ-melt-redox-accounting-r4-from-regolith-physics-2026-10-01.md`.
See **§13 Dropped or changed from r3** for the explicit diff. §12 (Dropped from r2)
is retained as historical carry-forward.

Folds:

- melt-redox-accounting DESIGN r3 (everything both reviewers confirmed CORRECT from r2:
  buffer plateau, mixing prediction with flag, evidence split, phase references, domain
  flags, competitor rule, chunk schedule, Mandate categories — all retained)
- **Controller synthesis** of sol + grok r3 confirms (shared P1 residual/outlet;
  sol P1 unknown-vector / ferric oxygen / endpoints; P2 fold list)
- Decision ids unchanged from r3: alkali couple **d-057**; Q-A..Q-D **d-058**;
  stirred whole-melt **d-059**. Real **d-056** = regolith-main admission-default —
  **do not reuse**
- sol r1 / r2 / r3 corrections still in force (Kelvin Ellingham,
  \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\), Ti extent, phase references)
- d-052 1 mbar hold remains optimizer input
- Headspace-bleed outlet law from LAND `5aebdc8bb` / r12c (quasi-steady \(P_{ss}\))
  reused for determined \(n_{\mathrm{Na,out}}\)

Seat numerics: `slot-y17` @ green `696299350e98b67f786c334b4b4ccc8856149379` (same tip
as r3; matches `origin/work-v064-green` at write time). Ellingham Kelvin, liquid-FeO IW,
parent activity conversion. openimcc / γ numbers cited from review probes are **FLAGGED /
extrapolated** (see §3.5). No invent of extract data or equipment FKs. Ferric-first
(b-645) sequencing remains owned on the shuttle lane; this plan states how Fe³⁺ enters
the **unified solve**.

Owner §9.1 (throttle delivery vs raise \(P_g\) when removal capacity is insufficient)
awaits a regolith-physics ruling — **open, does not block this r4 EDIT of P1/P2**.

North Star unchanged: predict-and-flag, ledger closure, derivation beside every threshold.

---

## 0. What this plan is / is not

| Is | Is not |
|---|---|
| ONE element-ledger state; oxidation states derived | Parallel fO₂ scalars or regime-named “truth” |
| ONE shared-\(\mu_{\mathrm{O_2}}\) (+\(p_{\mathrm{Na}}/p_{\mathrm{K}}\)) titration root on **ACTUAL-inventory element-balance residual** (no eq-gas booking) | Waterfall of independent couple equalities; interior \(\Delta G=0\) extent search; residual that books \(p^{\mathrm{eq}}V/RT\) |
| ONE activity model for solve + evaporation rails | Per-rail activity shopping |
| Whole-melt equilibrium per tick (**d-059**) | Separate lance-zone equilibrium volume (LZ-A/B/C **CLOSED**) |
| Equilibrium **targets** vs actual headspace **inventory** / duct-determined outlet debit / film-written interface | Treating headspace as both instantaneous eq compartment and finite-exchange compartment; booking eq-target gas in the determining residual |
| Luna-sized chunks after r3 chunks 7–8 land | Stack2 product commits in this seat |
| Alkali couple as a **live couple inside the solve** | Renaming M2 to M6 whenever Na is dosed |
| Fe–FeO equality as **titration plateau → steep fall near FeO exhaustion** | Asserting equality “preserved” or “lost” while metal+FeO coexist |

**Supersedes** alkali-r2 §4 owner fork (LZ-A default / LZ-B / LZ-C), r2’s interior
\(\Delta G=0\) U6 acceptance / mixed §4.3 dose oracle, and **r3’s determining residual
that booked** \(p_{\mathrm{Na}}^{\mathrm{eq}}V/RT\) **as inventory**. Still-valid r2/r3
content (Kelvin \(K\), joint Na/K, sites as explanatory, Fe/SiO withdrawal, chunk helpers
shape, buffer plateau, evidence split, phase refs, domain flags, competitor rule,
chunk schedule, Mandate categories) is folded below.

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

### 2.1 Formulation — ACTUAL-inventory residual; declared unknowns

One shared oxygen potential \(\mu_{\mathrm{O_2}}\) (\(\log f\mathrm{O_2}\)) for the whole
melt each tick. When alkali gas/dose is present with alkali oxide in the melt, also
alkali potentials (\(p_{\mathrm{Na}}\), \(p_{\mathrm{K}}\) — or joint Na/K constraints per
Q-C / **d-058**).

**While metal and FeO are both above NOOP (M2 live):**

- The **primary condensed unknowns** are the alkali reduction extents
  \(\xi_{\mathrm{Na}}\), \(\xi_{\mathrm{K}}\) (mol FeO reduced by Na and by K
  respectively), or equivalently one total Fe-reduction extent plus an **inner
  elimination** that partitions Na/K from the independent alkali balances.
  **One Fe extent alone cannot fix the Na/K split:** 10 mol FeO reduced can be
  \((10,0)\), \((5,5)\), or \((0,10)\) in \(\mathrm{Na_2O}/\mathrm{K_2O}\) increments
  (sol P1). Declare the partition unknowns (or the inner elim) in the implementer API.
- \(f\mathrm{O_2}\) and \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\) are **outputs**:
  \(f\mathrm{O_2}=\mathrm{IW}(a_{\mathrm{FeO}}(\xi))\);
  \(p_{\mathrm{Na}}\) from the boxed identity (§2.2).
- There is **no second root** for \(p_{\mathrm{Na}}\) during M2, and **no interior
  \(\Delta G_\times=0\) search**: once \(p_{\mathrm{Na}}\) follows the boxed identity,
  \(\Delta G_\times(\xi)/RT=\ln(Q_\times/K_\times)=0\) at **every** trial extent
  (sol verified for FeO inventories 99…0.001 mol; residuals \(\sim10^{-16}\)).

#### Determining residual — ACTUAL inventories only (changed from r3)

r3 booked \(\frac{p_{\mathrm{Na}}^{\mathrm{eq}}V}{RT}\) inside \(R_{\mathrm{Na}}\) and
labelled it “diagnostic.” That contradicts §4.1 (eq pressure is a target; film/duct
write inventory). Sol’s closed-eq counterexample invents ~51 mol Na gas the film never
delivered; grok’s 1 mbar extent bracket then has no root. **r4 forbids that booking.**

\[
R_{\mathrm{Na}}(\xi)
=
D_{\mathrm{Na}}
- 2\,n_{\mathrm{Na_2O}}(\xi)
- n_{\mathrm{Na,hs}}^{\mathrm{actual}}(\xi)
- n_{\mathrm{Na,out}}(\xi)
\;=\;0
\]

with independent \(R_{\mathrm{K}}\) when K is live. Terms:

| Term | Meaning |
|---|---|
| \(D_{\mathrm{Na}}\) | **Declare mode.** (A) **Total available** Na atoms this tick = initial elemental reductant retained + Na already in oxide inventory (as atoms) + dose delivery this tick. (B) **Incremental dose** only — then the residual **must** also carry the initial oxide Na inventory explicitly so atoms are not invented. Implementer API names which mode; acceptance fixtures cover both. |
| \(2\,n_{\mathrm{Na_2O}}(\xi)\) | Condensed oxide inventory (atoms of Na in \(\mathrm{Na_2O}\)) at trial extent |
| \(n_{\mathrm{Na,hs}}^{\mathrm{actual}}\) | **Ledger** headspace inventory of Na(g) — moles already on the gas ledger, **not** \(p^{\mathrm{eq}}V/RT\) |
| \(n_{\mathrm{Na,out}}(\xi)\) | **Determined** outlet debit (duct removal of Na over the tick) — **not** a free choice that closes any extent |

\(p_{\mathrm{Na}}^{\mathrm{eq}}\) and \(f\mathrm{O_2}\) stay **outputs**. If
\(p^{\mathrm{eq}}>P_g\), apply the §9 headspace policy **outside** this residual (flag
`alkali_p_headspace_capped`); do not dump the surplus into \(n_{\mathrm{Na,out}}\) from
the chemistry root (that would make the chemistry solve a second condenser writer).

#### Outlet law — reuse headspace bleed LAND `5aebdc8bb` / r12c (controller proposal)

Quasi-steady duct pressure and removal (already landed and reviewed):

\[
P_{ss}
=
\max\!\Bigl(
\sqrt{P_{\mathrm{out}}^2 + S/k},\;
S/C_{\mathrm{choke}}
\Bigr),
\qquad
P_{\mathrm{end}}
=
\max(P_{\mathrm{cmd}},\, P_{ss}).
\]

- \(S\) = total mass source into the headspace this tick (kg/s); \(k\) = Poiseuille
  coefficient kg/(s·Pa²); \(C_{\mathrm{choke}}\) = sonic coefficient kg/(s·Pa)
  (\(\gamma=1.4\) as in r12c until a species-\(C_p\) registry exists).
- The **duct** removes gas at that law; alkali vapour leaves **with** the rest of the
  headspace gas through the duct; the **condenser train takes alkali from the duct**.
- Then \(n_{\mathrm{Na,out}}(\xi)\) is that duct removal integrated over the tick for the
  **Na partial** (mole fraction of Na in the duct flow × total moles removed) — a
  function of headspace composition and the bleed law, **not** a free residual closer.

While gas holdup is \(\sim10^{-3}\)–\(10^{-2}\) mol at 1 mbar / 1 m³, the condensed root
typically sits on the dose or FeO endpoint; the bracket is those endpoints plus the
policy binder. Cap holdup at 1 mbar / 1 m³ / 1423.15 K is \(\sim0.00845\) mol Na.

#### Unknown vector and element equations (sol P1)

**Unknowns (M2, joint Na/K live):**

\[
\mathbf{x}
=
\bigl(
\xi_{\mathrm{Na}},\;
\xi_{\mathrm{K}},\;
\text{(optional retained elemental }n_{\mathrm{Na}}^0,\,n_{\mathrm{K}}^0\text{ if not eliminated)}
\bigr)
\]

or \(\xi_{\mathrm{Fe}}\) plus an **inner elimination** that solves the two alkali
balances for the Na/K partition at fixed \(\xi_{\mathrm{Fe}}\). Do **not** publish a
solver whose only free condensed unknown is a single Fe extent when both alkalis are live.

**Element equations (schematic; signs follow the usual reduction direction
\(2\mathrm{Na}+\mathrm{FeO}\to\mathrm{Na_2O}+\mathrm{Fe}\)):**

| Element / pool | Closure |
|---|---|
| Na | \(R_{\mathrm{Na}}=0\) as above (actual hs + determined outlet) |
| K | \(R_{\mathrm{K}}=0\) analogous |
| Fe (oxide + metal) | \(n_{\mathrm{FeO}}+2n_{\mathrm{Fe_2O_3}}+n_{\mathrm{Fe}^0}\) conserved for Fe atoms; extents move Fe between oxide and metal |
| O (iron-oxide pool) | **Conserved oxygen in the Fe-oxide subsystem** must include ferric: \(O_{\mathrm{Fe}}=n_{\mathrm{FeO}}+3\,n_{\mathrm{Fe_2O_3}}=N_{\mathrm{Fe,oxide}}+n_{\mathrm{Fe_2O_3}}\). Changing ferric at fixed oxide-Fe atoms changes oxygen demand (~0.49 mol extra O for ~0.49 mol \(\mathrm{Fe_2O_3}\) on a 100 mol oxide-Fe Kress example at 1673.15 K). **F1 Kress ferric is not a tagged post-step outside the balance** — it feeds this oxygen equation with **composition / activity feedback** before acceptance. Flag F1 as empirical approximation (§3.2); still close oxygen. |

#### Ferric oxygen — composition feedback (changed from r3)

r3’s residual-contract table listed “ferric oxygen from Kress as flagged post-step at
fixed \(f\mathrm{O_2}(\xi)\).” A flag does not conserve oxygen. After each trial
extent:

1. Evaluate \(f\mathrm{O_2}(\xi)=\mathrm{IW}(a_{\mathrm{FeO}}(\xi))\).
2. Apply F1 Kress (with Na₂O/K₂O term) → \(n_{\mathrm{Fe_2O_3}}\), \(n_{\mathrm{FeO}}\) at
   current composition.
3. Enforce \(O_{\mathrm{Fe}}\) closure (adjust metal/oxide partition or oxygen debit as
   the chosen convention requires — **state the convention in the API**; do not leave
   the 0.49 mol O unaccounted).
4. Re-evaluate activities at the updated composition (feedback) before accepting the
   residual.

#### Phase inequalities, endpoints, residual signs, iron-exhausted branch

| Item | Contract |
|---|---|
| Phase inequalities | \(n_{\mathrm{FeO}}\ge0\), \(n_{\mathrm{Fe}^0}\ge0\), \(n_{\mathrm{Na_2O}}\ge0\), \(n_{\mathrm{K_2O}}\ge0\) (when that oxide is an **activated product** — see §2.4 zero-product rule); headspace \(n_i\ge0\); \(\sum p_i\le P_g\) under the declared policy |
| Feasible interval | Bracket on \((\xi_{\mathrm{Na}},\xi_{\mathrm{K}})\) (or \(\xi_{\mathrm{Fe}}\) + inner elim) from dose / FeO / headspace-policy endpoints — **not** a stiff \(\mu_{\mathrm{O_2}}\) sweep across the buffer plateau |
| Endpoint binders | Stop when FeO → NOOP, dose exhausted, or headspace policy binds; **publish which binder won** |
| Endpoint residual signs | At \(\xi\to0\) (no reduction): \(R_{\mathrm{Na}}\) carries the unused dose / available Na (positive when dose is present and not yet in oxide/outlet). At FeO-exhaust endpoint with capped headspace and determined outlet: residual must reflect **actual** placed Na (oxide + hs ledger + duct debit) — the r3 1 mbar case that left +49.99 mol unplaced is exactly the defect this residual forbids. Acceptance fixtures include sol’s 100 mol FeO / 250 mol Na / 1 m³ case (uncapped closed-eq invents ~51 mol gas; capped ledger residual has no root that invents them). |
| Failure behaviour | Typed refusal (missing/invalid input) or FLAGGED prediction (out-of-domain physics); rollback unsolved numerical candidates; never silent zero / never certified equality on failure |
| After iron exhausted | Alkali equality relates \(p_{\mathrm{Na}}\), \(f\mathrm{O_2}\), and \(a_{\mathrm{Na_2O}}\) — it does **not** independently fix \(f\mathrm{O_2}\) without the **inventory / outlet closure**. Continue with the same ACTUAL-inventory residual(s) + headspace policy; K₂O refused until a liquid (or converted) reference exists. |

#### Monotonicity of the ACTUAL residual (changed from r3)

r3 claimed monotonic \(a_{\mathrm{FeO}}(\xi)\) in both sweeps. **False on openimcc:**
\(a_{\mathrm{FeO}}\) rises \(0.0675\to\sim0.085\)–\(0.088\) then falls (sol/grok).
Uniqueness for the **ACTUAL** residual still holds on the condensed branch because of
the \(-2\xi\) (dose→oxide) term — one condensed root. A bracket on \(f\mathrm{O_2}\)
does **not**. Acceptance must check the determining residual’s sign change on the
extent bracket, not claim monotonic \(a_{\mathrm{FeO}}\).

The Knudsen bridge’s effusion-flux oxygen balance is **not** this residual (fixed
supplied activities there).

#### Couple table (unchanged roles)

Every couple at equilibrium with that **same** \(\mu_{\mathrm{O_2}}\) at the **current**
composition:

| Couple | Role in solve |
|---|---|
| Fe³⁺/Fe²⁺ | Kress-type (carries Na₂O/K₂O term); ferric path §3; **feeds \(O_{\mathrm{Fe}}\) balance** with composition feedback; FLAGGED below Kress floor (~1200°C) |
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

**Zero-product activation (changed from r3; sol P2):** initially absent K₂O **cannot**
block \(2\mathrm{K}+\mathrm{FeO}\to\mathrm{K_2O}+\mathrm{Fe}\). Distinguish:

1. **Pre-solve reporting** — “no K₂O in committed inventory” is a classification fact.
2. **Reaction activation** — if elemental K (or K dose) and FeO are present, K₂O is an
   **activated product**; complementarity / endpoint treatment applies; do **not** refuse
   with `alkali_couple_incomplete` solely because initial \(n_{\mathrm{K_2O}}=0\).
3. **Missing phase conversion** — K₂O(cr) without a stated liquid-reference \(\Delta G\)
   remains a **separate** refusal (`alkali_phase_reference_unmatched`), independent of
   (1)/(2).

The r3 U3 acceptance line “Na₂O+K-only → `alkali_couple_incomplete`” is **dropped** when
K reductant can form K₂O; keep incomplete-pair refusal only for a true missing same-species
partner (e.g. Na oxide with no Na reductant path and no Na dose).

**Alkali-present enrichment (not a bolt-on M6 rename of M2):**

- While metal+FeO remain → stay **M2**; published iron equality is still IW-scale at
  current \(a_{\mathrm{FeO}}\); joint alkali potentials are additional solve **outputs**.
- When iron couple gone but complete alkali pair live → publish alkali equality from the
  same root (former “M6” **label** may remain as a report tag; not a second solver),
  with inventory/outlet closure still required (§2.1).
- Incomplete alkali pair (true missing partner, not zero-product) → flag
  `alkali_couple_incomplete`; do not publish alkali equality.

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
| Early / mid dose (most FeO still present) | \(\mathcal{O}(0.05\text{–}0.2)\); **not monotone** on openimcc (rises then falls) | **Plateau** near undosed IW+\(2\log a_{\mathrm{FeO}}\) under **openimcc** (\(\lvert\Delta\log_{10}f\mathrm{O_2}\rvert<0.3\) through ~70% Fe extracted). **γ path is steeper:** at the peralkaline dose γ already moves **−0.74 dex** (SiO factor ~2.35) — do **not** claim “dex change ≪ 1” for γ | Formal SiO amp on openimcc plateau stays \(\sim5\)–\(6\times10^{5}\) at \(P_g=1\) mbar; the broader \(10^{5}\)–\(10^{6}\) band covers plateau + start of fall |
| Near FeO exhaustion | Falls toward \(10^{-3}\)…\(10^{-5}\) and below | **Steep fall** (many dex) | Formal amp rises sharply; coating budget spent here |
| Imposed \(p_{\mathrm{Na}}=0.01\) (superseded) | Forced \(a_{\mathrm{FeO}}\sim2\times10^{-5}\) | ~7–8 dex shift while inventing that partial as input | **Not** a process claim under d-059 |

**Coating-budget layers (changed from r3):**

- **U7 — fixed-silica EQUILIBRIUM diagnostic.**
  \(A(\xi)/A(0)=\sqrt{f(0)/f(\xi)}=a_{\mathrm{FeO}}(0)/a_{\mathrm{FeO}}(\xi)\) at fixed
  \(a_{\mathrm{SiO_2}}\). Arithmetic is correct; **label it equilibrium / fixed-silica**.
  With the **same call’s** \(a_{\mathrm{SiO_2}}\), the nominal openimcc 10× point is only
  ~**1.7×** equilibrium SiO pressure (sol). Do not sell U7 as a process coating budget.
- **U8 — process coating budget.** Derive release/deposition from the **committed
  interface** + **wall / film model** with a **concrete fixture** and quantitative bounds.
  With zero oxygen exchange the committed interface remains gas pressure, not melt
  equality. Acceptance must name the fixture (species, geometry, tick) and fail mutations
  that freeze \(p_{\mathrm{Na}}=0.01\), mix activity models in one row, omit the condenser
  / duct sink, or swap a rail to a one-shot MELTS/MAGEMin map.

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
- \(2\mathrm{Na}+\mathrm{MgO}\to\mathrm{Na_2O}+\mathrm{Mg(g)}\): the quoted
  **−47 to −101 kJ** figures are **imposed-\(p_{\mathrm{Na}}\)** examples
  (\(p_{\mathrm{Na}}=10^{-3}\) and \(0.01\) bar at stated \(a_{\mathrm{MgO}}\),
  \(p_{\mathrm{Mg}}\)). On the **openimcc equilibrium partials** along the mare sweep,
  Mg stays **unfavorable** until ~**94%** of the Fe is gone (\(\Delta G\sim+37.5\) kJ
  undosed). Si stays positive on that whole curve (~+271 → +167 kJ). U5 fires on the
  **sign of \(\Delta G\) at the stated activities / partials for the case under test**,
  not on those two imposed-p kJ as the mare-curve result. A warning without competition
  in the balance can still overallocate dose to Fe/Ti when \(\Delta G<0\) at those
  stated conditions.

U5 acceptance: flag fires when \(\Delta G<0\) under **stated** refs and activities
**or** when reactions are omitted from a prediction that claims the Ti/Cr/alkali region;
not only when source omits the reaction name; not by hard-coding the imposed-p kJ as the
trajectory oracle.

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
| \(p_{\mathrm{Na}}^{\mathrm{eq}}\), \(p_{\mathrm{K}}^{\mathrm{eq}}\) | Solve outputs (boxed identity on M2; alkali residual after iron) — **equilibrium targets only**; **never** booked as \(p^{\mathrm{eq}}V/RT\) inside \(R_{\mathrm{Na}}\) |
| Actual headspace inventory \(n_i\), partials \(p_i\) | Ledgers; \(\sum p_i\le P_g\) under the **one declared headspace / outlet policy** |
| Duct removal / \(n_{\mathrm{Na,out}}\) | **Determined** by the r12c / LAND `5aebdc8bb` quasi-steady bleed law (§2.1); alkali leaves with the duct gas |
| Interface partials + condenser debit from duct | **Film / overhead machinery** — the **only** writer of interface partials and condenser debit taken **from the duct**; chemistry root must not invent a second condenser writer |
| Surplus alkali | Goes through the **duct → condenser** (explicit sink); clipping pressure without a destination is forbidden |

Stirring establishes a whole-melt approximation; it does **not** establish instantaneous
gas–melt equilibrium. Passive alkali film remains **later** (Q-D / **d-058**); first
landing is shuttle chemistry + O₂ film as today. Deferring the passive alkali film does
**not** supply an unspecified alkali sink — condenser / outlet policy must be named, and
\(n_{\mathrm{Na,out}}(\xi)\) must follow the duct law.

Counterexample scale (sol/grok): 100 mol FeO + 250 mol Na + 1 m³ headspace at 1423.15 K —
uncapped closed-eq root invents ~51.13 mol Na gas at 6.05 bar; at 1 mbar the headspace
holds only ~0.00845 mol and the extent bracket under an eq-gas residual has **no root**
(~49.99 mol unplaced). Surplus **must** hit the duct→condenser (or raise \(P_g\) /
throttle delivery per owner policy — §9.1 **awaiting physics ruling**, not blocking r4).

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
| ~5.91 kg (peralkaline crossing) | \(\sim2\times10^{-9}\) | ~0.07 | \(\sim8\times10^{-5}\) | ~same order as undosed (+0.03 dex openimcc) | **Not a stop**; FeO still ~100+ mol |
| ~10.5 kg | \(\sim9\times10^{-9}\) | ~0.006 | \(\sim5\times10^{-4}\) (**0.5 mbar**) | \(\sim2\times10^{-17}\) (~−2.15 dex) | ~2 dex drop; \(p_{\mathrm{Na}}\) is **below** 1 mbar here (r3 typo corrected) |
| ~10.76 kg (~1% Fe left) | \(\sim1.0\times10^{-8}\) | ~0.0017 | \(\sim1.1\times10^{-3}\) | steep | \(p_{\mathrm{Na}}\) **passes 1 mbar** near ~1% Fe remaining |
| Near FeO exhaust | rises further | → small | rises; may exceed \(P_g\) | steep fall | Coating budget spent here |

**γ-table path** (also out of point domain at 1423 K): steeper than openimcc — at the
peralkaline dose already **−0.74 dex** / SiO factor ~2.35; factor-2 coating budget at
~47% Fe extracted (γ) vs ~80% (openimcc). Same qualitative plateau→fall, **different**
quantitative budget. Acceptance must name which model. Do **not** claim “dex change ≪ 1”
for the γ path.

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
   criterion. **Name \(\kappa\)** (recommended default \(\kappa=100\) ulp-multiple pending
   implementer pin) or drop the symbol and state the ulp multiple explicitly, e.g.
   \(\max(10^{-12},\,N\cdot\varepsilon_{\mathrm{mach}}\cdot 100)\) mol per element per tick
   on every accepted titration / shuttle / exchange commit, plus a separate
   accumulated-conservation test. Binary64 spacing is \(\sim1.8\times10^{-12}\) mol at
   \(10^{4}\) mol and \(\sim1.2\times10^{-10}\) mol at \(10^{6}\) mol (sol).
   `OXYGEN_RESERVOIR_NOOP_MOL=1e-15` remains the inventory presence floor, not the
   closure tol. Relative tolerance on \(\Delta G/RT\) needs an absolute residual floor
   near zero.
2. **One writer per quantity** — r3 chunk 9: one respeciation writer; passive exchange
   once per hour; melt equality/bound fields are pure functions of the post-commit ledger
   (refresh without re-rooting exchange). Film / overhead duct machinery is the only
   writer of interface partials and condenser debit (§4.1). The chemistry residual must
   **not** invent a second condenser writer by free choice of \(n_{\mathrm{Na,out}}\).
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
6. **U3 and U6 are ONE residual** (changed from r3): U3 lands the ACTUAL-inventory
   alkali residual + joint partition; U6 is bounds / complementarity / endpoint /
   failure packaging of that **same** residual — not a second solver. Order after P1
   fix: U3 → U4 → U-Mc → U5 → U6 (bounds/failure only) → U7 → U8 → U9 → U10.

Chunk IDs use **U** (unified). A0-style helpers absorbed.

**U0. Thermodynamic helpers.** Kelvin Ellingham→\(K_{\mathrm{Na}}/K_{\mathrm{K}}\);
parent activity conversion; corrected \(K_\times\); phase-reference checks (Na₂O(s)
below 1405.2 K; K₂O(cr); refuse `ellingham("Fe")` as \(K_{\mathrm{FeO}}\));
**\(p_{\mathrm{Na}}\) from solve** helper (M2 rearrange + pure-alkali branch).  
Acceptance: \(K_{\mathrm{Na}}(1423.15\,\mathrm{K})=1.51490112\times10^{-7}\) within
\(10^{-6}\) rel; C-as-K rejected; imposing declared 0.01 bar in the helper fails d-059
unit test; K₂O without conversion → refuse.  
**Mutation (r4):** impose-0.01 against the **undosed openimcc pair**
(\(a_{\mathrm{Na_2O}}\sim1.45\times10^{-14}\), \(a_{\mathrm{FeO}}\sim0.0675\)) —
\(p^{\mathrm{eq}}/0.01\sim5\times10^{4}\); assertion that declared equals eq **must fail**.
(The boxed identity point \((10^{-8},\,2.079\times10^{-5})\to0.01\) remains a separate
identity check, not the impose mutation.)

**U1. Element-ledger state + derived oxidation map.** Ledger in → speciation key out;
dose write API without lance volume; committed-regime vs post-solve partition reporting.  
Acceptance: dose credits Na moles to melt/headspace tallies only; no \(V_L\) field.
Mutation: add lance-zone moles as equilibrium state → rejected.

**U2. Shared-\(\mu_{\mathrm{O_2}}\) titration root (iron-only first) — after chunk 11.**
Bracketed **extent / oxygen-balance** root reproducing M0–M5 committed-inventory limit;
bound/absence semantics for classification unchanged; F1 ferric feeds \(O_{\mathrm{Fe}}\)
when ferric is live.  
Acceptance: r3 discriminating fixtures still green; **numeric mutation** for M4→M2
using sol’s disproportionation fixture (\(3\mathrm{FeO}\rightleftharpoons\mathrm{Fe_2O_3}+\mathrm{Fe}\)
model-consistency move — not an experimental claim). Document the committed-regime vs
post-solve partition when they diverge.

**U3. Alkali potentials inside the same root (= the U6 residual).** Complete same-species
pair; joint Na/K with **declared** \((\xi_{\mathrm{Na}},\xi_{\mathrm{K}})\) or inner
elimination; M2 keeps IW formula at **current** \(a_{\mathrm{FeO}}\); publish
\(p_{\mathrm{Na}}^{\mathrm{eq}}\) from §2.2 as an **output**; **ACTUAL-inventory
residual** (§2.1) selects extent (no interior \(\Delta G=0\); no \(p^{\mathrm{eq}}V/RT\)
booking); \(n_{\mathrm{Na,out}}\) from the duct bleed law.  
Acceptance: mare/dose **one-call** sweeps reproduce \(p_{\mathrm{Na}}\) within tol —
oracle is the boxed identity on **that chunk’s one call**; name the model (at 5.91 kg:
openimcc \(\sim7.59\times10^{-5}\) bar; γ \(\sim2.74\times10^{-4}\) bar). True incomplete
pair → `alkali_couple_incomplete`; **zero initial K₂O with K reductant does not**
(§2.4). Mutation: `min(fO2)` publish path → fail; freeze \(p_{\mathrm{Na}}=0.01\) → fail
d-059; book eq-gas inventory in residual → fail; free choice of \(n_{\mathrm{out}}\)
bypassing duct law → fail; uncompensated ferric oxygen → fail.

**U4. openimcc activity wire (M-a) + F1 ferric path.** FLAGGED associate \(a_{\mathrm{Na_2O}}\);
**must record [1700,3000] K domain miss / extrapolated at C3** as the flag; Kress+Na₂O/K₂O
ferric term as flagged empirical approximation **feeding \(O_{\mathrm{Fe}}\)**; basis audit.  
Acceptance: peralkaline **rise** in expected direction on mare fixture (not a stop);
`activity_basis_unmatched` when bases diverge; 1423 K publish carries extrapolated flag.
**Caller attaches the extrapolated flag** — forced `evaluate()` returns
`extrapolated=True` with **empty** `flags` tuple and `envelope_status="inside"`; do not
rely on the pack’s own flags tuple alone.

**U-Mc. Activity-map discipline (M-c).** Point every vapour / SiO rail at the committed
activity map the solve used this tick.  
Acceptance: mutation that swaps a rail to a one-shot MELTS/MAGEMin disagreeing with the
root → fail; one-map fixture green.

**U5. Ti path + competitors.** 4a then optional 4b; \(\xi_{\max}\) with /2; TiO/Ti₃O₅
included or `ti_intermediate_omitted` limits the region; Si/Mg flags tied to sign of
\(\Delta G\) with solid-oxide refs in the flag text **at the stated activities**.  
Acceptance: old Ti hop rejected; Mg-favorable case (imposed-p examples or late-titration
openimcc partials where \(\Delta G<0\)) fires competitor handling; undosed/mid openimcc
mare curve where Mg is still positive must **not** be forced by the −47/−101 kJ quotes;
omitted intermediates cannot silently claim the full titration region.

**U6. Bounds / complementarity / endpoints for the U3 residual (not a second solver).**  
Acceptance: residual / complementarity / endpoint inequalities and residual signs (§2.1);
typed failures; solubility warning ≠ stop; **no** interior \(\Delta G=0\) acceptance;
**no** eq-gas booking; iron-exhausted branch still closes inventory/outlet; remove second
shuttle root if it only rediscovers equalities. Prerequisites of U2/U3 — bounds become
effective **with** those solvers, not only after a later package lands.

**U7. Titration-curve / fixed-silica EQUILIBRIUM coating diagnostic on mare fixture.**
\(\log f\mathrm{O_2}\), \(a_{\mathrm{FeO}}\), \(p_{\mathrm{Na}}^{\mathrm{eq}}\) vs added Na from
**one activity call** per point; Fe³⁺ plateau only where model+Kress domain predict it
(flag below ~1200°C); Fe–FeO **plateau then steep fall near exhaustion** (openimcc);
element closure scale-aware at every point; waterfall agreement only where couples ≥3 dex
**and** capacities allow; quantify the **fixed-silica** amp factor
\(a_{\mathrm{FeO}}(0)/a_{\mathrm{FeO}}(\xi)\) and **separately** report same-call
\(a_{\mathrm{SiO_2}}\)-aware SiO pressure ratio (~1.7× at the nominal 10× openimcc point).
**Label equilibrium / fixed-silica — not a process coating budget.**  
Mutation: freeze \(p_{\mathrm{Na}}=0.01\) → d-059 fail; mix activities across models in one
row → fail; omit condenser / duct sink under cap → fail; claim monotonic \(a_{\mathrm{FeO}}\)
as uniqueness proof → fail (check ACTUAL residual instead).

**U8. Coupled shuttle/film + process coating budget (Q-D / d-058).** Until green: no
Fe/SiO assurance in reports. **Derive** release/deposition from the committed interface +
wall / film model with a **concrete fixture** (name geometry, species, tick length,
activity map) and quantitative bounds — not undeclared “factors”. Revalidate film
capacity, feasible interval, and Jacobian after changing chemical partitioning.  
Acceptance: selective Fe reduction vs SiO release under whole-melt \(p_{\mathrm{Na}}\) +
finite M2 film within those derived bounds; headspace-cap flag exercised; duct→condenser
sink present; **not** the superseded \(10^{8}\) amp line; **not** U7’s fixed-silica
equilibrium diagnostic alone.

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

Remaining (narrow; do not block this r4 EDIT of P1/P2):

1. **§9.1 Owner policy (awaiting regolith-physics ruling):** when
   \(p_{\mathrm{Na}}^{\mathrm{eq}}>P_g\) or delivery exceeds removal capacity at the cap —
   **throttle delivery** vs **allow \(P_g\) / \(P_{\mathrm{end}}\) to rise**. Recommended
   engineering default remains: `alkali_p_headspace_capped` + exchange-limited interface +
   explicit duct→condenser sink under the r12c bleed law (§2.1). **Ruling does not block
   correcting the residual equations or documenting that default.** Controller will send
   the ruling separately.
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

Author design for regolith-physics: **melt redox accounting r4** (edit of r3). Answers
controller REQ-r4 synthesis; folds sol-r3-confirm + grok-r3-confirm (both REVISE);
retains everything both confirmed CORRECT from r2/r3. Decision ids **d-057 / d-058 /
d-059** unchanged. Seat: `slot-y17` @ `696299350e98b67f786c334b4b4ccc8856149379`
(design-only; no stack2 product commits). Owner §9.1 ruling pending — not a blocker for
this EDIT. Both reviewers re-check r4 under NOT-FIXED lens before implementation.

— regolith-empirical, 2026-10-01 ~21:40 ET

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


## 13. Dropped or changed from r3

Explicit edit list (controller REQ-r4 + sol/grok r3-confirms). Everything not listed
here is intended to carry forward from r3 (including §12’s r2→r3 dispositions).

| r3 item | r4 disposition |
|---|---|
| §2.1 \(R_{\mathrm{Na}}\) books \(\frac{p_{\mathrm{Na}}^{\mathrm{eq}}V}{RT}\) (“eq target inventory, diagnostic”) | **Dropped from determining residual.** Residual counts **ACTUAL** inventories only: elemental reductant retained, ledger headspace moles, and **determined** \(n_{\mathrm{Na,out}}\). \(p^{\mathrm{eq}}\) stays an output / target (§4.1). |
| \(n_{\mathrm{Na,out}}\) unnamed / free closer | **Determined** by headspace-bleed LAND `5aebdc8bb` / r12c: \(P_{ss}=\max(\sqrt{P_{\mathrm{out}}^2+S/k},\,S/C_{\mathrm{choke}})\), \(P_{\mathrm{end}}=\max(P_{\mathrm{cmd}},P_{ss})\); duct removes gas; alkali leaves with duct flow; condenser takes from duct. |
| \(D_{\mathrm{Na}}\) undefined (total vs incremental) | **Declare mode:** total available vs incremental dose; incremental requires explicit initial oxide inventory. |
| Residual contract as requirements table without equations / unknown vector | **Written:** unknowns \((\xi_{\mathrm{Na}},\xi_{\mathrm{K}})\) or \(\xi_{\mathrm{Fe}}\)+inner elim; element equations; phase inequalities; feasible endpoints; endpoint residual signs; iron-exhausted branch still needs inventory/outlet closure. |
| One Fe extent implied for joint Na/K | **Forbidden as sole free unknown** when both alkalis live — declare Na and K extents or inner elimination. |
| Ferric “flagged post-step” at fixed \(f\mathrm{O_2}\) | **Feeds conserved** \(O_{\mathrm{Fe}}=n_{\mathrm{FeO}}+3n_{\mathrm{Fe_2O_3}}\) **with composition feedback** before acceptance (F1 remains empirical-approximation flag). |
| “monotonic \(a_{\mathrm{FeO}}(\xi)\)” uniqueness claim | **Dropped.** openimcc \(a_{\mathrm{FeO}}\) rises then falls; check sign change of the **ACTUAL** residual; \(-2\xi\) term still gives one condensed root; no \(f\mathrm{O_2}\) bracket. |
| U3 acceptance “Na₂O+K-only → `alkali_couple_incomplete`” | **Dropped for zero-product case.** Initially absent K₂O cannot block \(2\mathrm{K}+\mathrm{FeO}\to\mathrm{K_2O}+\mathrm{Fe}\); distinguish pre-solve absence, reaction activation, and phase-reference refusal. |
| §2.5 “dex change ≪ 1 under openimcc / γ paths”; U7/U8 coating budget undeclared | **openimcc plateau only** for ≪1 dex; γ already −0.74 dex at peralkaline. **U7** = fixed-silica EQUILIBRIUM diagnostic (same-call \(a_{\mathrm{SiO_2}}\) → ~1.7× at nominal 10×). **U8** = derived process budget from committed interface + wall model with concrete fixture. |
| §4.3 ~10.5 kg note “still above 1 mbar \(p\)” | **Corrected:** \(p_{\mathrm{Na}}\sim5\times10^{-4}\) bar = **0.5 mbar**; passes 1 mbar near **~1% Fe left**. |
| §2.6 “−47 to −101 kJ” as mare-curve Mg result | **Labelled imposed-\(p_{\mathrm{Na}}\) examples.** On openimcc eq partials Mg stays unfavorable until ~94% Fe gone. U5 fires on sign at stated activities. |
| U3 and U6 as two solvers | **ONE residual:** U3 lands it; U6 is bounds/complementarity/failure packaging. |
| U0 impose-0.01 / U2 M4→M2 without numeric mutations | **U0:** impose-0.01 vs undosed openimcc pair (\(p^{\mathrm{eq}}/0.01\sim5\times10^{4}\)). **U2:** sol disproportionation fixture for M4→M2. |
| U4 flag reliance on pack `flags` tuple | **Caller attaches** extrapolated flag (`extrapolated=True` arrives with empty flags / `envelope_status="inside"`). |
| §5 \(\kappa\) unbound in scale-aware closure | **Name \(\kappa\)** (recommended default 100) or state ulp multiple explicitly. |
| §9.1 owner throttle vs raise \(P\) | **Still open** — awaiting physics ruling; recommended capped-interface + duct→condenser default documented; **does not block** this EDIT. |
| Seat / tip | Same seat **`slot-y17`** @ green `696299350` (design-only). |

End of r4 edit list.
