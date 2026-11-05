# Tachibana and Takigawa (2014) extraction audit

Source: Tachibana S. and Takigawa A. (2014), Experimental Studies on Dust
Formation in Space, PoS(LCDU2013)046, 10 pages, doi 10.22323/1.207.0046.
source_id `tachibana-takigawa-2014-forsterite-condensation`. PDF sha256
`bb0b1d64bc106cc48dedffa9c3fe6675126ad66b14da3df1435b079e3e454529`.
The PDF is born-digital. Numbers were taken from
`pdftotext-layout.txt` and checked against the page renders. Published page
equals PDF page. `pdf_page_index` is that page minus one.

The abstract is quantitative. It prints metallic-iron αc≈αe≈1, forsterite
αe=0.1–0.02, hydrogen pressure 5.6 Pa, substrate temperatures 1275 and 1160 K,
and forsterite αc=0.09–0.006.

## What was recorded

Nineteen observations and nineteen context rows.

This paper's forsterite condensation campaign (sections 4.1–4.4 and the
summary):

- αc at 1275 K and pH2 5.6 Pa, printed 0.006–0.09 in section 4.4 and
  0.09–0.006 in the abstract and the summary. Typed refusal. The campaign
  total pressure ~5.5 Pa and H2O/H2 0.015 from section 4.1 stay on this row
  and are not collapsed into 5.6 Pa.
- Supersaturation at 1320, 1275, and 1160 K: 1–5, 7–190, and 800–8000, with
  supercooling 0–50, 40–150, and 160–230 K. The 1320 K row records no
  condensate and the printed clause S≈1. Typed refusals. pH2 5.6 Pa is not
  attached to these rows.
- Figure 10 printed S labels, not pixel reads: runs 12–16 are 13-62, 12-56,
  14-66, 18-89, and 39-188, all at 1275 K and pH2 5.6 Pa. Typed refusals.
- Film thickness at 1160 K, ~30–150 nm, with the 93 h FIB duration from
  section 4.1. Typed refusal.
- Grain size at 1160 K, "sub-micron to 1 µm", with duration left unknown
  because the sentence says "irrespective of experimental duration". At
  1275 K for >40 h, "several micron-sized flat regions"; the Figure 8 right
  panel is 115 h and is stored as that micrograph's duration, not as the
  bound. At S of 7–190, "several to ten µm". Typed refusals.
- Apparatus, instruments, SiO/H2 ~0.7–3% of solar, ~15 times solar H2O/H2,
  duration 6 to 237 h, tube ~300 mm by 38 mm, mean free path less than 1 mm,
  and the silica-wall sink sentence are context, not copied onto every row.
- Stoichiometric forsterite is qualitative. There is no composition table, so
  `condensate_composition` was not filled. The 10 µm absorption, EBSD, and
  SAED statements are context.

Quoted evaporation coefficients that the schema can type:

- Forsterite αe ranges from 0.1-0.02 (page 5), 0.1–0.02 (abstract), and
  0.1 to 0.02 (summary). Stored as `evaporation_coefficient_alpha` with
  `alpha_range` low then high, 0.02 then 0.1. Temperature and hydrogen
  pressure on this row are unknown. The studied range 2073–1153 K is a
  separate sentence and is not the bounds of this coefficient. The summary
  clause "depending on temperature, hydrogen gas pressure, and
  crystallographic orientations" is stored on this row as printed text.
- Figure 10's dashed line is an evaporation flux at 1275 K drawn with αe of
  0.03, citation [8]. Section 4.4 says that flux, at the substrate
  temperature, is compared in Figure 11, citation [10]. Those are two
  context rows. 1275 K is the flux temperature. Neither sentence states αe
  at 1275 K, so that coefficient is not an observation and is not admitted
  at identity temperature 1275 K.
- αe of 0.03 at 1426 K and pH2 of 2 Pa from the Figure 11 caption. This is
  the admitted point. It does not carry citation [8] or [10].
- Metallic iron αe in vacuum (pFe=0) "close to unity". No numeric 1 was
  invented. Value unavailable, quantity `evaporation_coefficient_alpha`.

Quoted condensation statements that the schema cannot type:

- Metallic iron αc in vacuum "close to unity" (summary) and "unity" at 1235 K
  and at 1340 K, each with S larger than 10. The Figure 4 caption prints
  1337 K for the open symbols. The body prints 1340 K. Both are kept. The
  identity temperature of the 1340 K row is 1340 K; 1337 K is a separate
  point condition.

