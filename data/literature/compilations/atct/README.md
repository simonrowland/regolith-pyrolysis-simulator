# Active Thermochemical Tables — ANL version 1.222

Complete transcription of every species row in the supplied ANL bulk HTML
snapshot, retrieved 2026-09-06. The page identifies network version 1.222 as
of 07/26/2026. Citation, DOI, official retrieval URL and DOE Office of Science
Public Reusable Data Resource basis are preserved in `source/sidecar.yaml`
and `manifest.yaml`. Both supplied source files are copied byte-for-byte;
the manifest records their SHA-256 digests. No supplementary species pages
or image files were fetched.

The heading and sidecar say **3,444 species**, but the HTML contains **3,442
species rows**, numbered 1–3442. All 3,442 are ingested, including ions,
isotopes, condensed, aqueous and adsorbed species. The count discrepancy is
an unresolved corpus ambiguity; absent rows are not fabricated.

## Native format and ambiguities

`records/` contains one JSON file per source row. JSON preserves decimal
tokens as strings, including trailing zeroes, `exact`, empty fields and the
printed `±` uncertainty. The two formation-enthalpy columns are 0 K and
298.15 K; the single published uncertainty column remains shared and is
not duplicated or assigned a different meaning. The page describes its
uncertainties as estimated 95% confidence limits; printed ± 0.000 means
less than ± 0.0005 kJ/mol. No unit conversion or rounding is performed.
The printed units are `kJ/mol`, except the 20 exact reference-state rows
whose unit cells are empty. 590 rows have empty 0 K values. Empty cells
remain empty, never zero. Relative molecular mass text is also retained.

Each record retains the ATcT ID, CAS token, species name, preferred formula,
full parenthesized state/isomer label, footnote flags, and a byte-range plus
HTML row-ID locator into the unchanged source. `phase` retains the entire
published state label: `cr,l` is neither split nor merged into another row.
Formula/isomer labels are not canonical chemical identities. Qualified
state labels and named isomers are listed as unresolved mapping ambiguities.
The malformed formula-cell attribute in every source row is recorded;
formula text is read from its intact button. Source-row superscripts and
footnote links are retained if present (none occur in this snapshot).

## Loading and permitted use

Run `python3 -m tools.harvest_atct_compilation /path/to/raw/atct-anl-1.222`
to reproduce the ingest from the supplied corpus. `load_records()` in that
module loads the manifest-indexed native files; `numeric_token()` returns
exact `Decimal` values or `None` for absent/`exact` tokens. An `exact`
uncertainty is textual metadata, not a missing enthalpy.

This follows JANAF's current tools-based compilation consumption path.
**ATcT supplies formation enthalpies only, with no Cp/S functions.** The
engine may use it to check or anchor JANAF/Glenn ΔfH values, never as a
Gibbs table. These are assessed reference inputs, never measured validation
observations; they produce no battery scoring rows. No runtime source
selection is changed by this ingest.

`python3 -m pytest tests/test_atct_compilation.py -q` checks every record
against original source tokens and byte locators, hashes, count gaps, state
retention and feedstock-element coverage. Coverage uses all composition
keys in `data/feedstocks.yaml`, including trace inventory and named Stage-0
formula templates. Species counts use formula element tokens; D contributes
to H coverage only, without altering the stored formula. Missing elements
remain explicit zeroes in the manifest.
