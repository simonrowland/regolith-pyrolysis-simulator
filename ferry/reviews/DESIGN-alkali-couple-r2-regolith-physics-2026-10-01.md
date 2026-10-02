# Alkali-couple melt regime r2: extension of redox design r3 (d-056 / d-057)

Design-only. No stack2 product code in this delivery. **Edit of r1** (`DESIGN-alkali-couple-regolith-physics-2026-10-01.md`). Folds sol second opinion (UNSOUND as written; approach sound), AMEND (simultaneous titration), AMEND2 (structural sites), and OWNER RULING Q-A..Q-D (decided). Numerics re-run on seat `slot-z14` @ green tip `696299350e98b67f786c334b4b4ccc8856149379` against shipped Ellingham, liquid-FeO IW (`feo_iw_log10_fO2_bar`), `melt_activity.py` parent conversion, and openimcc `ext-v4` associates. No invent of extract numbers or equipment FKs. Ferric-first (b-645) remains a sequencing constraint owned elsewhere.

North Star unchanged: predict-and-flag, ledger closure, derivation beside every threshold.

Owner decision **d-056** (r3 §7 Q2): after iron, and whenever alkali dose/vapour is present with alkali oxide in the melt, the melt redox couple is the **alkali couple**

\[
\mathrm{Na_2O(melt)}=2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2},
\qquad
\mathrm{K_2O(melt)}=2\,\mathrm{K(g)}+\tfrac12\mathrm{O_2}.
\]

Owner rulings **d-057 / Q-A..Q-D** (this r2): openimcc associate = working \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) source, FLAGGED; ideal \(a_{\mathrm{Ti_2O_3}}=X\), FLAGGED, with JANAF Ti₂O₃/TiO/Ti₃O₅ + explicit reference states; **joint** Na/K solve before publishing equality (no `min(fO2)`); shuttle chemistry first, passive alkali film later, zero commit publishes gas; **r1 Fe/SiO assurance WITHDRAWN** pending coupled shuttle/film + selective-flux acceptance.

Ti³⁺/Ti⁴⁺ and Cr remain secondary couples in the simultaneous solve (not separate equality regimes). The shuttle's extent is thermodynamically limited (b-646). The **10 wt% Na₂O stoichiometric cap is dropped** (not retuned): the physical stop is \(\Delta G\to 0\); site saturation is why that happens early. Na must not take TiO₂ straight to Ti metal skipping Ti³⁺.

This document keeps r1/r3 structure: regime table, invariants, consumer contract, luna-sized chunks. It does not reopen r3 chunks 1–11 except where M6 / b-646 / titration touch their contracts. See **"Dropped or changed from r1"** at the end so nothing silently disappears.

---

## 0. What r3 already fixed, what r1 added, what r2 corrects

r3's M0–M5 iron story, finite M2 film, mole-log M5 inverse, absence handling, and committed-interface vapour read stay. r3 §7 Q2 default and b-618 holding pattern remain **superseded for alkali** by d-056.

r1 added M6, ΔG-limited shuttle, and a worked SiO consequence — but with Celsius-as-Kelvin Ellingham, inverted \(K_\times\), overdrawing Ti bound, incomplete \(p_{\mathrm{Na}}\) closure, waterfall framing, and an Fe/SiO assurance that sol refuted.

r2 corrects those, replaces the waterfall with a **simultaneous shared-\(\mu_{\mathrm{O_2}}\) titration**, makes structural Al (and Fe³⁺) sites explicit, folds decided Q-A..Q-D, and leaves **one owner fork**: lance-zone / local \(p_{\mathrm{Na}}\) model (options with numbers in §4).

---

## 1. Alkali-couple melt regime (M6)

### 1.1 Trigger predicate (ledger-first)

First-match precedence inserts **M6 after M2 and before M3**. While metal+FeO remain, regime stays M2 (equality formula is still IW-based) — but the **value** of that equality moves with depletion under the simultaneous solve (§2). Alkali does not rename M2.

| # | Regime | Ledger predicate, first match | Melt fO₂ | Respeciation / shuttle may write |
|---|---|---|---|---|
| M0 | `not_liquid` | (unchanged) | Absent | Nothing |
| M1 | `no_modelled_redox_couple` | Liquid; FeO≤NOOP; Fe₂O₃≤NOOP; **and** alkali couple absent | Absent. Flagged `out_of_domain` | Nothing |
| M2 | `fe_feo_buffer` | `n_Fe(metal)>NOOP` and `n_FeO>NOOP` | Equality `IW+2 log10(a_FeO)` at **current** \(a_{\mathrm{FeO}}\) (moves under dose) | Metal gas reaction (r3). Alkali cross-reaction is part of the shared-\(\mu_{\mathrm{O_2}}\) solve, not a second equality |
| **M6** | **`alkali_na_k_buffer`** | Liquid; **complete same-species pair** (Na₂O+Na dose/vapour, and/or K₂O+K dose/vapour); **and** not M2 | Equality from joint Na/K solve (§1.5) | Alkali gas reaction (§1.3). No invent of Fe₂O₃. Ti path stops at Ti³⁺ unless 4b margin opens (§2) |
| M3–M5 | (unchanged) | (unchanged) | (unchanged) | (unchanged) |

**Alkali couple absent** (for M1): no complete same-species pair. Na₂O+K dose (mismatched species) does **not** open M6 (r1 OR-groups rejected). Flag `alkali_couple_incomplete`. Oxide without reductant vapour, or reductant without oxide, leaves equality absent.

Trace inventory: `OXYGEN_RESERVOIR_NOOP_MOL = 1e-15` mol. No ε-fraction gate.

### 1.2 fO₂ equality — derivation, units, Kelvin, limiting cases

Dissociation for sodium (K analogous):

\[
\mathrm{Na_2O(melt)}=2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2(g)}.
\]

