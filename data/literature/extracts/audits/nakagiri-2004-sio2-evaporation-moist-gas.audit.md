# Nakagiri and Kurokawa (2004) extraction audit

Source: Nakagiri, T. and Kurokawa, K. (2004), “Evaporation Behavior of SiO2 in N2-O2-H2O Atmospheres,” *Materials Transactions* 45(2), 334-337. The born-digital text and MinerU output are under the corpus source id `nakagiri-2004-sio2-evaporation-moist-gas`.

## Table inventory and cell audit

The MinerU conversion was rerun for this seat. It returned four table candidates: two duplicate reads of printed Table 1 on PDF page 3 and two duplicate reads of printed Table 2 on PDF page 4. Table 2 reports crystallization rates, outside this evaporation extraction. QA flagged all four candidate tables for LaTeX and numeric mismatches; the two Table 1 candidates each had 0.692 numeric agreement. Table 1 was checked against the page render because the QA flags made the two machine reads insufficient on their own.

Printed Table 1 has three body rows, five columns, and 15 numeric cells: three temperatures and 12 rates. Each cell below was read on the PDF page 3 render (published page 336), method (b). The value columns in the committed compare output independently match all 12 MinerU values. The temperature cells are not values compared by that tool and were checked directly against the render.

| MinerU CSV row | Column 1, Temp. (K) | Column 2, N2-46.7%H2O | Column 3, N2-20%O2-46.7%H2O | Column 4, Si(OH)4 | Column 5, printed Si(OH)4 header |
|---:|---:|---:|---:|---:|---:|
| 3 | 1473 (b) | 1.87 × 10^-9 (b) | 1.10 × 10^-9 (b) | 4.37 × 10^-10 (b) | 5.16 × 10^-10 (b) |
| 4 | 1573 (b) | 2.24 × 10^-9 (b) | 1.87 × 10^-9 (b) | 6.30 × 10^-10 (b) | 1.94 × 10^-9 (b) |
| 5 | 1673 (b) | 2.68 × 10^-9 (b) | 1.22 × 10^-8 (b) | 7.57 × 10^-10 (b) | 5.84 × 10^-9 (b) |

The last table header is printed as Si(OH)4 in the render and in both machine reads. The caption identifies the comparison species as Si(OH)4 and SiO(OH)2, and the page 336 discussion assigns the second calculated series to SiO(OH)2. The extract preserves that attribution for the column-5 values and records the duplicated printed header here.

`nakagiri-2004-sio2-evaporation-moist-gas.mineru-compare.csv` is the table-tool compare output for the emitted 12-cell fragment: matched=12, mismatch=0, no-match=0. These rates are kept in context rather than emitted as scored observations because kg m^-2 s^-1 is an area-specific mass flux; the existing `evaporation_rate` quantity is a total rate in kg s^-1. `mass_loss` represents a fraction, not this flux. The schema gap is recorded and no unit conversion or substitute observable is introduced.

The compare command was also run on the final context-only extract. It returned total=0 because the comparator reads observation values and deliberately does not compare context payloads; the emitted-fragment compare and the focused extract test cover the preserved context values.

## Conditions and scope

The six measured Table 1 rates cover 1473, 1573, and 1673 K in each printed high-water gas mixture: `N2-46.7%H2O` and `(N2-20%O2)-46.7%H2O`. The method states 19.6 and 46.7 vol% water vapor, water-bath temperatures of 333 and 353 K respectively, and a gas flow of 8.33 × 10^-6 m^3/s at room temperature. It reports a hot linear velocity of 0.06-0.07 m/s over 1373-1673 K. These numeric prose conditions are present in both pdftotext and MinerU machine text.

The methods section gives the overall test temperature range as 1373-1673 K and exposure-time range as 3.6-176.4 ks. It does not assign one exposure duration to each Table 1 rate; the rates are slopes from Figure 2 time series. The plotted mass-change values in Figures 1 and 2, and the plotted values in Figures 3 and 4, are listed as figure-read candidates and were not digitized.

The figure-read candidate context preserves the ten printed Figure 1/2 condition series: four gas mixtures at 1473 K in Figure 1 and the two 46.7 vol% water mixtures at 1473, 1573, and 1673 K in Figure 2. The temperatures and gas compositions were cross-read in pdftotext and MinerU prose/captions (method (a)); MinerU's generated `<details>` chart tables were ignored. Their shared stated flow and unreported total pressure are recorded alongside those conditions. For the 19.6 vol% water series the paper says mass loss can be ignored; this qualitative statement is retained without converting it to a numeric zero.

No experimental total pressure is stated. The background discussion of atmospheric-pressure water-vapor chemistry is not an experiment condition. Every measured rate row therefore retains total pressure as unknown (`not_published`); no 1 atm, 1 bar, or inferred fO2 is recorded. The modeled comparison values use the paper's stated 5 × 10^4 Pa water-vapor partial pressure as an input, not as total experimental pressure. Their source inputs are not stored, so they are attributed to Hashimoto [4] and Krikorian [6] rather than classified as model-derived.

## Isolated migration and store expectation

The real migrator produced 0 observations, 0 hard issues, and 0 unavailable values. It carried 2 context rows under 1 work and emitted one expected `extract yielded no observations` queue entry: the paper's numerical rate cells are explicitly typed-refused for the schema gap. Expected store delta is +1 work, +2 context rows, +0 observations, and no regenerated store in this commit.

## Verification

- New focused test: 1 passed on the unmodified extract; it pins four Table 1 rates, the three-row count, published page 336, original flow units, unknown total pressures, the ten Figure 1/2 condition series, and migration counts.
- Mutation check: changing the first pinned `1.87 × 10^-9` value to `9.87 × 10^-9` made the focused test fail on that exact assertion. The extract was restored byte-for-byte afterward.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/test_literature_extracts.py`: attempted with `-n 0`; 158 passed before interruption at 1074.67 seconds. It reported one unrelated fixture assertion failure in `test_docs_private_reference_in_nested_prose_is_refused_but_path_is_allowed` and two 300-second timeouts while repository-wide validation/coverage parsed YAML. The full file did not complete.
