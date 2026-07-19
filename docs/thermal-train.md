# Downstream Thermal Train — O₂ condenser chain and frost-cistern storage

Status: Phase 2B — diagnostic sizing report. Nothing in this subsystem mutates the
ledger, the transport path, or the optimizer objective; it reads a run's recorded
history and reports what downstream hardware that run would require. Objective
(cost) wiring and physical capacity enforcement in the live bleed path remain
separate follow-on work. See `docs/model-limitations.md` for scope caveats.

## What it models

After the melt evolves vapor, everything downstream of the furnace throat is a
thermal train that must (a) capture each species on the right surface and (b) get
the residual O₂ cold enough to store — without coating anything on the way:

```
Furnace (mbar overhead, choked throat)
  [S-A] Hot ceramic radiator ducts (~melt T → ~1000 K)
        Static ceramic, no moving parts. Alkali (and any species whose condensation
        window sits in-band) condenses here; latent heat is rejected while the gas
        is hot, where radiator flux is ~57 kW/m². Cyclone drains liquid Na/K.
  [S-B] Post-separator O₂ stream, baffled radiator → passive floor (~150 K)
        Free radiative descent (night sink). Radiators fade as gas temperature
        approaches the sink; below the floor there is no passive cooling.
  [S-C/D] Seven-node Claude cycle: make-up metering orifice → return mixer →
        compressor → reject radiator → recuperator → HP split. Bypass flow drives
        a dry work expander; main flow crosses a JT valve and separates at the
        77 K LOX bath. Expander exhaust and separator vapor rejoin as cold return.
  [S-E] Free expansion to Kn ≫ 1 → frost cavern: ballistic desublimation onto cold
        walls. Collection is decoupled from drainage.
  [S-F] Batch drain: seal the cavern, warm the frost to the O₂ triple point
        (54.361 K, 146.33 Pa), drain LOX from a bottom sump.
```

### The frost cistern (strategic O₂ reserve)

The baseline storage architecture is a large in-situ-built frozen-O₂ cistern rather
than tankage sized per batch. The scaling argument: heat leak scales with surface
area while inventory scales with volume, so fractional boil-off *falls* as the
cistern grows (a buried olympic-pool-scale reference, ~2500 m³ ≈ 2.85 × 10⁶ kg LOX,
loses on the order of 0.02 %/day under warm overburden — a standing ~10 kW
re-liquefaction duty — and effectively nothing at permanently-shadowed-region
ambient, where the environment is already below the storage temperature). One
cistern is on the order of 10⁴ one-tonne batches: a fleet-years accumulation
target, which is why per-run reports treat storage headroom as effectively
unbounded and the capture tail as the default configuration.

## The rate axis: why the train couples to the recipe

Every element of the train scales on **mass flow**: radiator area, compressor
stages, duct bore all size on peak kg/hr, not on batch mass. A recipe that spikes
its evolution rate pays for spike-sized hardware; overflow beyond the train's
swallow capacity is O₂ that never reaches storage. The report therefore separates:

- `peaks` — observed per-species and max-concurrent-total flow peaks;
- `capacity` — authoritative cold-train throughput
  `C = min(compressor mass-flow rating, refrigeration freeze-rate rating)` and
  the freshly computed
  `thermal_train_overflow_kg_hr = max(0, cold_inlet − C)`;
- `observed_upstream_state` — the run's recorded upstream transport diagnostics,
  kept in a separate column because they are *not* consequences of the
  hypothetical train.

This remains diagnostic only. Display pricing estimates capex per campaign, O₂
value under a single-valuation policy, and cycle-time cost. Capacity becomes
physically binding through overhead backpressure in later P2-2/P2-3 chunks.

## The report screen

`/thermal-train` (also `GET /api/ledger/views/thermal_train` and the `ledger_api`
socket resource). Three data states, all read-only:

1. **Live run** — sizes the currently active simulation from its hourly history.
2. **Optimizer artifact** — sizes a stored result (read-only result-store access).
3. **No data** — typed empty state; a versioned precomputed default artifact
   (1 t batch, pyrolysis track, alkali shuttle on, MRE off) stands in so the screen
   is never blank.

Inlet authority: the hot stage reads the recorded per-species evaporation series;
the cold train reads the melt-offgas O₂ series. MRE-anode O₂ is a separate ledger
bin and is out of train scope. Species with no authoritative condensation window
and no sourced condensation enthalpy are *excluded, not estimated*: the report
carries `excluded_species` with their peak flows and flags
`train_closes_for_run: false` rather than fabricating a latent value.

## Parameters and provenance

`data/thermal_train_params.yaml` (`schema_version: thermal-train-v2`) carries the
engineering assumptions (emissivity, sink temperatures, compressor efficiency and
pressure ladder, frost/storage temperatures, cavern capacity), plus cold-end
ratings and their diagnostic `(p_ref, T_ref)` scalar-rating reference, Claude-cycle
splits, derived-orifice inputs, continuous relief law, and the `liquefier_77K`
endpoint. The reference state is carried for the P2-2 curve upgrade but does not
set C. Each authored value carries assumption class, range, and provenance; the
metering-orifice diameter is derived from C and never sets C.
Legacy parameters remain tagged
`assumption`. Display prices on the report are owner-ratified and tagged with
their ratification date; they are display-only and enter no objective
or cache identity. Compressor capex uses gross compressor shaft rating; cold-box
capex uses `Q_c` lift rating. Net plant shaft work is an energy ledger field and
is not priced again as a second machine.

## Known limitations

- The Claude-cycle report is first-order ideal-gas machinery. The 77 K LOX load
  includes sourced gas sensible heat and vaporization enthalpy; 90.188-to-77 K
  liquid subcooling is explicitly omitted until a sourced liquid-O₂ Cp anchor is
  configured. Compressor and dry-expander shaft terms use the same gas-Cp Δh
  basis; JT exit temperature is reported on that same first-order enthalpy basis,
  separately from the 77 K separator bath.
- `intercooled_compression()` and `cryogenic_tail()` remain public only for Phase-1a
  import compatibility. The v2 report does not call them; remove them after the
  compatibility window once downstream imports have migrated to the Claude-cycle
  report fields.
- SiO and CrO₂ carry reaction-class condensation enthalpies, not simple latents;
  until a condenser-side enthalpy source lands they route to `excluded_species`
  (fail-closed), so SiO-heavy runs will report the train as not closing. They are
  classified as expected trace only when the report caller supplies explicit
  trace authority; otherwise any nonzero unsized flow is conservatively major.
- The day-time (warm-sink) case refuses passive sizing below the sink temperature
  and reports the interval as active lift; cryo sizing is night-path.
- Knudsen anchors at the cavern require frost-side state the run record does not
  yet carry; they report as typed `inputs_required` rather than a number.
- No solar/furnace heat input model: the train sizes *rejection* of the recorded
  stream enthalpy, not the furnace energy budget.
