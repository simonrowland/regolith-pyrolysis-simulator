# Feedstocks

The simulator is intended to compare feedstock families, not optimize one idealized lunar composition. Different bodies offer different product streams, pressure environments, volatile hazards, and energy tradeoffs.

## Initial iron speciation

Optional `iron_speciation` observations carry `value`, an ordered `interval`,
`source: {reference: ..., page: ...}`, `method`, and `authority` (`measured`,
`literature_typical`, or `model_assumed`). Methods are `mossbauer`, `xanes`,
`wet_chemistry_titration`, `fmr_is_feo`, `vsm_saturation_magnetization`,
`sato_zirconia_cell`, and `literature_typical`.

Use `fe3_over_sum_fe` for ferric atoms divided by **all** iron atoms, including
declared metal, or a printed `feo_wt_pct` / `fe2o3_wt_pct` pair. These take
precedence over the formation-buffer prediction. `fe0_metal_wt_pct` is elemental
metallic Fe mass, with `form: nanophase | meteoritic_fe_ni | native`; optional
`metal_composition_wt_pct: {Ni: ..., Co: ...}` gives mass percentages within
that alloy. Existing elemental trace inventories fund those Ni/Co shares when
present. FeOt includes the metallic iron on an unsplit total-Fe assay; an
explicit FeO/Fe2O3 analysis already describes the separate oxides.

Alternatively store raw `is_over_feo` and the source sample's `feo_wt_pct`.
The redox owner converts these to 4–33 nm npFe⁰ using the cited Morris relation;
coarser native or meteoritic metal remains unconstrained. Lunar family proxies
are labelled `literature_typical`; their maturity intervals describe sample
spread, not batch measurement precision. Carbon, sulfur, and perchlorates
continue to use their existing composition inventory fields.

`intrinsic_redox` carries `delta_iw`, `interval`, `buffer`, the buffer's
`buffer_source`, and the offset's `source` / `locator`. The conventional
`solid_iron_wustite_sato1973` buffer is evaluated from Sato et al. (1973), p.1067
Eq.5, then converted once to the code's liquid-wüstite reference. Its
850–1300 °C range is explicit; extrapolated formation temperatures are flagged.
Results and hourly snapshots expose the ferric seed's typed `seed_authority`;
metal authority is recorded separately. First liquid contact reacts
Fe⁰ + Fe₂O₃ → 3 FeO through the existing redox reaction provider and atom ledger.

## Lunar Feedstocks

Lunar mare and highland materials are the baseline cases. They are useful for studying oxygen, Fe extraction, SiO suppression, glass preservation, Mg recovery, refractory residues, and MRE pretreatment.

Important questions:

- How much Fe and O2 can be extracted before electrolysis?
- When does pO2-managed pyrolysis preserve useful glass rather than boiling off SiO?
- How much Mg or Al-bearing residue remains for later stages?

Lunar simulant entries use the same inventory path as real-regolith feedstocks,
but they are not experiment-grade measurements by themselves. Each simulant must
carry machine-readable `provenance`, `glass_fraction`, `stage0_buckets`, and
`simulant_vs_real_caveat` fields. If a source-cited composition is not captured,
the entry stays in `status: blocked_*` until the oxide table and lot metadata are
added.

## Asteroid Feedstocks

Asteroid material broadens the materials ledger.

- M-type material can behave like an Fe-Ni-Co alloy source with silicate byproduct.
- S-type material can resemble silicate refining with different Mg, Fe, and volatile balances.
- C-type material introduces water, organics, sulfur, carbon, and volatile-management complexity.

The model is useful for separating product opportunities from gas-handling problems.

## Mars Feedstocks

Mars feedstocks are not processed in hard vacuum. The simulator uses `surface_pressure_mbar` to give Mars material a CO2 pressure floor in Stage 0. That changes the early bakeoff and hot-duct problem.

Mars cases make these issues visible:

- CO2 backpressure and altitude-dependent pumpdown.
- Sulfate, perchlorate, chlorine, and halide handling.
- SO2 sorbents, HCl/HF scrubbers, and salt traps.
- Higher alkali inventory compared with many lunar cases.
- CO/CO2 chemistry as both constraint and process resource.

## Feedstock Inventory Contract