Eleven figure records, in document order 5, 6, 7, 8, 9, 10, 11, 1, 2, 3, 4.
Each has axes, units, and series as printed in the caption or as tick labels
in the text layer. None has a `figure_digitised` point.

## Figures not digitised

No figure was digitised. A tick calibration that locked both axes of Figure
10 was not completed: a red-pixel search (r>180, g<80, b<80) on the
400 dpi page returned no marker centroids, and the gray frame was not a
boxed axis that could be calibrated in log space. Symbol positions described
by eye were not transcribed. Figure 11 range bars were left unread for the
same reason. The only numeric αc range in the text is 0.006–0.09.

Figure 5 and Figure 6 axis titles and caption numbers are recorded (vacuum
plotted at 10^-4 Pa; panels 1657, 1535, 1327, and 1153 °C; comparison curves
αe 0.1 and 0.01; MAC at 100 K; LDE temperature clauses >1600 K and <1600 K).
Decade and MAC tick labels sit in the figure artwork and are not in the text
layer. They were not copied. Figure 3, Figure 4, Figure 9, Figure 10, and
Figure 11 tick labels are in the text layer and are recorded as labels only.

Text inside the reproduced Astrophysical Journal figures (Tachibana et al.
2011, Takigawa et al. 2009, Takigawa and Tachibana 2012) was not copied as a
measurement of this paper. That includes anisotropy inequalities, IR peak
lists, thickness-versus-distance numbers, the mean k of −0.54 ± 0.15, and
the 2011 "supersaturation ~10 to ~35" sentences. Grant numbers 20684025,
25287140, 09J02084, and 12J02495 were not recorded. Introductory mineral
names were not recorded as measurements.

## Quantity gap

`Quantity` has `evaporation_coefficient_alpha` and `wall_deposit_mass`.
It has no condensation coefficient, no supersaturation, no molar flux, no
film thickness, and no grain size. `wall_deposit_mass` was not used: the
paper prints no deposited mass in kg. Weight-gain fluxes in Figure 10 were
not digitised, so no flux number was invented. `condensate_composition` was
not filled.

Rows that name those missing terms keep the printed words as the quantity
string. The migrator therefore stores quantity unknown with reason
`unsupported quantity '<printed term>'` and admission rejected
(`typed_refusal`). No schema or code change was made.

## Isolated migration

`Migrator(root=REPO_ROOT, index={}, aliases={})._migrate_extract` then
`finalize`, this extract only:

- work `10.22323/1.207.0046`
- 3 experiments
- 19 observations
- 19 context rows on that work (not scored)
- 0 hard issues
- 0 registry issues
- 26 `identity_incomplete` advisories
- 52 queue entries: unsupported quantity, unknown method token, or no mapped
  value. These are the typed refusals and the review furnaces. No geometry
  was invented to clear them.

Admission of the 19 observations: 16 rejected, 2 admitted, 1 pending.
Admitted: the forsterite αe interval 0.02–0.1 (temperature unknown) and the
Figure 11 point αe=0.03 at 1426 K and pH2 2 Pa. No
`evaporation_coefficient_alpha` point has identity temperature 1275 K.
Pending: metallic-iron αe "close to unity", value unavailable. Every
observation has a `point_conditions.temperature_K` with a locator. Where the
YAML prints `T_K`, the identity temperature equals that kelvin value.

The derived battery store was not regenerated.

## Tests

`validate_all` on this extract, with fidelity matching, returned 0 errors.

Local fallback after the remote runner refused (`shared box policy differs`
on request `56aca213c9574f48ab8202841ea60ad4`; it did not name a Studio and
did not run the tests). `.venv` python, `PYTHONPATH` this seat, `-n 0`:

- `tests/test_tachibana_takigawa_2014_forsterite_condensation_extract.py`:
  5 passed in 5.37 s. The validator accepts the extract and the fidelity
  pins match. Mutating `alpha_low_as_printed` from 0.006 to 0.007 makes
  `check_fidelity_sample_matches` fail. Mutating `T_K` 1275 to 1276 on
  `tt2014_fo_alpha_c_1275` changes the migrated identity temperature from
  1275 to 1276. The isolated migration has 0 hard issues. No stored
  evaporation-coefficient point has identity temperature 1275 K. The
  Figure 11 point stays admitted at 1426 K and pH2 2 Pa. Citations [8] and
  [10] are on separate context rows.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed in 83.97 s.

Whole-store scoring was not run.
