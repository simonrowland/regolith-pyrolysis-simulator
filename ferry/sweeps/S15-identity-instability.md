# S15 — identity instability (order / label / mutable-field ID minting)

**Repo tip (audited):** `fbe3491b2` (`origin/work-v064-green`) via worktree `/workspace/repos/wt/slot-05` (detached). READ-ONLY on product code; writes only to ferry-inbox. Do not push. No product commits.  
**Scope:** whole repo — observation / series / segment / experiment id **derivations** that change when data is re-labelled, re-sorted, re-segmented, or re-migrated.  
**Predicate seed:** splitting a JANAF series at a printed marker mints new `segment-{index}` observation ids (ordinal segment index in the id, not a content-stable key).  
**Mode:** static-only + corpus `rg` (store / extracts / migration-queue).  
**Batch Y calibration:** P0 only when a wrong number reaches a result/score/ledger **today**. Rematerialize / re-segment / re-sort / relabel triggers → **P1 at most**, even when unstable ids are already live in the store.

| site (file:line) | predicate match | trigger input | live? | severity | P0? |
|---|---|---|---|---|---|
| `simulator/battery/generators/janaf.py:1275-1277` (+ boundary gate `:1338-1351`, `_phase_change` `:575`, `_COMBINED_STATES` `:196`) | Series observation_id suffix is `{quantity}:segment-{segment.index}`. Segment index is the **ordinal** among boundaries harvested from printed short-row labels (`_phase_change` ∩ (`_COMBINED_STATES` ∨ named crystal transition on `state=="cr"`)). Teaching the parser to accept one more printed marker inserts a boundary and **renumbers every later segment** → new observation_ids for the same physical T-grid. | Rematerialize any multi-segment table after expanding boundary recognition (seed). Corpus today: `nist-janaf-4th:Fe-002:H_minus_H298:segment-0` / `…:segment-1` in `data/literature/observations-v2/compilations-janaf/janaf-Fe.yaml`; **2772** observation lines carry `segment-[1-9]`; `data/battery/migration-queue.yaml` cites `segment-` **12774** times. | live (ids stamped + queued) | **P1** | no |
| `simulator/battery/generators/janaf.py:1272` + `_subtype` `:1200-1205` | Transition observation_id is `transition_temperature:{subtype}` where subtype is minted from **printed** left/right label tokens (`LIQ`→`liquid`, spaces→hyphens). Relabel / normalize a printed side changes the id; T is **not** in the id (same subtype twice on one table would collide — none in corpus today). | Relabel a JANAF short-row transition marker and regenerate; or add a second same-subtype transition on one table. | live | **P1** | no |
| `simulator/battery/generators/usgs_b1259.py:1489-1494` · `usgs_b1452.py:1596-1601` · `usgs_b1544.py:1154-1160` (row_index from `enumerate(rows)`, e.g. b1259 `:380`) | Observation_id embeds **`row={token.row_index}`** (list ordinal) and **`T={temperature}`** (printed mutable field) plus column. Insert/reorder a harvest row or correct a printed T → new id for the same cell; score/ledger keys that followed the old id go stale. | Re-sort or insert a row in a B1259/B1452/B1544 harvest JSON and regenerate; or fix a typo’d temperature token. Live store has **~33313** `…:row=…` observation_ids under USGS compilation trees. | live | **P1** | no |
| `simulator/battery/migrate.py:4290-4298` + `_emit_exploded_point` `:7151` / `:7262` (`:7231/:7247/:7259`) | Series / tabulated ΔfG lists are exploded with **`{parent_id}::point:{index}`** where `index` is `enumerate(series)` order. Re-sorting the extract series rebinds `::point:N` to a different T/value while keeping the id. | Remigrate any extract whose `values.series` (or tabulated ΔfG list) order changes. `data/battery/migration-queue.yaml` already carries **1583** `::point:` observation_id lines (e.g. `ammin-76-904::…::point:0`). | live (migrate + queue) | **P1** | no |
| `simulator/battery/migrate.py:6628-6641` (`_measured_oxygen_yield_fields` `:2740-2749`) | Multi-field `measured_oxygen_yield` fans out to `{raw_obs_id}::point:{index}` in **field-tuple encounter order**. Adding/removing one numeric oxygen field renumbers sibling point ids. | Remigrate an extract that gains/loses a measured-oxygen numeric field listed in `_MEASURED_OXYGEN_YIELD_FIELDS`. | live (path) | **P1** | no |
| Extract corpus `::point:N` + migrate passthrough `migrate.py:6613-6614` | **1154** extract observation_ids already use author `…::point:{N}` (e.g. `data/literature/extracts-v2/ammin-76-904-lange-1991.yaml`). Migrate prefixes and preserves them — re-numbering points in the extract is an identity rewrite with no content-addressed key. | Edit extract point order / indices and remigrate. | live (extracts; queue) | **P1** | no |
| `simulator/battery/migrate.py:6063-6082` (`_experiment_id`) callers `:6925`, `:7527` | When no declared experiment id, experiment_id is `{work_id}::{locator.table\|record\|figure\|source_path}` — **printed / path labels**. Retitling a table/figure string or moving the extract path mints a new experiment_id for the same lab run. | Remigrate after renaming `locator.table` / `figure` on an extract that omits `experiment_id`. Live works already show label-shaped ids. | live | **P1** | no |
| `simulator/battery/migrate.py:7979-8007` | CaO KEMS raw pCa rows mint `cao_raw_pCa_{i}` via **`enumerate(raw_pCa)`**. Inserting a row at the head renumbers every subsequent id; **4** such ids live under observations-v2. | Reorder/insert `cao_reducing_cell_kems.raw_pCa` and remigrate refractory validation doc. | live | **P1** | no |
| `simulator/battery/migrate.py:7945-7964` | `refractory:{bucket}:{formula}` keys off **bucket name + formula string** (mutable labels), not a stamped node id / table locator alone. Relabel formula or rename bucket → new observation_id (**9** live). | Rename a formula key under the NIST-JANAF named-node buckets. | live | **P2** | no |
| `simulator/battery/migrate.py:8268-8332` | Generic compilation lift: `record_id = doc.record_id or path.stem` then `observation_id=f"{source_id}:{record_id}"`. Renaming the compilation file without a stable `record_id` field rewrites the observation_id. | Rename a compilation YAML stem that omits `record_id` and remigrate. | latent (path-rename) | **P2** | no |
| `scripts/calibration_battery.py:256`, `:401`, `:504` | Envelope observation_ids use **`point[{i}]` / `:{i}`** list ordinals (tooling envelopes, not store). Re-order input points → different envelope ids / joins. | Re-run calibration envelope build after shuffling series points. | latent (tooling) | **P2** | no |
| `simulator/battery/score.py:1231` (+ `simulator/diagnostic_helpers/binary_pot_scoring.py:1186`; `residual_key` `:435`) | Downstream mint `engine:{engine}:{reference.observation_id}` / pot cell ids **embed** the upstream observation_id. Any upstream instability is amplified into score/engine synthetic ids and residual keys. | Score after any of the rematerialize triggers above. | live (amplifier) | **P2** | no |

