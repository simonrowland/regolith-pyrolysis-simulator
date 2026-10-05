# BLIND ARCHITECTURE REVIEW: openimcc closed-mode redox plan

- Reviewer: regolith-empirical (seated reviewer), **reviewer 1 of 3, blind**. I did not open any other reviewer's work, the AMEND10 review or rulings files, or any other REVIEW-redox-plan file.
- Date: 2026-10-05, about 00:40 ET (04:40Z).
- Requested by: regolith-physics (`REQ-redox-plan-review-from-regolith-physics-2026-10-05.md`, 594 B).
- Inputs read, all from `ferry-inbox/regolith-physics-redox-plan-review-2026-10-05/`:
  - `review-brief.md` (3,103 B; it fixes the answer order)
  - `openimcc-redox-plan-FINAL-2026-10-05.md` (8,252 B; the plan, cited below as "plan L<n>")
  - `design-r13-amend10-joint-redox-root.md` (67,323 B; AMEND10 r3, read in full, cited as "A10 §n")
  - `MANDATE-CLAUDE.md` (27,970 B)
- Code read, read-only:
  - regolith-pyrolysis-simulator at green **`7027319c1`**, using `git show` and `git grep` only.
  - openimcc at the commit pinned by green: `pyproject.toml:49` at 7027319c1 pins `openimcc @ …@bc3ac65e4c1d…`. I read it from the local uv git checkout `~/.cache/uv/git-v1/checkouts/44104fda93070289/bc3ac65` (HEAD `bc3ac65e4c1da6949b9bece20f36668bd2f68fc2`, 2026-10-03). Its paths are cited as `openimcc:<file>:<line>`.
- Numerics run on the box:
  - Two tiny timing and monotonicity snippets against the pinned openimcc kernel. No pytest. Nothing was written to any repo or worktree.
  - Every constant in the toy is labelled ILLUSTRATIVE (not data).
- Labels:
  - **VERIFIED**: shown by a derivation here, or read in code at the cited line.
  - **INFERRED**: my judgement.
  - **FROM MEMORY**: a literature value I did not check against a held source.

## Bottom line (for the owner's question: is it over-specified?)

The core is right, and it is not over-specified:
- one λ = ln fO₂ root over one Gibbs model,
- with a FeO/FeO₁.₅ valence pair,
- a metal complementarity,
- uniqueness from convexity,
- a versioned pack that leaves v1.0.2 immutable,
- and an opt-in API that stays separate from the Knudsen gas root.

Each of those prevents a named failure (table in §1).

The over-specification is in **scope and sequence**, not in mechanism:
- a five-solute alloy (Si, Cr, Ni, Co, P) in the first alloy chunk;
- all feedstock traces carried in the redox registry;
- a non-convex phase-split "response" with no W rows yet admitted;
- the d-075 five-term G(T) migration of complex rows on this plan's critical path.

The plan has one real self-contradiction:
- L13 says "Do NOT start ideal".
- Chunk 3 (L32) says "add the ideal alloy phase".

It also misses three things AMEND10 requires from a binding:
- an imposed-fO₂ mode;
- the complete-response capacity C;
- an element-inventory input that is never a folded FeOt.

**Verdict: ADOPT-WITH-EDITS** (1 P0, 8 P1, 9 P2, 4 P3; §7).

---

## 1. Justification of each element (the failure it prevents)

