# DIAG: finite-headspace bleed kilobar P_ss (stack2 r12b)

Date: 2026-09-30 (ET)
Seat: `/workspace/repos/wt/slot-b565` @ `523c69d8bc5b95d8007e3a8cd80ffda93253a1a6` (`empirical/diag-stack2-r12b-headspace` / `review/stack2-r12b`)
Green comparison: `1bef59d9c4e88becf1d992e57f2c33d12f0f94af`
Requester: regolith-physics (REQ-headspace-rootcause)
Agent: regolith-empirical
Verdict: diagnosis only — no LAND/REVISE

## One-liner root cause

**`k` and `S` are fine; quasi-steady `P_ss = sqrt(P_down² + S/k)` is fed `P_down ≈ P_up` from `EffectiveTransportCapacity.downstream_pressure_bar`, which latches the pre-bleed ledger pressure (kilobar) instead of the vacuum/cistern outlet (~0).**

## Measured RH03 hour-1 probe (this tip, recipe-matched)

Instrumented `_dispatch_overhead_bleed` on the RH03 gram-lab fixture
(`dynamic_surface_geometry_fixture`, 1000 kg, 2200 °C, 13 mbar, 2 h). Numbers
are from a live run, not hand estimates.

| Quantity | Code (live) | Hand / expected |
| --- | ---: | ---: |
| `d` | 0.02 m | 0.02 m (fixture `equivalent_diameter_m`) |
| `L` | 0.063662 m | `ΣA/(πd)` from lab surfaces (0.004 m² / π·0.02) |
| `T` | 2200 °C = 2473.15 K | same |
| `μ` | 7.8807e-5 Pa·s | `1.8e-5·(T/300)^0.7` |
| `M` | 0.03283 kg/mol | holdup mole-weighted |
| code `k` = `_pipe_conductance(1 Pa)` | **6.2485e-10** kg·s⁻¹·Pa⁻² | **6.2485e-10** (identical) |
| `S_mass` | 0.12990 kg/s (**467.6 kg/h**) | evaporative source this tick |
| of which `S_O2` | 1.105 mol/s ≈ 127 kg/h | rest is Fe/Mg/SiO/Na vapor |
| `V` | 0.074094 m³ | freeboard + pipe segments |
| `n` before bleed | 4963 mol (O2 3978, Mg 466, SiO 300, Fe 168, …) | flush-before-bleed bolus |
| ETC `upstream_pressure_bar` | **12473.82** | `nRT/V` of that bolus |
| ETC `pipe_capacity_kg_hr` | **4.54e12** | `k·P_up²·3600` at kilobar |
| ETC `downstream_pressure_bar` | **12473.82** | `P1·√(1 − C/C0) ≈ P1` because C/C0∼1e-10 |
| ETC saturation | 1.07e-10 | capacity does **not** bind |
| `P_ss` with that `P_down` (reported) | **12473.82 bar** | matches summary `P_total_bar` |
| `P_ss` if `P_down = 0` (correct boundary) | **0.144 bar** | `√(S/k)` |
| command | 0.013 bar | 13 mbar |

r12b report RH03 h1 = 10395 bar is the same latch on the 24 h schedule
(composition/`S` differ slightly; mechanism identical). Our 2 h probe prints
12474 bar.

### Where code and hand diverge

They do **not** diverge in `_pipe_conductance` or in `S`:

- Hand: `k = π d⁴ M / (256 μ R T L)` → 6.2485e-10
- Code: `simulator/overhead.py:1585-1593` with `COMPRESSIBLE_POISEUILLE_DENOMINATOR = 256` → same
- REQ’s “k ~1488+” is **not** the coefficient; capacity at ~1 bar with d=0.02 is O(10³) kg/h. The coefficient at 1 Pa is ~6e-10.

They diverge in **which pressure is called “downstream”** when forming `P_ss`:

```text
physics:   P_down = vacuum / cistern outlet ≈ 0
code:      P_down = ETC.downstream_pressure_bar ≈ P_up (ledger)
⇒          P_ss = √(P_up² + S/k) ≈ P_up   (kilobar latch)
```

## File:line root cause chain

1. **Seed pressure.** Evaporative O₂ buffer flush + vapor holdup credit the
   ledger *before* bleed (`simulator/core.py` `_flush_evaporative_o2_buffer_to_headspace`
   / source-rate path ~5712–5776). For RH03 h1 that is ~5000 mol in 0.074 m³
   → ~14 kbar ideal-gas pressure.

2. **ETC rates the pipe at that ledger upstream.**
   `PyrolysisSimulator._controlled_o2_transport_capacity`
   (`simulator/core.py:10618-10633`) passes
   `upstream_pressure_Pa=self._headspace_upstream_pressure_Pa()` into
   `OverheadGasModel._controlled_o2_transport_capacity`
   (`simulator/overhead.py:870-919`), which calls `_pipe_conductance` at that
   Pa. Capacity becomes ~10¹² kg/h.

