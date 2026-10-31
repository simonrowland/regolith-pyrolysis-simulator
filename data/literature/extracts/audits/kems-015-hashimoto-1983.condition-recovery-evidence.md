# Hashimoto (1983) condition recovery evidence

## Isolated migration, green versus current tip

| Metric | Green | Current tip |
|---|---:|---:|
| Observations | 666 | 666 |
| Hard issues | 0 | 0 |
| Unavailable values | 16 | 16 |
| Queue entries | 110 | 110 |
| `residue_component_composition` observations | 648 | 648 |
| Table 3 run-linked `__run_` observations | 216 | 216 |
| Table 3 run-linked fO2 identity | `not_applicable / oxygen_unbuffered_vacuum` (216) | `not_applicable / oxygen_unbuffered_vacuum` (216) |
| Self-identity outcome on those run rows | EQUAL (216/216) | EQUAL (216/216) |

The paper states that the experiments were unbuffered with respect to P_O2 and gives vacuum as an example; it prints no numerical run fO2 or pO2 (PDF p. 17, published p. 127). The extract's closed `fO2_control.during_run: none` token is mapped by `simulator/battery/migrate.py::_emit_exploded_point` to `not_applicable / oxygen_unbuffered_vacuum`. This records the run oxygen axis as not applicable while the printed numerical fO2/pO2 remains absent. It is not a numeric zero. The 216 Table 3 run-linked observations have zero quantity-unknown and zero phase-unknown rows. The whole-extract oxygen identity census is 336 not-applicable / `oxygen_unbuffered_vacuum`, 312 unknown / `source has no located oxygen condition for this run`, and 18 absent, unchanged from green.

## Admitted C3 (2) observations

The five admitted component observations for sample C3 (2), run 17C3 (PDF p. 6, published p. 116), were emitted by the current isolated migration. Each has fO2 identity `not_applicable / oxygen_unbuffered_vacuum`, temperature 1973.15 K, and the printed run pressure (about 10^-4 Torr = 0.01333223684210526315789473684 Pa).

| Component | Printed residue | Current observation ID | PDF page index | fO2 identity |
|---|---:|---|---:|---|
| Al2O3 | 3.57 wt% | `kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::T=1973.15:row=c3-(2):h=0bc862a8ce7c` | 5 | not_applicable / `oxygen_unbuffered_vacuum` |
| CaO | 3.09 wt% | `kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::T=1973.15:row=c3-(2):h=9dae5937039e` | 5 | not_applicable / `oxygen_unbuffered_vacuum` |
| FeO | 27.92 wt% | `kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::T=1973.15:row=c3-(2):h=539d848a3929` | 5 | not_applicable / `oxygen_unbuffered_vacuum` |
| MgO | 26.32 wt% | `kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::T=1973.15:row=c3-(2):h=a0448824e1e8` | 5 | not_applicable / `oxygen_unbuffered_vacuum` |
| SiO2 | 39.09 wt% | `kems-015-hashimoto-1983::hashimoto_1983_table3_residue_composition_series_quoted__run_17c3-2::T=1973.15:row=c3-(2):h=2190d72e364f` | 5 | not_applicable / `oxygen_unbuffered_vacuum` |

## Score sample

The current-tip sample was generated through `simulator/battery/score.py::score_store` with OpenIMCC pin 66ec4a3 (source tree hash `308672269d6e6090023f4a86c4fc0d83c4c24688`). The call returned 120 scored references and 120 engine candidates in 2816 seconds. The ten admitted Table 3 rows were selected from the prior ten run-row anchors, with the component chosen by sorted formula where a run expands to multiple component observations. All ten engine executions were produced; each residual is `no_band` with refusal reason `decision_rule_missing` and detail `unparseable_printed_uncertainty` from the printed balance accuracy notice. The generated IDs and per-row results are recorded in `kems-015-hashimoto-1983.score-sample.csv`. No scoring thresholds or values were tuned.

## Expected store delta

No battery-store regeneration is committed. The isolated migration retains 666 observations, 648 point values, 16 unavailable values, 2 categorical values, and zero hard issues. Run-linked fO2 identities match green, including all 216 Table 3 `__run_` rows. The added point conditions change 216 full observation IDs, while prefix pairing retains all 666 observations and their values. Of 450 full IDs shared with green, 225 differ only by the corrected PDF page index (current index is one lower). No shared numeric values or fO2 identities change.
