# t-1129 — Design note: reader path for `Quantity.LIQUIDUS_COMPOSITION`

Author: regolith-empirical (seat worker). 2026-10-04.
Tip when written: `8089eadbf` (`work-v064-green`). **Design only — no reader implementation until main reviews.**
First payloads (corpus, already extracted; typed Observations = 0): Görnerup & Wijk 1996 Table 1 (39 rows); Morey 1964 invariant tables with printed liquid compositions (T3–T6, T8–T9, T26; compilation).

## Problem

`Quantity.LIQUIDUS_COMPOSITION = "liquidus_composition"` exists (`simulator/battery/enums.py:89`) with unit `component_mole_fraction_vector` (`enums.py:488`) and score metric `MetricOperation.ABSOLUTE` (`score.py:240`). Identity profile already requires `temperature_K`, `total_pressure_Pa`, `composition` (`identity.py:695–706`). There is **no** entry in `QUANTITY_SOURCE_FIELDS` (`migrate.py:6851–6966`), so `empty_value_from_payload` / `select_declared_source` (`migrate.py:7937–7999`) yield unavailable; migrator leaves rows in Work context and queues typed absence (Görnerup report; Morey fidelity review).

## 1. Value representation

| Layer | Proposal |
|---|---|
| Printed payload | Component-labelled map + declared printed basis (`AmountBasis.MASS_PERCENT` for Görnerup/Morey; quote retained). Source field name to add later: `composition_mass_percent` (and synonyms already used in extracts). |
| Canonical stored value | One Observation per table row whose **value** is the full liquid composition vector in the quantity unit `component_mole_fraction_vector`. Convert mass% → mole fraction with stated molar masses at read time; keep printed mass% in provenance / `read_from`. |
| Schema gap | `Observation.value` is `Value` (`records.py:1077–1081`); `ValueKind` is scalar-only (`enums.py:412–420`; `records.py:484–496`). A vector does not fit POINT/SERIES. **Ask main to choose before code:** **(A)** add `ValueKind.COMPOSITION_VECTOR` carrying `records.Composition` (`basis`, `components`, `amount_basis=MOLE_FRACTION`); **(B)** residue-style split: N scalar Observations (one oxide species each) sharing T/P/coexisting solid. Prefer **(A)** — unit and ABSOLUTE metric are already vector-shaped; Görnerup/Morey print a labelled whole; split would invent per-oxide residuals without a shared vector identity. |
| Zeros / blanks | Printed 0 stays 0. Missing component = absent from the map (not invented 0). Morey “No liquid” / solid–solid rows → typed refusal, not an Observation. Two-liquid rows (e.g. Morey T8 row 12, T5 row 5) → two Observations or one row with two labelled liquids — prefer two Observations sharing T and reaction context. |
| Species | Liquid phase (`Phase.L`); components are oxide labels on the Composition, not N separate species identities under (A). |

## 2. Conditions (what fixes the liquidus point)

Liquidus composition is the liquid in equilibrium with a **named coexisting solid** at **T** (and P). It is the inverse of `TRANSITION_TEMPERATURE` / subtype `liquidus` (`identity.py:655–677`), which fixes composition and measures T.

| Axis | Role |
|---|---|
| `temperature_K` | Required identity (`identity.py:702`). |
| `total_pressure_Pa` | Required by profile; often `State.unknown` / `not_published` (Görnerup Ar atmosphere; Morey 1 atm when stated). |
| Coexisting solid(s) | **Must travel with the row** (Görnerup: CaO / 2CaO·SiO2 / 3CaO·SiO2; Morey: reaction-as-printed). Today Identity has no `saturation_phase` field — carry as `point_conditions["saturation_phases"]` (and/or subtype string) until a typed axis exists. |
| `identity.composition` | Profile requires it (`identity.py:702`), but it **must not duplicate the measured liquid** (that would bake the answer into identity). Propose: stoichiometric composition of the **saturating solid** when univariant solid+liquid; for invariant multi-solid (+ liquid) use the printed reaction assemblage in conditions and set composition to the **system component set** with amounts unknown / not applicable pending main’s equality rule. Görnerup bulk charge is not published — never invent bulk. |
| fO2 | Profile marks `fO2_Pa` N/A for this quantity (`identity.py:705–706`); keep that. |

## 3. Consumers that will read it (cite tip)

| Consumer | File:line | Today |
|---|---|---|
| Enum + unit | `enums.py:89`, `enums.py:488` | Present |
| Identity profile | `identity.py:695–719` | Requires T, P, composition; subtype N/A |
| Score metric map | `score.py:240` | ABSOLUTE wired |
| `rail_for_quantity` | `score.py:556–601` | Returns `None` → `no_headline_rail:liquidus_composition` (`score.py:604–616`). No `Rail` for silicate liquidus (`enums.py:39–50`). Leave as none until main assigns a rail. |
| `QUANTITY_SOURCE_FIELDS` + value select | `migrate.py:6851–6966`, `7937–7999` | **Gap — reader work** |
| Engine predict | `score.py` (~2905+ residue branch; no LIQUIDUS_COMPOSITION branch) | Falls through / unsupported — expected until engine isothermal-saturation path exists |
| Validity apparatus gates | `validity.py:326–345` | Not method-scoped by orifice; no change |
| Melt liquidus finder | `melt_backend/liquidus.py:152–209`, `find_liquidus_solidus_by_fraction` | Returns **T_liquidus at fixed bulk**, not X_liquid at fixed T+solid. Path points can carry `liquid_composition_wt_pct` (`liquidus.py:211–228`) — useful engine-side output shape, different call pattern |

Context lift only (`_lift_extract_context`) already preserves Görnerup/Morey composition maps in Work context; this note does not change that.

## 4. What a score against an engine liquidus compares

- **Measured:** mole-fraction vector \(X^\mathrm{meas}\) of the saturated liquid at printed T, with coexisting solid assemblage \(S\).
- **Engine:** at the same T (and P when known), the equilibrium liquid composition \(X^\mathrm{eng}\) in contact with the same solid(s) \(S\) — an **isothermal saturation** solve, **not** `find_liquidus_solidus` (which returns T for fixed bulk X). If the engine cannot place \(S\) at T (no crystal parent, out of domain), typed refusal — never a numeric residual.
- **Residual (per QUANTITY_METRIC ABSOLUTE):** component-wise \(R_i = X^\mathrm{eng}_i - X^\mathrm{meas}_i\) in mole fraction (dimensionless). Optional summary \(\sum_i |R_i|\) or \(\max_i |R_i|\) for reporting only; do not invent a second metric token without review.
- **Basis match:** score only after both sides share the same component set and mole-fraction basis; refuse on basis mismatch rather than silent renormalization of omitted oxides (Görnerup omits analyzed MgO/MoO3 from the normalized table — score the printed three-component simplex; note omitted components in provenance).
- **Evidence:** Görnerup `measured_tabulated`; Morey `compilation_assessed` / `quoted_attributed` — compilation rows stay scored only under compilation lineage rules already used elsewhere.

## Out of scope (this commit)

No `migrate.py` reader, no ValueKind change, no extract edits, no baseline/guard changes. Implementation waits on main’s choice of (A) vs (B) and the identity.composition / saturation-phase rule above.

## D-062 (design-note-only)

See REPORT after `git diff --stat`: docs only; no behaviour change.
