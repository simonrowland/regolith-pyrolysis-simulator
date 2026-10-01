# Alkali-couple melt regime: extension of redox design r3 (d-056)

Design-only. No code in this delivery. Built from converged `redox-design-r3.md` plus the C3 alkali shuttle as attached at tip `e7bd0cd5243ea7aa7e3eb565ed94b36f866f1c4c` (`metallothermic_step-e7bd0cd52.py`). That tip is push-pending; `origin/review/stack2-redox` remains at `c5674a2d248cd7f915fb272d40e242355dfc574d`. No invent of extract numbers or equipment FKs. Ferric-first ordering (b-645) is in flight on the physics side and is named only as a sequencing constraint.

North Star unchanged from r3: predict-and-flag, ledger closure, derivation beside every threshold.

Owner decision **d-056** answers r3 §7 Q2: after iron, and whenever alkali dose or vapour is present with alkali oxide in the melt, the melt redox couple is the **alkali couple**

\[
\mathrm{Na_2O(melt)}=2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2},
\qquad
\mathrm{K_2O(melt)}=2\,\mathrm{K(g)}+\tfrac12\mathrm{O_2}.
\]

Ti³⁺/Ti⁴⁺ and Cr remain secondary. The shuttle’s extent becomes thermodynamically limited (b-646); the 10 wt% Na₂O stoichiometric cap with a 0.75 Ti accessibility factor is retired as the stopping rule. Na must not take TiO₂ straight to Ti metal skipping Ti³⁺.

This document is an EDIT/extension of r3. It keeps r3’s structure: regime table, invariants, consumer contract, luna-sized chunks with derived acceptance and runnable mutations. It does not reopen r3 chunks 1–11 except where M6 / b-646 touch their contracts.

---

## 0. What r3 already fixed, and what this adds

r3’s M0–M5 iron story, finite M2 film, mole-log M5 inverse, absence handling, and committed-interface vapour read stay. r3 §7 Q2 default (“none until iron returns”) and the b-618 holding pattern (“flagged absence; couple itself is not in this plan”) are **superseded for the alkali case only** by d-056. Ti/Cr ratio couples remain research candidates and stay out of this plan’s equality rows.

What this design adds:

1. Melt regime **M6** `alkali_na_k_buffer` (not a fold into M1 — M1 stays “no modelled couple”).
2. Thermodynamically limited shuttle extent (b-646) replacing the solubility/accessibility stop.
3. A worked C3 SiO consequence under sustained Na lancing, with Fe/SiO de-confliction preserved while FeO+metal remain.
4. Owner questions only where data genuinely cannot decide (t-999 activity engine; Ti₂O₃ activity provenance; Na/K parallel weighting).

---

## 1. Alkali-couple melt regime (M6)

### 1.1 Trigger predicate (ledger-first)

First-match precedence inserts **M6 after M2 and before M3**, so retained Fe–FeO coexistence keeps its equality while metal+FeO are both present; alkali does not silently rename M2. When iron oxides and metal are gone, M6 fires instead of M1 whenever the alkali inventory is live.

| # | Regime | Ledger predicate, first match | Melt fO₂ | Respeciation / shuttle may write |
|---|---|---|---|---|
| M0 | `not_liquid` | (unchanged) | Absent | Nothing |
| M1 | `no_modelled_redox_couple` | Liquid; FeO≤NOOP; Fe₂O₃≤NOOP; **and** alkali couple absent (see below) | Absent. Flagged `out_of_domain` | Nothing |
| M2 | `fe_feo_buffer` | `n_Fe(metal)>NOOP` and `n_FeO>NOOP` | Equality `IW+2 log10(a_FeO)` (r3) | Metal gas reaction only (r3). Alkali **cross-reaction** may still move FeO↔Fe via the shuttle (b-646), but does not replace this equality while both phases remain |
| **M6** | **`alkali_na_k_buffer`** | Liquid; alkali oxide in melt (`n_Na2O>NOOP` or `n_K2O>NOOP`); **and** alkali dose/vapour inventory live (`n_Na(g or dose)>NOOP` or `n_K…>NOOP`, including lance reagent and melt-surface vapour partial); **and** not M2 | **Equality** from §1.2 at the live alkali(s) | Alkali gas reaction (§1.3). No invent of Fe₂O₃. Ti path stops at Ti³⁺ (§2) |
| M3–M5 | (unchanged) | (unchanged) | (unchanged) | (unchanged) |

