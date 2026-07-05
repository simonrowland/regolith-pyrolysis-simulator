# Chemistry Methods

This document is the quantitative companion to [`docs/concepts.md`](concepts.md) (the physical
intuition) and [`docs/process-model.md`](process-model.md) (what the simulator tracks). It describes
*how the chemistry is computed* and *where the numbers come from*: the analytic core, the equations it
evaluates, the coefficients it consumes, the diagnostic engines that shadow it, and the assumptions
each method rests on. It is written to be audited. Every grounded value below points to a primary
source and a trust tier, and the machine-readable provenance for those values lives in
[`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml) with full bibliographic detail in
[`docs/references/references.yaml`](references/references.yaml).

The discipline this page holds itself to is stated in [`docs/citation-policy.md`](citation-policy.md):
a number we cannot trace to a source is a number we cannot defend, and where a value is a fit or an
engineering choice rather than a primary measurement, this page says so plainly. The known
simplifications are stated as current limitations with their physical reason, consistent with
[`docs/model-limitations.md`](model-limitations.md); this page does not restate that document's full
limitation list, but it points to it wherever a method carries a caveat.

---

## 1. How the simulator computes chemistry

The simulator computes chemistry through a small set of **analytic process kernels** — vapor pressure,
evaporation flux, condensation routing, metallothermic reduction, iron redox, and electrolysis — that
feed or own ledger transitions as applicable (some kernels, such as vapor pressure, return diagnostics
that later kernels turn into a transition; others write the transition directly), and a set of
**diagnostic engines** (external thermodynamic codes) that shadow those kernels for comparison but do
not write results.

The reason for the split is that no single external thermodynamic engine covers the whole process.
Silicate phase-equilibrium codes (MELTS, MAGEMin) solve for which crystalline and liquid phases are
stable, but they do not model the kinetic, non-equilibrium steps that make this a *process* rather than
a *phase diagram*: Hertz–Knudsen evaporation from a stirred melt, directional vapor transport to a
cold condenser, electrolytic reduction under an applied voltage, or metallothermic reduction by a dosed
reductant. The analytic kernels fill exactly those gaps. Where a real engine *does* have competence —
silicate liquid fraction, phase boundaries, gas speciation over the melt — it is run alongside the
kernel as a diagnostic shadow, so its answer is visible and comparable without being allowed to
silently replace the authoritative one.

Authority is assigned **per quantity, not per engine**. The builtin analytic provider is authoritative
for vapor pressure, evaporation flux, condensation routing, metallothermy, native-iron saturation, and
electrolysis. The silicate-equilibrium engines are authoritative for nothing in the mol-native
ledger; they inform liquid fraction and phase context as diagnostics. This assignment, and the rule
that a diagnostic engine may never be silently promoted into an authoritative slot, is the subject of
§9.

---

## 2. Vapor pressure over the melt

Vapor pressure is the driving quantity for the whole extraction sequence: it sets which species can
leave the melt at a given temperature and pressure. The authoritative provider is analytic — an
Antoine reference for the pure component, coupled to an Ellingham/activity correction that converts the
pure-component pressure into the effective pressure over the actual melt.

### Pure-component reference

Each volatile species carries an Antoine fit for its pure-component saturation pressure:

```
log10(P_Pa) = A − B / (T_K + C)
```

The coefficients are NIST Chemistry WebBook fits keyed to the original primary measurement, converted
to pascals. For example, the sodium fit
(`A = 7.46077, B = 1873.728, C = −416.372`, valid 924–1118 K) traces to Rodebush & Walters 1930
(*J. Am. Chem. Soc.* 52(7):2654–2665, [doi:10.1021/ja01370a011](https://doi.org/10.1021/ja01370a011))
via the WebBook Antoine table; potassium traces to Fiock & Rodebush 1926
([doi:10.1021/ja01421a006](https://doi.org/10.1021/ja01421a006)), calcium to Hartmann & Schneider 1929
([doi:10.1002/zaac.19291800129](https://doi.org/10.1002/zaac.19291800129)), and aluminium to the Stull
1947 compilation ([doi:10.1021/ie50448a022](https://doi.org/10.1021/ie50448a022)). These are the
`pure_component_psat` species (Ca, Al, Ti, Cr, Mn, and the alkalis' pure references) and their fits are
CITED — traceable to a primary measurement on the basis the reference used.

Iron is a documented exception. The WebBook carries no simple high-temperature elemental Antoine row
for iron, so the iron pure-component pressure is a constant-enthalpy Clausius–Clapeyron form anchored at
one atmosphere at its normal boiling point (3135 K). It is labelled UNCERTIFIED accordingly: defensible,
but a derivation rather than a fitted primary.

Several species that the pure-component approach cannot reach directly — sodium and potassium at recipe
temperature, iron, silicon monoxide — instead carry a **pseudo-Antoine** fit whose coefficients are
back-solved so that the activity-scaled Antoine product reproduces a VapoRock gas-speciation target on a
fixed reference grid (seven lunar and Mars feedstocks, 31 temperature points from 1350 to 1950 K, at an
iron–wüstite oxygen fugacity per Kress & Carmichael 1991). These are the
`pseudo_psat_backsolved_from_vaporock` rows. They are honestly a model-to-model fit, not a measurement:
the residual against VapoRock grows with distance from the calibration grid (about 0.38 log-decades for
Na, 0.69 for K, 0.27 for SiO), and every such row is UNCERTIFIED. The VapoRock target itself comes from
Wolf et al. 2023 (*ApJ* 947:64,
[doi:10.3847/1538-4357/acbcc7](https://doi.org/10.3847/1538-4357/acbcc7)).

### From pure component to effective pressure over the melt

The pure-component pressure is not the pressure over the melt. The effective equilibrium pressure is
the pure-component pressure scaled by the species' activity in the melt:

```
P_eff = a_M × P_sat(T)
```

For a metal, `a_M` is the elemental-metal activity computed from the oxide-decomposition equilibrium.
It is derived from the Ellingham free energy of the metal/oxide pair, `K = exp(−ΔG_f / RT)`, together
with the moles of metal and oxide in the formation reaction and the prevailing oxygen partial pressure.
The chain is single-counted by construction: the metal activity already carries the oxide-stability
information, so the pressure is `a_M × P_sat` and nothing multiplies it a second time. The Ellingham
free energies are a linear refit of NIST-JANAF condensed-phase data (Chase 1998, NIST-JANAF
Thermochemical Tables 4th ed., Monograph 9), on a per-mole-O₂ basis with the elemental fusion
corrections (iron 13.81 kJ/mol at 1811 K, silicon 50.21 kJ/mol at 1687 K, chromium 21.0 kJ/mol at
2180 K) built into the segment deltas.

### The oxygen-pressure dissociation lever

Silicon monoxide is the species where oxygen pressure is the direct lever, because oxygen sits inside
its evolution equilibrium:

```
SiO2(melt) → SiO(g) + ½ O2(g)
p(SiO) ∝ a(SiO2) / √pO2
```

The effective SiO pressure carries an explicit inverse-root oxygen-pressure term,
`a(SiO₂) × √(pO₂_ref / pO₂)`, so that lowering the overhead oxygen pressure raises the SiO pressure and
raising it suppresses SiO. This is the physical basis of the "hold pO₂ to lock silicon in the melt,
drop toward vacuum to release it" control described in `docs/concepts.md`. To keep the equilibrium from
diverging as oxygen pressure approaches zero, the term is clamped at a hard-vacuum reference of
1×10⁻⁹ bar; below that the model reads the clamped reference rather than extrapolating the divergence.
The species-specific oxygen-pressure slopes for the other oxides (the `−1/n_M` family in
`docs/concepts.md`) follow from the same formation-reaction stoichiometry.

### Metal vapor versus oxide vapor

The two branches are kept distinct so the energy books do not double-count. A metal leaves as its
element (`a_M × P_sat`, with `a_M` from the Ellingham activity). An oxide-vapor species — SiO, and the
diagnostic chromium-oxide vapor CrO₂ — leaves through a single parent-oxide dissociation reaction with
its own oxygen-pressure exponent, and is charged the reaction enthalpy once rather than a metal latent
heat plus a separate dissociation. When the melt temperature exceeds a row's measured Antoine range,
the provider switches to a bounded fallback fit and guards against numerical blow-up rather than
extrapolating the pure-component curve unphysically.

---

## 3. Melt-oxide activity

Section 2 needs an activity for every species. How that activity is obtained is the single most
consequential modelling choice on this page, because in a real silicate melt the oxide activities are
far from ideal.

**The activity that feeds the authoritative vapor-pressure path is the builtin analytic treatment, not
a melt-equilibrium engine.** For every oxide except iron oxide, the builtin provider takes the activity
as the oxide's weight-fraction proportion of the melt — an **ideal solution with activity coefficient
γ = 1**. Iron oxide is the exception: when an intrinsic oxygen fugacity is supplied, the FeO activity is
the redox-resolved ferrous activity from the Kress & Carmichael / CALPHAD treatment of §7; when it is
not supplied, iron oxide falls back to the same ideal weight-fraction proportion as the other oxides.
This ideal-for-non-iron, Kress-for-iron activity surface is what the vapor pressures of §2 are built on.

The silicate-equilibrium engines compute activities too, on the MELTS convention

```
a_i = exp( (μ_i − μ_i0) / RT )
```

(where `μ_i` is the melt chemical potential of the component and `μ_i0` is the pure-endmember reference
at the same temperature and pressure — the same convention VapoRock uses to build its gas abundances).
But that surface is used for **silicate phase equilibrium and as diagnostic / fallback-context data**,
not as the authoritative activity source for the vapor-pressure path. The authoritative vapor-pressure
activity is the builtin treatment described above.

The physical cost of the ideal treatment is largest, and points in a known direction, for the alkalis.
In a silicate melt the alkali oxides are held in the aluminosilicate network far more strongly than an
ideal solution would predict — their true single-cation activity coefficients are of order 10⁻³, not 1.
Because the alkali partial pressure is **linear** in that coefficient (see below), taking γ = 1
overstates the alkali activity, and therefore the alkali vapor pressure and evaporation rate, by
roughly that same factor of a thousand. An ideal treatment predicts sodium and potassium boiling off
far too readily. This is why the grounded alkali coefficients matter, and why they are recorded even
though the builtin activity path does not yet consume them.

### The grounded alkali coefficients and why the basis is load-bearing

The correct formulation for the alkali activity, and the one the grounded values are measured on, is:

- **Component basis: single-cation.** The melt component is written with one cation — NaO₀.₅, KO₀.₅ —
  not the di-cation Na₂O.
- **Standard state: Raoultian.** The activity is referenced to the pure liquid oxide (activity → 1 for
  the pure component), which is the same reference the pure-oxide JANAF equilibrium constant uses, so
  the activity correction composes cleanly with a pure-oxide K.
- **Concentration measure: single-cation mole fraction.**

On this basis the vaporization reaction is written per single cation,

```
NaO0.5(l) = Na(g) + ¼ O2
p_Na = γ_NaO0.5 × X_NaO0.5 × p_Na,pure
```

so the alkali partial pressure is **first-order (linear) in the activity coefficient**. There is no
square-root exponent on the alkali activity coefficient on this basis. This is what makes the basis
load-bearing rather than clerical: the exponent on the coefficient is fixed by how many cations the
vaporization reaction consumes. A single-cation coefficient inserted into a di-cation formulation —
writing Na₂O(l) = 2 Na(g) + ½ O₂ with `p_Na ∝ a(Na₂O)^½` — recovers only the square root of the true
suppression, because the di-cation activity is the square of the single-cation one. Numerically that
mistakes a ~1/735 suppression for a ~1/27 one. A correct coefficient applied on the wrong basis is a
new, wrong number wearing a real citation; the code and its provenance record therefore fix the basis
and apply it consistently.

The grounded values, at 1673 K on a ferrobasalt melt, are:

| Component | γ (single-cation, Raoultian) | 1σ | Tier |
|---|---|---|---|
| NaO₀.₅ | 1.0 × 10⁻³ | ± 2.2 × 10⁻⁴ | UNCERTIFIED |
| KO₀.₅ | 2.2 × 10⁻⁴ | ± 5.5 × 10⁻⁵ | UNCERTIFIED |

They are tagged UNCERTIFIED because they are a single-temperature datum extrapolated into the hotter
1773–2173 K recipe band, where the true coefficient rises toward unity (the melt holds the alkali less
tightly as it gets hotter) and the temperature dependence is not yet fit.

The chosen source is Sossi et al. 2019 (*Geochim. Cosmochim. Acta* 260:204–231, §4.5.1, Tables 3–4;
[doi:10.1016/j.gca.2019.06.021](https://doi.org/10.1016/j.gca.2019.06.021)). It is chosen because it is
a primary Knudsen-effusion mass-spectrometry measurement on a **ferrobasalt** — the mafic, iron-bearing
melt composition of interest — reported on exactly the single-cation Raoultian basis the physics
requires, with the vaporization reaction written explicitly. The review compilation of Sossi & Fegley
2018 (*Rev. Mineral. Geochem.* 84:393–459, Table 2 pp. 409–410; Raoultian definition Eqn 24 and the
single-cation reaction Eqn 25, p. 413; the γ(T) trend Fig. 5, p. 414;
[doi:10.2138/rmg.2018.84.11](https://doi.org/10.2138/rmg.2018.84.11)) is where the framework and the
exponent come from, and it is included in the provenance — but its tabulated NaO₀.₅ datum is from a
different melt system (a soda–lime–silica melt, Mathieu et al. 2011), so the composition-matched
ferrobasalt value is used as the coefficient and the review is treated as a pointer to the framework
and the underlying primaries. DeMaria et al. 1971 (Apollo 12022 lunar-basalt KEMS) is retained as the
validation case rather than the coefficient source: it reports absolute alkali partial pressures over a
real lunar basalt (p_Na ≈ 3.2 × 10⁻² Pa at 1538 K), which is the independent measurement the corrected
vapor path is checked against. A gasifier-slag KEMS measurement was considered and set aside because it
is the wrong feedstock and is tabulated on the di-cation basis.

Full comparative provenance — chosen source, alternatives, and what each alternative lacks — is the
`gamma_alkali_melt_activity` entry in [`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml).

