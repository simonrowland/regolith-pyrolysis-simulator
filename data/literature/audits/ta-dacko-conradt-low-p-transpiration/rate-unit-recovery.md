# Dacko, Wilsmann & Conradt (2004): Table 2 rate-unit recovery

Dispatch: `bugs1026-dacko`; task b-985; examined on 2026-10-26.
Starting commit: `f5ad90ada533f93b65a4862ca87fbbd6fe4e3543`.
Green comparison: `5fd6fe8d3`.

Decision: **the numerical unit of Table 2 is not groundable from the available
evidence**. Equation (5) establishes mass flux (mass/area/time), but does not
establish the units or multiplier of the integers in Table 2. Retain the printed
numbers and unit text; give all 18 rows `admission_status: typed_refusal` and the
exact reason `rate unit not stated`. The nine experimental rows must leave the
alkali_shuttle rail's scored residuals and the headline. No replacement residual,
conversion factor, or score has been invented. No derived store was regenerated.

## Sources and reading order

Read both supplied text extractions before examining the figure/table images:

- `/private/tmp/claude-501/dacko/pdftotext-layout.txt` (line numbers below).
- `/private/tmp/claude-501/dacko/ta-dacko-conradt-low-p-transpiration.md`
  (MinerU; equations (5)–(9) and Table 2 agree with the PDF).
- `/private/tmp/claude-501/dacko/ta-dacko-conradt-low-p-transpiration.pdf`.

The supplied PDF has eight pages: printed pp. 205–212 correspond to PDF pages
1–8. Thus Figure 2 is PDF page 4, Table 2 is page 5, and Table 3 is page 6.
Existing extract `pdf_page_index` values are retained as requested; the printed
page numbers below identify the evidence unambiguously.

## Quoted evidence and methods

Printed p. 206, Experimental (layout lines 78–86):

> The pre-melted glass was put in a small platinum shuttle (100x10x10 mm³).

Printed p. 207 (layout lines 112–120) identifies the measured quantity as
“the loss of weight of the alkali borosilicate glass melt under different
temperatures, pressures and exposition times”. This describes glass-melt weight
loss, not a stated sample-mass normalization. The paper does not give an initial
sample mass or a weighing/tare protocol here. The earlier 500 ml platinum crucible
is for preparing the glass, not a stated test-sample volume.

Printed p. 207 (layout lines 126–148): temperatures 1400–1600 °C, pressure
0.2 × 10⁵ to 1 × 10⁵ Pa, and “the exposition time varied from 0.5 to 2 h”.
Table 1 explicitly labels `t [h]` and `Δq [mg/cm²]`. The prose says:

> Table 1 shows the result of the loss of weight Δq in mg/cm² depending on
> temperature T, internal pressure P and the exposition time t at 60 % relative
> humidity (referred to room temperature).

Printed p. 208 (layout lines 161–165):

> In the calculation for Δq, the loss of weight was referred to the surface of the
> glass melt in the platinum shuttle.

Printed p. 209, equation (5) and symbol key (layout lines 229–238), read from the
PDF and cross-checked against MinerU:

```text
r_i = β_i · P_i · M_i / (R · T)                         (5)
β_i – the mass transfer coefficient of i species
M_i – the molar mass
P_i – the partial vapour pressure
T – the absolute temperature
R – the gas constant
```

Printed p. 209 (layout lines 241–249):

> The calculated evaporation rate as defined in equation (5) was calculated for
> our experimental model on the basis of the technological values shown in table 3
> [4].

The caption is exactly “Comparison of the calculated evaporation rate and
experimental results”. Table 2 labels `r_calc.` and `r_exp.`, the pressures
`1.0 bar`, `0.4 bar`, `0.2 bar`, and the columns `1400°C`, `1500°C`, `1600°C`.
There is **no rate unit, scale multiplier, or exposure time** in its caption,
headers, row labels, or surrounding text. The PDF confirms that `r_exp.` at
1.0 bar is 16, 9, 4 in that temperature order; these are not OCR-reversed columns.

Printed p. 210, Table 3, caption “Technological values for the mass transfer”
(layout lines 264–284; equations verified in the PDF):

```text
β_i = Sh_i · D_i / L                                  (6)
Re = v · L · ρ / η                                    (7)
Sc = η / (D · ρ)                                      (8)
Sh = 1.615 · (Re · Sc · d/L)^(1/3)                     (9)
```