**Alkali couple absent** (for M1): both `(n_Na2O≤NOOP and n_K2O≤NOOP)` **or** `(no Na dose/vapour and no K dose/vapour)`. Oxide without reductant vapour, or reductant vapour without oxide, does not open an equality; flag `alkali_couple_incomplete` and leave equality absent (predict-and-flag, same shape as M2’s activity-unavailable).

Trace inventory uses the same `OXYGEN_RESERVOIR_NOOP_MOL = 1e-15` mol. No ε-fraction gate.

### 1.2 fO₂ equality — derivation, units, limiting cases

Dissociation equilibrium for sodium (potassium is identical with K symbols):

\[
\mathrm{Na_2O(melt)}=2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2(g)}.
\]

Standard states: melt oxide activity \(a_{\mathrm{Na_2O}}\) (pure liquid Na₂O reference as declared by the activity engine; see §2.3 / t-999), gases at 1 bar. Equilibrium constant from the standard Gibbs energy of that reaction at \(T\):

\[
K_{\mathrm{Na}}(T)=\exp\!\bigl(-\Delta G^\circ_{\mathrm{dissoc,Na}}(T)/(RT)\bigr)
=\frac{(p_{\mathrm{Na}}/\mathrm{bar})^2\,(f\mathrm{O_2}/\mathrm{bar})^{1/2}}{a_{\mathrm{Na_2O}}}.
\]

**Relation to the repo Ellingham Na line.** The shipped JANAF-backed fit is for the oxidation family written per mole O₂,

\[
4\,\mathrm{Na}+\mathrm{O_2}=2\,\mathrm{Na_2O},
\qquad
\Delta G^\circ_{\mathrm{ox,Na}}(T)
\equiv
\texttt{ellingham\_delta\_g\_kj\_per\_mol\_o2("Na", }T_\mathrm{C}\texttt{)},
\]

so one mole of the dissociation above is minus half of that oxidation:

\[
\Delta G^\circ_{\mathrm{dissoc,Na}}(T)=-\tfrac12\Delta G^\circ_{\mathrm{ox,Na}}(T).
\]

(Phase of Na follows the Ellingham segment: liquid below the boil, gas above — already encoded in the fit segments.)

Solve for oxygen fugacity:

\[
\boxed{
f\mathrm{O_2}/\mathrm{bar}
=
\left(
\frac{K_{\mathrm{Na}}(T)\,a_{\mathrm{Na_2O}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2}
\right)^{\!2}
}.
\]

Units: \(f\mathrm{O_2}\) and \(p_{\mathrm{Na}}\) in bar; \(a_{\mathrm{Na_2O}}\) dimensionless; \(K_{\mathrm{Na}}\) dimensionless under those standard states. Publish `equality_fO2_log = log10(fO2/bar)` when all of \(K\), \(a\), and \(p_{\mathrm{Na}}\) are available and positive. Otherwise equality absent with a typed flag (`alkali_activity_unavailable`, `alkali_p_Na_unavailable`, or `alkali_K_unavailable`).

**Limiting cases**

| Limit | Behaviour |
|---|---|
| \(a_{\mathrm{Na_2O}}\to 0\) at fixed \(p_{\mathrm{Na}}\) | \(f\mathrm{O_2}\to 0\); more reducing; directional release capacity of the oxide vanishes with the inventory |
| \(p_{\mathrm{Na}}\to 0\) at fixed \(a\) | Equality requires vanishing \(f\mathrm{O_2}\) only if \(K a\) stays finite — but the couple is **incomplete** without live vapour/dose; do not publish equality |
| \(p_{\mathrm{Na}}\to\infty\) (sustained lance) | \(f\mathrm{O_2}\propto 1/p_{\mathrm{Na}}^4\); interface collapses; SiO \(\propto 1/\sqrt{f\mathrm{O_2}}\) surges (§3) |
| \(a_{\mathrm{Na_2O}}\to 1\), \(p_{\mathrm{Na}}=1\) | \(f\mathrm{O_2}=K_{\mathrm{Na}}^2\); pure-liquid Na₂O coexistence line |
| Hard vacuum, \(k_g=\infty\) | Exchange regime E0 still wins for passive O₂ film (r3). Alkali equality may still be published as melt authority; SiO reads committed `interface_pO2_bar`, not a substituted equality |

### 1.3 Directional capacity for oxygen exchange

Write the alkali gas reaction with \(d>0\) = melt → headspace (same sign convention as r3):

\[
\mathrm{Na_2O}\to 2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2}
\quad\Rightarrow\quad
\Delta n_{\mathrm{Na_2O}}=-2d,\quad
\Delta n_{\mathrm{head}}=d,\quad
\Delta n_{\mathrm{Na(g)}}=+4d
\]