`composition_wt_pct` may contain more than the melt model consumes. The
simulator projects only these cleaned silicate melt oxides into
`MeltState`: `SiO2`, `TiO2`, `Al2O3`, `FeO`, `Fe2O3`, `MgO`, `CaO`,
`Na2O`, `K2O`, `Cr2O3`, `MnO`, `P2O5`, `NiO`, and `CoO`.

Apollo-derived lunar bulk chemistry usually reports total iron as `FeO` or
`FeO_T`, not as a separate `FeO`/`Fe2O3` assay. When a measured split is
unavailable, the simulator predicts that split once at load using Kress91
and the feedstock's intrinsic redox and formation-temperature anchors in
`data/feedstocks.yaml`. It renormalizes the complete analysis to the batch
mass without importing oxygen. The run reports the predicted split and any
defaulted anchor as certification gaps. The ledger split then determines
the melt's redox state. This fallback does not infer metallic iron or claim
a measured ferric split; the source analysis remains unchanged.

**Batch-mass convention (owner-visible).** A declared unsplit `FeO` assay is
treated as total iron expressed as FeO (FeOt), then re-split using the cited
Kress & Carmichael (1991), REF-001, relation and the feedstock's stated anchor
([doi:10.1007/BF00307328](https://doi.org/10.1007/BF00307328)). The citation
supports the ferric/ferrous relation; preserving the requested batch mass is
the simulator's accounting convention. Ferric iron adds oxygen to the
reconstructed analysis, so the complete batch, including iron-free species,
is renormalized. If the original complete analysis has mass A and ferric
oxygen adds dA, an originally normalized non-iron mass scales by A/(A+dA).
For the FeO 95 / Na2CO3 5 default-anchor example, the factor is
0.9994940904872219: 50.0 kg Na2CO3 becomes 49.974704524361094 kg in a
1000 kg batch. Stage 0 cleanup carbon demand uses this same correction;
any carbon supplied beyond the corrected demand remains additive inventory.

The alternative is to preserve the declared non-iron masses and let the total
batch mass increase by the added ferric oxygen. That alternative is not the
current convention. Changing this owner-visible choice requires an owner
ruling.

Other raw components are preserved in the batch inventory instead of being
silently dropped. Examples include `H2O`, `C`, `S`, `SO3`, `Cl`, `ClO4`,
native `Fe`/`Ni`/`Co`, `ZrO2`, and `REE_oxides`. The inventory also has
reserved buckets for Stage 0 gas/volatile products, salts, sulfide matte,
drain-tap metal/alloy material, and inert terminal slag/ceramic components.
Metal/alloy entries are separated phase inventory, not volatilized Stage 0
products. Those buckets are provenance and routing surfaces for process
chemistry; they do not make the melt backend a whole-regolith equilibrium
model.

If a feedstock provides `anhydrous_silicate_after_degassing`, it must also
declare `stage0_profile: carbonaceous_degas_cleanup` and an explicit
`stage0_temp_range_C`. That composition is the cleaned melt input after Stage
0. The bulk volatile-rich composition is kept as raw inventory and product yield
provenance. If no explicit surviving silicate mass is listed, the simulator
uses the CI-family default of 725 kg/t for the handoff.

Mars feedstocks with CO2 atmosphere, sulfate, chloride, perchlorate, or carbon
pre-reduction notes use the Mars carbon-cleanup Stage 0 profile. That profile
runs to 1050 C, records carbon reagent demand from `kg C/t` process notes, and
routes sulfate/halide/perchlorate products outside `MeltState`.

Carbon-rich entries such as CI/CM chondrites, Ceres, cometary material, and Mars
profiles carry `carbon_speciation_provenance` with REF-backed source strings and
interval bounds for organic carbon, carbonate salts, or process carbon where the
literature supports only a range.

Simulant entries must also declare explicit Stage 0 buckets for water, organics,
carbon, sulfur, halides, perchlorates, salts, native metals, sulfides, and
refractory traces. Use `not_reported` when the source does not report a bucket;
do not silently treat unknown non-oxide inventory as zero. `buy_url` belongs in
the YAML `provenance` mapping, with `buy_url_reason` when it is
`not_available`; comments are retained only for human audit.

## Adding Feedstocks

Built-in feedstocks live in `data/feedstocks.yaml`. A feedstock should include
a label, source/confidence notes, composition, and any environment fields such
as `surface_pressure_mbar` and `atmosphere`. Use silicate oxide names for
material that enters melt calculation. Use explicit raw component names for
volatiles, salts, native metals, sulfides, and trace extras preserved for
preprocessing chemistry.