The key defines “Sh – Sherwood number”, “D – the diffusion coefficient in the
atmosphere”, “L – the characteristic length of the surface”, “v – velocity”,
“ρ – density”, “η – viscosity”, and “d – the tube diameter”. These are formulas
and symbol definitions, **not numerical technological values with units**.
Reference [4] is *VDI-Wärmeatlas*, 8th edition, Springer, Berlin 1997.

## Dimensional analysis: a flux, but no published numerical scale

The symbol meanings imply the following dimensions. The SI units in this
derivation are a dimensional choice, **not units quoted for Table 2**: the paper
does not state units for β, D, M, or R beside equations (5)–(9).

```text
[Sh] = [Re] = [Sc] = 1
[D] = length²/time, [L] = length
[β] = [Sh D/L] = length/time                       (SI: m/s)
[P/(RT)] = amount/length³                          (SI: mol/m³)
[M] = mass/amount                                 (SI: kg/mol)
[r_i] = [β P M/(RT)] = mass/(length² time)         (SI: kg/(m² s))
```

Using Pa for P, Pa·m³/(mol·K) for R, kelvin for T, and kg/mol for M produces
kg/(m²·s). Using g/mol instead changes the numerical scale by 1000. Other area
and time units change it further. Equation (5) contains no sample area, so it
does not by itself produce kg/s. A grounded flux unit **and** an applicable area
would be needed to convert a flux to the canonical total mass rate. The shuttle
dimensions do not establish a Table-2-specific exposed area. Table 5 gives
different measured areas (9.543–14.277 cm²) for its four 1 h chemical-analysis
conditions; the paper does not assign these to the nine Table 2 runs.

## Figure 2 and numerical cross-check

Read the supplied Figure 2 image on the current GPT-6 Codex worker, after both
text passes, checking visible ticks rather than accepting MinerU's generated
coordinate table. Exact axis labels (printed p. 208):

- Vertical: **“weight loss in mg/cm²”**; labeled ticks 0, 3, 6, 9, 12.
- Horizontal: **“time in min”**; labeled ticks 0, 30, 60, 90, 120.

The upper family is labeled `1600°C`, the lower `1400°C`; pressure labels are
`0.2 bar`, `0.4 bar`, and `1 bar`. The caption calls it “Weight loss per surface
area in dependence on time”. At 120 min the 1600 °C, 0.2 bar points are about
13 mg/cm², agreeing with Table 1's 13.03781 and 13.11232 mg/cm². This calibrates
the axes as cumulative areal loss, not the Table 2 rate. No other figure plots
rates: Figure 1 is the apparatus schematic.

An independent calculation using the printed Table 1 numbers illustrates why
assigning the Figure 2 derivative unit to Table 2 is unsafe. Take the mean of
the two printed replicates at each time (the replicates are not assigned paired
run IDs), and calculate a 1–2 h secant at 1 bar:

| Temperature | Mean loss at 1 h (mg/cm²) | Mean loss at 2 h (mg/cm²) | Secant (mg/cm²/h) | Table 2 r_exp. |
|---|---:|---:|---:|---:|
| 1400 °C | 1.40794 | 3.203235 | 1.795295 | 16 |
| 1600 °C | 6.17173 | 9.79329 | 3.62156 | 4 |

The secants are 0.02992158 and 0.06035933 mg/cm²/min. Ratios of secant to
Table 2 raw number in mg/cm²/h are 0.1122059375 and 0.90539, differing by about
8.07. The temperature trends oppose each other, so a single scale factor does
not reconcile these comparisons. These secants are a consistency probe, not a
claim that Table 2 uses this interval or that its columns should be corrected.
Table 2 does not identify the interval used.

Table 2's calculated/experimental ratios range from 22/20 to 8/5. Agreement within
such ratios cannot identify a dimensional unit: multiplying both columns by
any common factor preserves the ratios. There is no independent stated mass
loss, time, and area linked to a Table 2 entry that closes an absolute check.
Equations (2)–(4) also omit the numerical unit convention for r; fitting those
expressions cannot substitute for a published unit label.

## Green-base treatment and score consequence

Read-only `git show 5fd6fe8d3:...` inspection, without regenerating a store:

- The green `extracts-v2/ta-dacko-conradt-low-p-transpiration.yaml` stores all 18
  raw integers as `value.kind: point`, unchanged. The nine experimental rows
  are `measured_direct` with defaulted `admission.status: admitted`.
- At that commit, `simulator/battery/migrate.py::_selection_from_named_field`
  passes these amounts to `_convert_compilation_amount`; the latter returns
  `(amount, "as_published")` for this non-thermochemical quantity even with the
  ungrounded unit string. It supplies no conversion or source-rate-unit proof.