per mole O₂ transferred (\(d\) in mol O₂). Bounds while inventories last:

\[
-n_{\mathrm{Na(g\,or\,dose)}}/4 \le d \le n_{\mathrm{Na_2O}}/2,
\]

intersected with the headspace floor bound from r3. Potassium is the same with K inventories. When Na and K are both live, capacity is the sum of the two signed intervals evaluated on their own inventories; the published equality uses the **parallel fugacity rule** in §1.5.

### 1.4 Coexistence with M2 — which sets fO₂?

While `n_Fe>NOOP` and `n_FeO>NOOP`, the melt regime stays **M2**. The Fe–FeO buffer equality

\[
\log_{10}(f\mathrm{O_2}/\mathrm{bar})=\mathrm{IW}(T)+2\log_{10}(a_{\mathrm{FeO}})
\]

is the published melt equality (r3). The alkali couple does not overwrite it.

The two couples are linked by the cross equilibrium (example for Na):

\[
2\,\mathrm{Na(g)}+\mathrm{FeO(melt)}=\mathrm{Na_2O(melt)}+\mathrm{Fe(metal)}.
\]

Combining the alkali dissociation with Fe–FeO elimination of \(\mathrm{O_2}\) gives, at mutual equilibrium,

\[
K_\times(T)
=\frac{a_{\mathrm{Na_2O}}\,a_{\mathrm{Fe}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2\,a_{\mathrm{FeO}}}
=\frac{K_{\mathrm{Na}}(T)}{K_{\mathrm{FeO}}(T)},
\]

with \(a_{\mathrm{Fe}}=1\) on pure metal and \(K_{\mathrm{FeO}}\) the FeO dissociation constant implied by IW. **At equilibrium the two expressions for \(f\mathrm{O_2}\) agree.** Away from that point the shuttle advances the cross reaction while \(\Delta G_\times<0\) (b-646), moving \(a_{\mathrm{FeO}}\), \(a_{\mathrm{Na_2O}}\), and/or \(p_{\mathrm{Na}}\) until either inventories bind or \(\Delta G_\times=0\).

**Which sets the interface the vapour sees?** Same rule as r3: SiO and other release rails read `interface_pO2_bar` from the exchange regime, not a substituted melt equality. While M2 holds and passive exchange commits under the finite metal film, the interface is the M2 root (E4/E5) or gas on zero commit (E3). The alkali cross reaction is a ledger mutation in the metallothermic step; it is not a second passive O₂ film. After iron is gone and M6 is classified, passive exchange may use the alkali gas reaction of §1.3 with a finite film analogous to M2 (chunk plan below) — until that film lands, zero-commit hours still publish gas.

**Process reading (Fe/SiO de-confliction).** While FeO+metal remain, the buffer pins \(f\mathrm{O_2}\) near IW-scale at the actual \(a_{\mathrm{FeO}}\). Sustained Na lancing then spends itself on the cross reaction (Fe metal product) rather than collapsing the published M2 equality to the pure-alkali \(p_{\mathrm{Na}}^{-4}\) line. That is exactly the shuttle’s Fe-first role. Once FeO and ferric are exhausted, M6 takes over and the interface can fall to the alkali line — by which time Fe has already been tapped. See §3.

### 1.5 How K and Na combine

Both alkalis open M6. Published equality when both vapour partials and both oxides are live:

1. Compute \(f\mathrm{O_2}^{(\mathrm{Na})}\) and \(f\mathrm{O_2}^{(\mathrm{K})}\) from §1.2.
2. If both are available, the melt cannot sit at two different equalities. The **equilibrium** state requires the cross reaction \(\mathrm{Na}/\mathrm{K}\) exchange through the shared oxygen to drive them together; the published number is the common value once \(\Delta G\) for \(\mathrm{Na_2O}+2\mathrm{K}=\mathrm{K_2O}+2\mathrm{Na}\) is zero at the current activities/partials.
3. **Predict-and-flag fallback** while that joint solve is not implemented: publish the **more reducing** of the two available equalities (lower \(f\mathrm{O_2}\)) and flag `alkali_parallel_min_fO2_provisional`. Directional capacity still sums both inventories. This is a named provisional, not a silent min().
4. Executable Ellingham already refuses K→FeO through most of the practical melt window (provider gate; setpoints note formal K/Fe crossover ~836 °C). Na remains the primary lance for Fe cleanup; K remains available for oxide targets where its margin stays positive.

---

## 2. Thermodynamically limited shuttle extent (b-646)

### 2.1 Stop condition

For each target oxide reaction family, the metallothermic step advances while

\[
\Delta G_{\mathrm{rxn}}(T,\{a_i\},p_{\mathrm{Na\,or\,K}})<0
\]

and inventories remain above NOOP. It **stops** at the first of:

- \(\Delta G_{\mathrm{rxn}}\ge 0\) at current activities and reductant partial (equilibrium extent for this dose);
- reductant dose exhausted;
- target oxide exhausted;
- typed refusal (activity unavailable without fallback authority; Ellingham extrapolation beyond certified flag policy).

The 10 wt% Na₂O / K₂O solubility cap (`NA2O_SOLUBILITY_WT_PCT = 10` in the attached shuttle) and the Ti accessibility factor (`TI_ACCESSIBILITY = 0.75`) are **retired as stopping rules**. Solubility may remain as a **process / freeze-gate warning** (liquidus / slag volume), never as a substitute for \(\Delta G\). Accessibility is not a thermodynamic quantity; it must not clip Ti toward metal.

### 2.2 Target order and reactions (Na shown; K analogous)

Ferric-first (b-645, separate / in flight) runs before FeO→Fe when Fe₂O₃ is present:

| Order | Reaction | Notes |
|---|---|---|
| 1 | \(2\mathrm{Na}+\mathrm{Fe_2O_3}\to\mathrm{Na_2O}+2\mathrm{FeO}\) | b-645; does not mint metal |
| 2 | \(2\mathrm{Na}+\mathrm{FeO}\to\mathrm{Na_2O}+\mathrm{Fe}\) | Opens/keeps M2 metal |
| 3 | \(6\mathrm{Na}+\mathrm{Cr_2O_3}\to 3\mathrm{Na_2O}+2\mathrm{Cr}\) | After FeO stage when stage-aware |
| 4a | \(2\mathrm{Na}+2\mathrm{TiO_2}\to\mathrm{Na_2O}+\mathrm{Ti_2O_3}\) | **Required intermediate** |
| 4b | \(6\mathrm{Na}+\mathrm{Ti_2O_3}\to 3\mathrm{Na_2O}+2\mathrm{Ti}\) | Only if \(\Delta G<0\) **and** Ti₂O₃ inventory exists; never a single stoich hop TiO₂→Ti |

The legacy attached path `4 Na + TiO2 → 2 Na2O + Ti` with 0.75 accessibility is **forbidden** under d-056 / b-646. Extent for 4a at equilibrium for a dose \(n_{\mathrm{Na}}^0\):

\[
\xi_{4\mathrm{a}}=\min\bigl(
n_{\mathrm{Na}}^0/2,\;
n_{\mathrm{TiO_2}},\;
\xi^{(\Delta G=0)}
\bigr),
\]

where \(\xi^{(\Delta G=0)}\) is the root of \(\Delta G_{\mathrm{rxn}}(T,a(\xi),p_{\mathrm{Na}})=0\) along the stoichiometric line. If that root is non-positive at the initial state, \(\xi=0\) and the step refuses with `thermodynamic_margin_nonpositive` (existing refusal token, new basis).

### 2.3 Activities required and who supplies them today

| Activity | Needed for | Engine today | Gap handling |
|---|---|---|---|
| \(a_{\mathrm{FeO}}\) | M2 equality; cross \(K_\times\); FeO shuttle margin | CALPHAD / core FeO activity fixed point (r3 M2) | Flag `fe_feo_buffer_activity_unavailable`; no equality (r3) |
| \(a_{\mathrm{Fe}}=1\) | M2; cross reaction | Pure metal assumption when metal phase present | If metal is alloyed, refuse or flag dilution — out of this design’s scope |
| \(a_{\mathrm{Na_2O}}\), \(a_{\mathrm{K_2O}}\) | M6 equality; all shuttle \(\Delta G\) | **t-999 open.** Shipped constant-\(\gamma\) table in `melt_activity.py` (`γ(NaO0.5)=1e-3` @ 1673 K anchor, Sossi et al. 2019 / Sossi & Fegley 2018; `γ(KO0.5)=3.5e-5` @ 1500 K, DeMaria 1971 via Sossi & Fegley) is an **UNCERTIFIED** predict-and-flag fallback | Interface §2.4 |
| \(a_{\mathrm{TiO_2}}\), \(a_{\mathrm{Ti_2O_3}}\) | Ti path 4a/4b | openimcc liquid activities are **ferrous-only** today; MAGEMin/MELTS with imposed buffers may return Ti⁴⁺ oxide activity under a named buffer, not a free Ti³⁺/Ti⁴⁺ couple | Typed refusal `ti_activity_unavailable` **or** flagged ideal-solution extrapolation with `evidence_tier=ASSUMED_IDEAL_SOLUTION` — never silent |
| \(a_{\mathrm{Cr_2O_3}}\) | Cr shuttle | Same engine map as Ti; table \(\gamma\) exists for some Cr rows in the constant-\(\gamma\) table | Flag / ideal fallback as above |
| \(p_{\mathrm{Na}}\), \(p_{\mathrm{K}}\) | M6; \(\Delta G\) | Lance dose → local interface partial from existing vapour / injection accounting; not invent | If only bulk headspace Na is known, flag `alkali_p_local_unresolved` and use it as provisional |

**openimcc:** ferrous-only liquid activities — may supply \(a_{\mathrm{FeO}}\)-class numbers where wired; must **not** be asked for \(a_{\mathrm{Na_2O}}\) as if certified. **MAGEMin/MELTS:** imposed-buffer modes can fix \(f\mathrm{O_2}\) and return oxide activities consistent with that buffer; using them to *close* M6 is circular if the buffer was the alkali couple itself — allowed only as a consistency check, flagged.

### 2.4 t-999 interface + predict-and-flag fallback

```text
AlkaliOxideActivityRequest
  component: Na2O | K2O
  T_K, composition_mol (ledger oxides)
  single_cation_basis: NaO0.5 | KO0.5   # engine declares

AlkaliOxideActivityResult
  activity: float | absent
  gamma: float | absent
  x_single_cation: float | absent
  evidence_tier: CERTIFIED | UNCERTIFIED | ASSUMED_IDEAL_SOLUTION | ABSENT
  citation: str
  limitation: str
  valid_range_K: (lo, hi) | absent
  domain: in_domain | out_of_gamma_domain | unknown
```

**Fallback ladder (no invent of numbers):**

1. Certified engine activity in domain → use; M6 equality live.
2. Else shipped constant-\(\gamma\) table (existing `melt_oxide_activity`) → use with `evidence_tier=UNCERTIFIED`, flag on the tick and on the shuttle diagnostic (`Na2O_activity_limitation` already present on the attached tip).
3. Else ideal \(a=X\) (single-cation or parent — declare which) → `ASSUMED_IDEAL_SOLUTION`, flag.
4. Else activity absent → M6 equality absent; shuttle margin may still run on **standard-state** Ellingham only with flag `margin_standard_state_only`, or refuse if the owner gate requires activity-shifted margin (today’s Na path shifts by activity when present).

**Corpus note (cite, do not invent).** Stolyarova 1992 (`kems-016`) records chemical potentials of Na₂O in Na₂O–B₂O₃–SiO₂ at 1273 K and Na₂O–K₂O–SiO₂ Gibbs tables; current extracts mark those quantities unsupported / phase-map refused — they are a t-999 source target, not a runtime number in this design. `kems-ms2000-044` carries K₂O activity rows in K₂O–SiO₂ melts (phase-map gaps similarly). Shornikov/Stolyarova KEMS CaO–Al₂O₃–SiO₂ extracts in the corpus do not by themselves supply \(a_{\mathrm{Na_2O}}\); do not cross-apply them.

### 2.5 Worked equilibrium extent (symbolic)

Dose \(n_{\mathrm{Na}}^0\), target FeO, reaction 2. Initial \(a_{\mathrm{FeO}}\), \(a_{\mathrm{Na_2O}}\), \(p_{\mathrm{Na}}\) from ledger/engines. Along \(\xi\) moles of FeO reduced:

\[
n_{\mathrm{FeO}}(\xi)=n_{\mathrm{FeO}}^0-\xi,\quad
n_{\mathrm{Na_2O}}(\xi)=n_{\mathrm{Na_2O}}^0+\xi,\quad
n_{\mathrm{Na}}(\xi)=n_{\mathrm{Na}}^0-2\xi,
\]

activities re-evaluated at each \(\xi\). Stop at \(\xi^*\) where \(\Delta G(\xi^*)=0\) or an inventory hits NOOP. Acceptance fixtures use a fixed activity model so \(\xi^*\) is reproducible without claiming certified \(a_{\mathrm{Na_2O}}\).

---

## 3. Process consequence — worked C3 SiO number

### 3.1 Setup (declared assumptions; not extract invention)

- Undosed C3 overhead hold (d-052): \(P_g=1\,\mathrm{mbar}=10^{-3}\,\mathrm{bar}\). SiO rails read committed `interface_pO2_bar`; on zero-commit exchange that is \(P_g\) (r3 E3).
- Sustained Na lancing local interface: \(p_{\mathrm{Na}}=0.01\,\mathrm{bar}\) (declared lance partial for this worked example).
- \(a_{\mathrm{Na_2O}}=10^{-8}\) (order cited in owner physics notes for sodiothermic Si in melt; **not** a Stolyarova table readout — flagged UNCERTIFIED input to this example).
- \(K_{\mathrm{Na}}(T)\) from shipped Ellingham Na via §1.2 (executable on the VPS seat; not a full W3 run).

### 3.2 Alkali-line \(f\mathrm{O_2}\) and SiO amplification

SiO equilibrium \(\mathrm{SiO_2}=\mathrm{SiO}+\tfrac12\mathrm{O_2}\) gives \(P_{\mathrm{SiO}}\propto 1/\sqrt{p\mathrm{O_2}}\) at fixed \(a_{\mathrm{SiO_2}},T\). Amplification versus the undosed 1 mbar interface:

\[
\frac{P_{\mathrm{SiO}}^{\mathrm{(lance)}}}{P_{\mathrm{SiO}}^{\mathrm{(hold)}}}
=\sqrt{\frac{10^{-3}}{f\mathrm{O_2}^{\mathrm{(M6)}}}}.
\]

Executable evaluation with the seat’s Ellingham Na:

| \(T\) | \(f\mathrm{O_2}^{\mathrm{(M6)}}\) / bar | \(\log_{10} f\mathrm{O_2}\) | SiO amp vs 1 mbar |
|---|---|---|---|
| 1150 °C (cool Na FeO window) | \(5.17\times10^{-28}\) | −27.29 | \(\sim 1.4\times10^{12}\) |
| 1500 °C | \(1.36\times10^{-18}\) | −17.87 | \(\sim 2.7\times10^{7}\) |

Derivation check at 1150 °C: \(\Delta G^\circ_{\mathrm{ox,Na}}\approx -525.5\,\mathrm{kJ/mol\,O_2}\), \(\Delta G^\circ_{\mathrm{dissoc}}=-\frac12\Delta G^\circ_{\mathrm{ox}}\approx +262.7\,\mathrm{kJ/mol\,Na_2O}\), \(K_{\mathrm{Na}}\approx 2.27\times10^{-10}\), then \(f\mathrm{O_2}=(K a / p_{\mathrm{Na}}^2)^2\) with the inputs above.

These numbers are **the pure-M6 line** (iron couple gone, or M6 equality substituted). They are the ceiling on how hard SiO can surge if the interface were handed the alkali equality.

### 3.3 Is Fe/SiO de-confliction preserved?

**Yes, while M2 holds.** Per §1.4, retained Fe+FeO keeps melt regime M2 and pins equality at IW-scale. The lance’s job in that window is the cross reaction (Fe metal product), not rewriting `equality_fO2_log` onto the \(10^{-28}\) bar line. Zero-commit vapour still sees \(P_g\) (E3); nonzero M2 exchange sees the finite metal-film interface — both are orders of magnitude above the pure-M6 line at cool C3. Fe can be tapped without inviting the \(10^{12}\) SiO multiplier.

**After iron oxides are gone**, M6 is the couple (d-056). The worked table then applies if exchange hands the alkali equality to the interface (future alkali film chunk) or if a consumer illegally substitutes melt equality for `interface_pO2_bar` (still forbidden — r3 consumer contract). Optimizer consequence (already in owner notes): do not strip O₂ / lance so hard that SiO surges and the CAS rump freezes before Fe/alkali finish — backpressure and dose are redox levers, not only transport costs.

**Cool-Na temperature gate.** Executable setpoints pin cool Na FeO cleanup ≲1150 °C with an activity-shifted provider margin (~+11 kJ/mol O₂ at 1150 °C on the attached tip’s diagnostic). Standard-state JANAF Na−Fe margin on the seat fit is still positive at 1150 °C (~+130 kJ/mol O₂) with a higher standard-state crossover (~1455 °C). Design rule: the **activity-shifted** margin is the shuttle gate when \(a_{\mathrm{Na_2O}}\) is available; standard-state-only is flagged. Do not re-pin crossover from this prose.

---

## 4. Owner questions (only where data cannot decide)

**Q-A. t-999 certified engine.** Which engine is authoritative for \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) in lunar-basalt-like melts at 1100–1600 °C: CALPHAD assessment, openimcc extension, MAGEMin, or a Stolyarova/Sossi KEMS inversion baked as a certified table? Until answered, the §2.4 ladder stays.