Standard states: melt oxide activity \(a_{\mathrm{Na_2O}}\) on the **parent** formula basis from `melt_activity.thermodynamic_parent_activity` / openimcc parent activities (\(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2=(\gamma X_{\mathrm{Na}})^2\) when using the constant-\(\gamma\) ladder); gases at 1 bar. **Phase references must match** before combining with Raoultian liquid activities (§1.6).

\[
K_{\mathrm{Na}}(T)=\exp\!\bigl(-\Delta G^\circ_{\mathrm{dissoc,Na}}(T)/(RT)\bigr)
=\frac{(p_{\mathrm{Na}}/\mathrm{bar})^2\,(f\mathrm{O_2}/\mathrm{bar})^{1/2}}{a_{\mathrm{Na_2O}}}.
\]

**Ellingham API is Kelvin** (`ellingham_delta_g_kj_per_mol_o2(species, temperature_K)`). The shipped JANAF fit is for oxidation per mole O₂:

\[
4\,\mathrm{Na}+\mathrm{O_2}=2\,\mathrm{Na_2O},
\qquad
\Delta G^\circ_{\mathrm{dissoc,Na}}(T)=-\tfrac12\Delta G^\circ_{\mathrm{ox,Na}}(T).
\]

**Seat-verified at 1150 °C = 1423.15 K** (passing `1150` selects Na(l)/Na₂O(β) and is the r1 bug):

| Quantity | r1 (C as K / mixed) | r2 corrected |
|---|---:|---:|
| \(\Delta G^\circ_{\mathrm{ox,Na}}\) / kJ mol⁻¹ O₂ | −525.47 | **−371.6126** |
| \(\Delta G^\circ_{\mathrm{dissoc}}\) / kJ mol⁻¹ Na₂O | +262.7 | **+185.8063** |
| \(K_{\mathrm{Na}}\) | \(2.274\times10^{-10}\) | **\(1.514901\times10^{-7}\)** |
| M6 \(f\mathrm{O_2}\) at \(a=10^{-8}\), \(p_{\mathrm{Na}}=0.01\) bar | \(5.17\times10^{-28}\) | **\(2.294925\times10^{-22}\)** |
| Formal SiO amp vs 1 mbar | \(\sim1.4\times10^{12}\) | **\(2.087448\times10^{9}\)** |
| Standard-state Na/Fe margin / kJ mol⁻¹ O₂ | ~+130 | **+11.089** (matches setpoints) |

At 1500 °C = 1773.15 K: \(K_{\mathrm{Na}}=9.725844\times10^{-4}\), M6 \(f\mathrm{O_2}=9.459204\times10^{-15}\) bar, formal SiO amp \(3.251417\times10^{5}\).

Solve:

\[
\boxed{
f\mathrm{O_2}/\mathrm{bar}
=
\left(
\frac{K_{\mathrm{Na}}(T)\,a_{\mathrm{Na_2O}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2}
\right)^{\!2}
}.
\]

Publish `equality_fO2_log = log10(fO2/bar)` only when \(K\), \(a\), and \(p_{\mathrm{Na}}\) are available and positive; else typed flag (`alkali_activity_unavailable`, `alkali_p_Na_unavailable`, `alkali_K_unavailable`, `alkali_phase_reference_unmatched`).

**Limiting cases (corrected)**

| Limit | Behaviour |
|---|---|
| \(a_{\mathrm{Na_2O}}\to 0\) at fixed \(p_{\mathrm{Na}}\) | \(f\mathrm{O_2}\to 0\); more reducing; oxide release capacity vanishes with inventory |
| \(p_{\mathrm{Na}}\to 0\) at fixed positive \(Ka\) | **\(f\mathrm{O_2}\to\infty\)** (r1 said vanishing — wrong). Exact absence of vapour/dose → couple **incomplete**; do not publish equality |
| \(p_{\mathrm{Na}}\to\infty\) (sustained lance) | \(f\mathrm{O_2}\propto 1/p_{\mathrm{Na}}^4\); SiO \(\propto 1/\sqrt{f\mathrm{O_2}}\) surges (§3) — formal only until film couples |
| \(a_{\mathrm{Na_2O}}\to 1\), \(p_{\mathrm{Na}}=1\) | \(f\mathrm{O_2}=K_{\mathrm{Na}}^2\); pure-liquid Na₂O coexistence line |
| Hard vacuum, \(k_g=\infty\) | Exchange E0 still wins for passive O₂ film (r3). Alkali equality may publish as melt authority; SiO reads committed `interface_pO2_bar` |

### 1.3 Directional capacity for oxygen exchange

Same sign convention as r3 (\(d>0\) = melt → headspace):

\[
\mathrm{Na_2O}\to 2\,\mathrm{Na(g)}+\tfrac12\mathrm{O_2}
\quad\Rightarrow\quad
\Delta n_{\mathrm{Na_2O}}=-2d,\quad
\Delta n_{\mathrm{head}}=d,\quad
\Delta n_{\mathrm{Na(g)}}=+4d
\]

per mole O₂. Bounds: \(-n_{\mathrm{Na(g\,or\,dose)}}/4 \le d \le n_{\mathrm{Na_2O}}/2\), intersected with headspace floor. When both Na and K live, capacity sums both inventories **only after** the joint solve has produced a common equality (Q-C); do not authorize summed capacity at a provisional `min(fO2)`.

### 1.4 Coexistence with M2 — corrected \(K_\times\); Fe/SiO assurance withdrawn

While `n_Fe>NOOP` and `n_FeO>NOOP`, regime stays **M2**. Published equality remains

\[
\log_{10}(f\mathrm{O_2}/\mathrm{bar})=\mathrm{IW}(T)+2\log_{10}(a_{\mathrm{FeO}})
\]

at the **current** \(a_{\mathrm{FeO}}\) from the simultaneous solve — not a frozen initial IW-scale value.

Cross equilibrium \(2\mathrm{Na(g)}+\mathrm{FeO}=\mathrm{Na_2O}+\mathrm{Fe}\):

\[
\boxed{
K_\times(T)
=\frac{a_{\mathrm{Na_2O}}\,a_{\mathrm{Fe}}}{(p_{\mathrm{Na}}/\mathrm{bar})^2\,a_{\mathrm{FeO}}}
=\frac{K_{\mathrm{FeO}}(T)}{K_{\mathrm{Na}}(T)}
}.
\]

(r1 had the reciprocal — inverted.) Seat at 1150 °C with liquid-FeO IW: \(K_{\mathrm{FeO}}=7.286023\times10^{-7}\), \(K_\times=4.809570\). At \(a_{\mathrm{Na_2O}}=10^{-8}\), \(p_{\mathrm{Na}}=0.01\), \(a_{\mathrm{Fe}}=1\): mutual equilibrium requires \(a_{\mathrm{FeO}}=2.079188\times10^{-5}\); both M2 and M6 lines then agree at \(f\mathrm{O_2}=2.294925\times10^{-22}\) bar **while FeO+metal still coexist**.

**Dose counterexample** (100 mol FeO, 1000 mol inert, 250 mol Na, ideal FeO, shipped \(\gamma_{\mathrm{NaO_{0.5}}}=10^{-3}\), fixed \(p_{\mathrm{Na}}=0.01\)): equilibrium at FeO remaining **0.069214 mol** (≫NOOP), \(a_{\mathrm{Na_2O}}=2.774254\times10^{-8}\), shared M2/M6 \(f\mathrm{O_2}=1.766286\times10^{-21}\) bar. M2 still applies; its equality has moved ~7+ dex vs an initial \(a_{\mathrm{FeO}}\sim0.1\).

**Consequence:** "M2 holds ⇒ Fe/SiO de-confliction preserved" is **refuted**. Replace with a **coupled shuttle/film calculation** and a **selective-flux acceptance test** (chunk A8). r3's finite metal film remains load-bearing for the actual SiO interface; this design no longer claims de-confliction from the regime label alone.

**Which sets the interface vapour sees?** Unchanged from r3: SiO reads `interface_pO2_bar` from the exchange regime. Shuttle is a ledger mutation; not a second passive O₂ film. Zero-commit hours still publish gas (Q-D).

### 1.5 How K and Na combine (Q-C decided)

Both alkalis can open M6. **Joint Na/K solve required** before publishing a shared melt equality when both pairs are live (OWNER RULING Q-C). The shared-\(\mu_{\mathrm{O_2}}\) titration of §2 does this. **No `min(fO2)` shortcut** — r1's provisional `alkali_parallel_min_fO2_provisional` is dropped as a publishable equality.

Executable Ellingham still refuses K→FeO through most of the practical melt window; Na remains the primary lance for Fe cleanup.

### 1.6 Phase / standard-state contract (P3)

- Ellingham phase selection does **not** automatically satisfy pure-liquid oxide + gaseous-reductant standard states.
- Below the alkali boiling point, condensed-metal Ellingham data require **conversion to a gas reference** before use with \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\) in the boxed formula.
- Potassium's shipped K₂O path uses a **crystalline** reference in places; convert or refuse before combining with Raoultian liquid \(a_{\mathrm{K_2O}}\).
- Never mix incompatible standard states between openimcc parents, constant-\(\gamma\) table, and Ellingham segments. Flag `alkali_phase_reference_unmatched` when conversion is unavailable.

---

## 2. Simultaneous shared-\(\mu_{\mathrm{O_2}}\) equilibrium (replaces waterfall)

### 2.1 Formulation (AMEND)

One shared oxygen potential \(\mu_{\mathrm{O_2}}\) (\(\log f\mathrm{O_2}\)) and, if the reacting zone is closed in alkali, one alkali potential (\(p_{\mathrm{Na}}\) / \(a_{\mathrm{Na}}\)) for the whole zone (melt + metal + lance gas). Every couple is at equilibrium with that **same** \(\mu_{\mathrm{O_2}}\) at the **current** composition:

- Fe³⁺/Fe²⁺ (Kress-type; carries Na₂O/K₂O term)
- Fe²⁺/Fe⁰
- Cr
- Ti⁴⁺/Ti³⁺/Ti⁰ (with JANAF intermediates; §2.5)
- Na₂O/Na, K₂O/K
- SiO₂/Si, MgO/Mg(g) — **in scope for prediction limits** (§2.6); may be inactive under declared flags

**Unknowns:** \(\mu_{\mathrm{O_2}}\) (and \(p_{\mathrm{Na}}\) if closed). **Constraints:** element mass balance (O, Na, K, Fe, Ti, Cr, Si, Mg) over melt + metal + gas. Solve as a **bracketed 1-D** (or 2-D with \(p_{\mathrm{Na}}\)) root on the oxygen (and alkali) balance — same shape as openimcc's Knudsen oxygen-balance root — then read every extent off the solution.

The "sequence" **emerges** as titration plateaus where couples are separated by many log units. Overlapping couples (Fe³⁺/Fe²⁺ vs Fe²⁺/Fe⁰ vs Ti⁴⁺/Ti³⁺ vs Cr) partition **simultaneously**. Waterfall is the limit of well-separated couples.

**Waterfall failure at C3 (seat numbers, 1150 °C):** with liquid-FeO IW, M2 \(f\mathrm{O_2}\) at \(a_{\mathrm{FeO}}=0.1\) is \(5.31\times10^{-15}\) bar (\(\log=-14.28\)); at mutual-eq \(a_{\mathrm{FeO}}=2.079\times10^{-5}\) it is \(2.29\times10^{-22}\) (\(\log=-21.64\)). A waterfall that freezes \(f\mathrm{O_2}\) at the initial IW-scale while depleting FeO **underpredicts reducing power by ~7.4 dex** while FeO+metal still coexist. That is the quantified C3 failure mode.