---

## 4. Evaporation flux (Hertz–Knudsen)

Vapor pressure sets the driving force; the evaporation flux sets the rate at which a species actually
leaves the melt. The flux is computed as a **series of three resistances** in the path from the melt
interior to the bulk gas: a melt-side surface-renewal resistance, the free-molecular Hertz–Knudsen
interface resistance, and the continuum boundary-layer gas-diffusion resistance:

```
J = (P_eff − P_bulk) / ( r_melt + r_interface + r_gas )

  r_interface = 1 / (α × k_HKL)             free-molecular impingement
  r_gas       = (1 − f) / k_MT              gas-side boundary layer
  r_melt      = melt-side surface renewal   (see below)

  k_HKL = √( M / (2π R T) )                 free-molecular impingement rate
  k_MT  = D_AB(T, P) × Sh                   boundary-layer (continuum) mass transfer
  f     = Kn / (Kn + 0.01)                  Knudsen-regime weight
```

In the free-molecular limit (high Knudsen number) the regime weight sends the gas-side boundary-layer
resistance to zero and the Hertz–Knudsen interface term rate-limits; in the viscous limit (the millibar
sweep-gas regime the recipes actually run in) the boundary-layer diffusion rate-limits. The diffusion
coefficient `D_AB` is a per-species Chapman–Enskog value rather than a fixed constant, and the Sherwood
number carries an induction-stirring enhancement (`Sh_eff = 3.66 × √stir_factor`, with the stirring
factor from the recipe, default giving `Sh_eff ≈ 9`) so that stirring the melt increases the flux in the
diffusion-limited regime.

