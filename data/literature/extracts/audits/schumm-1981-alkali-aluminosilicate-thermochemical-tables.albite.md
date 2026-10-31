# Schumm (1981), albite tables audit

Both printed tables contain 4 numeric columns stored at every temperature plus the separately stated S298 anchor. The S−S298 entropy-increment column and log10 Kf column are excluded: only exact quantity tokens are accepted.

## Low albite (PDF p. 24, printed p. 18)

Printed row count: 13. MinerU table p024-t11; the pdftotext layout is the second machine read. The page render checked the full table layout and every stored cell because QA reported extensive numeric disagreement for the source. Values and signs in all four stored columns agree between the MinerU draft and render; no unresolved differences.
Printed annotations: S298: 76OPE/HEM. H−H298 and S−S298: 78ROB/HEM (60KEL, 78HEM/KRU). ΔfH298: 77HEM/ROB. Cp and ΔfG have no separate source annotation. S298 = 49.57 ± 0.10 cal/deg,mol; ΔfH298 uncertainty ±0.50 kcal/mol; confidence levels not stated.
The S−S298 column is an entropy increment, not absolute S. `Quantity.S` is defined in `simulator/battery/enums.py::Quantity.S`, and the closed `Quantity` enum (`simulator/battery/enums.py::Quantity`) has no entropy-increment token; `TYPE_QUANTITY` (`simulator/battery/migrate.py::TYPE_QUANTITY`) and `QUANTITY_ALIASES` (`simulator/battery/migrate.py::QUANTITY_ALIASES`) have no exact alias. The values are therefore omitted rather than mislabeled as S.
Low albite remains without a polymorph token: `Polymorph` in `simulator/battery/enums.py::Polymorph` has no low-albite member, and `_EXTRACT_POLYMORPH_ALIASES` (`simulator/battery/migrate.py::_EXTRACT_POLYMORPH_ALIASES`) has no `low albite` mapping; `polymorph_from_extract` reads the observation-level field at `simulator/battery/migrate.py::polymorph_from_extract`.

## High albite (PDF p. 25, printed p. 19)

Printed row count: 14. MinerU table p025-t12; the pdftotext layout is the second machine read. The page render checked the full table layout and every stored cell because QA reported extensive numeric disagreement for the source. Values and signs in all four stored columns agree between the MinerU draft and render; no unresolved differences.
Printed annotations: S298: 78ROB/HEM. H−H298 and S−S298: 78ROB/HEM (78HEM/KRU). ΔfH298: 68WAL, 68HOL/KLE. Cp and ΔfG have no separate source annotation. S298 = 54.11 ± 0.10 cal/deg,mol; ΔfH298 uncertainty ±0.60 kcal/mol; confidence levels not stated.
The S−S298 column is an entropy increment, not absolute S. `Quantity.S` is defined in `simulator/battery/enums.py::Quantity.S`, and the closed `Quantity` enum (`simulator/battery/enums.py::Quantity`) has no entropy-increment token; `TYPE_QUANTITY` (`simulator/battery/migrate.py::TYPE_QUANTITY`) and `QUANTITY_ALIASES` (`simulator/battery/migrate.py::QUANTITY_ALIASES`) have no exact alias. The values are therefore omitted rather than mislabeled as S.
High albite is bound with the existing `condensed_form.polymorph: high albite` alias, which maps to `Polymorph.ALBITE_HIGH` in `_EXTRACT_POLYMORPH_ALIASES` (`simulator/battery/migrate.py::_EXTRACT_POLYMORPH_ALIASES`) and is read by `polymorph_from_extract` (`simulator/battery/migrate.py::polymorph_from_extract`); the token is defined in `simulator/battery/enums.py::Polymorph`. No new enum member is added. Source annotations now populate `values.attribution`, which the migrator carries into evidence.