### 2.2 Melt structure / polymerization (AMEND + AMEND2)

Polymerization and complexing enter **only** through composition-dependent activities re-evaluated as dose changes the melt. Prefer **ONE** activity model:

| Role | Working source (Q-A) | Flag |
|---|---|---|
| \(a_{\mathrm{Na_2O}}\), \(a_{\mathrm{K_2O}}\) | **openimcc associate** (`ext-v4` datapack: NaAlO₂, NaAlSiO₄, NaAlSi₂O₆, NaAlSi₃O₈, Na₂SiO₃, Na₂Si₂O₅, Na₂TiO₃, KAlO₂, KAlSiO₄, KAlSi₃O₈, K₂SiO₃, KCaAlSi₂O₇, …) | **FLAGGED uncertified** until battery evidence certifies; certified table later replaces the flag |
| Ladder fallback | `melt_activity` constant-\(\gamma\) + `thermodynamic_parent_activity` (\(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2\)) | UNCERTIFIED |
| \(a_{\mathrm{FeO}}\) | CALPHAD / core FeO fixed point (r3 M2); openimcc may supply ferrous-class numbers where wired | per r3 |
| Pure metals | \(a=1\) **outside** the melt model | — |
| openimcc limits | **Ferrous-only; no metal; no ferric associates; ideal mixing among associates** | Flag all four whenever the step or Fe³⁺ competition is published |

**Structural sites (AMEND2):** Na⁺ and K⁺ first occupy charge-balancing sites of tetrahedral Al³⁺ (and tetrahedral Fe³⁺ when present), as NaAlO₂ / KAlO₂ / NaAlSiO₄ / NaAlSi₃O₈-type associates (large negative formation energies in the datapack). Dosed alkali oxide sits at **very low activity** until those sites saturate. Beyond Na+K ~ Al (peralkaline boundary, corrected for Ca/Mg competition: preference K > Na > Ca > Mg), further alkali is a network modifier at activity orders of magnitude higher — a **step** in \(a_{\mathrm{Na_2O}}\) vs dose that no bare Ellingham line shows. Shuttle driving force collapses near site saturation.

Alkali also stabilizes tetrahedral Fe³⁺ (M⁺-balanced). Fe³⁺-first reduction therefore **competes** with Na stabilizing Fe³⁺. openimcc cannot quantify that competition today (no ferric associates) — **predict-and-flag**: report Fe³⁺ plateau from Kress-type with Na₂O/K₂O term where available; flag missing ferric-associate feedback.

### 2.3 Seat: \(a_{\mathrm{Na_2O}}(\mathrm{dose})\) on declared mare fixture

**Declared 100 kg mare fixture:** majors from `data/feedstocks.yaml::lunar_mare_low_ti` normalized to 100 wt% (SiO₂ 45.81, TiO₂ 1.54, Al₂O₃ 13.90, FeO 16.98, MgO 9.26, CaO 11.32, Na₂O 0.41, K₂O 0.10, Cr₂O₃ 0.36, MnO 0.21, P₂O₅ 0.10). FeO = 236.41 mol; Na to exhaust FeO ≈ 10.87 kg. Undosed (Na+K)/Al = 0.057; peralkaline boundary (Na+K)/Al = 1 at **+128.6 mol Na₂O** (~5.91 kg Na) while FeO still remains.

**Shipped-\(\gamma\) parent \(a_{\mathrm{Na_2O}}\)** (stoichiometric FeO-consuming dose illustrations, not equilibrium extents), \(p_{\mathrm{Na}}=0.01\) bar, 1150 °C:

| Reacted Na | Parent \(a_{\mathrm{Na_2O}}\) | Formal SiO amp vs 1 mbar |
|---|---:|---:|
| 1 kg | \(1.03057\times10^{-9}\) | \(2.02553\times10^{10}\) |
| 4 kg | \(1.04264\times10^{-8}\) | \(2.00208\times10^{9}\) |
| 10.87 kg (exhaust FeO) | \(6.00647\times10^{-8}\) | \(3.47533\times10^{8}\) |