The **melt-side surface-renewal resistance** `r_melt` accounts for the finite rate at which stirring
brings fresh, un-depleted melt to the evaporating surface — a resistance in series with the gas-side
terms, so a species cannot evaporate faster than the melt can resupply its surface concentration. It is
an owner-ratified engineering term, enabled by default in the recipe data (`melt_resistance_enabled`),
with a base surface-renewal conductance that scales with the same induction-stirring factor. The full
three-resistance series form and its derivation are documented in
[`docs/model-limitations.md`](model-limitations.md).

### The evaporation coefficient

The coefficient `α` (the fraction of impinging-rate flux actually realized) is the physically uncertain
term. Its **coverage** — whether a species has any grounded coefficient at all — falls into three
classes. These coverage classes are about *whether a value exists and where it comes from*; they are not
the CITED / ASSUMED / UNCERTIFIED trust tiers of the citation policy, and a species can be in the
"grounded" coverage class while its value is still UNCERTIFIED.

- **Grounded coefficient.** Sodium, potassium, iron, magnesium, and SiO carry a grounded coefficient
  with a citation. The sodium value (α ≈ 1.0, envelope 0.9–1.0, over 1700–2273 K) is from Sossi et al.
  2019 ([doi:10.1016/j.gca.2019.06.021](https://doi.org/10.1016/j.gca.2019.06.021)), an open-furnace
  mass-loss measurement. It is chosen over the competing KEMS value of ≈ 0.13 from Fedkin et al. 2006
  (*Geochim. Cosmochim. Acta* 70:206–223,
  [doi:10.1016/j.gca.2005.08.014](https://doi.org/10.1016/j.gca.2005.08.014)) on physical grounds: the
  disagreement is methodological — the sealed KEMS chamber measures an intrinsic coefficient against a
  re-condensing reservoir, whereas the recipes run at millibar overhead with a continuous sweep gas,
  which is the open-furnace regime with little back-flux. Because the two credible measurements are not
  reconciled, the sodium coefficient is tagged UNCERTIFIED in the provenance registry, and an operator
  can select the conservative Fedkin value through the setpoints. These grounded rows carry a
  confidence-tier-2 label in the coefficient data — a proxy-or-conditional confidence, not a direct
  regolith-melt measurement — so "grounded" here means sourced and cited, not high-confidence.
- **Proxy coefficient.** Calcium and titanium use a perovskite (CaTiO₃) proxy; aluminium uses a broad
  conflicting-proxy envelope. These are labelled as proxies, not regolith-melt measurements.
- **No grounded coefficient.** Chromium, manganese, and the chromium-oxide vapor have no grounded
  coefficient. The provider default is to fail loud — it returns a missing-coefficient signal and
  refuses to evaporate them rather than silently assuming α = 1. This default can be overridden by an
  unmeasured-alpha fallback that substitutes α = 1 and records that it was used; that override is
  **enabled in the checked-in setpoints**, so in the default configured run these species do evaporate
  under a recorded α = 1 fallback. The distinction matters for auditing: the provider will not invent a
  coefficient on its own, but the shipped recipe configuration opts into the fallback, and the output
  records which species used it.

SiO is treated specially because its coefficient is strongly temperature-dependent, and the
hot-source and cold-wall interfaces are physically different. Hot-source evaporation uses the Wetzel &
Gail 2013 Arrhenius compilation, `α_s(T) = 0.52 × exp(−3685/T)` (grounded ≈ 1000–1800 K, envelope
0.003–0.067; *A&A* 553:A92,
[doi:10.1051/0004-6361/201220803](https://doi.org/10.1051/0004-6361/201220803)), with microscopic
reversibility applying at that interface. Cold-wall condensation, below the valid range of that fit,
uses the Pound 1972 high-supersaturation unity condensation coefficient (α_c = 1.0; *J. Phys. Chem.
Ref. Data* 1:135, [doi:10.1063/1.3253096](https://doi.org/10.1063/1.3253096)). The model does not
extrapolate the hot-source Arrhenius onto cold walls: the evaporation and condensation coefficients are
deliberately different off-equilibrium at high supersaturation.

### The one-hour reservoir model

Within each one-hour simulation step, the driving force is evaluated **once** at the start of the step
— vapor pressures, bulk pressure, and temperature are fixed — and the parent-oxide and shared-oxygen
pools then deplete as first-order reservoirs across the hour. This is an analytic integration, not a
fresh equilibrium solve at each instant: it smooths the time integration but assumes the driving force
is constant over the tick, which accumulates error when the melt composition swings hard within a
single hour. It is stated as a current approximation in
[`docs/model-limitations.md`](model-limitations.md).

---

## 5. Condensation routing to stages

Evolved vapor is routed to condenser stages by temperature. Each canonical species has a designated
stage, and the fraction that condenses in a given stage rises as the stage temperature falls below the
species' condensation temperature and as the residence time in the stage grows relative to the
species' condensation time constant. Iron is designated to the hottest condenser, then SiO, with
magnesium, sodium, and potassium condensing in progressively cooler stages. SiO that reaches a cold
surface disproportionates on condensation (`SiO → ½ SiO₂ + ½ Si`), which is why its captured product
is silica rather than a recoverable monoxide.

The condensation reference temperatures used for this routing (for example, iron at 1250 °C, SiO at
1050 °C, magnesium at 580 °C, sodium at 480 °C, potassium at 420 °C, at a 1 mbar partial pressure) are
**engineering routing thresholds, tagged ASSUMED**, not measured condensation temperatures. Where a
threshold approximately tracks a pure-component saturation crossing it does so by coincidence; several
(iron, chromium) deliberately do not match the pure-component curve because the routing target is the
condenser stage-band alignment, not the pure-substance vapor-pressure crossing. They are documented as
such in the recipe data and are an engineering approximation pending physical validation of the real
condenser geometry.

The simulator reports the outcome of routing as a per-stage purity account (designated mass versus
impurity mass per stage) and pins the routing against per-pipe-segment wall temperatures with a
cold-spot diagnostic, so that a species landing on the wrong surface is visible in the output. The
design invariant that upstream ducting stays hot (above roughly 1400 °C) so vapor reaches its
designated condenser rather than depositing early is described in `docs/concepts.md`.

---

## 6. Wall coating: deposition rate to furnace lifespan

Wall coating is the second failure mode (the first is incomplete extraction). It is the fraction of
evolved vapor that lands on pipe and vessel walls instead of reaching its designated condenser, and it
is modelled as a **continuous per-species deposition rate that accumulates into a furnace lifespan**,
not as a hard operating gate — a furnace that fouls slowly enough to be re-sintered on a schedule is a
costed operator tradeoff, not a forbidden state.

The deposited mass is tracked per species (and per pipe segment) in the mol-native ledger, written once
through the condensation route inside the mass-balance closure. At each wall the Hertz–Knudsen wall
sink competes against the onward condensed sink, and the split is set by a per-species, per-segment,
temperature-dependent wall sticking coefficient. The remainder — the capture budget minus the wall
deposit — is what reaches the designated condenser.

Wall re-evaporation is handled by a per-species reactivity class:

- **Physisorbing species** (calcium, manganese, chromium, aluminium, titanium, and the chromium-oxide
  vapor) use a reversible pure-species saturation backstop `P_sat(T_wall)`, so a sufficiently hot wall
  rebounds the deposit.
- **SiO is reactive.** Wall capture disproportionates it to physical products (`SiO → ½ SiO₂ + ½ Si`)
  with an effective product saturation pressure near zero, so the deposit does not rebound — which is
  why SiO is the worst fouling offender even under a hot-wall design.
- **Cross-species wall chemistry** is modelled: magnesium reduces wall silica (`2 Mg + SiO₂ → 2 MgO +
  Si`) and iron reacts with free wall silicon (`Fe + Si → FeSi`).
- **Sodium and potassium** credit their elemental deposit but carry a diagnostic activity-depression
  state anchored to a disilicate saturation (0.5 mol alkali oxide per mol SiO₂, from the Kracek-family
  phase data).

The accumulated wall load maps to furnace lifespan by a thickness proxy: per segment, the deposited
mass of each species divided by its density and the segment area gives a cumulative wall thickness, and
the service life is the number of campaign runs before any segment reaches its thickness limit. Where a
resinter threshold has been set, the model reports campaigns-to-resinter as the threshold divided by the
total wall load; where it has not, it reports the symbolic ratio rather than inventing a number. This
lifespan grounding is explicitly provisional pending empirical validation of the threshold, and the
remaining gaps in the wall chemistry (higher silicides, iron-oxygen-fugacity-dependent partition, the
alkali rate law beyond the saturation cap, magnesium passivation, and run-to-run fouling beyond the
transient wall state) are enumerated in [`docs/model-limitations.md`](model-limitations.md).

The Knudsen number is reported per segment as a transport diagnostic and drives cold-spot warnings, but
it does not gate deposition routing — the transport regimes are treated as continuous, consistent with
modelling coating as a rate rather than a threshold.

---

## 7. Melt and redox chemistry

### Iron redox and the total-iron convention

Iron is the one melt cation whose activity is redox-sensitive, and its treatment begins with an
**input convention that must be stated plainly.** Feedstock analyses report all iron as a single "FeO"
number — total iron expressed as ferrous oxide — and do **not** resolve the ferric/ferrous
(Fe³⁺/Fe²⁺) split. This is the standard laboratory reporting convention: electron-microprobe and X-ray
fluorescence measure total iron by X-ray intensity and cannot separate the oxidation states at
collection time, so all iron is booked as FeO and the redox state must be inferred afterward. Every
feedstock in the catalog follows this convention, and its annotations say so.

The model therefore **infers** the redox state rather than reading it. From the total-iron content, the
melt composition, temperature, and oxygen fugacity, it computes the ferric/ferrous split using the
Kress & Carmichael 1991 relation (*Contrib. Mineral. Petrol.* 108:82–92,
[doi:10.1007/BF00307328](https://doi.org/10.1007/BF00307328)), which gives `ln(X_Fe₂O₃/X_FeO)` as a
function of oxygen fugacity, inverse temperature, and the melt cation fractions. The resulting ferrous
mole fraction, scaled by an FeO activity coefficient, is the iron-oxide activity that feeds the iron
vapor pressure. The activity authority switches by regime: below the iron–wüstite reference the melt is
metal-saturated and uses a CALPHAD/Holzheid stoichiometric-wüstite activity coefficient (γ_FeO ≈ 1.70,
Holzheid 1997); above iron–wüstite plus one log unit it uses the Kress & Carmichael ferric limb; between
them it blends smoothly, with the activity clamped at the pure-FeO ceiling.

Because the split is an **inference from an assumed oxygen fugacity, temperature, and composition — not
a measurement — it carries a systematic bias, and the bias is oxidizing.** The redox reference is
anchored to the pure-FeO iron–wüstite buffer rather than a self-consistent basaltic metal-saturation
point, and a basaltic melt with iron-oxide activity below unity reaches metal saturation at a lower
oxygen fugacity than pure FeO does. The net effect is that the inferred iron redox runs roughly 0.8–1.2
log units more oxidizing than basaltic saturation at the buffer. Contributing to that bias: untracked
in-melt reductants (dissolved carbon or hydrogen are not in the composition input, so a locally lower
oxygen fugacity is invisible to the inference), oxygen-pressure control uncertainty, and the steep
temperature sensitivity of the ferric fraction. The intrinsic oxygen-fugacity seed that feeds the split
still carries ungrounded composition offset terms, though the Kress & Carmichael mapping from oxygen
fugacity to the split is itself grounded. This is stated as a current limitation in
[`docs/model-limitations.md`](model-limitations.md), and the oxygen-fugacity buffer groundings (IW,
QFM, CCO) are the O'Neill 1987, Frost 1991, and Jakobsson & Oskarsson 1994 entries in the reference
registry.

### The metallothermic shuttle

The alkali shuttle reduces iron oxide chemically rather than thermally: dosed elemental sodium strips
oxygen from ferrous oxide (`2 Na + FeO → Na₂O + Fe`), freeing metallic iron that can be tapped or
evaporated at a different point in the sequence than the SiO window, which is what de-conflicts the
overlapping iron and SiO thermal windows. The reaction is gated by a strict thermodynamic acceptance
test built on the JANAF Ellingham refit: the crossover temperatures put sodium/iron at ≈ 1173 °C and
potassium/iron at ≈ 832 °C, and the executable gate refuses any dispatch with non-positive
thermodynamic margin at its temperature — so potassium is refused as an iron-oxide reductant across the
practical melt window, sodium is refused above its crossover, and each refusal is recorded in the run
output. Inside the gate the reaction is treated as temperature-independent; it does not interpolate
yields across the crossover band. The physics and the crossover values are developed in
`docs/concepts.md`.

### Molten regolith electrolysis

Electrolysis is modelled as a reduced Nernst/Faraday cell, not a full electrochemical simulation. Each
reducible oxide carries a standard-state decomposition voltage `E° = −ΔG_f°/(nF)` evaluated near
1873 K, with the electrons per formula unit and Faraday's constant (96485.33 C/mol) setting the current
relationship, and the runtime applies the Nernst melt-activity and oxygen-pressure correction on top of
the standard-state rung. The voltage ladder is raw-thermodynamically anchored from NIST-JANAF (Chase
1998) and companion evaluations (Barin; O'Neill 1988 for the FeO rung; Hemingway 1990 and
Robie–Hemingway for NiO) — for example NiO at 0.39 V, FeO at 0.75 V, SiO₂ at 1.45 V, TiO₂ at 1.70 V,
Al₂O₃ at 1.95 V. The alkali-oxide rungs (Na₂O, K₂O at 0.5 V) and the alkaline-earth rungs are held at
legacy values pending activity- and vapor-aware grounding, because those species are volatile at cell
temperature, and are labelled UNCERTIFIED. The ferric full-reduction rung is reference-only; the live
path can reduce ferric to ferrous through an explicitly uncertified diagnostic route rather than a
validated ferric-current-partition model. Metal-phase settling and drain-tap are not modelled —
reduced metal accumulates in a single account and is reported directly as product.

---

## 8. Energy and thermal budget

The thermal budget closes the hourly energy books against the chemistry: the heat drawn each step is
the sum of the sensible heat to reach and hold temperature, the latent heat of the species that
evaporate, and the reaction enthalpy of the oxide dissociations that occur. Latent heats of
vaporization and parent-oxide dissociation enthalpies are taken from NIST-JANAF (Chase 1998,
Monograph 9) on a standard-state basis (for example, SiO₂ dissociation at 910.94 kJ/mol, MgO at
601.60 kJ/mol, the sodium latent heat at 97.42 kJ/mol), and are CITED. The metal-vapor and oxide-vapor
branches are charged separately and once each, so an oxide leaving as SiO is not also charged a metal
latent heat — the single-counting discipline of §2 carried into the energy ledger.

Heat *transfer* is simplified: solar concentration is assumed to maintain the target temperature rather
than fully modelling radiative, conductive, and convective losses, and the melt radiative loss uses an
assumed total-hemispherical emissivity of 0.85 (an engineering mid-band value for oxidized
high-temperature silicates, supported by Jones et al. 2019 and lunar-simulant measurements in Kost et
al. 2021, tagged ASSUMED because no primary total-hemispherical datum exists for a basaltic melt at
these temperatures). The electrical energy reported for electrolysis is the cell energy from the
voltage/current model and is kept as a separate bin from the thermal budget. These simplifications are
in [`docs/model-limitations.md`](model-limitations.md).

---

## 9. The engines: why several, and which decides what

The simulator can run several thermodynamic engines, and it assigns authority per computed quantity so
that each engine is used only where it is competent. The rule the whole architecture protects is that a
**diagnostic engine is never silently promoted into an authoritative slot**: if an authoritative
dispatch has no usable result, the simulator raises rather than quietly falling back to a diagnostic
engine's number, unless the operator explicitly sets the `allow_fallback_vapor` flag and accepts the
recorded warning.

- **The builtin analytic model** is the authoritative provider for vapor pressure, evaporation flux,
  condensation routing, metallothermy, native-iron saturation, and electrolysis — the analytic kernels
  of §§2–8. It is deterministic and transparent, it covers the non-equilibrium kinetic steps no
  equilibrium engine models, and it fails loudly out of range rather than extrapolating. It supplies the
  oxide activities its own vapor-pressure path consumes — the ideal-for-non-iron, Kress-for-iron
  treatment of §3 — but it does not run a Gibbs-energy minimization or compute phase boundaries. In
  trust-architecture vocabulary this is the `internal-analytical` model (serialized under the legacy
  name `stub`), and it is denylisted from certification claims: it can supply exploratory diagnostic
  evidence but cannot certify a yield or phase claim.

- **AlphaMELTS / MELTS** (via ThermoEngine / PetThermoTools) is the live path for silicate equilibrium:
  Gibbs-energy minimization over the silicate liquid and crystalline phases, supplying liquid fraction
  and phase boundaries. Its melt oxide activities (the MELTS convention of §3) inform silicate-phase
  context and serve as diagnostic and fallback-context data; they are not the authoritative activity
  source for the vapor-pressure path, which uses the builtin treatment. It runs in an isolated
  subprocess (it can hang on spinel-saturated compositions, and a subprocess kill is logged as a
  diagnostic failure rather than a ledger error), and it does not itself write the mol-native ledger.

- **MAGEMin** is a fast Gibbs-minimization engine over an igneous phase set, used as a narrow-gate
  shadow for liquid fraction and phase context. It is quick and broad but returns stoichiometric phases
  only (no activity coefficients) and does not cover manganese, so it is a diagnostic companion rather
  than an authority. It is an optional install.

- **VapoRock** computes silicate-vapor speciation over a melt composition and is run as a
  **diagnostic-only shadow** for vapor pressure. Its full gas speciation is reported for comparison and
  is the calibration target behind the pseudo-Antoine fits of §2, but it does not own the pressure
  surface that evaporation consumes and it never writes the ledger. The builtin analytic provider
  remains authoritative for vapor pressure whether or not VapoRock is available.

- **FactSAGE / ChemApp** is not part of this checkout: the adapter has been archived and removed, and
  an explicit request for it raises rather than falling through. It is noted here only so that a reader
  who finds references to it elsewhere knows it is not a selectable engine in the current code.

**Internal data tables.** Distinct from the analytic model is the tabulated thermochemical data the
analytic kernels consume — the pure-component Antoine coefficients, the JANAF-derived Ellingham and
latent/dissociation tables, and the redox-buffer fits. In trust vocabulary this is the
`internal-datatables` evidence class. It is a trust category, not a separate runtime backend: the data
is embedded in the builtin engines and the reference data files, it carries no ledger authority, and it
is preferred over MELTS only within the diagnostic silicate-context intent and only in the refractory
(high-alumina, low-iron) regime where MELTS extrapolates outside its igneous training set.

---

## 10. Feedstock assumptions

The cleanup stage that precedes the main extraction sequence takes a messy feedstock and hands the melt
model a cleaned silicate oxide composition plus an explicit residual ledger. What it *assumes* about the
non-rock species is stated here as assumptions, because those assumptions bound what the downstream
chemistry is allowed to conclude.

### Carbonaceous chondrite: refractory carbon

A carbonaceous chondrite carries several weight percent carbon, in two broad forms: insoluble organic
matter (the dominant fraction, of order 90% of total carbon) and mineral carbonate. The published
analyses this is grounded on are organic-dominated (Yokoyama et al. 2023 on Ryugu; Pearson et al. 2006;
Alexander et al. 2007), and the model books the carbon in exactly those two buckets.

The current code treats **all** of that carbon as reactive. The organic fraction is completely oxidized
during the bake (to CO₂, water, and nitrogen oxides using controlled cleanup oxygen), and the carbonate
fraction is decomposed to CO₂ with its metal oxide left in the melt. There is no separate
refractory-carbon (graphite / coarse insoluble organic matter) inventory that survives the bake as an
inert phase or as a downstream in-melt reductant. This is a deliberate simplification, and it is
conservative: real chondrites contain a graphite fraction whose oxidation kinetics depend on particle
size and the oxygen ramp, and some coarse graphite could survive an oxidizing pretreatment. Lumping it
with the reactive organic matter tends to overestimate carbon loss and underestimate any surviving
reductant carbon. The assumption is stated so it can be revisited; it is not presented as a measured
fate.

### CNOPS handling

The cleanup stage's treatment of carbon, nitrogen, oxygen, phosphorus, and sulfur is:

- **Carbon** — removed as CO₂. Organic carbon is oxidized to CO₂; carbonate carbon is thermally
  decomposed to CO₂, with the carbonate's metal oxide (CaO, MgO, Na₂O) credited to the cleaned melt
  rather than lost — the decomposition extent is set by the mineral's thermal decomposition curve, so
  only residual undecomposed carbonate is routed to the salt bucket. There is no reduction-to-metal,
  carbide, or dissolved-carbon pathway (see the refractory-carbon assumption above).
- **Nitrogen** — assumed to volatilize. There is no nitride phase and no retained-nitrogen model.
- **Oxygen** — the oxygen carried in water and carbonate leaves as water vapor and CO₂; structural
  oxide oxygen stays with the retained oxide inventory.
- **Phosphorus** — **retained in the melt as P₂O₅ (phosphate)**, which is igneous-correct: phosphorus
  is not volatilized in cleanup and phosphate stays in the cleaned silicate composition. All phosphorus
  collapses to a single P₂O₅ account; individual phosphate minerals (apatite, merrillite) are not
  separately resolved.
- **Sulfur** — oxidized to SO₂ or retained as sulfate depending on oxygen pressure, routed through a
  sulfur-saturation gate. When the optional PySulfSat integration is installed and the melt composition
  falls inside its calibration windows, the gate reports the sulfide-capacity (SCSS) and
  sulfate-capacity (SCAS) limits and the sulfide/sulfate partition; otherwise it falls back to the
  builtin bucketing with a recorded warning. The gate never mutates the atom ledger.

The mechanism behind this cleanup is, for most species, **name-routing rather than reductant-driven
thermodynamics**: raw feedstock components are matched by name and dropped into terminal buckets
(offgas, salt phase, sulfide matte, drain-tap metal, inert slag), and only the melt oxides pass through
to the melt model. A handful of reaction families (organic oxidation, carbothermal sulfate reduction,
the Boudouard reaction, carbonate thermal decomposition, perchlorate decomposition) consume reagents or
apply real reaction stoichiometry; everything else is routed by name. The consequence is that the
"unlimited reductant" framing of the bake is an assertion in the routing tables for most species, not a
modelled thermodynamic clearance, and several routings are known simplifications: carbonate mineral
mixtures and their decomposition extents are approximate and not fully speciated (though the decomposed
oxide is correctly credited to the melt and only residual carbonate goes to the salt bucket — the cation
is not wholesale lost), refractory fluorides are carried to the rump, chlorides can volatilize and
re-condense on cold walls, and nitrates have no explicit coverage. The per-species detail and the honest
coverage holes are in [`docs/model-limitations.md`](model-limitations.md) and the Stage 0 contract in
[`docs/process-model.md`](process-model.md).

Feedstock compositions themselves include literature-derived ranges and estimates; the lunar-simulant
composition is grounded on Engelschion et al. 2020 (EAC-1A) and the carbonaceous and Mars-volatile
compositions on the observation and measurement references in the registry.

---

## 11. Provenance and how to audit it

Every grounded value on this page is meant to be checkable without archaeology. The provenance is
layered:

1. **The comparative registry**, [`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml), is the
   single machine-readable source of truth. Each entry records the value, its units, its trust tier,
   the standard-state/basis it is applied on, its temperature and composition range, its uncertainty,
   the chosen source with page-and-table locus and DOI, the alternatives considered and *what each one
   lacks*, and the code sites that consume it. This is the layer that answers not just "what is the
   number" but "why this source and not the others."

2. **The bibliography**, [`docs/references/references.yaml`](references/references.yaml), carries the
   full citation for every reference id (author, title, journal, year, DOI, and where held).

3. **The benchmark literature corpus** holds OCR'd copies of the primary papers, so a value can be
   re-checked against the actual table it came from.

The trust tiers used throughout this page are the ones defined in
[`docs/citation-policy.md`](citation-policy.md): **CITED** (traceable to a primary source, applied on a
consistent basis — the only tier permitted to back a certification claim), **ASSUMED** (a stated
engineering default with no direct measurement), and **UNCERTIFIED** (grounded but with unclosed
scatter, and denied from certification claims until the scatter is reconciled). Where this page marks a
value ASSUMED or UNCERTIFIED, that is the honest status of the number, not a placeholder to be quietly
upgraded.

The reproduction error bar is treated as a deliverable, not a disclaimer: where the model is validated
against a laboratory experiment, the residual is decomposed and reported rather than tuned away. The
governing discipline, stated in `CLAUDE.md` and the citation policy, is that when the model disagrees
with reference data the response is investigation, never retuning a coefficient to force agreement. The
appropriate and inappropriate uses of the simulator's numbers are enumerated in
[`docs/model-limitations.md`](model-limitations.md).