| # | Element (plan line) | Concrete failure it prevents | Status |
|---|---|---|---|
| E1 | **Outer 1-D root in λ** (L5–L7) | Without it, either a ferric ratio is imposed from another model (A10 §1 coherence rule; b-724's −27.9 vs −11.77 class), or a full multiphase Gibbs minimiser has to be built. The 1-D root reuses the existing fixed-parent kernel and serves both modes. | **Justified.** Cost on the metal branch needs an edit (§4, P1-6). |
| E2 | **Inner constrained IMCC equilibrium with FeO/FeO₁.₅ valence parents** (L5) | Prevents mixing activities from one model with fO₂ from another for the same melt state (A10 §1). VERIFIED blocker today: `solve_imcc_sf04` refuses positive Fe₂O₃ (`openimcc:kernel.py:1081–1085`) and holds every parent fixed (`kernel.py:1112`, `x = parent_mol / basis`). | **Justified.** Simplest implementation is in §6 and needs no solver change. |
| E3 | **Versioned redox pack; v1.0.2 immutable** (L11) | Prevents silent identity and golden drift for existing callers. The kernel binds pack identity into a digest (`openimcc:kernel.py:352` `_kernel_datapack_binding_payload`), and the simulator bridge digests the pack it loads (`simulator/melt_backend/openimcc_bridge.py:595` `_pack_digest`). VERIFIED. | **Justified.** |
| E4 | **Generalised parent/species registry covering every feedstock element** (L11, L19) | Prevents ad-hoc per-element code paths later. Chunk 1 needs only Fe. | **Over-specified for chunk 1.** Design the schema fields generally, populate Fe only (P2-12). |
| E5 | **Full G(T) = a+bT+cT lnT+dT²+e/T for complex rows (d-075)** (L11) | Prevents two G evaluators and ΔCp = 0 truncation of complexes. The redox solve does not need it: redox K comes from species G functions already in the condensate tables. VERIFIED: all 38 published rows are `log10 K = A + B/T` (`openimcc:kernel.py:1159`; pack rows read). | **Off the critical path.** Its own chunk (P2-12). |
| E6 | **Redox K from species G; Fe₂O₃(l) by R-fus estimate** (L11) | Prevents untraceable redox constants. Precedent exists: gas.py already carries "labelled constant-Cp supercooled-liquid" continuations, e.g. Cr₂O₃(l) and V₂O₃(l) (`openimcc:gas.py:2085`, statuses at 1067 and 1110). | **Justified.** Reuse that one mechanism. |
| E7 | **Fe-rich alloy phase with Fe, Si, Cr, Ni, Co, P** (L13) | The Fe part prevents the b-724 / −72 dex class (no metal ⇒ unbounded reduction). Si prevents under-predicting Si reduction under deep reduction. Ni and Co keep siderophile metals in the metal ledger (mandate §2 ingot purity). P covers P-in-metal. | **Fe: justified, essential.** Si, Cr, Ni, Co, P all at once: **over-specified** for the first alloy chunk (§3, P1-9). P has the weakest case: Stage 0 removes P compounds (mandate §3). |
| E8 | **Henrian γ° + first-order interaction parameters, ideal only as a red fallback** (L13) | Prevents an ideal-mixing under-prediction of Si and P uptake (γ°_Si ≪ 1). | **Physics right in intent.** The formalism needs to come from one G^ex (P1-4) and match the alloy phase state (P1-5). It contradicts chunk 3's "ideal alloy phase" (P0-1). |
| E9 | **Trace elements in the registry, rail order, never omission** (L17–L24) | Prevents a false claim that an omitted trace stays in the melt; that matters on the vapour rail (mandate predict-and-flag). | **Justified as an activity-model goal.** Non-multivalent traces do not belong in the redox loop at all. Multivalent traces can enter R(λ) at ~zero cost (§3, P2-10). |
| E10 | **Monotonicity by convexity** (L7) | Prevents multiple-root ambiguity and any need for an all-roots search (A10 §2: "a common-Gibbs binding gets uniqueness from convexity"). | **Justified and correct, given premises the plan must state** (§2, P1-7). |
| E11 | **Non-convex phase-split gate** (L7) | Prevents returning an unstable single-liquid state once W terms make G non-convex. | **Premature.** The published pack is ideal-associated and therefore convex by construction (VERIFIED §2). A split is also a new solver, which contradicts L13's "not new solvers". Replace it with a stability test plus flag now (P1-8). |
| E12 | **Range rules: band intersection, extrapolate-and-flag, refuse only missing/invalid input** (L28) | Prevents both a false refusal and an unflagged extrapolation. Matches mandate §4 "Prediction posture". | **Justified.** Two clarifications (P2-16): every published complex row is [1700, 3000] K (VERIFIED), and the kernel's default refuses out of band (`kernel.py:1136`). |
| E13 | **Chunks 1–4** (L30–L33) | Chunk 4 serialisation prevents co-edit conflicts with the engine session. Chunk 2's closed≡fixed match is a self-consistency test. Held-out Kress scoring prevents fitting to validation data (A10 §8, d-073). | **Justified,** apart from the chunk-1 breadth and the chunk-3 contradiction. |
| E14 | **API: opt-in `evaluate_closed_redox`, `evaluate()` and `evaluate_gas(fO2=…)` intact; gas root not repurposed** (L15) | Prevents breaking existing callers. Prevents conflating two different equations: inventory O balance vs effusion flux balance (VERIFIED §5). | **Justified but incomplete.** It lacks the imposed mode and C (P1-2) and an element-inventory input (P1-3). |

Elements with no stated failure-they-prevent, or that look added for reasons other than physics or data (INFERRED):
- **E5** (a schema-consistency programme, d-075).
- The breadth of **E4 and E9** (completeness goals from d-076 that belong to the activity model, not the redox root).

Neither is wrong. Neither belongs on this plan's critical path.

---

## 2. Physics correctness

### 2a. Convexity: ∂n_O/∂μ_O ≥ 0 at fixed non-oxygen inventory, including metal appearance

**Derivation (VERIFIED).**

Setup:
- Species amounts are n ≥ 0 over all phases: melt species and alloy species.
- A is the element×species matrix.
- b = A n is the element inventory.
- G(n) = Σ_s n_s μ°_s + Σ_phases G_mix,p(n_p).

The ideal (associated) mixing term RT Σ_{s∈p} n_s ln(n_s/N_p) is convex:
- It is the perspective of a convex function.
- Its Hessian is RT(diag(1/n_s) − 𝟙𝟙ᵀ/N_p), which is positive semidefinite with null vector n_p.
- So G is convex on n ≥ 0 if every phase's G^ex is convex.

Define the equilibrium potential Ĝ(b) = min_n {G(n) : A n = b, n ≥ 0}.

Step 1: Ĝ is convex in b.
- Partial minimisation of a jointly convex function over an affinely parametrised feasible set gives a convex function of the parameter.
- Proof: take optimal n₁ and n₂ for b₁ and b₂. Then θn₁ + (1−θ)n₂ is feasible for θb₁ + (1−θ)b₂.
- Therefore Ĝ(θb₁ + (1−θ)b₂) ≤ θĜ(b₁) + (1−θ)Ĝ(b₂).

Step 2: phase appearance and disappearance are covered.
- They are only changes in which bounds n_alloy ≥ 0 are active inside that same minimisation.
- So Ĝ stays convex across them. It is piecewise smooth, with kinks where a phase enters.

Step 3: the element potentials are the multipliers.
- μ_e ∈ ∂Ĝ/∂b_e.
- Fix all non-O element amounts. Then Ĝ(n_O) is convex in n_O, and μ_O(n_O) is a monotone non-decreasing (maximal monotone) graph.
- Its inverse n_O(μ_O) = ∂Ĝ*(μ_O) is monotone non-decreasing too.
- Since μ_O = ½(μ°_O₂ + RT λ), we get dn_O/dλ = (RT/2) dn_O/dμ_O ≥ 0.

Smooth interior point, second-order form:
- KKT gives ∇G = Aᵀμ and A n = b.
- Differentiating: H dn = Aᵀ dμ and A dn = db, so ∂μ/∂b = (A H⁻¹ Aᵀ)⁻¹ ≡ M, which is SPD.
- At fixed cations, dμ_O = M_OO dn_O, so **dn_O/dμ_O = 1/M_OO > 0**.
- AMEND10's Σ ν n f(1−f) s is this quantity for independent ideal couples, so the plan's L7 claim that it is "the ideal special case" is correct.

Numeric illustration (VERIFIED by running; constants ILLUSTRATIVE, not data):
- Real SF04 pack rows at 1800 K on a mare-like composition.
- One pseudo-complex FeO₁.₅ = FeO·K_r·fO₂^¼.
- A pure-Fe metal saturation a_Fe = a_FeO/(K·fO₂^½).
- O_eq(λ) was strictly increasing on a 46-point grid from log fO₂ = −13 to −4. Min step +1.0e-4 and max +5.9e-2 mol O per 100 g basis.
- Metal appears at log fO₂ ≈ −10.40 with a kink and no jump.
- Brent found a unique root on both branches.

What the argument needs, which the plan should state (P1-7):
1. **One G.** Every row in the inner solve, alloy activities included, must derive from a single Gibbs function. A truncated Wagner first-order series is not integrable to a consistent G at finite concentration, so the mass-action system it defines is not a Gibbs minimisation (P1-4).
2. **Global minimum.** Each phase's G is convex, so the inner solver's local stationary point is the global minimum. This holds for the ideal-associated pack: the kernel is exactly ideal-associated mass action (`openimcc:kernel.py:630–664`).
3. **"Fixed metal inventory" (L7) means fixed non-oxygen element amounts.** It does not mean fixed metal-phase amount, which is part of the response (P3-19).

What breaks it, or makes it set-valued:
- **Non-convex G^ex** (a miscibility gap). The true equilibrium Ĝ, the convex hull, is still convex. A single-liquid local solution can show M_OO < 0, giving an S-shaped R(λ) and multiple roots, and Brent would silently pick one. That is the case the phase-split gate is for. The response is correct physics, but it needs a detection criterion, and it is premature now (P1-8).
- **Affine segments of Ĝ.** When the phase assemblage fixes μ_O (Gibbs variance zero in μ_O at fixed T, P and cations), n_O(λ) has a **vertical segment**: one λ, a range of n_O. Example: the Fe–O binary limit with Fe metal plus an FeO-rich melt, or a melt saturated in a pure oxide.
  - Brent still converges to the right λ.
  - But λ no longer determines the partition (metal amount m).
  - The metal branch must then be solved in the extent or n_O variable. This is A10 §5.1's plateau segment.
  - In openimcc the liquid gives a_FeO = x*_FeO < 1 whenever other oxides are present, so this arises only near the Fe–O binary limit, for example after deep evaporative enrichment (INFERRED). It is still a branch the plan must name.
- **Kinks of Ĝ** (zero-capacity flats in n_O(λ)), on the oxidised side as f→1 and at vanishing dissolved Fe. λ is then poorly determined, which is A10's one-sided endpoint and `redox_poorly_buffered`.

### 2b. Henrian dilute alloy coupled to an associated-solution melt

**Consistent in principle (VERIFIED by derivation).** Chemical-potential equality does not depend on reference states, provided each side's activity is referenced to the same standard state that enters ΔG°. Example for Si:

  SiO₂(melt) = Si(alloy) + O₂:  RT ln a_Si = RT ln a_SiO₂ − ΔG°_r − RT λ,  ΔG°_r = μ°_Si(l) + μ°_O₂ − μ°_SiO₂(l).

Melt side:
- IMCC activity is the unbound-parent fraction, a_SiO₂ = x*_SiO₂, referenced to pure liquid oxide. VERIFIED: γ → 1 as K → 0 (`openimcc:kernel.py:663–664`), and "Condensed parents use their pure-liquid standard state" (`openimcc:gas.py:1812–1813`).

Alloy side:
- a_Si = γ_Si x_Si, Raoultian to pure Si(l), with γ_Si = γ°_Si · exp(interaction terms).
- Equivalently, a 1 wt% Henrian standard: μ°(1 wt%) = μ°(l) + RT ln(γ° M_Fe / (100 M_Si)) (standard conversion, FROM MEMORY).

Four ways it goes wrong, each a required row field or rule:
1. **The reference phase of γ° must match ΔG°.** Several sourcebook γ° are tabulated against the pure solid solute, e.g. Cr(s) at 1873 K (FROM MEMORY, check per row). Conversion is by ΔG_fus of the solute, by the same supercooled-liquid mechanism as E6. Si(l) is metastable below about 1687 K and Cr(l) below about 2130–2180 K (FROM MEMORY).
2. **wt%-basis e_i^j must be converted** to mole-fraction ε_i^j by the Lupis relation (FROM MEMORY). They must not be mixed.
3. **The solvent Fe activity must come from the same G^ex** (Gibbs–Duhem). First-order Wagner is only consistent at infinite dilution. Use the Unified Interaction Parameter formalism (Pelton & Bale 1986, FROM MEMORY) or an equivalent single G^ex (P1-4).
4. **Alloy phase state.** Pure Fe melts at about 1811 K (FROM MEMORY). Below the alloy liquidus the metal is fcc or bcc solid, and liquid-Fe Henrian data do not apply to a solid solvent. The plan's temperature range starts at about 1300 K, and A10's hero window (1030–1450 K) and the Na/Fe crossover (about 1454 K, mandate §4) are below Fe melting. The pure-Fe chunk must use the stable Fe(cr/l) G(T). Minor-solute partition below the alloy liquidus has to be flagged extrapolation or deferred (P1-5).

The melt formula unit must also match ΔG°'s unit per parent (FeO₁.₅ vs Fe₂O₃; PO₂.₅ vs P₂O₅). The choice changes the ideal mixing term, not μ° per unit (see 2c).

Henrian magnitudes the plan relies on (FROM MEMORY; the sourcebook rows must confirm):
- γ°_Si in Fe(l) at 1873 K is about 1e-3.
- γ°_Cr is about 1; γ°_Ni about 0.6–0.7; γ°_Co about 0.5–0.6.
- γ°_P is strongly negative-deviating (order 1e-3 to 1e-2; uncertain).

So "orders of magnitude below 1" holds for Si and probably P. Ni and Co are near-ideal, and Cr is about ideal.

### 2c. Fe₂O₃(l) via crystal plus metastable fusion, and the FeO/FeO₁.₅ parent choice

**Parent choice: correct, and it should be stated as the reason (VERIFIED by derivation).**

On the FeO₁.₅ monomer basis, ideal mass action gives:
- x_FeO₁.₅ / x*_FeO = K_r fO₂^¼.
- So ln(Fe³⁺/Fe²⁺) has slope ¼ in λ and does not depend on total Fe. That matches observations to first order: Kress's 0.196 sits below ¼ (A10 §2).

On a dimer Fe₂O₃ basis:
- x_Fe₂O₃ = K x*_FeO² fO₂^½.
- The cation ratio 2x_Fe₂O₃/x*_FeO ∝ x*_FeO · fO₂^½: slope ½, and proportional to Fe content. That is badly wrong.

Standard state:
- The pure liquid at composition FeO₁.₅ is the same substance as Fe₂O₃(l), so **μ°_FeO₁.₅ ≡ ½ μ°_Fe₂O₃(l)** exactly.
- Using Fe₂O₃(l) as the data source is consistent with FeO₁.₅ as the species.
- Any ferrite complex row written on an Fe₂O₃ basis must be re-based, with ν_FeO₁.₅ = 2·ν_Fe₂O₃ (P3-20).

**Fusion estimate: sound as a flagged estimate. Its sensitivity should be published.**
- Hematite does not melt congruently at low pO₂. It decomposes to magnetite + O₂ around 1660 K in air, and melts near about 1840 K only under elevated pO₂ (FROM MEMORY). So Fe₂O₃(l) is metastable or hypothetical at every operating condition.
- Crystal + ΔH_fus/T_m estimate + constant-Cp liquid continuation is standard practice for liquid oxide endmembers. It is the same mechanism openimcc already labels for Cr₂O₃(l) and V₂O₃(l).

Sensitivity (VERIFIED arithmetic): on the metal-free branch at fixed Fe³⁺/Fe²⁺, δλ = 4 δG°(FeO₁.₅)/(RT). At 1673 K, a 5 kJ/mol FeO₁.₅ error moves λ by 1.44 ln = **0.62 dex**.

On the metal-saturated branch, λ ≈ λ_sat is set by Fe(cr/l), FeO(l) and a_FeO. It feels G(FeO₁.₅) only through the ferric share of Fe, about 2% near saturation (A10 §3). INFERRED consequences:
- Fe₂O₃(l) quality governs oxidised feedstocks: Mars Fe³⁺, ferric simulants, E33-type ferric validation.
- FeO(l) and a_FeO govern the vacuum, metal-saturated regime that the mandate mostly runs in.

Validation expectation to pre-register (P2-18):
- With ferrous associates only (Fe₂SiO₄ with ν_FeO = 2, FeTiO₃, FeAl₂O₄; VERIFIED in pack rows 19–21) and no ferric associates, d ln(Fe³⁺/Fe²⁺)/dλ ≈ ¼, and is raised slightly above ¼ by the ν = 2 associate (derivation sketch: r = K_r fO₂^¼ / (1 + 2K x*_FeO x*_SiO₂ + …)).
- Kress's 0.196 therefore cannot be reached until ferric associates that consume a limited partner exist (e.g. NaFeO₂-type, which lowers the slope as x*_Na₂O is drawn down).
- Expect a held-out slope residual of roughly +28% (0.25 vs 0.196). Score it; do not tune.

### 2d. Trace γ by homologue

**Defensible as the flagged third rung, with three conditions** (INFERRED):
1. Transfer **complexation constants**, not γ values. γ depends on composition, and the IMCC computes γ from complexes. The plan's "Rb₂O and Cs₂O follow K₂O's complexation" is at the right level.
2. "Follow" should mean **extrapolate along the homologous series**, with uncertainty at least the difference between adjacent members. Larger alkalis complex more strongly in silicate melts, so γ_Cs₂O < γ_K₂O < γ_Na₂O (FROM MEMORY). Copying K constants to Cs biases Cs high in the vapour.
3. Chained homologues (In₂O₃ → Ga₂O₃ → Al₂O₃) need a compounded-uncertainty flag.

Published γ (Sossi et al. 2019 on moderately volatile elements; Wood & Wade 2013 from metal–silicate partitioning; both FROM MEMORY as to scope) must enter openimcc **as IMCC rows**: complex constants fitted at, and validated against, the published conditions. They must not enter as activity overrides grafted onto an IMCC melt, which would combine two activity models for one melt state (A10 §1 coherence rule; P2-11).

---

## 3. Scope and sequence

**Traces inside the λ solve or after?** Split by kind:
- **Non-multivalent traces** (Rb, Cs, Ga, Ge, In, …) have a fixed melt oxygen content. They contribute nothing λ-dependent to R(λ). Evaluate their speciation **once, after the solve**, at the converged major state. That is exact to O(x_trace) on the majors.
- **Multivalent traces** (Eu, Ce; V at tens of ppm) must stay in R(λ). That keeps A10 §4 ("every couple with an admitted law enters (1)"; "there is no separate trace tier"). It costs nothing extra if they are evaluated at **frozen major speciation**:
  - At infinite dilution every trace species is linear in the trace's unbound fraction: x_j = K_j(λ) · x*_tr · Π x*_major^ν.
  - So each trace's partition at a trial λ is closed form given the major solve: n_tr = x*_tr · N · (1 + Σ_j K_j(λ) Π …).
  - Its oxygen term adds to R(λ) with no extra Newton dimension (VERIFIED by derivation).
  - Report the a posteriori residual. Do not let a tolerance decide which chemistry exists (A10 §4's objection to r1's tier).
- Order of magnitude (INFERRED from A10 §3 numbers): Eu at about 1 ppm displaces at most ½·ν·n ≈ 1e-3 to 1e-2 mol O/t, against C ≥ 2 mol O per ln at saturation. Δλ ≲ 0.005 ln.

**Alloy start set: fewer.** Recommended order (P1-9):
1. **Fe only.** Pure metal, a_Fe = 1 recorded in the binding identity. No mixing model needed, so "ideal" is exact. Same premise as internal-analytical (A10 §2).
2. **Ni and Co.** Near-ideal in Fe, strongly siderophile, and they matter for metal product purity (mandate §2) and chondritic feedstocks. **Data-gated**: openimcc's public data lacks NiO and CoO liquid parents. VERIFIED: "Only Ni(g) has a public row; the NiO gas and NiO liquid parents are absent" (`openimcc:gas.py:1278`), and the same for Co (`gas.py:1295`) and Mn (`gas.py:1261`).
3. **Cr.** First as the melt Cr²⁺/Cr³⁺ couple, because displacement is A10 §3's real reason for Cr: about 1 dex metal-free. Then Cr in alloy. Needs CrO(l) and CrO₁.₅(l); CrO has no stable crystal (FROM MEMORY), so it is an estimate. Only a Cr₂O₃(l) continuation exists today (`gas.py:1067` status).
4. **Si.** Matters only under deep reduction: Na-lance zone, C6 Mg thermite, λ well below IW. Rough scale, FROM MEMORY thermochemistry: x_Si in metal is about 2e-3 at IW−2 and about 0.2 at IW−4 (1873 K, a_SiO₂ about 0.5, γ°_Si about 1.3e-3). Needs a UIP-consistent γ.
5. **P last.** Stage 0 removes P compounds (mandate §3), and the ext pack's P₂O₅ is "crystal-reference screening proxy; no P₂O₅(l) claim" (`openimcc:data/packs/imcc-sf04-ext-v4.json:1241`). Both melt and alloy sides are data-gated.

**Premature in chunk 1/2:**
- The generalised all-element registry population.
- The d-075 five-term complex G(T) migration.
- The trace rows.
- The phase-split gate.
- S as a "row". Sulfide substitutes for oxide, so S needs its own element balance (an fS₂ potential, or closed S inside the inner solve), not a cation-oxide row. Defer it, consistent with A10 §3 and Stage 0.
- V is a positive: in a species-based Gibbs model each valence (VO, VO₁.₅, VO₂, VO₂.₅) is a species, so A10 §3's "sequential-couple generalisation" is automatic. Note it as a strength.

---

## 4. Numerics and cost

**Measured cost (VERIFIED on this VPS, pinned openimcc, 8 parents, ideal pack):**
- One `solve_imcc_sf04` takes **6.3–40 ms**: 1300 K, 9.4 ms (51 nfev, direct); 1700 K, **40 ms (230 nfev, continuation path)**; 2200 K, 10.6 ms; 3000 K, 6.3 ms.
- Nested toy at 1800 K, with the pseudo-complex FeO₁.₅ and pure-Fe complementarity solved by an inner Brent in m, about 20 ms per inner solve:
  - closed root, metal-free branch: 10 outer iterations, **22 inner solves (≈0.45 s)**;
  - closed root, metal branch: 16 outer iterations, **121 inner solves (≈2.4 s)**.
- A time-stepping simulator makes at least one bulk call per tick plus surface calls per S3 trial (A10 §5.1). Naive nesting is therefore seconds per tick on the metal branch, which is the operating regime under vacuum. INFERRED: likely to break r13's 1.3× budget (A10 §6 cost line).

**Fixes, in order** (P1-6, P2):
1. **Do not nest three levels** (λ → m → IMCC). Use A10 §2's decision rule:
   - Evaluate the metal-free form at λ_sat(0).
   - If the root is above it, run a 1-D root in λ.
   - Otherwise run a 1-D root in m (A10 eq. 4 analogue), with λ = λ_sat(m) read from the IMCC a_FeO at that m. That is a short fixed point, because the ferric share is about 1–2%.
   - This also gives the plateau and vertical-segment case its natural variable (§2a).
2. **Warm start.** The kernel always starts from y₀ = ln x (`openimcc:kernel.py:758`). Successive Brent trials differ by small Δλ, so an optional `y_init` (absent ⇒ bit-identical) should cut nfev severalfold (INFERRED).
3. **Use the analytic capacity.** The converged inner Jacobian gives dO/dλ by the implicit-function theorem. It is needed anyway as the published C (P1-2). With a bracket it allows a safeguarded Newton (rtsafe-type), typically 4–6 evaluations instead of 10–16 (INFERRED).
4. Later, optionally: one augmented Newton system (y, λ, ln m) with the O balance and a_Fe = 1 appended, with Brent as fallback.

**Bracket and endpoint classification:**
- Use **thermodynamic** brackets, not a fixed window:
  - lower: the fully reduced admissible state (all reducible Fe in metal; n_O = n_O,fix);
  - upper: f → 1 or fO₂ = P_total (A10 change 19).
- Do **not** reuse the gas root's (−30, 0) log10 window. That window is a Knudsen molecular-flow validity limit (`openimcc:gas.py:2200–2208`), not a melt limit. Closed melts at 1000–1300 K under reduction can sit below 1e-30 bar (INFERRED from IW at those T, FROM MEMORY).
- Classify with A10 §2's endpoint table and five-case taxonomy (P2-14).

**Convergence risks (VERIFIED in the toy):**
- With dissolved FeO driven to about 1e-9 of its total (near Fe exhaustion), the unchanged kernel raised `ImccNonconvergenceError` at tol = 1e-12.
- Dissolved-Fe exhaustion therefore needs an **endpoint classification** (A10: `iron_exhausted_fO2_underdetermined`), not an inner solve.
- An inner refusal during the outer root must surface as solver status, never as chemistry.

**Noise floor:**
- Inner tol 1e-12 on mole fractions times N gives R noise of about 1e-12·N mol O. λ noise is about that divided by C. That is negligible except as C → 0 on the oxidised side, where A10's `redox_poorly_buffered` applies.

---

## 5. Coherence (duplication, contradictions)

**No duplication of the gas root (VERIFIED).**
- `oxygen_balance_from_pressure_model` (`openimcc:gas.py:2288`) solves an effusion-flux balance F(log10 pO₂) with `brentq` (2373). It requires each species pressure to be a **declared power law in pO₂**: it checks monotone weights at 2307–2321 and refuses if the observed exponent differs by more than 1e-6 (2396–2420).
- The melt closed mode solves an inventory O balance. These are different equations, as L15 says.

Consequence the plan should state (P2-15):
- The redox **imposed mode must not be called inside the gas root's `pressure_model`**.
- Melt re-speciation with pO₂ (ferric split, metal) makes pressures non-power-law, and the gas root would refuse at 2417.
- Coupling the interface redox to the flux balance is S3's iteration in the simulator (A10 §5.1), not an openimcc nested root.

**No duplication against internal-analytical (INFERRED, per d-072).**
- openimcc's root uses its own Gibbs law set, not the Kress or IW laws.
- A10 §8 names this exact chunk: "ferric species and closed-oxygen mode".
- The simulator has no alloy activity model to duplicate (VERIFIED by `git grep` at 7027319c1). Henrian and Wagner appear only as battery reference-state tokens and scored quantities: `simulator/battery/enums.py:209–210`, `simulator/battery/migrate.py:410`.
- Synergy: alloy γ° and ε rows can be scored on the battery's `INTERACTION_PARAMETER` quantity.

**Gaps against AMEND10 r3:**
1. **Imposed-fO₂ mode and C are missing from the API** (L15 lists only `evaluate_closed_redox`).
   - A10 §1 and §5.1 / K0 require every binding's `potentials()` to support closed **and** imposed modes. The imposed mode returns partition, uptake in mol O and its O₂ equivalent, the complete-response derivative, `metal_buffered` and endpoint status.
   - The b-655 step predicates need C (A10 §2).
   - L7's "reuse this residual for imposed-fO₂ and closed modes" says it in prose, but the API omits it. (P1-2)
2. **Inventory input.**
   - A10 §2: the closed answer depends only on element amounts and n_O. S1 carries FeO, Fe₂O₃ and Fe metal separately.
   - The simulator bridge today **folds Fe₂O₃ into 2 FeO** (`simulator/melt_backend/openimcc_bridge.py:341–344`, notice `openimcc_fe2o3_fold` at 499–507). That erases ½ O per Fe³⁺, which is exactly the quantity the root solves for, and it passes no metal.
   - If chunk 4's "inventory adapter" reuses that projection, the closed mode solves a different inventory (the b-710 class). (P1-3)
3. **The d-073 linkage is not named.** A10 §6/§8 says v1-composite carries `redox_from_other_binding` "until openimcc's ferric chunk ends it". Chunk 2/4 acceptance should say what ends it. (P2-17)
4. **Endpoint semantics.** S2 publishes binding results with one semantics: point, one-sided bound, absence, and five distinct cases. The plan's "classify endpoint states" should adopt A10 §2's table verbatim. (P2-14)
5. **Scope beyond A10 §8.** The alloy solutes and traces go beyond the chunk AMEND10 names. That is allowed per binding (d-072), but should be staged as later chunks, not folded into this one (INFERRED).

**Internal contradictions in the plan:**
- **C1 (P0):** L13 "Do NOT start ideal … ideal mixing is the last-resort fallback, flagged red" vs L32 "add the ideal alloy phase".
- **C2:** L13 "nonideal activity … data gates, not new solvers" vs L7 "the response is a phase split". A two-liquid split is a new solver.
- **C3:** L7's convexity guarantee vs L13's first-order interaction parameters, which are not a single convex G at finite x.
- **C4:** L28 "refusal is reserved for missing or invalid input" vs the kernel's out-of-band refusal by default (`kernel.py:1136`). The closed mode must call it with `allow_extrapolation=True` and carry flags.
  - Also, "below the liquidus, return a liquid-only limitation" needs a liquidus. openimcc has none (L35 risk).
  - A10 change 9 makes the simulator's `melt_regime()` the one liquidity predicate. openimcc should publish a `liquid_only_model` notice and not decide liquidity.
- **C5:** L13 "not pure-Cr/Ti saturation" mentions Ti, which is not in the alloy set.
- **C6:** L31 "must match fixed-fO₂ speciation at its solved λ". Today's fixed-fO₂ path has no ferric, so this must mean the new imposed mode.

---

## 6. The simplest correct version (minimal first deliverable)

**Cut or defer:**
- alloy solutes Si, Cr, Ni, Co and P (each a later data-gated chunk, order in §3);
- trace rows (an activity-model plan);
- the phase-split solver (until W rows exist);
- the d-075 complex G(T) migration (its own chunk);
- S;
- engine integration until the API is pinned (as L33 already says).

**Minimal first deliverable (one Fe chunk; correct, not a toy):**
1. **Data:** a redox pack v0 with one species function, FeO₁.₅(l) ≡ ½[Fe₂O₃(cr) + estimated fusion]. Build it with the existing labelled supercooled-liquid continuation, with its band, evidence class and an explicit ΔG uncertainty. Compute the reaction K from species G at evaluation, using FeO(l), O₂(g) and Fe(cr/l) from the existing tables. Store no fitted reaction coefficients that duplicate species tables.
2. **Imposed mode = the existing kernel, unchanged.** Add λ-dependent pseudo-complex rows: FeO₁.₅ as {FeO: 1} with ln K_eff = ln K_r + λ/4, and later ferrites re-based to FeO₁.₅. S_j = 1 leaves the kernel's denominator D unchanged.
   - VERIFIED working with the unmodified `_solve_active` in the toy.
   - Returns the partition, O content and dO/dλ.
3. **Closed mode:**
   - metal-free branch plus pure-Fe metal (a_Fe = 1 in the binding identity, stable Fe(cr/l));
   - A10 §2 decision rule (root in λ above λ_sat(0), root in m below it);
   - A10 endpoint table and five-case result;
   - publishes λ, partition, m, C, closure residuals, band and row flags;
   - takes an element inventory (or FeO, Fe₂O₃ and Fe metal separately), never folded FeOt.
4. **Tests:**
   - element and O closure;
   - **split invariance** (same elements and n_O with different FeO/Fe₂O₃/Fe splits give the same state);
   - closed ≡ imposed at the solved λ;
   - monotonicity grid sweep;
   - a flipped-saturation-comparison mutation;
   - the inner-nonconvergence → status path;
   - held-out ferric ratios (Kress & Carmichael 1991 and the A10 §8 independent sets), scored and never fitted, with the ≈¼ vs 0.196 slope residual pre-registered;
   - a cost profile (CPU per call at 1300/1700/2200/3000 K, metal-free and metal branches) against r13's 1.3× budget before chunk 4.

This is correct physics for every Fe-only state the simulator runs. It closes b-724 and −72-dex-class states for openimcc, and it is the base the alloy and couple chunks add rows to without a new mechanism.

---

## 7. Verdict and edits

**Verdict: ADOPT-WITH-EDITS**

### P0
1. **Resolve the alloy-ideal contradiction (L13 vs L32).**
   - Chunk 3a is pure-Fe metal: single component, a_Fe = 1, exact. No mixing model.
   - Solutes enter only with their sourced γ° rows.
   - Si and P are never ideal. If forced, they get a red flag plus a predict-and-flag note, not a silent ideal.

### P1
2. **API completeness.** Add the imposed-fO₂ mode, or a `mode` argument, returning partition, uptake (mol O and O₂), complete-response dO/dλ, `metal_buffered` and endpoint status. The closed mode must also return C (A10 §1, §2 b-655, §5.1, K0).
3. **Closed-mode input is the element inventory** (or FeO, Fe₂O₃ and Fe⁰ separately, plus alloy inventory).
   - The bridge's Fe₂O₃→2FeO fold (`openimcc_bridge.py:341–344`) must never feed the closed mode.
   - Chunk 2/4 acceptance includes a split-invariance test.
4. **Alloy activities from one G^ex** (UIP or equivalent).
   - Each row records the γ° reference phase (pure liquid vs solid solute; convert with ΔG_fus) and the e→ε conversion.
   - First-order Wagner is not admitted as the model, because otherwise the convexity proof does not hold.
5. **Alloy phase state.**
   - Below the alloy liquidus (≈1811 K for Fe, FROM MEMORY) the metal is solid. The pure-Fe chunk uses Fe(cr/l).
   - Liquid-Fe Henrian data are applied below the liquidus only as flagged extrapolation, or deferred.
6. **Metal-branch numerics and cost.** Measured: nested λ→m→IMCC used 121 inner solves (≈2.4 s) vs 22 (≈0.45 s) metal-free.
   - Adopt A10 §2's decision rule (root in m on the metal branch).
   - Add warm start (`y_init`) and the analytic dO/dλ.
   - Add a pre-integration cost gate against r13's 1.3× budget.
7. **State the monotonicity premises.**
   - one G, each phase convex, inner solve at the global minimum;
   - fixed **non-oxygen element** amounts;
   - vertical segments (invariant assemblages, the Fe–O binary limit) handled in the extent or n_O variable (A10 §5.1 segment), because λ there does not fix the partition.
8. **Replace the phase-split "response" with a gate that is buildable now.**
   - Assert that every admitted row is ideal-associated or has a convex G^ex. The published pack is convex, VERIFIED.
   - When W rows land, run a post-solve stability test (projected-Hessian minimum eigenvalue or tangent-plane distance) that flags (predict-and-flag).
   - The two-liquid split is a named later solver chunk. Fix L13's "not new solvers" wording accordingly.
9. **Alloy start set:** Fe → Ni+Co (after the NiO(l)/CoO(l) parents, absent today, `gas.py:1278/1295`) → Cr (melt couple first) → Si → P (Stage 0; P₂O₅ is a screening proxy, `ext-v4.json:1241`).

### P2
10. **Traces.** Non-multivalent traces are evaluated once at the solved state. Multivalent traces enter R(λ) at frozen major speciation (closed form, zero extra Newton dimensions), with an a posteriori residual reported. This keeps A10 §4.
11. **Trace γ data enter as IMCC rows** (complex constants checked against the published γ at their conditions), never as activity overrides. Homologues are extrapolated along the series with adjacent-member uncertainty; chained homologues get a compounded flag.
12. **Trim chunk 1** to the Fe rows and the format: the schema is general, but only Fe is populated. Move the d-075 five-term complex G(T) migration off this plan's critical path, into its own chunk.
13. **Implementation note: imposed mode = existing kernel plus λ-dependent pseudo-complex rows** (FeO₁.₅ = {FeO: 1}, ln K_eff = ln K_r + λ/4). One solver, no fork (VERIFIED in the toy).
14. **Adopt A10 §2's endpoint table and five-case result taxonomy verbatim** (bracket / conditioning / input uncertainty / unmodelled / infeasible).
    - Thermodynamic brackets, never the gas root's (−30, 0) window.
    - Dissolved-Fe exhaustion is an endpoint, not an inner solve: the kernel failed to converge there in the toy.
15. **Gas-root boundary.** State that the imposed melt mode is never called inside `oxygen_balance_from_pressure_model`'s pressure model, because of the power-law check (`gas.py:2396–2420`). Interface coupling belongs to S3.
16. **Range.** State that all published complex rows are [1700, 3000] K, so the staged and shuttle window (≈1030–1454 K) is extrapolated for every complex. The closed mode calls the kernel with `allow_extrapolation=True` and carries flags. Liquidity is the caller's `melt_regime()`; openimcc publishes `liquid_only_model`.
17. **Name the d-073 linkage.** Chunk 2/4 acceptance states when v1-composite's `redox_from_other_binding` notice ends: when the openimcc path takes ferric and metal inventory without the fold.
18. **Pre-register the Fe₂O₃(l) sensitivity and the slope expectation.**
    - δλ = 4δG°/RT (5 kJ/mol ⇒ 0.62 dex at 1673 K, metal-free).
    - Ideal FeO₁.₅ with ferrous-only associates gives a slope of about ¼ (or slightly higher) vs Kress's 0.196.
    - Score on the held-out rail; do not tune.

### P3
19. Change "at fixed metal inventory" (L7) to "at fixed non-oxygen element amounts".
20. State μ°_FeO₁.₅ ≡ ½ μ°_Fe₂O₃(l), and that ferrite rows written per Fe₂O₃ are re-based (ν doubles).
21. L13: drop Ti from "pure-Cr/Ti saturation", or add Ti to the alloy roadmap explicitly.
22. L31: "match fixed-fO₂ speciation" should read "match the new imposed-mode speciation at the solved λ". Today's fixed-fO₂ path carries no ferric.

**Counts: P0 = 1, P1 = 8, P2 = 9, P3 = 4 (22 edits).**

**FINAL VERDICT: ADOPT-WITH-EDITS**