3. **ETC invents `P2 ≈ P1`.**
   `controlled_flow_capacity` (`engines/builtin/overhead_bleed.py:124-136`):

   ```python
   downstream_pressure = upstream_pressure * sqrt(max(1 - swallowed/pipe_capacity, 0))
   ```

   With `swallowed/pipe_capacity ~ 1e-10`, this returns **`P2 ≈ P1`**.
   That quantity is an *implied duct-outlet backpressure when equipment
   swallows C ≪ C₀(P₁)* — a diagnostic, not the vacuum boundary of the
   furnace→cistern pipe. With `runtime_enforcement_disabled`, equipment is
   `None` and binding is `controlled_o2_no_equipment`, so this P2 has no
   physical outlet meaning.

4. **Quasi-steady consumes that P2 as `P_down`.**
   `_dispatch_overhead_bleed` (`simulator/core.py:10647-10670`) sets

   ```python
   p_downstream_bar = self._headspace_downstream_pressure_bar(effective_transport_capacity)
   # → effective_transport_capacity.downstream_pressure_bar   (core.py:3860-3861)
   p_end_Pa = self._headspace_quasi_steady_pressure_Pa(p_downstream_Pa=... * 1e5, ...)
   ```

   and `_headspace_quasi_steady_pressure_Pa` (`simulator/core.py:3998-4000`):

   ```python
   pressure_ss_Pa = sqrt(p_downstream_Pa**2 + source_mass_kg_s / k_kg_s_Pa2)
   ```

5. **Debit target = latched kilobar.** Bleed removes only enough to hold
   `n(P_ss) ≈ n(P_up)`, i.e. roughly this tick’s source. The bolus never
   drains. Hour 2 in the probe: `S → 0.046 kg/h` but `P_ss` stays 12193 bar
   because `P_down` is still the ledger pressure.

Secondary (not the kilobar cause):

- `S` includes condensables (Fe/Mg/SiO/Na). O₂-only `S` ≈ 127 kg/h would give
  vacuum Poiseuille `P_ss ≈ 0.075 bar`. Condensables inflate `S` ~3.7× but
  cannot make kilobar.
- Lab `L = ΣA/(πd)` (`simulator/equipment.py:271-276`) is a surface-area
  surrogate length (6.4 cm), not a surveyed duct. Affects `k` by O(10), not 10⁹.
- Shared transport capacity does **not** cap removal here (saturation ~1e-10).

## Corrected relation (derivation)

**Premise.** Isothermal ideal-gas laminar compressible flow in a circular duct
from headspace pressure `P_up` to a true outlet pressure `P_out` (vacuum pump
or O₂ cistern, not an algebraic echo of `P_up`):

```text
ṁ = k (P_up² − P_out²),   k = π d⁴ M / (256 μ R T L)
```

Units: `k` in kg·s⁻¹·Pa⁻²; `P` in Pa; `ṁ` in kg/s.
(`simulator/overhead.py:87-97`, `COMPRESSIBLE_POISEUILLE_DENOMINATOR = 256`.)

**Quasi-steady balance** over a tick whose residence time ≪ Δt, with gas
source rate `S` (kg/s) into a volume that bleeds through that duct:

```text
S = k (P_ss² − P_out²)  ⇒  P_ss = √(P_out² + S/k)
P_end = max(P_commanded, P_ss)
```

**Limiting case.** Vacuum outlet `P_out → 0`: `P_ss = √(S/k)`.
RH03 h1 live numbers: `√(0.1299 / 6.2485e-10) = 1.442e4 Pa = 0.144 bar`.

**What the code did wrong.** It substituted
`P_out ← P1·√(1 − C/C0(P1))` with `C0` evaluated at the *ledger* `P1`.
When the pipe is not equipment-limited, `C/C0 → 0` ⇒ `P_out → P1` ⇒
`P_ss → P1`. That is an identity latch, not a balance.

## Laminar Poiseuille validity / choking

At the *correct* vacuum-Poiseuille state (0.144 bar, 468 kg/h, d=0.02 m):

| Check | Value | Verdict |
| --- | ---: | --- |
| ρ | 0.023 kg/m³ | — |
| mean duct speed | ~1.8×10⁴ m/s | ≫ sound speed |
| Re | ~1.1×10⁵ | not laminar |

So laminar Poiseuille is **not** the right law at this flux through a 2 cm
lab duct. Isentropic choked orifice estimate
`ṁ* = A P₀ √(γ/(R_spec T)) · (2/(γ+1))^((γ+1)/(2(γ−1)))`:

| Case | P₀ for ṁ = S | Note |
| --- | ---: | --- |
| full S 468 kg/h, d=0.02 | **~4.8 bar** | choked |
| O₂-only ~127 kg/h, d=0.02 | **~1.3 bar** | choked |
| 100 kg/h, d=0.02 (REQ) | ~1.0 bar | matches physics hand-check |

**Physically expected headspace pressure** (after fixing `P_out`):

