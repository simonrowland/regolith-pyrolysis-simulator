# Neumann (1954) extraction audit

## Table 1, PDF page 2

The rendered page, MinerU table HTML, and pdftotext table were checked for all 12
rows and numeric cells. Numeric pairs below are printed alpha, delta; `—` means
the delta cell is blank. The table-row assignments and comma decimals agree
between the two machine reads and the render, except for the species label
recorded below.

| Row | Printed species | Alpha | Delta | Printed attribution | Cell confirmation |
|---:|---|---:|---:|---|---|
| 1 | Hg | 1.00 | — | Knudsen 1915 [1] | MinerU + pdftotext; render row/column check |
| 2 | Hg | 1.00 | — | Volmer and Estermann 1921 [2] | MinerU + pdftotext; render row/column check |
| 3 | K | 1 | — | Neumann, this work | MinerU + pdftotext; render row/column check |
| 4 | CCl4 | 1 | 1 | Alty 1931 [3], Prüger 1940 [4], Baranaev 1939 [5] | MinerU + pdftotext; render row/column check |
| 5 | C9H6 | 0.9 | 0.85 | Baranaev 1939 [5] | Render identifies row 5 as C9H6; pdftotext `CCI,` is an OCR error. It is distinct from row 4 CCl4. |
| 6 | CHCl3 | 0.16 | 0.54 | Baranaev 1939 [5] | MinerU + pdftotext; render row/column check |
| 7 | C2H5OH | 0.02 | 0.02 | Baranaev 1939 [5] | MinerU + pdftotext; render row/column check |
| 8 | C2H5OH | 0.024 | 0.02 | Bucka 1950 [6] | MinerU + pdftotext; render row/column check |
| 9 | CH3OH | 0.045 | 0.05 | Baranaev 1939 [5] | MinerU + pdftotext; render row/column check |
| 10 | C3H5(OH)3 | 0.05 | — | Wyllie 1949 [7] | MinerU + pdftotext; render row/column check |
| 11 | H2O | 0.04 | 0.04 | Alty and Mackay 1935 [3] | MinerU + pdftotext; render row/column check |
| 12 | H2O | 0.02 ± 0.04 | 0.04 | Prüger 1940 [4] | MinerU + pdftotext; render confirms ± and row/column assignment |

The row 5 `C9H6` formula is distinct from row 4 `CCl4`; the render resolves the
pdftotext OCR error and confirms `C9H6` as printed, without chemical reinterpretation.
Delta is retained as source context; the schema has no free-angular-ratio
quantity token. The K row is Neumann's own result, not an attribution to prior
work.

## Table 2, PDF page 13

MinerU ends at page 12. The pdftotext output had corrupted/lost exponents, so
every numeric cell in all five rows was checked against the p13 `pdftoppm`
render. Listed cell order is theta (°C), p (mm), 2a0 (cm), 2b0 (cm), t, A
(cm/sec), B (cm3/sec), l−2a0 (cm), alpha.

| Printed row | Render-confirmed cells |
|---:|---|
| 66.6 °C | 66.6; 1.20×10^-6; 0.15; 0.12; 200 Std; 2.55×10^-8; 1.6×10^-10; 0.55; 0.89 |
| 66.7 °C | 66.7; 1.21×10^-6; 0.12; 0.11; 211 Std; 2.7×10^-8; 0.7×10^-10; 0.18; 0.93 |
| 104.5 °C | 104.5; 2.66×10^-5; 0.12; 0.10; 180 min; 0.93×10^-6; 2.7×10^-9; 0.38; 1.01 |
| 111.0 °C | 111.0; 4.23×10^-5; 0.11; 0.10; 220 min; 1.45×10^-6; 2.7×10^-9; 0.29; 1.00 |
| 119.2 °C | 119.2; 7.4×10^-5; 0.12; 0.10; 190 min; 2.40×10^-6; 6.0×10^-9; 0.38; 0.95 |

The printed row count is five. `Std` is Stunden (hours). The five alpha values
are retained as printed and classified measured_reduced: the paper says A and B
are evaluated from equations (4)–(6), and alpha is calculated from those
measured droplet evaporation results. p is the saturation pressure calculated
from equation (11), not a measured chamber pressure. The paper describes the
runs as high vacuum and prints no chamber pressure. The p values remain in the
`p_mmHg_as_published` column as context and are not stored as total pressure.

## MinerU draft and compare

Used `tools/mineru_table_draft.py` (offer-21 line; run standalone).
The converter produced the p2 draft at
`tables/tables/neumann-1954-liquid-k-evaporation-coefficient/p002-t01.csv` with the
associated `index.yaml`. The p13 table is outside the MinerU chunk.

The required compare output was:

```text
species,observation_id,point_index,pdf_page,value,status,matched_cell,draft_candidate_count,path
compare: matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0
```

This staged compare version does not inspect alpha fields, so it emitted no
candidate points. That is recorded as no compare coverage, not as confirmation;
the p2 cells were compared machine-to-machine and checked against the page render,
and every p13 numeric cell was checked against its render.

## Isolated migration and remaining scope block

Real `Migrator._migrate_extract` on this extract emitted 17 observations (12
Table 1 rows and five Table 2 points), 0 registry hard issues, and 0 unavailable
values. It left 18 queue entries: 11 Table 1 prior-work methods are not stated in
Neumann's summary table, and the six measured_reduced K results (the Table 1
summary plus five Table 2 points) have no separate stored raw-data observations
to name as derivation parents. These are source-coverage queues, not hard issues.
The focused regression test pins all nine numeric cells in each Table 2 row, the
printed row count, and the migrated alpha and temperature values.
Mutation check: changing the first stored alpha from 0.89 to 0.88 made the
focused test fail on the render-pinned cell; the source value was restored.

For the Table 2 rows, the migrated identity axes are:

| theta °C | composition | total_pressure_Pa | fO2_Pa | temperature_K |
|---:|---|---|---|---:|
| 66.6 | unknown: no composition mapped from source | unknown: no total_pressure_Pa mapped from source | unknown: no fO2_Pa mapped from source | 339.75 |
| 66.7 | unknown: no composition mapped from source | unknown: no total_pressure_Pa mapped from source | unknown: no fO2_Pa mapped from source | 339.85 |
| 104.5 | unknown: no composition mapped from source | unknown: no total_pressure_Pa mapped from source | unknown: no fO2_Pa mapped from source | 377.65 |

The row conditions retain the printed temperatures and mark fO2 unknown because
no oxygen fugacity is reported. The source reports pure K but not a melt
composition. Total pressure remains unknown because printed p is equation-derived
saturation-pressure context, not chamber pressure.

No battery-store regeneration was committed. Expected store delta after
regeneration: 17 observations from this source.