## Counts

- Sites: **12**
- Live (stamped in store / extracts / migration-queue / live mint path): **9**
- Latent / tooling: **3**
- **P0: 0** · P1: 8 · P2: 4 · P3: 0  
  (Batch Y: no site puts a wrong numeric result on the wire *today* without a rematerialize / re-segment / re-sort / relabel step.)

## Fix direction (summary)

- Prefer **content-stable** observation keys: phase/polymorph + quantity + closed T-bounds (or boundary-label digests), not ordinal `segment-{i}`.
- USGS / series points: key by **printed locator** (table/record + column + as-published T token / line id); drop naked `row=` / `::point:{i}` ordinals.
- Migrate explode / oxygen fan-out: require author-stable point ids, or hash `(T, quantity, species, channel)`; never renumber silently.
- `_experiment_id`: require declared experiment ids for empirical extracts; locator labels only as display, not identity.
- `cao_raw_pCa_{i}` / refractory buckets: stamp explicit `observation_id` / `node_id` in the source doc.
- Score/engine synthetic ids: inherit only after upstream ids are stable (or key residuals by identity tuple, not observation_id string).

## No-hit areas (audited, out of class or safe)

- **Author-declared observation_id / experiment_id** that do **not** embed ordinals — passthrough is stable under remigrate of unchanged extracts.
- **`simulator/battery/identity.py` quantity/species equality** — compares physics identity, does not mint store ids.
- **S10 identity special-cases** (`source_id == "janaf"` predicates) — different class (behavior branching by identity token, not mint instability).
- **S11 non-determinism** (hash-seed / unsorted YAML order) — artifact byte drift without necessarily changing id **strings**.
- **Pins `data/battery/pins.yaml`** — currently keyed as `janaf-4th::JANAF1998_…` style, not `nist-janaf-4th:…:segment-N` (no live pin breakage from segment renumber **today**).
- **Deduping / `DedupeAlias`** — records alias after mint; does not itself choose ordinal segment/point indices.
- **`SOURCE_ID = "…"` stamp constants** in generators — fixed source tokens, not order-dependent.

SWEEP: S15 | sites=12 | live=9 | P0=0 P1=8 P2=4 P3=0 | base=fbe3491b2 work-v064-green | path=/workspace/ferry-inbox/sweeps/S15-identity-instability.md