| Case | Poiseuille √(S/k) | Choked-orifice scale | Command |
| --- | ---: | ---: | ---: |
| RH03 h1 (this probe) | 0.144 bar | ~1–5 bar | 0.013 bar |
| RH03 h12 (r12b table) | O(0.1 bar) if S similar; else → command once bolus cleared | O(1 bar) | 0.013 bar |
| C2B h3 (~38 kg/h; d=0.12 default ⇒ k~1e-7, or lab d=0.02) | 0.003–0.03 bar | ≪1 bar (d=0.12) / ~0.4 bar (d=0.02) | 0.0015 bar |
| Lunar 1 kg C2A hero h61 | source ≪ pipe if `P_out=0` and d≥0.02; should stay near command (~0.01 bar) once latch gone | — | 0.01 bar |

Kilobar is **not** a choking result. Choking caps expectation at O(1–5 bar) for
the 1000 kg / 2 cm lab fixture.

## Green (`1bef59d9c`) — same symptom, different mechanism

Green has **no** `_headspace_quasi_steady_pressure_Pa`.
`controlled_o2_transport_capacity` rates the pipe at **commanded** mbar only
(`allowed_pressure_Pa = max(p_total_mbar·100, 1)`, green `overhead.py` ~840–850).
Capacity at 13 mbar with d=0.02 is ~0.3 kg/h; RH03 demand is hundreds of kg/h
⇒ inventory accumulates ⇒ grok-tail green RH03 h1 **4808 bar**. Same published
kilobar, but from **under-rated removal**, not from a `P_down≈P_up` latch.
r12/r12b fixed the rating-at-ledger part, then re-broke the quasi-steady
boundary by wiring ETC’s diagnostic `P2` into `P_out`.

## C2A hero reconciliation (r12 vs r12b)

- **r12** (`f540a5b` / seat base): hero `P_total` stayed 0.01 bar for 225 h —
  1 kg source fits even a millibar-rated pipe; ledger empty.
- **r12b** (`523c69d`): first pressure delta at hour 61, candidate
  `P_total_bar = 12.8` vs baseline 0.001. That is the quasi-steady path
  interacting with a transient source/campaign boundary. With a correct
  `P_out≈0` it should not kilobar; 12.8 bar is still above command and should
  be re-checked after the `P_out` fix (possible residual choke/`S` composition
  effect, not the ETC latch at 10⁴ bar).

## Minimal fix plan (do not push from this seat)

1. **Boundary for quasi-steady:** In
   `_dispatch_overhead_bleed` / `_headspace_quasi_steady_pressure_Pa`, set
   `P_out` from the *configured duct outlet*
   (`headspace.downstream_pressure_bar`, else **0** for vacuum/cistern),
   **never** from `EffectiveTransportCapacity.downstream_pressure_bar` when
   that value was derived as `P1·√(1−C/C0)` with `C0=k P1²` at ledger `P1`.
   File focus: `simulator/core.py:10647-10670`, `3856-3861`.

2. **ETC role:** Keep `controlled_flow_capacity`’s `downstream_pressure_bar`
   as a diagnostic of equipment-imposed backpressure only when
   `equipment_capacity` actually binds (`binding_cause == controlled_o2_equipment`
   and `C ≈ equipment < C0`). If equipment is `None` /
   `controlled_o2_no_equipment`, force diagnostic `P2 = 0` (vacuum).
   File focus: `engines/builtin/overhead_bleed.py:124-136`.

3. **Regression probe (targeted, not full W3):** RH03-like 1–2 h assert
   `P_total_bar == approx(max(command_bar, sqrt(S/k)/1e5), rel=1e-3)` with
   `P_out=0`, and assert ETC saturation is not the drain limiter when
   `√(S/k)` is below a few bar. Mirror the instrumented capture used here.

4. **Follow-ons (separate commits):**
   - Replace laminar Poiseuille with a choked/compressible duct law when
     Re/Mach invalidate laminar (expected RH03 ~1–5 bar, not 0.144 bar).
   - Optionally exclude fast-condensables from `S_mass` for the gas-phase
     pipe balance (or condense them before the duct).
   - Revisit lab `L = ΣA/(πd)` vs a declared duct length.

## Suspects cleared / confirmed

| Suspect | Result |
| --- | --- |
| Unit slip in `k` (mbar/Pa, kg/h vs kg/s) | **Cleared** — code `k` matches hand at live `d,L,T,μ,M` |
| Unit slip in `S` | **Cleared** — 468 kg/h is a plausible 1000 kg / 2200 °C source |
| Shared transport capacity cap after conductance | **Cleared as cause** — saturation ~1e-10; capacity is huge |
| Wrong geometry (orifice vs duct) | **Partial** — lab `L` from surface area is stylized, O(10) on `k` only |
| μ or T at wrong place | **Cleared** |
| `S` includes condensables | **Secondary** — inflates `S` ~3.7×, not 10⁹× |
| Tiny `V` | **Cleared for `P_ss`** — formula independent of `V`; `V` only sets seed `nRT/V` |
| **`P_down` ← ETC `P2≈P1` latch** | **CONFIRMED** — vacuum `P_ss=0.144 bar` vs reported 12474 bar |

## Artifacts

- Probe: live monkeypatch of `_dispatch_overhead_bleed` on tip `523c69d8`
- Context: `regolith-physics-headspace-2026-09-30/{r12-report.md,grok-tail.md}`
- No code pushed to `review/stack2-r12b`; diagnosis only
