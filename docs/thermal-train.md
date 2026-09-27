# Downstream thermal train

The downstream thermal train turns the furnace's hot output into useful products
without asking the furnace to do every heat-management job at once. It captures
alkalis in a dedicated hot separator and handles the oxygen product as a
separate stream. The result is a product path that makes both alkali recovery
and oxygen storage visible in the process story.

The train is sized by **peak mass flow**. A faster bake-off produces a larger
instantaneous vapor and oxygen load, which drives radiator area, separator
throughput, compressor size, and cold-end capacity. A slower bake-off can
reduce installed capacity, but it takes longer. Bake-off rate is therefore an
economic recipe variable, not a free setting that the equipment absorbs without
cost.

## Stage sequence

1. **Hot ceramic ducts and alkali capture.** Hot ceramic radiator ducts keep
   evolved vapor moving toward its intended separator instead of depositing on
   an upstream cold spot. In the alkali section, a condenser/cyclone captures
   the alkali product. Its latent heat is rejected while the stream is still
   hot, where radiative rejection is effective:

   ```text
   q_rad ≈ εσ (T⁴ − T_sink⁴)
   ```

   The liquid alkali drains from the cyclone; its condensation heat is not
   pushed into the oxygen cold end.

2. **Oxygen stream to the radiator floor.** After the separator removes the
   condensable product, the oxygen-rich stream is separated from the sweep gas
   and sent to a passive radiator floor. This removes sensible heat before
   compression. The radiator does the work at its available temperature rather
   than at a cryogenic temperature.

3. **Staged, intercooled oxygen compression.** Compress the separated O₂ in
   several stages to roughly 10–20 bar. Intercoolers reject each stage's
   compression heat at radiator temperature. That heat is not rejected at the
   final condensation temperature, and the compressor is not a substitute for
   a radiator: it supplies shaft work while moving the oxygen to a pressure
   where passive condensation is possible.

4. **Passive condensation.** At the higher pressure, condense O₂ at roughly
   120–132 K against a passive night-sky radiator. Oxygen's saturation
   temperature is about 90 K at 1 bar, about 120 K at 10 bar, and about 132 K
   at 20 bar. The critical point is about 154.6 K and 50.4 bar. These phase
   data are reported in the [NIST Chemistry WebBook oxygen record](https://webbook.nist.gov/cgi/cbook.cgi?ID=C7782447&Mask=1E).

5. **Pressurized LOX storage.** Store the condensed oxygen as ordinary
   pressurized liquid oxygen (LOX) in insulated tanks. Within the available
   cold-end capacity, the passive radiator rejects the heat from re-condensing
   tank boil-off, and the condensate returns to storage. The baseline has no
   frost cavern and no deep-cryo tail.

6. **Explicit excess handling.** The cold train has a finite capacity. Oxygen
   above that capacity is vented and reported as an oxygen stream, rather than
   being treated as unexplained loss. On an airless body, the Ca/Mg hard-vacuum
   stage likewise vents its co-evolved oxygen directly to space because that
   oxygen is not part of the millibar thermal train.

## Two design principles

### 1. Expansion does not remove heat

An expansion nozzle trades enthalpy for directed kinetic energy. In a steady
flow description, stagnation enthalpy is approximately

```text
h₀ = h + v²/2
```

The flow can become colder while it accelerates, but it re-heats when the
kinetic energy is dissipated in a diffuser, wall impact, or reservoir. An
expansion ratio therefore does not provide a heat sink. Radiators reject heat;
a work-producing expander can export energy as shaft work; and the product and
vent streams carry their own energy out of the train.

### 2. Reject heat hot, and lift the cold end with compression

Radiated power grows approximately with the fourth power of radiator
temperature, `P_rad ∝ εσT⁴`. Latent heat and other high-temperature loads
should therefore be rejected in hot ceramic and radiator sections. The oxygen
compressor then raises the cold-end pressure and saturation temperature until
passive night-sky radiators can condense the product. This uses compression to
move the cold end into a workable radiator range instead of adding a deep-cryo
refrigeration tail.

## Simulator status

This is the current design baseline: pressurized LOX storage, passive
condensation near 120–132 K, and peak-mass-flow sizing. The simulator still
carries the earlier frost-cavern representation in its thermal-train model.
Updating the code and reports to represent the pressurized-LOX baseline is
planned work.
