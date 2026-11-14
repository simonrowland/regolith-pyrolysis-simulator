# Rego, Sigworth, and Philbrook (1988), Table I

Transcription checked against the rendered PDF, physical page 5 (published
page 659), Table I. The exact title is “Activity of SiO₂ as Function of
Composition in the Terminal Binary Melts”; the grouped column header is
`log a_SiO2`. The column has no separate units label. The adjacent text says
these activities are referenced to pure liquid silica at 1400 °C, so the
activity and its logarithm are dimensionless. The table's positive and
negative log signs are literal: `log a = x` gives `a = 10^x` (common log).
Linear activities below are derived and rounded to three significant
figures; they are not printed cells.

| Table I row | Binary | X(SiO2) | y | Printed log a(SiO2(l)) | Derived a(SiO2) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | CaO-(SiO2)y | 0.60 | 1.50 | -0.057 | 0.877 |
| 1 | Na2O-(SiO2)y | 0.60 | 1.50 | -0.982 | 0.104 |
| 2 | CaO-(SiO2)y | 0.65 | 1.86 | 0.023 | 1.05 |
| 2 | Na2O-(SiO2)y | 0.65 | 1.86 | -0.644 | 0.227 |
| 3 | CaO-(SiO2)y | 0.70 | 2.33 | 0.050 | 1.12 |
| 3 | Na2O-(SiO2)y | 0.70 | 2.33 | -0.401 | 0.397 |
| 4 | CaO-(SiO2)y | 0.75 | 3.00 | 0.072 | 1.18 |
| 4 | Na2O-(SiO2)y | 0.75 | 3.00 | -0.233 | 0.585 |

The extract stores `printed_form: log10` on each typed-refused printed-log
input. The reduced linear sibling has no `printed_form`, preventing the
migrator from exponentiating its already-linear value again. For the worked
cell, `log a_SiO2(l) = -0.057` gives `a_SiO2 = 10^-0.057 = 0.877`.

All eight converted activities are positive and finite, ranging from 0.104
to 1.18. Three CaO-binary log values are positive and therefore yield
activities above one. These are the source's Table I values: the nearby text
describes estimating the CaO–SiO₂ terminal-binary activities by extrapolation
from the liquidus and Na₂O–SiO₂ data. Activity is positive but has no
universal upper bound of one relative to a selected standard state.

Table I's silica standard state is pure liquid SiO2 at 1400 °C. The listed composition
is completed to the binary mole fractions: CaO is `1 - X(SiO2)` for the CaO
rows and Na2O is `1 - X(SiO2)` for the Na2O rows. The source page gives the
temperature context as 1400 °C and the table locator is page 5, folio 659,
PDF page index 4. The closed compositions recorded for the four X(SiO2)
rows are CaO/SiO2 `0.40/0.60`, `0.35/0.65`, `0.30/0.70`, `0.25/0.75` and
Na2O/SiO2 with the same respective fractions.

The extract's row locator notes are:

- `Table I row 1; CaO-(SiO2)y terminal binary; X_SiO2=0.60.`
- `Table I row 1; Na2O-(SiO2)y terminal binary; X_SiO2=0.60.`
- `Table I row 2; CaO-(SiO2)y terminal binary; X_SiO2=0.65.`
- `Table I row 2; Na2O-(SiO2)y terminal binary; X_SiO2=0.65.`
- `Table I row 3; CaO-(SiO2)y terminal binary; X_SiO2=0.70.`
- `Table I row 3; Na2O-(SiO2)y terminal binary; X_SiO2=0.70.`
- `Table I row 4; CaO-(SiO2)y terminal binary; X_SiO2=0.75.`
- `Table I row 4; Na2O-(SiO2)y terminal binary; X_SiO2=0.75.`