**Q-B. Ti₂O₃ activity.** Is ideal \(a_{\mathrm{Ti_2O_3}}=X_{\mathrm{Ti_2O_3}}\) acceptable as a flagged interim for reaction 4a, or is shuttle Ti reduction refused until a certified Ti³⁺ activity exists? (Data cannot invent Ti₂O₃ γ from the ferrous-only openimcc path.)

**Q-C. Parallel Na+K equality.** Keep provisional `min(fO2)` (§1.5.3) through first landing, or require the joint Na/K exchange solve before M6 may publish equality when both are live?

**Q-D. Alkali film for passive exchange.** When M6 is classified, should passive O₂ exchange grow a finite alkali-inventory film (M2-shaped) in the same plan, or remain E2-like (gas interface, shuttle-only reductant chemistry) until a follow-on ticket? Default proposed: **shuttle-only first** (chunks below); alkali passive film is a later chunk so Fe/SiO behaviour does not change before M2 consumers settle.

No other owner fork is open for d-056 / b-646. Ferric-first sequencing is b-645’s ticket. C3 1 mbar hold stays an optimizer knob (d-052).

---

## 5. Invariants (extensions to r3 §2)

- **M1 is not M6.** Flagged absence after iron remains only when the alkali couple is incomplete or absent.
- **M2 wins coexistence.** Alkali does not replace `IW+2 log10(a_FeO)` while metal+FeO remain.
- **No TiO₂→Ti hop.** Ledger transitions must show Ti₂O₃ (or refuse).
- **ΔG stops the shuttle, not 10 wt% / 0.75.** Solubility may warn; it must not clip extent when \(\Delta G<0\) and inventories remain.
- **Activity honesty.** UNCERTIFIED / ideal / absent are distinct flags; no silent γ.
- **Vapour still reads `interface_pO2_bar`.** Alkali equality is never substituted into SiO (r3 §3 preserved).
- **Ledger closure.** Shuttle debits/credits stay atom-balanced (existing `build_atom_balance_proof`); Na₂O credited to melt; metal to metal phase; no invented equipment FK.

