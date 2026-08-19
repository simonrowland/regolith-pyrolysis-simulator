# MD target list: questions molecular dynamics is well-suited to answer here

Standing target list for the Mac-Studio fleet's idle cores (owner directive 2026-08-18:
the studios will be there for months; keep this list live). Each entry states the
question, why MD's *current* certification level suffices for it, and which in-tree
consumer the answer feeds. Companion to `docs/model-limitations.md` (claim classes) and
the task store (umbrella task + per-item tasks).

**Certification state this list is calibrated to (2026-08-18):** MACE checkpoints
reproduce Na₂SiO₃ *structure* essentially perfectly (Si-O-Si 134.7° vs 133–144°
experimental, NBO/Si exactly 2.00, Q²-dominant speciation, Na preferring NBO 3.6:1)
while missing absolute density by ~20%. MPS float32 checkpoints are staged md5-verified
on studios 1–2 per the fleet record (~2.55× wall-clock), with a known +0.0079 g/cm³
MPS-high offset — fine for structural questions, not for cross-checkpoint comparison.
Checkpoints are NOT the whole story: mace 0.3.16 needs the MPS hotfix recorded at
`patches/mace/0001` to run on MPS at all, which is applied+verified on **studio-2's
`imcc-md` venv only**; before any MD on another box, run
`patches/mace/verify-applied.sh <venv-python>` (exit 0 required — absent or
undeterminable both refuse the launch). **Absolute free energies
are NOT certified**; the pre-registered Mg₂SiO₄ Frenkel–Ladd pilot (≤5 kJ/mol RSS gate)
is the gatekeeper, and its prereg explicitly states a pass does not generalize to
Na-bearing associates. The tiers below respect that boundary.

The discipline that makes this list honest: MD answers **topological and relative**
questions at today's certification (does a plateau exist; which species is faster; does
the network change), and **absolute** questions only behind the pilot gate. An entry
must not be promoted across that line by enthusiasm.

## Tier A — structure and topology (MD's certified strength, runnable now)

