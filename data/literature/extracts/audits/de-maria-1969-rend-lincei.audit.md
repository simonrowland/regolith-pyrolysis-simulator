# De Maria & Piacente (1969) extraction audit

## Source reads and table reconciliation

The transcription starts from the MinerU markdown and table blocks for the
available chunk, then uses `pdftotext-layout.txt`. The table draft/QA tools
were run against those files. QA flagged both Table I blocks: PDF page 7 had
0.365 numeric agreement and page 8 had 0.000; the source grid was therefore
checked against page renders cell by cell. The invented `<details>` figure
table was ignored.

Printed Table I contains 17 temperature rows and 15 mass-to-charge columns
(m/e 23, 24, 25, 26, 27, 39, 40, 44, 45, 46, 50, 51, 52, 56, 78). The two
page blocks supply 133 populated current cells: 125 exact current readings
and 8 printed `<` limits. The eight limits remain separate typed refusals;
they are not treated as exact readings. The row and column assignments for
all 17 temperatures, 15 mass headers, and 133 current cells were checked on
the renders of PDF pages 7–8 (published pages 529–530). Species/ion
assignments were checked against the printed mass headings and the Results
text. `cell-audit.csv` records those checks and the prose numeric cells. Its
198 records comprise 133 Table I current cells, 34 temperature cells (17 on
each page block), 15 mass headers, 11 Table II appearance-potential cells,
one separate SiO appearance-potential statement, one mass-80/mass-78 ratio,
and three partial-pressure estimates.

Table I continues on PDF page 8 under the printed caption “Segue: Table I”.
The compare tool does not recognize that continuation caption, so the extract
uses printed locator `table: I`, verified from the render. Table II is on PDF
page 12 (published page 534); no MinerU table block for it was supplied, so
its 11 mass-to-charge/appearance-potential pairs were transcribed from the
machine text and checked against the page render. The separate m/e 44 SiO
appearance potential is in prose on that same page.

The committed `mineru-compare.txt` is the final row-aware compare output:
166 records, 0 matched, 3 no-match, 0 mismatch, 140 locator errors, and 23
unverifiable. The three no-match rows are the prose pressure estimates, which
are not MinerU table cells. Locator errors are the page-8 continuation-label
limitation (including repeated point/condition paths); the unverifiable
Table II entries lack MinerU caption evidence. These statuses are not being
used as a substitute for review: the Table I cells, Table II cells, prose
appearance potential, ratio, and pressure estimates are individually
resolved in the render audit. On PDF page 13, the OCR-rendered temperature
digits for the Na/K and Fe pressure estimates were checked against the render
and corrected to 1200 K and 2000 K.

## Extracted results and conditions

- Table I stores all 15 printed mass channels across 17 temperatures. The
  current readings are authors' direct measurements (`measured_direct`),
  with the printed 70 eV electron energy and the surface-ionization and
  photo-effect corrections recorded in the extract context. The Na+ surface
  contribution at 1600 K (about 15%) and the 5–10% contribution for m/e
  50–52 are retained as printed.
- All 11 Table II appearance potentials and the separate prose SiO value
  (12.0 ± 0.8 eV) are preserved as printed, with their uncertainties and
  source locators. Curve points in Figures 2–4 are not digitized. These
  appearance-potential rows are typed refusals: `OBSERVATION_TYPES` has no
  appearance-potential observation type, `Quantity` and `QUANTITY_ALIASES`
  have no exact appearance-potential quantity, and `rate_series` would infer
  `mass_loss_rate` through `TYPE_QUANTITY`. The rows therefore use the
  accepted `rate_series` carrier only for the refusal, with a descriptive
  non-token quantity. Similar printed results in `kems-133-costa-2017` and
  `shornikov-2000-silica-gas-equilibria` do not supply an exact quantity
  token. The focused test pins the exact set of 12 quantity-unknown IDs.
- The three printed partial-pressure estimates are Na, order 10^-5 atm at
  1200 K; K, order 10^-4 atm at 1200 K; and Fe, 5·10^-5 atm at 2000 K.
  They are attributed to the present study but follow reference [6], and the
  intermediate current/calibration inputs are not printed here, so they are
  `quoted_attributed` under d-110. No SiO partial pressure is printed. The
  Na/K order-of-magnitude qualifiers remain approximate; migration converts
  the printed atm values to 1.01325 Pa, 10.1325 Pa, and 5.06625 Pa,
  respectively, without hand-converting the extract.
- The prose average I+80/I+78 ratio is 10.2 across 1550, 1720, and 1840 K;
  individual readings are not printed. The comparison value 6.3 is the
  stated theoretical K abundance. The average is `quoted_attributed` because
  the contributing readings are unavailable in the paper.
- Table I and pressure-point temperatures use `T_K` and located
  `point_conditions.temperature_K`. The extract preserves the printed
  temperature context, including the 300–2330 K study range, 2400 K maximum
  heating temperature, 1800 K cell outgassing, 300–500 K and 500–1800 K
  stages, 1200–1300 K melting interval, 1300–1900 K and approximately
  2000–2300 K vaporization stages, above 2300 K residue stage, 1400–1800 K
  mass-40 observation, approximately 1800 K SiO appearance, and the 1550,
  1720, and 1840 K ratio measurements. Every printed Table I point has its
  own temperature locator. No measurement temperature is assigned to the
  appearance potentials where the paper gives none.
- The printed container is a molybdenum effusion cell (1.2 cm high, 1 cm
  external diameter, 1 mm knife-edge orifice), previously outgassed at
  1800 K. The sample is crushed andesinic basalt from Nuraghe Silvanis; the
  paper refers to reference 10, Table IV, sample 2 for composition, without
  printing that composition here.
- Background pressure is retained as apparatus context: below 10^-6 Torr
  in the Knudsen-cell region and 10^-7 Torr in the analyzer. These are not
  sample total-pressure conditions. Total pressure and composition remain
  unknown on the observation conditions and identities. The source gives no
  experimental fO2. The observed oxygen/molybdenum reaction statement is
  located on PDF page 13; the hypothetical neutral-condition oxygen statement
  and the “most perfect neutral conditions possible” conclusion are on PDF
  page 14. No reducing fO2 is assigned from the molybdenum container.
- The paper's activities use Nesmeyanov vapor-pressure data [17]. Since
  those inputs are not stored, the reported Na, K, and Fe activity values
  remain context rather than a derived activity observation. Figure 1 and
  Figures 2–4 are listed as figure-read candidates only; no figure-only
  values were digitized.

## Migration and schema checks

The isolated real-migrator check produced 149 observations, 0 hard issues,
and 20 unavailable values (8 censored Table I currents and 12 appearance
potentials). The Table I censored rows keep `Quantity.ION_INTENSITY` but
migrate to unavailable/rejected rather than exact points: `ValueKind.BOUND`
exists, while `QUANTITY_SOURCE_FIELDS` and `select_declared_source` do not
decode a censored ion-intensity point. The exact printed `<` cell is retained
in the typed refusal. All phases map to gas; no phase-unknown observations
remain.

The focused test checks all 149 migrated observations: 136 rows with printed
temperatures carry the same value through `point_conditions.temperature_K`
and the identity, while 13 rows without a single printed temperature retain
an unknown source temperature and no identity value. It also verifies
unknown (not inferred) total pressure, fO2, and composition on every source
condition, and prints the identity axes for three rows. The ion-intensity
profile marks pressure and fO2 as not applicable in identity; the source
conditions themselves remain explicitly unknown. No unprinted room
temperature, ambient pressure, assumed vacuum, or reducing fO2 is added.
Store regeneration was not run; expected store delta is zero.