---

## 6. Consumer contract deltas

| Consumer | Change |
|---|---|
| SiO / surface release | Unchanged field: `interface_pO2_bar`. New risk is M6 hours if a future alkali film commits a very low interface — still the committed field, not equality |
| Liquidus / PT-0 / MRE / optimizer numeric fO₂ | May read M6 `equality_fO2_log` when present, same as M2/M5; absence includes incomplete alkali couple |
| Native-Fe split | Unchanged on M2/M4/M5. M6 does not precipitate Fe by itself |
| Metallothermic provider | Margin from \(\Delta G(T,a,p)\); refuse TiO₂→Ti; emit activity audit; drop accessibility clip |
| Reports | Render M6 equality with authority/tier; show bound/absence empty, never as 0 |

---

## 7. Plan — luna-sized chunks

Default for Q-D is shuttle-only first. Each chunk: one change; derived acceptance; mutation restores old behaviour and must fail; tree restored. Focused tests only (VPS). No full W3.

**A1. Regime table: M6 predicate + M1 narrowing.** Classifier grows M6; M1 requires alkali couple absent. Acceptance: FeO=Fe₂O₃=0, Na₂O>NOOP, Na dose>NOOP → M6; same without dose → M1 with `alkali_couple_incomplete`. Mutation: remove dose check; incomplete state falsely classifies M6.

