# Validation of a First-Principles Vacuum-Pyrolysis Process Model Against Gram-Scale Solar-Furnace Experiments

**Draft v0 — skeleton (2026-06-12). JBIS voice. Table slots are marked
`<!-- SLOT:name source=<campaign deposit> status=<draft|refined|final> -->`
and carry placeholder column structures; replace the table body, never the
slot marker, so provenance survives refinement.**

---

## Abstract

<!-- SLOT:abstract source=design-robinot-reconciliation (rev 3, converged) status=draft -->

*Placeholder structure:* A first-principles analytical model of solar-thermal
vacuum pyrolysis of regolith — developed as the process kernel of a
multi-product regolith refinery simulator — is compared without parameter
fitting against the published gram-scale solar-furnace experiments of
Robinot et al. (2026), with secondary anchors from Šeško et al. (2024) and
Sauerborn (2005). We report the model–experiment daylight per observable
(oxygen yield, per-surface deposition, time-resolved evolution), decompose
the discrepancy into a quantified error budget (model parameters,
experimental uncertainty, structural model gaps), and state the resulting
accuracy envelope as the headline validation result. The experimental
run-to-run reproducibility of the anchor dataset (≈11 % on oxygen yield)
sets the floor below which no model comparison is meaningful. [Final
headline numbers TBD from converged synthesis.]

## 1. Introduction

### 1.1 Context: regolith pyrolysis as a multi-product ISRU route
<!-- Prose: the pressure/temperature-lever bet (Mandate §1), where validation fits. Cite MRE/hydrogen/carbothermal baselines. -->

### 1.2 The validation posture
<!-- Prose: no curve-fitting policy; negative results as results; the experiment as a hypothesis subject to the same adversarial standard; "headline accuracy is the product". One paragraph on why a published accuracy envelope serves users better than a tuned match. -->

### 1.3 The empirical corpus

<!-- SLOT:corpus-table source=hypothesis-registry.md + sauerborn-anchors.md status=draft -->

| Source | Apparatus | Scale | Regime | Observables | Role in this work |
|---|---|---|---|---|---|
| Robinot et al. 2026 (exp. 1) | PROMES 1.5 kW solar furnace, flow-through Ar at 13 mbar | 3.38 g pellet (EAC-1A) | viscous/transitional | O₂ time series, per-surface deposit masses (Fig. 4c), 60 min | primary anchor |
| Robinot et al. 2026 (exp. 2, supplement) | same rig, lower heating rate | 3.35 g pellet | viscous/transitional | full 1 s instrument record (O₂, power, energy), 95 min | reproducibility floor; secondary anchor |
| Šeško et al. 2024 | [apparatus TBD from preset] | [TBD] | molecular | [TBD] | regime discriminator (pending P0b+ transport wiring) |
| Sauerborn 2005 (3 selected of 8) | DLR solar furnace | [TBD] | [TBD] | mass-loss rates, deposits | independent cross-validation (pending presets) |

## 2. The Analytical Model

### 2.1 Derivation chain from melt to outlet
<!-- Prose: HKL evaporation flux, activity/Ellingham chain, P_sat sources, condensation routing, transport regime. Cite the kernel docs. -->

### 2.2 Parameter provenance — the no-fitting ledger

<!-- SLOT:parameter-provenance source=derivation-audit-findings.md status=pending-wave -->

| Parameter | Value/band | Provenance class (first-principles / literature / assumption) | Source | Robinot-path sensitivity |
|---|---|---|---|---|
| *populated from the derivation audit; the published table must contain ZERO entries of class "fitted"* | | | | |

### 2.3 Evaporation coefficients: principled treatment

<!-- SLOT:alpha-table source=alpha-principles.md status=draft -->

| Species | α value or band | T, composition conditions | Method (Knudsen/Langmuir) | Source | Status (pinned/banded/assumption) |
|---|---|---|---|---|---|
| *from alpha-principles: 1 pinnable / 4 banded / 3+ assumption-only* | | | | | |

### 2.4 Geometry: evaporation area as a derived quantity
<!-- Prose + slot when area-geometry lands: area from melt volume + vessel geometry; the shallow-pot aspect-ratio result if it survives. -->
<!-- SLOT:area-model source=area-geometry-findings.md status=pending-wave -->

## 3. Reproduction Method

### 3.1 Faithful-run protocol
<!-- Prose: preset schema (conditions captured verbatim with units; provenance classes; assumption-with-sensitivity marking), runner --preset/--leg, digest-scoped artifacts, mass balance closing exactly. The Šeško idealized-schedule catch as an example of the fidelity standard. -->

### 3.2 What "faithful" cannot capture
<!-- Prose: the four assumption-marked geometry surfaces (paper states no numeric geometry); sensitivity treatment. -->

## 4. Results: Model–Experiment Daylight

### 4.1 Oxygen yield

<!-- SLOT:o2-comparison source=o2-probes-findings.md + attack-alpha-findings.md status=pending-wave -->