- `simulator/battery/enums.py` at the green commit declares
  `Quantity.MASS_LOSS_RATE: "kg_per_s"`. Thus 16 was carried as a canonical
  16 kg/s point (and 32 as 32 kg/s), rather than a published calibrated rate.
  The row's unrelated pressure derivation with `output_unit: Pa` is not rate
  provenance.

This was an ungrounded identity treatment, not evidence of an author-published
kg/s unit. The old residuals cannot be defended. The exact error factor remains
**unknown**, since neither the Table 2 scale nor its run-specific area has been
grounded. Do not report a guessed factor. The honest b-985 offer loses nine
measured-direct residuals from alkali_shuttle and the headline; the nine author
calculations were not measured-direct residuals. No numerical headline or rail
score delta was computed in this task.

## Observation node IDs and validation

Every ID below has the full prefix
`ta-dacko-conradt-low-p-transpiration::` in the typed store:

```text
dacko_2004_table2_rate_calc_1400c_1bar
dacko_2004_table2_rate_calc_1500c_1bar
dacko_2004_table2_rate_calc_1600c_1bar
dacko_2004_table2_rate_calc_1400c_0p4bar
dacko_2004_table2_rate_calc_1500c_0p4bar
dacko_2004_table2_rate_calc_1600c_0p4bar
dacko_2004_table2_rate_calc_1400c_0p2bar
dacko_2004_table2_rate_calc_1500c_0p2bar
dacko_2004_table2_rate_calc_1600c_0p2bar
dacko_2004_table2_rate_exp_1400c_1bar
dacko_2004_table2_rate_exp_1500c_1bar
dacko_2004_table2_rate_exp_1600c_1bar
dacko_2004_table2_rate_exp_1400c_0p4bar
dacko_2004_table2_rate_exp_1500c_0p4bar
dacko_2004_table2_rate_exp_1600c_0p4bar
dacko_2004_table2_rate_exp_1400c_0p2bar
dacko_2004_table2_rate_exp_1500c_0p2bar
dacko_2004_table2_rate_exp_1600c_0p2bar
```

Validation is limited to YAML parsing, an exact semantic comparison with HEAD
after removing the two added refusal fields from each of the 18 rows, a direct
call to the existing admission mapper, the independent Table 1 arithmetic above,
and `git diff --check`. No pytest,
migration, or whole-store scoring was run; there are no new pytest node IDs.
The existing `admission_for("typed_refusal", refusal_reason=...)` branch maps
this established extract pattern to rejected admission with that exact reason.
The existing b-985 converter continues to make the unsupported rate unavailable.
The derived extracts-v2 file is deliberately unchanged, per the no-store-files
instruction; the controller must regenerate it when integrating b-985.

Self-review: no physics implementation, new conversion, duplicated logic,
imports, dependencies, public API, schema, or guard changes. Only the source
refusal metadata and this evidence report change.

Independent read-only review: PASS after correcting the calculated/experimental
ratio minimum to 22/20. The reviewer checked the PDF evidence, dimensions,
arithmetic, green-base treatment, refusal mapping, and semantic preservation.

## Certification item: recover the missing Table 2 convention

Fetch Dacko's thesis, *Theoretische und experimentelle Untersuchung der
Verdampfungsmechanismen bei reduziertem Druck am Beispiel glasbildender
Borosilicatschmelzen* (reported 2007), candidate file
<https://publications.rwth-aachen.de/record/50284/files/Dacko_Dawid.pdf>.
The legacy candidate is
<http://darwin.bth.rwth-aachen.de/opus3/volltexte/2008/2492/pdf/Dacko_Dawid.pdf>.
These were located during discovery; the thesis contents were **not recovered**:
the web tool returned “not accessible via this tool” for the RWTH record and
file and the legacy HTTPS file; direct retrieval failed DNS resolution. The
title/date and candidate URL are discovery leads, not unit evidence.

Read the thesis text first, then the corresponding table/axes, looking for the
same 18 numbers, an explicit rate unit/multiplier, the applicable surface area
and interval, and an explanation of the opposed temperature trends. Also seek
any companion by Dacko and Conradt that reproduces this comparison. The supplied
paper's reference list contains no Dacko-and-Conradt companion: [3] is Conradt's
2001 thermochemical model and [10] is Conradt and Scholze (1986). A web search did
not yield a separate companion with a matching quoted rate table. Neither a
general convention from VDI-Wärmeatlas nor another author's flux units can
certify these integers. Restore the rows only after an exact quoted label or
definition and an independent numerical cross-check establish the scale.