**A2. Equality publisher.** Implement §1.2 from Ellingham Na/K + activity result + \(p\). Acceptance: fixture with fixed \(K,a,p_{\mathrm{Na}}\) reproduces the boxed \(f\mathrm{O_2}\) within 1e-8 relative; missing \(a\) → equality absent + flag. Mutation: hard-code \(a=1\) silently; flag assertion fails.

**A3. M2 coexistence.** With metal+FeO+Na dose, regime M2; equality is IW line; shuttle may still reduce FeO. Acceptance: classifier M2; equality matches FeO buffer fixed point, not alkali line. Mutation: prefer alkali line whenever dose>0; M2 equality assertion fails.

**A4. b-646 stop + retire accessibility/solubility stop.** Shuttle loops targets while \(\Delta G<0\); removes `TI_ACCESSIBILITY` clip and solubility `break` as hard stops (warning OK). Acceptance: synthetic negative-margin fixture refuses with `thermodynamic_margin_nonpositive` at \(\xi=0\); positive-margin Ti path writes Ti₂O₃ not Ti. Mutation: restore 0.75 clip on a \(\Delta G<0\) TiO₂→Ti₂O₃ fixture; extent must fail the full-\(\Delta G\) expectation.

**A5. Ti two-step.** Replace `4 Na + TiO2 → Ti` with 4a then optional 4b. Acceptance: TiO₂ reduction credits Ti₂O₃; Ti metal credit only from Ti₂O₃ inventory. Mutation: single-hop Ti credit from TiO₂; atom-proof or intermediate-inventory assertion fails.

