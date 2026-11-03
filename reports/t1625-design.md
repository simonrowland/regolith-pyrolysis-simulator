# t-1625: slag + metal charge

A slag–steel paper prints three things: a slag oxide mix, an alloy elemental mix, and the mass of each phase. The engine input is one composition. Metallic elements stay metals. No oxidation state is assigned, and an unprinted balance is not filled.

## Where the input is built today

`Composition` is the engine input (`simulator/battery/records.py`). Migration builds it in `simulator/battery/migrate.py`:

- `wt_pct_to_mole_fraction` turns one printed wt% map into `basis="printed_oxides"`, `amount_basis=mole_fraction`. A parser-identified element is kept as that element; it is not rewritten as an oxide. The map is one phase. It cannot apply two phase masses.
- `sample.initial_composition` on an extract experiment is loaded by `_composition_located_from_plain`. `_apply_equilibrium_starting_bulk` copies a mole-fraction or mol-inventory initial composition onto an `equilibrium_phase_composition` row and stamps `printed_starting_bulk`. The analysed component amount stays the measurement.
- `mass_weighted_mixture_wt_pct` (`composition.py`) is the t-1593 named-material path only. It fires when the body has `named_material_records`. A metallic element stays elemental there too. This charge is not that spec.
- `element_mass_fraction_redox` relabels elemental wt% plus measured oxygen onto FeO_T with fixed valences. That is the conversion this charge must not use.
- Scoring (`score.py` `_predict_phase_row`) turns the composition into oxide wt% with `composition_wt_pct`, then `partition_engine_components` drops names the engine basis does not list and rescales. That drop is legal for a trace outside an oxide charge (H2O, Cl, Re). Dropping the metal of a slag–steel charge is a different system: the metal is the oxygen sink.

Adapters do not decide this. `project_melt_to_oxide_projection` would drop a non-basis name again if the scorer sent it. The refusal is in the scorer, before that call.

## Representation

One `Composition`:

- `basis = "slag_metal_charge"`
- `amount_basis = mol_inventory`
- `input_provenance = printed_starting_bulk` (existing token: a starting charge, not the analysed phase)
- components = slag oxide moles + metal element moles

Keys stay the printed identity. Slag `MnO` stays `MnO`. Metal `Mn` stays `Mn`. `CrO1.5` uses the existing canonical `Cr2O3` (same rule as `wt_pct_to_mole_fraction`: `M(Cr2O3) = 2 M(CrO1.5)`, so the chromium count is unchanged). Alloy `S` stays `S`, not `SO2` or `SO3`.

Not added:

- A second phase block. `equilibrate` takes one bulk vector. A second block would not change the mole total, and no adapter reads one.
- A phase tag. Van Ende's starting charge and Kim's printed start do not repeat a key (`Mn` vs `MnO`, `Si` vs `SiO2`, `C`/`S` vs `CaO`/`Al2O3`). The failure a tag would prevent is collapsing the same formula printed in both phases. A repeated key is refused (`slag_metal_charge_key_collision`) instead of summed. These sources do not need the tag.
- An element-basis slag. That would destroy the printed oxide and invent free oxygen.

The basis token is what the scorer branches on. Without it, elemental `S` is the existing bulk-sulfur component and would be retained and sent, and elemental `Mn` would be omitted and the slag rescaled. Ordinary oxide rows, including a bulk `S`, do not carry this basis, so their `INPUT_OMITTED` path stays.

The charge is recognized only when the composition body has all four fields: `slag_oxide_wt_pct`, `metal_element_wt_pct`, `slag_mass_g`, `metal_mass_g`. A partial body is `slag_metal_charge_incomplete` and is not parsed as an oxide map. A metal-map oxide (`MnO`) is `slag_metal_charge_metal_not_element`. A slag-map element (`Mn`) is `slag_metal_charge_slag_not_oxide`. Neither is converted.

Real Van Ende 2008 and Kim 2018 extracts do not carry those fields. Table 2 and Table 3 are context CSV, the masses are procedure prose (15 g slag / 25 g steel; 2 g slag / 6 g alloy), and Fe is the unprinted balance. Those rows keep today's missing composition. The census is the check. Filling Fe or hard-coding the prose masses is not part of this path.

Distribution-ratio rows receive the same inventory when the experiment initial composition has this basis. Other starting bulks are still copied only onto equilibrium phase rows.

## Engines

Accepted inputs, read from the adapters:

| Engine | Accepted bulk | Metal component |
| --- | --- | --- |
| alphamelts, thermoengine | `MELTS_OXIDE_BASIS` (SiO2, TiO2, Al2O3, FeO, Fe2O3, MgO, CaO, Na2O, K2O, Cr2O3, MnO, P2O5, NiO, CoO) | none |
| magemin | `OXIDE_SPECIES` + FeOt + O + H2O + S | none. `O` is excess redox oxygen. `S` is bulk sulfur, not alloy sulfur |
| openimcc | `OPENIMCC_PARENT_OXIDES` (Na2O, K2O, SiO2, FeO, MgO, CaO, Al2O3, TiO2) | none |
| vaporock | `OXIDE_SPECIES` + H2O + CO2 | none |
| internal-analytical, nasa_cea_9, ellingham, antoine_sidecar, catalog_evaluator | no oxide-bulk phase input | none |

No adapter lists an elemental metal. A positive elemental component on `slag_metal_charge` (`elemental_charge_symbol`: one element, count 1, including C, S, N, O, Mn, Cr, Fe) is refused with `RefusalReason.ENGINE_CANNOT_ACCEPT_METAL` (`engine_cannot_accept_metal`) before `composition_wt_pct` and before `partition_engine_components`. The assemblage is not called. Nothing is rescaled.

`ENGINE_DOES_NOT_MODEL_SULFUR` and `ENGINE_DOES_NOT_REPORT_PHASE_COMPOSITION` stay. They already return before a drop. Vaporock, OpenIMCC, and internal-analytical phase rows still take `engine_does_not_report_phase_composition`. The new reason is the path that would otherwise omit the metal: alphamelts, thermoengine, and magemin.

`_metallic_element_symbol` stays the t-1593 rule (an element that is not in `_COMPOSITION_COMPONENTS`). The charge uses `elemental_charge_symbol` because alloy S is already in that table and must not be oxidized. The scorer gates on the basis, so a bulk-S oxide row is not a metal refusal.

## Masses

Owner: `composition.slag_metal_charge_moles`.

Premise: each printed wt% is of that phase, and the printed phase mass is the grams equilibrated. The two phases are one inventory. No oxidation state closes oxygen. Components that are not printed are absent.

Algebra: `n_i = m_phase_g * w_i / (100 * M_i)`. Slag `M_i` is `oxide_molar_mass` (the `CrO1.5` → `Cr2O3` mass). Metal `M_i` is the same owner on the element.

Units: g × (g/100 g) / (g/mol) = mol.

Sanity, Van Ende 2008 run 1-1. Table 3 SiO2 is 28.0 mass% of 15 g slag: printed SiO2 mass 4.200 g. Table 2 Mn is 11.8 mass% of 25 g steel: printed Mn mass 2.950 g. `oxide_molar_mass` gives M(SiO2) = 60.083 g/mol and M(Mn) = 54.938044 g/mol, so n(SiO2) = 4.200/60.083 mol and n(Mn) = 2.950/54.938044 mol, and n·M returns those printed masses. Table 3 CrO1.5 at 10 mass% of 15 g is stored as Cr2O3; n·M(Cr2O3) = 1.500 g. Fe is not in Table 2 and is not in the inventory.