| Quantity | Experiment (exp. 1 / exp. 2) | Model (faithful) | Model (literature-α forward prediction, band) | Daylight |
|---|---|---|---|---|
| O₂ yield (mg) | 35 / 39.2 | [880 — pre-α] | [TBD ± band] | [TBD] |
| O₂ yield (% of sample) | 1.05 / 1.17 | [TBD] | [TBD] | [TBD] |

*Experimental reproducibility floor: |1.17 − 1.05| / 1.11 ≈ 11 % — the
benchmark against which model daylight is judged.*

### 4.2 Per-surface deposition

<!-- SLOT:wall-comparison source=wall-probes-findings.md status=draft -->

| Surface | Experiment (g, Fig. 4c) | Model (faithful) | Model (conductance-routed) | Daylight |
|---|---|---|---|---|
| Holder | 0.20 | | | |
| Window | 0.35 | | | |
| Condenser | 0.20 | | | |
| Filter | 0.51 | | | |
| Total vaporised-captured | 1.26 | | | |

### 4.3 Time-resolved evolution
<!-- SLOT:timeseries-comparison source=supplement-exp2 CSVs + staged-T rerun artifacts status=pending -->
<!-- Figure slot: model vs exp. 2 cumulative O₂(t) at 1 s resolution. -->

### 4.4 Hypothesis disposition

<!-- SLOT:hypothesis-disposition source=hypothesis-registry.md + all attack-wave deposits status=pending-synthesis -->

| # | Hypothesis (class) | Attack wave | Disposition (killed / weakened / survives) | Evidence |
|---|---|---|---|---|
| *16 rows: 7 model / 5 experiment / 4 comparison* | | | | |

## 5. Error Budget and Accuracy Envelope

<!-- SLOT:error-budget source=design-robinot-reconciliation rev 3 status=pending-synthesis -->

| Budget term | Class | Band | Basis |
|---|---|---|---|
| Evaporation coefficients (α) | model parameter | [TBD] | alpha-principles |
| Evaporation area basis | model parameter | [TBD] | area-geometry |
| Melt temperature (ε = 1 pyrometry) | experiment | [TBD] | attack-pyrometry |
| Mass accounting | experiment | [suspect; bounded by 0.265 g discrepancy] | attack-experiment |
| Run-to-run reproducibility | experiment | ≈11 % | exp. 1 vs exp. 2 |
| Structural (speciation/condensation pathway) | model structure | [named, bounded where possible] | synthesis |

**Headline accuracy statement** *(the product of this work)*:
<!-- SLOT:headline source=rev 3 synthesis status=pending-synthesis -->
> The model reproduces [observable] within [X] under [conditions], limited
> by [dominant budget term]; experimental reproducibility bounds achievable
> validation at ≈11 %.

## 6. Discussion

### 6.1 What the daylight means for the refinery concept
<!-- Prose: which design conclusions are robust to the stated envelope (sequencing, selectivity levers) vs which are sensitive (absolute rates, cycle times). -->

### 6.2 Recommendations for future lab benches

<!-- SLOT:instrument-recommendations source=instrument-recommendations.md status=draft -->

| Instrument | Discriminates | Feasibility at ~1.5 kW solar rig | Priority |
|---|---|---|---|
| Calibrated QMS | vapor speciation + O₂ quantification | [from deposit] | [top pick per feasibility study] |
| Ratio (two-colour) pyrometry | true melt T (kills ε assumption) | | |
| Outlet FTIR | SiO/CO transport speciation (n.b. O₂ IR-inactive) | | |
| IR thermography | wall/condenser temperatures under opacification | | |
| QCM / witness plates | time-resolved per-surface deposition | | |

### 6.3 Limitations and future work
<!-- Prose: Šeško molecular-regime run pending P0b+; Sauerborn presets; Fig. 4c digitization status; what a third dataset adds. -->

## 7. Conclusions
<!-- Prose: restate the envelope, the no-fitting posture, and the standing invitation to falsify. -->

## Acknowledgements / Data availability
<!-- Note: all comparison artifacts digest-scoped and reproducible from presets; cite the provenance machinery. -->

## References
<!-- Robinot et al. 2026 ASR; Šeško et al. 2024 Acta Astronautica; Sauerborn 2005 (Bonn); Engelschiøn et al. 2020 (EAC-1A); Safarian & Engh 2013; alpha-principles primary sources (Hashimoto, Richter, Mendybaev et al.); JANAF; NIST. -->

---

### Appendix A — Hypothesis registry (full)
<!-- SLOT:appendix-registry source=hypothesis-registry.md status=draft -->

### Appendix B — Parameter provenance table (full)
<!-- SLOT:appendix-provenance source=derivation-audit-findings.md status=pending-wave -->

### Appendix C — Faithful-run artifacts
<!-- Run digests, preset SHAs, code version per comparison row. -->