(Matches sol's published table to <0.1% — same fixture construction.)

**openimcc `ext-v4` parent \(a_{\mathrm{Na_2O}}\)** on the same fixture (FeO-consuming sweep, `allow_extrapolation/out_of_envelope` for the high-dose wing; FLAGGED):

| \(\xi\) mol Na₂O | (Na+K)/Al | openimcc \(a_{\mathrm{Na_2O}}\) | vs \(\gamma\)-table |
|---|---:|---:|---:|
| 0 | 0.057 | \(1.45\times10^{-14}\) | ~\(4\times10^{-4}\times\) table |
| 40 | 0.35 | \(2.49\times10^{-11}\) | ~\(9\times10^{-3}\times\) |
| 128.6 (peralkaline) | 1.00 | \(1.95\times10^{-9}\) | ~0.09× |
| 236.4 (FeO gone) | 1.79 | \(1.05\times10^{-8}\) | ~0.18× |
| 350 | 2.62 | \(4.03\times10^{-8}\) | ~0.39× |

Activity rises ~6 orders from undosed metaluminous to deep peralkaline. Steepest \(\mathrm{d}\log_{10}a/\mathrm{d}\xi\) is early (filling Al sites), then softens through the nominal (Na+K)/Al = 1 boundary — **ideal mixing among associates softens the step**; missing ferric associates further distort Fe³⁺-region curvature. Flag both. The step is still the physical reason \(\Delta G\to 0\) arrives early; **do not reintroduce a 10 wt% Na₂O cap**.

**Battery hook (scored prediction):** Shornikov / Stolyarova / Zaitsev KEMS \(a_{\mathrm{Na_2O}}\) / \(a_{\mathrm{K_2O}}\) vs composition series in the corpus across the peralkaline boundary — including Zaitsev Na₂O–SiO₂ (via `kems-019` / Tsaplin–Zaitsev rows in basalt-bench), Stolyarova 1992 (`kems-016`) Na₂O–B₂O₃–SiO₂ / Na₂O–K₂O–SiO₂, and Shornikov/Stolyarova CaO–Al₂O₃–SiO₂-adjacent alkali series as they land. The peralkaline step itself is a battery-scored prediction for Q-A certification.

### 2.4 Thermodynamically limited extent — bounded root, not `while ΔG<0`

For each couple in the simultaneous solve, extents are **read off** the shared root. When a single-reaction diagnostic is needed:

\[
\Delta G_\times=\Delta G_\times^\circ+RT\ln\frac{a_{\mathrm{Na_2O}}a_{\mathrm{Fe}}}{p_{\mathrm{Na}}^2 a_{\mathrm{FeO}}},
\]

recompute every term along \(\xi\), including **\(p_{\mathrm{Na}}(\xi)\)** from the executable local source (§4).

**Stop** at the first of: \(\Delta G=0\) root; inventory NOOP; typed refusal. Replace unbounded `while ΔG<0` with:

- bracketed root on \([\xi_{\min},\xi_{\max}]\) with endpoint limits;
- residual and extent tolerances (propose \(10^{-10}\) relative on \(\Delta G/RT\), \(10^{-12}\) mol on element closure);
- typed failures: `no_bracket`, `endpoint_bound`, `tolerance_exhausted`, `activity_unavailable`, `p_Na_unresolved`.

Newly forming products at zero initial activity are a limiting reaction quotient, not necessarily missing activity authority.

**Ti reaction 4a** \(2\mathrm{Na}+2\mathrm{TiO_2}\to\mathrm{Na_2O}+\mathrm{Ti_2O_3}\):

\[
\boxed{\xi_{\max}=\min(n_{\mathrm{Na}}/2,\ n_{\mathrm{TiO_2}}/2)}.
\]

(r1 omitted the TiO₂/2 divisor and could drive TiO₂ negative.)

**Physical cap:** stop when reduction \(\Delta G\to 0\). Site saturation is why that happens early. **Drop `NA2O_SOLUBILITY_WT_PCT = 10` and `TI_ACCESSIBILITY = 0.75` as stops.** Solubility may remain a freeze-gate **warning** only.

### 2.5 Ti path, JANAF intermediates, activity (Q-B)

| Order | Reaction | Notes |
|---|---|---|
| 1 | \(2\mathrm{Na}+\mathrm{Fe_2O_3}\to\mathrm{Na_2O}+2\mathrm{FeO}\) | b-645; does not mint metal |
| 2 | \(2\mathrm{Na}+\mathrm{FeO}\to\mathrm{Na_2O}+\mathrm{Fe}\) | Opens/keeps M2 metal |
| 3 | Cr path | Simultaneous with others when live |
| 4a | \(2\mathrm{Na}+2\mathrm{TiO_2}\to\mathrm{Na_2O}+\mathrm{Ti_2O_3}\) | Required intermediate |
| 4b | \(6\mathrm{Na}+\mathrm{Ti_2O_3}\to 3\mathrm{Na_2O}+2\mathrm{Ti}\) | Only if \(\Delta G<0\) and Ti₂O₃ inventory exists |

Legacy `4 Na + TiO2 → 2 Na2O + Ti` with 0.75 accessibility is **forbidden**.

**Q-B decided:** ideal \(a_{\mathrm{Ti_2O_3}}=X_{\mathrm{Ti_2O_3}}\), **FLAGGED** (`evidence_tier=ASSUMED_IDEAL_SOLUTION`). Use JANAF Ti₂O₃ / TiO / Ti₃O₅ standard Gibbs data from the corpus (`janaf-Ti` / Chase NIST-JANAF; polymorph dictionary already names Ti₃O₅ α/β). Declare reference states explicitly on every margin. A two-step ledger path does **not** prove TiO / Ti₃O₅ irrelevant — cover them in the simultaneous solve or state the prediction limit (`ti_suboxide_phases_not_in_solve`).

### 2.6 Competing Si / Mg chemistry — cover or limit

Seat at 1150 °C (correct Kelvin):

| Reaction | \(\Delta G^\circ\) | Design example (\(a_{\mathrm{Na_2O}}=10^{-8}\), \(p_{\mathrm{Na}}=0.01\), …) | Other point |
|---|---:|---:|---:|
| \(4\mathrm{Na}+\mathrm{SiO_2}\to 2\mathrm{Na_2O}+\mathrm{Si}\) | **+285.34 kJ** (per reaction as written = one O₂) | **+75.58 kJ** (unfavorable; \(a_{\mathrm{SiO_2}}=0.5\)) | At \(p_{\mathrm{Na}}=1\), \(a_{\mathrm{Na_2O}}=10^{-11}\): **−305.86 kJ** (favorable — t-1018 competitor cannot be excluded generally) |
| \(2\mathrm{Na}+\mathrm{MgO}\to\mathrm{Na_2O}+\mathrm{Mg(g)}\) | +252.77 kJ | **−101.42 kJ** at \(a_{\mathrm{MgO}}=0.1\), \(p_{\mathrm{Mg}}=10^{-10}\) bar (product removal) | — |

(Note: sol's Si \(\Delta G^\circ=+570.69\) is \(2\times\) the per-reaction Ellingham difference; qualitative conclusions unchanged. Seat uses +285.34.)

**Prediction-limit policy for first landing:** include Si and Mg couples in the simultaneous solve when their activities/partials are available; otherwise flag `si_competitor_not_in_solve` / `mg_competitor_not_in_solve` and do not claim selective Fe-only reduction. Na₂O–silica complexing belongs in the activity model only — do not double-count as an independent energetic benefit.

### 2.7 Activities required (updated)

| Activity | Engine today | Gap handling |
|---|---|---|
| \(a_{\mathrm{FeO}}\) | CALPHAD / core (r3) | Flag; no equality |
| \(a_{\mathrm{Fe}}=1\) | Pure metal when metal present | Alloy dilution out of scope |
| \(a_{\mathrm{Na_2O}}\), \(a_{\mathrm{K_2O}}\) | **openimcc associate (Q-A, FLAGGED)**; fallback constant-\(\gamma\) parent conversion; else ideal; else absent | Ladder retained under new primary |
| \(a_{\mathrm{TiO_2}}\), \(a_{\mathrm{Ti_2O_3}}\) | TiO₂ from engines where available; Ti₂O₃ = X (Q-B, FLAGGED) | Explicit reference states |
| \(a_{\mathrm{Cr_2O_3}}\) | Same map as Ti | Flag / ideal |
| \(p_{\mathrm{Na}}\), \(p_{\mathrm{K}}\) | **Lance-zone model (§4)** — executable \(p(\xi)\) | `alkali_p_local_unresolved` |

---

## 3. Process consequence — dose-specified SiO (Fe/SiO withdrawn)

### 3.1 Setup

- Undosed C3 overhead: \(P_g=1\,\mathrm{mbar}=10^{-3}\,\mathrm{bar}\) (d-052).
- Declared lance local \(p_{\mathrm{Na}}=0.01\,\mathrm{bar}\) (distinct from bulk — §4).
- Activities from §2.3 fixture / engines, not a free \(10^{-8}\) float without dose.

### 3.2 Corrected pure-M6 line (iron couple gone)

| \(T\) | \(f\mathrm{O_2}^{\mathrm{(M6)}}\) / bar at \(a=10^{-8}\), \(p=0.01\) | Formal SiO amp vs 1 mbar |
|---|---:|---:|
| 1150 °C | \(2.294925\times10^{-22}\) | \(2.087448\times10^{9}\) |
| 1500 °C | \(9.459204\times10^{-15}\) | \(3.251417\times10^{5}\) |

These are equilibrium-pressure ratios with silica activity held fixed — **not** achievable flux multipliers. Finite inventories, transport, silica depletion, and competing reactions matter.

### 3.3 Fe/SiO de-confliction — WITHDRAWN

r1 claimed preservation while M2 holds. Sol's dose counterexample and the ~7.4 dex M2-equality move refute that. **Pending:** coupled shuttle + finite-film calculation with selective-flux acceptance (chunk A8). Until then: do not claim Fe can be tapped without SiO risk from the regime label alone; optimizer still must not strip O₂ / lance so hard that SiO surges and the CAS rump freezes.

**Cool-Na temperature gate:** activity-shifted Na/Fe margin is the shuttle gate when \(a_{\mathrm{Na_2O}}\) is available; standard-state-only is flagged. Seat standard-state margin at 1150 °C is **+11.089 kJ/mol O₂** (not ~+130).

---

## 4. Owner fork remaining: lance-zone / local \(p_{\mathrm{Na}}\) (with numbers)

0.01 bar Na exceeds the 0.001 bar bulk total pressure, so the worked example **requires** a pressurized lance zone distinct from bulk headspace, plus an explicit connection to the exchange interface.

| Option | Model | Seat numbers | First implementation? |
|---|---|---|---|
| **LZ-A (proposed default)** | Local equilibrium in a lance volume \(V_L\); \(p_{\mathrm{Na}}\) from local inventory + ideal-gas / delivery; exchange with bulk melt/headspace at a stated hourly rate | At 1150 °C, \(p_{\mathrm{Na}}=0.01\) bar: \(V_L=0.1/1/10\) L holds \(n_{\mathrm{Na(g)}}=8.45\times10^{-6}/8.45\times10^{-5}/8.45\times10^{-4}\) mol. Closed-zone depletion: \(p_{\mathrm{Na}}(\xi)=p_0(n_0-2\xi)/n_0\) (e.g. \(n_0=1\) mol gas-eq → \(p\) falls 0.01→0.002 bar by \(\xi=0.4\)) | **YES — first landing** |
| **LZ-B** | Whole-melt equilibrium at a single \(p_{\mathrm{Na}}\) (bulk = lance) | Forces \(p_{\mathrm{Na}}\le P_g=0.001\) bar under the 1 mbar hold, or requires raising bulk \(P_g\). At \(p=0.001\), \(a=10^{-8}\): M6 \(f\mathrm{O_2}=(K a/p^2)^2=2.295\times10^{-18}\) bar — 4 dex higher than the 0.01 bar lance line | Flag as certification / sensitivity item |
| **LZ-C** | Continuous delivery: \(\dot{n}_{\mathrm{Na}}\) sets quasi-steady local \(p_{\mathrm{Na}}\) against local consumption + leak to bulk | Needs a delivery-rate setpoint (process knob); not fixed by reagent kilograms alone | Later |

**Executable source for \(p_{\mathrm{Na}}(\xi)\):** under LZ-A, `local_p_Na = f(n_Na_zone, V_L, T, delivery_rate, leak_to_bulk)` updated each root evaluation. Reagent kilograms alone cannot supply \(p_{\mathrm{Na}}\). Flag `alkali_p_local_unresolved` if only bulk headspace Na is known.

**This fork still needs owner choice** if LZ-A default is rejected; otherwise proceed with LZ-A flagged and LZ-B as certification sensitivity.

---

## 5. Decided owner rulings (not open)

| ID | Ruling |
|---|---|
| **Q-A** | openimcc associate = working \(a_{\mathrm{Na_2O}}/a_{\mathrm{K_2O}}\) source, **FLAGGED**; certify via battery (Shornikov/Stolyarova/Zaitsev KEMS, incl. peralkaline boundary); certified table later replaces the flag |
| **Q-B** | ideal \(a_{\mathrm{Ti_2O_3}}=X_{\mathrm{Ti_2O_3}}\), **FLAGGED**, with JANAF Ti₂O₃/TiO/Ti₃O₅ + explicit reference states |
| **Q-C** | **JOINT** Na/K solve before any shared melt equality; **no min(fO2)** |
| **Q-D** | Shuttle chemistry first; passive alkali film later; zero commit publishes gas; **r1 Fe/SiO assurance WITHDRAWN** pending coupled shuttle/film + selective-flux acceptance |

---

## 6. Invariants (extensions to r3 §2 / corrections to r1)

- **M1 is not M6.** Incomplete same-species pair → absence, not equality.
- **M2 keeps the IW formula while metal+FeO remain; the value moves** under the simultaneous solve.
- **No TiO₂→Ti hop.** Ledger must show Ti₂O₃ (or refuse).
- **\(\Delta G\to 0\) stops the shuttle**, not 10 wt% / 0.75. Site saturation explains early stop.
- **Activity honesty.** openimcc FLAGGED ≠ certified; ideal / absent distinct.
- **Vapour still reads `interface_pO2_bar`.** Never substitute alkali equality into SiO.
- **Ledger closure.** Atom-balanced shuttle; element closure \(10^{-12}\) mol on titration points.
- **No Fe/SiO de-confliction claim** from M2 label alone (withdrawn).
- **Kelvin into Ellingham.** Production path must pass temperature_K.

---

## 7. Consumer contract deltas

| Consumer | Change vs r1 |
|---|---|
| SiO / surface release | Unchanged field `interface_pO2_bar`. Risk remains if a future alkali film commits very low interface — still the committed field |
| Liquidus / PT-0 / MRE / optimizer | May read M6 equality when present; absence includes incomplete couple |
| Native-Fe split | Unchanged on M2/M4/M5 |
| Metallothermic provider | Margin from \(\Delta G(T,a,p(\xi))\); refuse TiO₂→Ti; Ti \(\xi_{\max}\) with /2; drop accessibility clip and 10 wt% stop; emit activity + phase-reference audit |
| Reports | Render M6 with authority/tier; show bound/absence empty, never as 0; show titration plateaus when diagnostics on |

---

## 8. Plan — luna-sized chunks (revised)

Default Q-D: shuttle-only first. Integrate with **r3 chunk 9** (move passive exchange after shuttle) explicitly — A-chunks must not assume exchange-before-shuttle ordering.

**A0. Thermodynamic helpers.** Kelvin Ellingham→\(K_{\mathrm{Na}}/K_{\mathrm{K}}\); parent activity conversion; corrected \(K_\times\); phase-reference checks. Acceptance: fixture reproduces seat \(K_{\mathrm{Na}}(1423.15\,\mathrm{K})=1.514901\times10^{-7}\) within 1e-8 relative; C-as-K mutation rejected. Production-path temperature / activity-basis / phase-reference tests required (standalone sketch must not pass with wrong \(K\)).

**A1. Regime table: M6 + same-species pair.** Acceptance: Na₂O+Na dose → M6 eligible; Na₂O+K dose only → `alkali_couple_incomplete` / M1. Mutation: unmatched OR-groups classify M6.

**A2. Equality publisher + joint Na/K.** Implement §1.2 + §1.5 joint solve; no min(fO2) publish path. Acceptance: fixed \(K,a,p\) reproduces boxed \(f\mathrm{O_2}\); both alkalis live → common equality or typed absence until solve converges.

**A3. M2 coexistence + depletion.** With metal+FeO+Na dose: regime M2; equality is IW at **current** \(a_{\mathrm{FeO}}\); after dose, M2 and M6 agree at mutual equilibrium (seat counterexample tolerances). Mutation: freeze initial IW-scale or prefer alkali line whenever dose>0 — both fail.

**A5 before A4 Ti acceptance.** Replace `4 Na + TiO2 → Ti` with 4a then optional 4b; assert Ti₂O₃ intermediate explicitly (atom-balance alone will not reject the old hop). \(\xi_{\max}=\min(n_{\mathrm{Na}}/2,n_{\mathrm{TiO_2}}/2)\).

**A4. b-646 bounded root + retire 10 wt% / 0.75.** Interior \(\Delta G=0\) root fixture; pressure-depletion fixture; solubility-cap discrimination (warning OK, stop not OK). Typed failure modes.

**A6. Consumer absence widen** for M6 activity/\(p\) unavailable (not only incomplete-couple→M1). Align with r3 chunk 5 absence-guard cleanup.

**A7. Titration-curve acceptance (AMEND).** On declared mare fixture: \(\log f\mathrm{O_2}\) vs added Na showing Fe³⁺ plateau, Fe²⁺/Fe⁰ plateau, Ti/Cr/alkali region; element closure \(10^{-12}\) mol at every point; waterfall agreement only where couples are well separated (≥N dex — propose 3 dex default).

**A8. Coupled shuttle/film + selective-flux (Q-D).** Until green: no Fe/SiO assurance in reports. Acceptance: selective Fe reduction flux vs SiO release under LZ-A + finite M2 film within declared bounds.

**A9. (Later)** Finite alkali film for passive exchange — M2-shaped. Not first landing.

**A10. openimcc certification path.** Battery score peralkaline step; when pass criteria met, lift Q-A flag to certified table.

Studio SiO CPU remains out of VPS scope.

### Runnable mutation sketch (A0 / corrected A2)

```python
import math
from simulator.chemistry.ellingham_thermo import ellingham_delta_g_kj_per_mol_o2

R = 8.314462618

def K_Na(T_K: float) -> float:
    dG_ox = ellingham_delta_g_kj_per_mol_o2("Na", T_K)  # Kelvin
    dG_dissoc = -0.5 * dG_ox * 1000.0
    return math.exp(-dG_dissoc / (R * T_K))

def fO2_alkali(K, a, p_Na):
    return (K * a / (p_Na ** 2)) ** 2

def acceptance():
    T_K = 1423.15
    K = K_Na(T_K)
    assert abs(K / 1.514901e-7 - 1.0) < 1e-6
    assert abs(fO2_alkali(K, 1e-8, 0.01) / 2.294925e-22 - 1.0) < 1e-3

acceptance()

def mutation_celsius_as_kelvin():
    # r1 bug: pass 1150 into Ellingham, then exp at 1423.15
    dG_ox = ellingham_delta_g_kj_per_mol_o2("Na", 1150.0)
    K = math.exp(-(-0.5 * dG_ox * 1000.0) / (R * 1423.15))
    return K

try:
    assert abs(mutation_celsius_as_kelvin() / 1.514901e-7 - 1.0) < 1e-3
    raise AssertionError("C-as-K mutation survived")
except AssertionError as e:
    if "survived" in str(e):
        raise
print("C-as-K mutation rejected; restored acceptance OK")
acceptance()
```

---

## 9. Mapping to tickets

| Ticket / decision | This design |
|---|---|
| d-056 | M6 alkali couple after iron / whenever dose+oxide live |
| d-057 / Q-A..Q-D | Decided rulings folded (§5) |
| b-646 | ΔG-limited shuttle; no 10 wt% / 0.75; no Ti skip; bounded root |
| b-645 | Ferric-first; competes with Fe³⁺ site stabilization (flagged) |
| b-618 / r3 Q2 | Superseded for alkali |
| t-999 | openimcc working source FLAGGED; battery certification path |
| d-052 | 1 mbar hold remains optimizer input |
| r3 chunk 9 | Exchange after shuttle — hard dependency for A-chunks |

---

## 10. Dropped or changed from r1

| r1 item | r2 disposition |
|---|---|
| Celsius passed as Kelvin into Ellingham; \(K_{\mathrm{Na}}=2.27\times10^{-10}\); dissoc +262.7; M6 \(f\mathrm{O_2}\sim5\times10^{-28}\); SiO amp \(\sim1.4\times10^{12}\); Na/Fe margin ~+130 | **Corrected** to seat Kelvin values (§1.2) |
| \(K_\times=K_{\mathrm{Na}}/K_{\mathrm{FeO}}\) | **Inverted →** \(K_\times=K_{\mathrm{FeO}}/K_{\mathrm{Na}}\) |
| "M2 holds ⇒ Fe/SiO de-confliction preserved" | **WITHDRAWN**; replace with coupled shuttle/film + selective-flux (A8) |
| Ti 4a \(\xi_{\max}=\min(n_{\mathrm{Na}}/2,n_{\mathrm{TiO_2}},\ldots)\) | **Corrected** \(\min(n_{\mathrm{Na}}/2,n_{\mathrm{TiO_2}}/2)\) |
| `while ΔG<0` stop | **Replaced** by bracketed root + tolerances + typed failure |
| \(p_{\mathrm{Na}}\) from reagent kg / bulk headspace | **Replaced** by lance-zone executable \(p_{\mathrm{Na}}(\xi)\) (§4); owner fork LZ-A default |
| Free \(a_{\mathrm{Na_2O}}=10^{-8}\) SiO example | **Replaced** by dose-specified 100 kg mare table (§2.3) |
| Activity interface declares basis only | **Requires** parent conversion \(a_{\mathrm{Na_2O}}=a_{\mathrm{NaO_{0.5}}}^2\) |
| Ordered waterfall target list as thermodynamics | **Replaced** by simultaneous shared-\(\mu_{\mathrm{O_2}}\) titration; list is operator / emergent plateaus |
| 10 wt% Na₂O as process stop (even as "retired stop" still named as cap) | **Dropped**; physical cap is \(\Delta G\to 0\); site saturation explains early stop |
| Q-A..Q-D open | **Decided** (§5); min(fO2) provisional **dropped** |
| \(p_{\mathrm{Na}}\to 0\Rightarrow f\mathrm{O_2}\to 0\) | **Corrected** \(\to\infty\) at fixed \(Ka\); absence → no equality |
| A4 before A5; A1 unmatched OR; A3 "M2 wins" only; A2 sketch with wrong \(K\) | **Chunk plan revised** (§8); A5 before A4 Ti; integrate r3 chunk 9 |
| Si/Mg competitors omitted | **Covered** with seat ΔG + prediction-limit flags (§2.6) |
| TiO / Ti₃O₅ ignored | **Named**; JANAF data + prediction limit if not in solve |
| openimcc "must not be asked for \(a_{\mathrm{Na_2O}}\)" | **Superseded by Q-A**: openimcc is the working FLAGGED source |
| Structural Al/Fe³⁺ sites implicit | **Explicit** peralkaline step + Fe³⁺ competition (§2.2–2.3) |
| "No other owner fork is open" | **Retracted**; lance-zone fork remains (§4) |

---

## 11. Review disposition

Author design r2 for regolith-physics. Seat numerics: Ellingham Kelvin, liquid-FeO IW \(K_\times\), dose counterexample, mare \(\gamma\)-table and openimcc \(a_{\mathrm{Na_2O}}(\mathrm{dose})\), Si/Mg ΔG, waterfall dex gap. openimcc installed on seat for associate probes only — **no product commits**. Tip `e7bd0cd52` shuttle consulted as attached source only. Sol verdict addressed point-by-point; remaining owner decision is lance-zone LZ-A vs LZ-B/C.

— regolith-empirical, 2026-10-01 ~20:08 ET