**A6. Consumer absence widen.** Liquidus/PT-0/MRE treat M6 absence like M1/M3/M4. Acceptance: incomplete alkali couple does not `float(None)`. Mutation: M3-only guard left in place; incomplete alkali reaches `float(None)`.

**A7. (Later, if Q-D flips)** Finite alkali film for passive exchange — M2-shaped surface inventory on \(n_{\mathrm{Na_2O}}/2\). Not in the default first landing.

Studio measurement of SiO CPU remains out of VPS scope (r3 chunk 12 pattern).

### Runnable mutation sketch (A2)

```python
def fO2_alkali(K, a, p_Na):
    return (K * a / (p_Na ** 2)) ** 2

def acceptance():
    K, a, p = 2.274235e-10, 1e-8, 0.01
    assert abs(fO2_alkali(K, a, p) / 5.172e-28 - 1.0) < 1e-3

acceptance()

def mutation_force_a_one(K, a, p_Na):
    return fO2_alkali(K, 1.0, p_Na)  # silently drops activity

try:
    K, a, p = 2.274235e-10, 1e-8, 0.01
    assert abs(mutation_force_a_one(K, a, p) / 5.172e-28 - 1.0) < 1e-3
    raise AssertionError("a=1 mutation survived")
except AssertionError as e:
    if "survived" in str(e):
        raise
    print("a=1 mutation rejected")

acceptance()
print("Restored acceptance completed")
```

---

## 8. Mapping to tickets

| Ticket / decision | This design |
|---|---|
| d-056 | M6 alkali couple after iron / whenever dose+oxide live |
| b-646 | ΔG-limited shuttle; no 10 wt% / 0.75 stop; no Ti skip |
| b-645 | Ferric-first order constraint; implementation elsewhere |
| b-618 / r3 Q2 | Superseded for alkali; Ti/Cr still secondary |
| t-999 | Activity interface + fallback ladder |
| d-052 | 1 mbar hold remains optimizer input for undosed comparison |

---

## 9. Review disposition

Author design extension for regolith-physics, not an independent confirmation of r3. Numerical worked values evaluated on the empirical VPS seat from shipped Ellingham + declared example inputs; repository mutations remain A1–A6 work. Tip `e7bd0cd52` consulted as attached shuttle source only; no commit on `review/stack2-redox` from this delivery.

— regolith-empirical, 2026-10-01