1. **Does γ_Na flatten (Henry's-law plateau) below x_Na₂O ≈ 0.05, and roughly where?**
   The in-tree KEMS record below x ≈ 0.05 is empty, and lunar mare sits at x_Na₂O ≈
   0.0042 (parent basis; 0.0076 single-cation) — dozens of times more dilute than the
   measured binaries (in-tree bench x from 0.205; lowest extract 0.09). Plateau *existence
   and location* is a topological question — MD's strong suit — not an absolute-activity
   question. Feeds: whether the ×8.9 Na yield bar (t-689) transfers to mare dilution at
   all. (Task t-707.)

2. **Does the melt network feel the minors?** Add 0.3–0.5 wt% P₂O₅ / TiO₂ / Cr₂O₃ /
   REE-oxide to a CMAS base and measure NBO/T, Qⁿ distribution, ring statistics. A null
   result validates, at structure level, the spectator premise behind ignoring loud
   minor-species predictions; a non-null names which minor is not a spectator. Feeds:
   the sensitivity queue's priors (t-705), `docs/model-limitations.md` claim-class notes.

3. **Does Na's environment change with dilution?** The 3.6:1 NBO preference is measured
   at concentrated compositions; if it persists to mare dilution, the mechanistic basis
   for extrapolating the activity model holds; if coordination shifts, extrapolation is
   structurally unjustified regardless of what any fit says. Feeds: t-689 caveat, t-707.

4. **Are alkali–Ca–aluminosilicate associates structurally real?** IMCC/SF04 carries
   `KCaAlSi2O7`, a species that holds a median 99.2 % of all K in the kernel while the
   reference workbook behaves as if it is absent — and the settling document (Hastie &
   Bonnell 1985) is unobtainable. MD cluster statistics on K-Ca-Al-Si-O melts can say
   whether a persistent K·Ca-aluminosilicate association *exists structurally* at
   relevant T — independent evidence bearing on t-697 that requires no library access.

5. **Immiscibility onset and droplet chemistry.** Sulfide/metal clustering vs FeO and S
   content: at what composition does a separate phase nucleate in-box, and what does it
   scavenge? Supports the claim that immiscible ingredients exit the activity problem
   (near-pure phases, a ≈ 1), and gives the matte/sulfide model (SulfLiq wrapper) a
   structural cross-check. Feeds: t-181 metal stratification, S-routing honesty.

6. **Relative density and volume trends.** The ~20 % absolute density miss largely
   cancels in derivatives: d(rho)/dx and d(rho)/dT trends across CMAS+alkali space are
   usable for melt-level/volume bookkeeping even though absolute rho is not. Feeds:
   melt-geometry inputs to transfer models.

## Tier B — transport and dynamics (MD-natural; relative/ordinal use)

7. **Diffusion-coefficient ordering and Arrhenius slopes** for Na, K, Fe, Mg, Ca vs the
   network formers, across T. Consumer stated precisely (review 2026-08-19): the live
   evaporation path REFUSES authoritative melt resistance today, raising a typed error
   naming "species- and state-specific D_i, k_L,i, and dp_eq/dC_i" as exactly what it
   lacks (`engines/builtin/evaporation_flux.py:616–631`); the Higbie-style
   k_melt = 2·√(D_melt·s/π) design lives in the t-099 task text, not yet in code. MD
   D_i is the input that would let that typed refusal be lifted — a consumer in the
   form of a standing refusal, which is this project's strongest kind.

8. **Viscosity trends** (Green–Kubo or NEMD) vs T and composition — ordinal use in the
   stirring/mass-transfer lever (t-099's h_melt ceiling) and the frozen-skull picture
   (t-042). Absolute η not required; the trend and the activation energy are the value.

9. **Surface segregation of alkalis.** Is the outermost melt layer Na/K-enriched
   relative to bulk? HKL evaporation uses bulk activity; systematic surface enrichment
   would bias effective alpha and would matter most exactly for the volatile species we
   extract first. A yes/no + magnitude-class answer reshapes how alpha values are read.

## Tier C — free-energy path (GATED on the Mg₂SiO₄ Frenkel–Ladd pilot)

10. **The pilot itself** — pre-registered, 3 temperatures, error budget 4.6 kJ/mol RSS,
    pass ≤ 5 kJ/mol. Nothing in this tier proceeds until it reports.

11. **If passed: a stepwise ladder toward dilute-alkali activities** by thermodynamic
    integration — binary Na₂O-SiO₂ first, at compositions inside the KEMS empty band.
    This is a route to *absolute* dilute-Na activity that does not wait on new
    experimental acquisition. (Correction 2026-08-19: Mathieu 2011 is NOT circular —
    it is the primary behind Sossi & Fegley 2018 Table 2, on a soda-lime-silica melt;
    its acquisition is a live parallel option with a composition-transfer caveat.)
    Each rung needs its own error budget; the pilot's non-generalization warning stands.

12. **Supercooled-liquid reference states below Tm.** CEA correctly refuses liquid
    polynomials below their melting points (SiO₂_L ≥ 1996 K, Al₂O₃_L ≥ 2327 K, MgO_L ≥
    3100 K, CaO_L ≥ 3172 K), which is exactly why the b-205 solid↔liquid basis
    conversion is impossible from tables: ΔG_fus at 1823–2173 K requires a hypothetical
    supercooled liquid no experiment measures. **MD can compute metastable-liquid free
    energies where experiment cannot go.** A certified ΔG_fus(T < Tm) for CaO/MgO/Al₂O₃
    would un-strand the 292 Kume pure-solid-referenced points against liquid-basis
    engines — turning b-205's "undeterminable/mismatch" wall into a usable conversion.

## Tier D — surfaces and kinetics (exploratory; highest payoff per success)

13. **Sticking/accommodation coefficients on cold walls.** Vapor-atom (Na, K, SiO, Fe)
    impingement on amorphous silica and metal surfaces at wall temperatures: even the
    *relative* sticking ordering would be the first grounded input to the coating model
    — the least-certified, cardinal-class output the simulator has, with **zero
    deposition datasets in the validation lake** (none of DS-001..DS-014 measures
    deposition). Feeds: t-044 capture budget, t-056
    coating rate, and the wanted-experiments registry's top entry. Rare-event sampling
    makes this hard; a crude first pass (thermal impingement at normal incidence,
    counting bounce vs stick vs re-evaporation within a dwell window) still beats the
    current state, which is nothing.

14. **Evaporation-coefficient (alpha) ordering across species** from free-surface MD.
    Absolute alpha from MD is a research problem; the *ordering* (is alpha_Na ≈ 1 while
    alpha_SiO ≪ 1?) cross-checks the DS-007 evaporation-coefficient rows and the alpha
    fallback policy for unmeasured species.

## Tier B/D addendum — plumbing-wall corrosion (owner question 2026-08-19)

Context: hot-wall design converts the coating claim from cardinal to threshold class
(see `model-limitations.md`), at the price of a corrosion claim. The in-tree corrosion
model (`data/wall_materials.yaml` `chemical_attack`) is severity labels — 18 direct /
35 analogous / 27 uncharacterized — with no rate law. Corrosion of oxide walls by alkali
vapor is typically diffusion-limited after the first monolayers (parabolic kinetics,
x² ∝ D·t), which decomposes the problem into exactly the pieces MD can and cannot do:

15. **Alkali attack on amorphous SiO₂ / Al₂O₃ surfaces** (Tier A/B). Na or K vapor
    contacting the wall surface at 1400–1800 °C: adsorption energy class, whether the
    network depolymerises at the interface (NBO formation as alkali enters — the same
    structural observable MACE reproduces exactly in bulk Na₂SiO₃), penetration depth
    over MD-accessible time. Mechanism and tendency, not absolute rate.
16. **D_alkali in the wall material vs T** (Tier B). The Arrhenius diffusion coefficient
    of Na/K in silica glass and alumina is the rate-limiting input of the parabolic law.
    MD-computable transport; with it, corrosion depth vs campaign time becomes a
    *derived* estimate with stated provenance instead of a severity adjective — absolute
    calibration then needs exactly one coupon experiment (WE-009), not a test matrix.
17. **Wall-material ranking under identical vapor** (Tier B; ordinal, MD's strength).
    Same alkali/SiO exposure against silica, alumina, zirconia — **and the refractory
    rump composition itself**. The bootstrap claim (mandate §5: next-furnace liners from
    the rump) currently rests on "refractory oxides survive"; an in-silico ranking of the
    rump ceramic against conventional refractories under the actual vapor stream is a
    direct, cheap test of the self-bootstrap story's weakest link.
18. **Reactive SiO(g) interaction with oxide walls** (Tier D). SiO does not need to
    supersaturate to deposit — it can react. Whether MACE-class potentials handle the
    SiO-on-Al₂O₃ chemistry credibly needs a validation step of its own; treat as
    exploratory. Glaze/wetting behaviour of condensed alkali-silicate films (the
    `glazed`/`unglazed` wall-state enum) sits here too.

What MD cannot do for corrosion, stated so nobody oversells: hours-to-campaigns kinetics
(bridge via the diffusion coefficient, never by brute-force trajectory), grain-boundary
penetration and spalling (polycrystal mechanics), and any absolute rate without the
one-coupon anchor.

## Standing rules for anything run off this list

- Every run states its tier and, for Tier C/D, its gate status. Promoting a topological
  answer into an absolute number is the failure mode; the tier header is the guard.
- Blind protocols where a held value exists (the pilot's discipline generalizes: no
  consulting the target value mid-run).
- Results land as typed evidence with provenance, alongside their error budget — same
  rules as any grounded coefficient (`docs/chemistry-provenance.yaml` conventions).
- Negative results are results and get recorded (an absent plateau, a null NBO/T
  response, a non-existent associate are all decision-grade findings).
