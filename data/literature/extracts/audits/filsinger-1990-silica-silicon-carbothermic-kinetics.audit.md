# Filsinger and Bourrie (1990) extraction audit — draft

## Source coverage

The scanned seven-page article has no text layer. MinerU identifies Table I on
PDF page 2, Table II on page 4, and Table III on page 7. Table II has 10
experiment rows; Table III has 8 kinetic rows (six reactions, with two
crucible-vent conditions for two reactions). The Table II and III row/column
layouts were checked against page renders. Table III's comma-decimal slope
`−6,10` is `−6.10`; the printed table confirms the minus signs, temperature
ranges, activation energies, and frequency factors.

The relevant kinetics are the six reactions summarized in Table III. The paper
derives first-order rate constants from isothermal weight responses and fits
`ln k = A(10^4/T) + B`; Table III reports the fit range, A, B, activation
energy, and preexponential factor. Figure 6 plots Arrhenius data; Figures 3 and
4 show example weight/time data. Numerical figure reads are excluded by this
extraction wave, so those plotted inputs are not stored as observations.

The reported common carrier gas is helium with `<0.5 ppm` oxygen. The average
helium flow is `3 mm/s`; samples were heated at `20 °C/min`. The stated `70
kPa` is the estimated tamping pressure on the sample, not chamber pressure.
The article says high-temperature analyses above `1600 °C` were conducted at
atmospheric pressure, but Table III ranges either straddle that threshold or
lie below it. No total-pressure value is assigned to those fit rows. Table
III's open, 0.4 mm vent, and loose-lid conditions are retained row by row.
Carbon and SiC reactant systems remain distinct from the SiO(s) vaporization
and SiO2+Si reaction rows.

## Schema and evidence issue

The printed kinetic rate constant, Arrhenius coefficients, activation energy,
and frequency factor have no exact matching quantity in
`simulator/battery/enums.py::Quantity`. `LOG10_K_REACTION` is an equilibrium
quantity, while `MASS_LOSS_RATE` is a mass/time quantity. The closed observation
set is `tools/validate_literature_extracts.py::OBSERVATION_TYPES`; its
`rate_series` mapping in `simulator/battery/migrate.py::TYPE_QUANTITY` defaults
to `MASS_LOSS_RATE`. These fitted outputs therefore need descriptive
non-token quantity labels and typed refusal; do not bind either near-miss.

The task-specific brief directs that fitted constants be `measured_reduced`
with located raw inputs. The shared evidence rule requires stored input rows
for that class, or `quoted_attributed` when the inputs cannot be stored. The
raw numerical time/weight and Arrhenius data are available only in figures,
whose values this wave prohibits extracting. Controller ruling is needed on
this evidence-class conflict before the final extract is committed.
