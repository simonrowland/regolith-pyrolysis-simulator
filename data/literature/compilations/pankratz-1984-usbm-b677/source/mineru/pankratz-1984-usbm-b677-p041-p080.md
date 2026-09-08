Calculate the resulting change in entropy; part 2: Calculate the overall entropy change if the mixing is followed by heating the gas mixture to 600 K at 1 atm. Assume that $C_{p}^{\circ} = 2.5$ R for each gas.

Solution: The properties of each ideal gas are independent of the presence of other gases in a gas mixture, and dependent on temperature and its own pressure, which is called its partial pressure in the mixture. Therefore, it is necessary to compute $P_{j}$ for each gas j in each part.

Part 1: Each gas expands upon mixing and occupies the entire volume of mixture at the same temperature; therefore, $P_{Ar} = 1/4$ and T = 300 K after mixing, because $P_{Ar} + P_{Ne} = 1$ in the mixture. The energy of an ideal gas is a function of temperature only, i.e., $E = E(T)$ , and the differential of E at constant temperature is zero, i.e., $d(E)_{T} = 0$ ; thus, the integration of equation 44 from 1 atm to 1/4 atm gives

$$
\int_ {P = 1} ^ {1 / 4} d S = S (A r \text { in   mixt. }) - 0. 2 5 S ^ {\circ} (A r) = \int_ {P = 1} ^ {1 / 4} \frac {P d V}{T}, \tag {118}
$$

where $S^{\circ}(Ar)$ is the molar entropy of pure Ar at 1 atm. Differentiation of PV = 0.25RT at constant temperature yields PdV = -VdP, and further, $V = (0.25RT/P)$ ; therefore, $PdV = -(0.25RT/P)dP$ . Substitution in equation 118 gives

$$
S (\text { Ar   in   mixt. }) - 0. 2 5 S ^ {\circ} (\text { Ar }) = - \int_ {P = 1} ^ {1 / 4} \frac {0 . 2 5 R d P}{P} = - 0. 2 5 R \ln \frac {1 / 4}{1} = 0. 3 4 6 6 R. \tag {119}
$$

The same type of calculation for 0.75 mol of Ne at P = 3/4 in the gas mixture yields

$$
\mathrm{S} (\mathrm{Ne} \text { in   mixt. }) - 0. 7 5 \mathrm{S} ^ {\circ} (\mathrm{Ne}) = - 0. 7 5 \mathrm{R} \ln (3 / 4) = 0. 2 1 5 8 \mathrm{R}. \tag {120}
$$

Addition of these equations, with the notation that S(1-mol mixt.) - 0.25S°(Ar) - 0.75S°(Ne) ≡ ΔS(mixing per mol of mixture) ≡ ΔS(mixing), yields

$$
\Delta S (\text { mixing }) = - 0. 2 5 \mathrm{Rln} (1 / 4) - 0. 7 5 \mathrm{Rln} (3 / 4) = 0. 5 6 2 4 \mathrm{R}. \tag {121}
$$

The generalized form of this equation for c components, each with its mol fraction of $x_{i}$ , is

$$
\Delta S (\text { mixing }) = - R \sum_ {i = 1} ^ {c} x _ {i} \ln x _ {i}. \tag {122}
$$

(It may be noted that if the pressure of the gas mixture were 4 atm instead of 1 atm, the change in the entropy of Ar would have been zero.) This equation is also used for calculating the ideal entropy of mixing of 1 mol of alloy from $x_{i}$ mol of pure component metals in pure state at 1 atm pressure.

Part 2: The gas mixture is an ideal monatomic gas itself, for which $C_{p}^{\circ} = 2.5R$ ; therefore,

$$
\begin{array}{l} S (\text { mixt.   at   600   K }) - S (\text { mixt.   at   300   K }) = \int_ {3 0 0} ^ {6 0 0} \frac {C _ {p} ^ {\circ}}{T} d T \tag {123} \\ = 2. 5 \mathrm{R} 1 \mathrm{n} \frac {6 0 0}{3 0 0} = 1. 7 3 2 9 \mathrm{R}. \\ \end{array}
$$

The total change in entropy for both steps is therefore 0.5624R + 1.7329R = 2.2953R.

Example 17: One mol of $CH_{4}$ is sealed in a chamber at 0.330 atm and 300 K, and heated to 800 K at constant volume. Assume that $CH_{4}(g)$ dissociates according to

$$
\mathrm{CH} _ {4} (\mathrm{g}) \rightarrow \mathrm{C} (\mathrm{gr}) + 2 \mathrm{H} _ {2} (\mathrm{g}),
$$

without other concurrent reactions. The final pressure in the chamber is 1.197 atm. Calculate $K_{p}$ as follows: Part 1, by using the pressure change; part 2, from the appropriate tables.

Solution: Part 1: It is evident that if there were no dissociation, the pressure at 800 K would have been directly proportional to the temperature in K, i.e., P = 0.330 x 800/300 = 0.880 atm. However, owing to the change in the number of mols of gaseous species after dissociation, the pressure is higher than 0.880 atm. Thus, from the complete dissociation of x mols of methane out of 1 mol, there are 1 - x, x, and 2x mols of $CH_{4}$ , C, and $H_{2}$ , respectively, at equilibrium, i.e.,

$$
\mathrm{CH} _ {4} (\mathrm{g}) = \mathrm{C} (\mathrm{gr}) + 2 \mathrm{H} _ {2} (\mathrm{g}); \tag {124}
$$

$$
(1 - x) \quad (x) \quad (2 x)
$$

hence, the total number of gaseous mols is $(1 - x) + 2x = 1 + x$ . From the observed pressure and from the ideal gas law, $1.197V = (1 + x)800R$ , and from the initial conditions, the volume is given by $0.330V = 300R$ ; division of these equations side by side gives

$$
\frac {1 . 1 9 7 \mathrm{V}}{0 . 3 3 0 \mathrm{V}} = \frac {(1 + \mathrm{x}) 8 0 0 \mathrm{R}}{3 0 0 \mathrm{R}}; \tag {125}
$$

hence,

$$
1. 1 9 7 = (1 + x) 0. 8 8 0, \quad \text { and } \quad x = 0. 3 6 0 2. \tag {126}
$$

The equilibrium constant is therefore

$$
K _ {p} = \frac {\left(\frac {2 x}{1 + x} 1 . 1 9 7\right) ^ {2}}{\left(\frac {1 - x}{1 + x} 1 . 1 9 7\right)} = 1. 1 9 7 \frac {(0 . 5 3 0) ^ {2}}{0 . 4 7 0 4} = 0. 7 1 5. \tag {127}
$$

Part 2: The dissociation reaction is the reverse of the formation reaction from the pure components. The table for $\mathrm{CH}_{4}(\mathrm{~g})$ gives $\Delta Gf_{800}^{\circ} = -533$ , therefore, for the dissociation reaction, $\Delta G_{800}^{\circ}(\mathrm{dissoc.}) = +533$ , and

$$
5 3 3 = - 8 0 0 \mathrm{R} 1 \ln \mathrm{K} _ {\mathrm{p}}; \quad \mathrm{K} _ {\mathrm{p}} = 0. 7 1 5, \tag {128}
$$

which is in agreement with the experimental observation.

Example 18: Calculate the partial pressure of $O_{2}$ , $P_{O_{2}}$ , over (1) $\mathrm{Na_{2}O(c)} + \mathrm{Na_{2}O_{2}(c)}$ mixtures at 800 K and (2) $\mathrm{C(gr)} + \mathrm{CO(g)}$ at 2000 K when $P_{CO} = 1$ atm; (3) calculate also $P_{O_{2}}$ in a gas mixture containing an equimolar mixture of CO and $CO_{2}$ at a total pressure of 1 atm and 2000 K.

Part 1: The reaction that yields oxygen is

$$
\mathrm{Na} _ {2} \mathrm{O} _ {2} (\mathrm{c}) \rightarrow \mathrm{Na} _ {2} \mathrm{O} (\mathrm{c}) + 0. 5 \mathrm{O} _ {2} (\mathrm{g}); \quad \mathrm{K} _ {\mathrm{p}} = \mathrm{P} _ {\mathrm{O} _ {2}} ^ {0. 5}. \tag {129}
$$

It is assumed that the solid species are mutually insoluble; therefore, the equilibrium constant is simply $P_{O_{2}}^{0.5}$ . From the tables, $\Delta G^{\circ}$ for this reaction is

$$
\Delta G ^ {\circ} = - 7 3 3 0 0 + 8 0 6 8 4 = - 8 0 0 \mathrm{RlnP} _ {\mathrm{O} _ {2}} ^ {0 \cdot 5}. \tag {130}
$$

Hence, $P_{O_{2}} = 9.24 \times 10^{-5}$ atm, or 0.07 mm Hg. Therefore, it is possible to dissociate $\mathrm{Na_{2}O_{2}(c)}$ in a vacuum furnace at 800 K at a pressure less than 0.07 mm Hg, which is readily attained with a mechanical vacuum pump.

Part 2: The oxygen pressure in this case is calculated from

$$
C (\mathrm{gr}) + 0. 5 \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} (\mathrm{g}); \quad \mathrm{K} _ {\mathrm{p}} = \frac {\mathrm{P} _ {\mathrm{co}}}{\mathrm{P} _ {\mathrm{o} _ {2}} ^ {0 . 5}}. \tag {131}
$$

Since $P_{co} = 1$ , then $K_{p} = P_{o_{2}}^{-0.5}$ . The required data, equation, and $P_{o_{2}}$ are as follows:

$$
\Delta G _ {2 0 0 0} ^ {8} = - 6 8 3 4 3 = - 2 0 0 0 \mathrm{R} 1 \mathrm{nP} _ {\mathrm{o} _ {2}} ^ {- 0. 5}; \quad \mathrm{P} _ {\mathrm{o} _ {2}} = 1. 1 6 \mathrm{x} 1 0 ^ {- 1 5}. \tag {132}
$$

Part 3: The reaction, equilibrium constant, and result are as follows:

$$
\mathrm{CO} _ {2} = \mathrm{CO} + 0. 5 \mathrm{O} _ {2};
$$

$$
\begin{array}{l} \Delta G ^ {\circ} = \Delta G ^ {\circ} f (C O) - \Delta G ^ {\circ} f \left(C O _ {2}\right) = - 6 8 3 4 3 + 9 4 7 2 8 = - 2 0 0 0 R 1 n \frac {P _ {c o} P ^ {0 . 5}}{P c o _ {2}} \\ = - 2 0 0 0 \mathrm{R} \ln \mathrm{P} _ {\mathrm{O} _ {2}} ^ {0. 5}, \tag {133} \\ \end{array}
$$

$$
P _ {O _ {2}} = 1. 7 1 \mathrm{x} 1 0 ^ {- 6}. \tag {134}
$$

In equation 133, $P_{co}:P_{co_{2}}$ is unity because molar concentrations of CO and $CO_{2}$ were given as equal. Observe that from pure CO to equimolar mixtures of CO and $CO_{2}$ , the oxygen pressure varies from $1.16 \times 10^{-15}$ to $1.71 \times 10^{-6}$ , or roughly a factor of 1 billion. This interesting control of oxygen pressure by changing $CO:CO_{2}$ ratios (as well as $H_{2}O:H_{2}$ ratios in similar separate equilibria) is used in studying oxidation-reduction equilibria, and activity and solubility of oxygen in metals (5). The succeeding example illustrates an oxidation-reduction equilibrium.

Example 19: Calculate the equilibrium molar ratio of $CO_{2}:CO$ at 1600 K for the following reaction:

$$
\mathrm{NiO} (\mathrm{c}) + \mathrm{CO} (\mathrm{g}) = \mathrm{Ni} (\mathrm{c}) + \mathrm{CO} _ {2}; \quad \mathrm{K} _ {\mathrm{p}} = \frac {\mathrm{P} _ {\mathrm{co} _ {2}}}{\mathrm{P} _ {\mathrm{co}}}. \tag {135}
$$

The reaction does not change the number of gas molecules; therefore, $K_{p}$ is also equal to the molar ratio of $CO_{2}:CO$ .

Solution: For reaction 135, $\Delta G^{\circ} = \Delta Gf^{\circ}(CO_{2}) - \Delta Gf^{\circ}(NiO) - \Delta Gf^{\circ}(CO)$ , and substitution of the respective values for $\Delta Gf^{\circ}$ at 1600 K from the tables gives

$$
\begin{array}{l} \Delta G ^ {\circ} = - 9 4 7 2 5 \left(\mathrm{CO} _ {2}\right) + 2 3 5 7 9 (\mathrm{NiO}) + 6 0 2 7 9 (\mathrm{CO}) \\ = - 1 0 8 6 7 = - 1 6 0 0 \mathrm{R} 1 \mathrm{nK} _ {\mathrm{p}}; \mathrm{K} _ {\mathrm{p}} = \mathrm{CO} _ {2} / \mathrm{CO} = 3 0. 5. \tag {136} \\ \end{array}
$$

It is evident that $P_{CO_{2}}:P_{CO}$ is the same as the molar ratio $CO_{2}:CO$ because a pressure change of a few atmospheres for $CO_{2} + CO$ gas mixture has no effect on this reaction. Thus, a gas mixture of 96.83 mol pct $CO_{2}$ and 3.17 mol pct CO is in equilibrium with Ni and NiO at 1600 K. Similar calculations can be carried out (11) with $H_{2}O + H_{2}$ instead of $CO_{2} + CO$ , i.e., by starting with $\mathrm{NiO(c)} + \mathrm{H}_{2}(\mathrm{g}) = \mathrm{Ni(c)} + \mathrm{H}_{2}\mathrm{O}(\mathrm{g})$ .

Example 20: Calculate the composition of a gaseous mixture of $S_{i}(i = 1 \text{ to } 8)$ at 1 atm total pressure of all $S_{i}$ species as follows: Part 1, at 1000 K, and part 2 at 2000 K.

Solution: There are eight sulfur species, and we take 1 mol of all the species as the basis so that the mol fraction and the partial pressure of each species are identical. It is simple to take the chemical symbol $S_{i}$ (i = 1, 2, ....8) the same as the mol fraction of $S_{i}$ to simplify the notation. Equilibria between $S_{2}$ and the remaining seven species have been selected to express seven equilibria as shown in the tables, e.g.,

$$
\begin{array}{l} [ 0. 5 S _ {2} (g) = S (g); K _ {P _ {1}} = \frac {S _ {1}}{S _ {2} ^ {0 . 5}} ]; [ 1. 5 S _ {2} (g) = S _ {3} (g); K _ {P _ {2}} = \frac {S _ {3}}{S _ {2} ^ {1 . 5}} ]; \\ \dots (4 S _ {2} (g) = S _ {8} (g); K _ {p _ {7}} = \frac {S _ {8}}{S _ {2} ^ {4}} ]. \tag {137} \\ \end{array}
$$

Part 1: There are seven equilibrium constants for the eight species, and the eighth equation is the sum of the mol fractions, which must be equal to 1. The equilibrium constants calculated from $\Delta G_{1000}^{\circ} = -1000R\ln K_{pi}$ are as follows:

$$
\frac {S _ {1}}{S _ {2} ^ {0} \cdot^ {5}} = 6. 9 3 \mathrm{x} 1 0 ^ {- 9}; \frac {S _ {3}}{S _ {2} ^ {1} \cdot^ {5}} = 0. 0 9 8 3 9; \frac {S _ {4}}{S _ {2} ^ {2}} = 9. 0 6 2 \mathrm{x} 1 0 ^ {- 3}; \frac {S _ {5}}{S _ {2} ^ {2} \cdot^ {5}} = 0. 0 2 7 0 8;
$$

$$
\frac {S _ {6}}{S _ {2} ^ {3}} = 0. 0 1 3 2 9; \frac {S _ {7}}{S _ {2} ^ {3} \cdot^ {5}} = 5. 0 7 0 \mathrm{x} 1 0 ^ {- 3}; \frac {S _ {8}}{S _ {2} ^ {4}} = 1. 0 3 4 \mathrm{x} 1 0 ^ {- 3}. \tag {138}
$$

The eighth equation is the sum of the mol fractions, i.e.,

$$
\sum_ {i = 1} ^ {8} S _ {i} = 1 = 6. 9 3 \times 1 0 ^ {- 9} S _ {2} ^ {0. 5} + S _ {2} + 0. 0 9 8 3 9 S _ {2} ^ {1. 5} + 9. 0 6 2 \times 1 0 ^ {- 3} S _ {2} ^ {2} + 0. 0 2 7 0 8 S _ {2} ^ {2. 5}
$$

$$
+ 0. 0 1 3 2 9 \mathrm{S} _ {2} ^ {3} + 5. 0 7 0 \mathrm{x} 1 0 ^ {- 3} \mathrm{S} _ {2} ^ {3} \cdot^ {5} + 1. 0 3 4 \mathrm{x} 1 0 ^ {- 3} \mathrm{S} _ {2} ^ {4}. \tag {139}
$$

This equation can be solved by successive approximations. Since $S_{2}$ has to be less than 1, we start with $S_{2} = 0.9$ and proceed toward $S_{2} = 0$ ; thus,

$$
[ S _ {2} = 0. 9; \text { equation } 1 3 9 = 1. 0 2 6 0 3 ]; [ S _ {2} = 0. 8; \text { equation } 1 3 9 = 0. 9 0 1 2 5 ].
$$

These results show that for an interval of 0.1 in $S_{2}$ , equation 139 decreases 1.02603 - 0.90125 = 0.12478, so that for a decrease of 1.02603 - 1.0000 = 0.02603, $S_{2}$ should decrease from 0.9 by $(0.02603/0.12478)x0.1 \approx 0.02$ , i.e., $S_{2} \approx 0.88$ . Substitution of $S_{2} = 0.87$ and $S_{2} = 0.88$ in equation 139 gives

$$
[ S _ {2} = 0. 8 7; \text { equation } 1 3 9 = 0. 9 8 8 2 8 ]; [ S _ {2} = 0. 8 8; \text { equation } 1 3 9 = 1. 0 0 0 8 3 ].
$$

These results would yield a slight refinement over $S_{2} = 0.88$ to make $S_{2} = 0.87934$ by linear interpolation similar to that carried out in the preceding approximation. Thus, equation 139 is satisfied well within the five significant figures selected for $S_{2}$ . The remaining mol fractions are calculated from the equilibrium constants in equation 138 by using $S_{2} = 0.87934$ . All the results are as follows:

$$
\frac {S _ {1}}{6 . 5 0 \times 1 0 ^ {- 9}} \quad \frac {S _ {2}}{0 . 8 7 9 3 4} \quad \frac {S _ {3}}{0 . 0 8 1 1 3} \quad \frac {S _ {4}}{0 . 0 0 7 0 1} \quad \frac {S _ {5}}{0 . 0 1 9 6 4} \quad \frac {S _ {6}}{0 . 0 0 9 0 4} \quad \frac {S _ {7}}{0 . 0 0 3 2 3} \quad \frac {S _ {8}}{0 . 0 0 0 6 2}
$$

The equilibrium constants indicate that as the total pressure decreases by orders of magnitude the predominant species becomes $S_{2}$ . This can be verified by the reader by repeating the foregoing calculations at 0.01 atm.

Part 2: The equilibrium constants at 2000 K are

$$
\begin{array}{l} \frac {S _ {1}}{S _ {2} ^ {0 . 5}} = 3. 1 7 4 \mathrm{x} 1 0 ^ {- 3}; \frac {S _ {3}}{S _ {2} ^ {1 . 5}} = 5. 3 0 3 \mathrm{x} 1 0 ^ {- 3}; \frac {S _ {4}}{S _ {2} ^ {2}} = 8. 8 0 \mathrm{x} 1 0 ^ {- 6}; \frac {S _ {5}}{S _ {2} ^ {2 . 5}} = 4. 2 6 \mathrm{x} 1 0 ^ {- 7}; \\ \frac {S _ {6}}{S _ {2} ^ {3}} = 1. 6 0 \mathrm{x} 1 0 ^ {- 9}; \frac {S _ {7}}{S _ {2} ^ {3} \cdot^ {5}} = 2. 8 4 \mathrm{x} 1 0 ^ {- 1 1}; \frac {S _ {8}}{S _ {2} ^ {4}} = 1. 1 4 \mathrm{x} 1 0 ^ {- 1 3}. \tag {140} \\ \end{array}
$$

The species beyond $S_{4}$ are negligible; hence,

$$
\sum_ {i = 1} ^ {4} S _ {i} = 1 = 3. 1 7 4 \times 1 0 ^ {- 3} S _ {2} ^ {0} \cdot^ {5} + S _ {2} + 5. 3 0 3 \times 1 0 ^ {- 3} S _ {2} ^ {1} \cdot^ {5} + 8. 8 0 \times 1 0 ^ {- 6} S _ {2} ^ {2}. \tag {141}
$$

This equation is satisfied by $S_{2} = 0.99159$ as obtained by successive approximations. The composition of the gas is

$$
\frac {S _ {1}}{0 . 0 0 3 1 6} \frac {S _ {2}}{0 . 9 9 1 5 9} \frac {S _ {3}}{0 . 0 0 5 2 4} \frac {S _ {4}}{8 . 7 \times 1 0 ^ {- 6}} \frac {S _ {5}}{4 . 2 \times 1 0 ^ {- 7}} \frac {S _ {6}}{1 . 6 \times 1 0 ^ {- 9}} \frac {S _ {7}}{2 . 8 \times 1 0 ^ {- 1 1}} \frac {S _ {8}}{1 . 1 \times 1 0 ^ {- 1 3}}
$$

These results show that the simpler molecules, such as S and $S_{2}$ , tend to dominate in the gas mixture as the temperature is increased. For more complex problems involving many reactions and numerous species, a computer is necessary. Several computer programs are available for this purpose $(4, 7)$ .

Example 21: One mol of $C_{2}H_{4}$ and 3 mols of $H_{2}$ react at 1000 K and 1 atm of pressure as follows:

(I): $C_{2}H_{4}(g) + 2H_{2}(g) = 2CH_{4}(g)$ ; $\Delta G_{1000}^{\circ}/1000R = -9.638$ ;   
(II): $C_{2}H_{4}(g) + H_{2}(g) = C_{2}H_{6}(g)$ ; $\Delta G_{1000}^{\circ}/1000R = +1.166$ .

Assuming that there are no other concurrent reactions, calculate the number of mols of each species at equilibrium.

Solution: We write the number of mols at equilibrium under each species and use the atomic balance as follows:

$$
\text {(I)}: \quad \begin{array}{l} C _ {2} H _ {4} + 2 H _ {2} = 2 C H _ {4}; \\ (v) (x) (y) \end{array} \quad K _ {p} (I) = \exp \left(- \frac {\Delta G ^ {\circ}}{R T}\right) = \exp (9. 6 3 8) = 1 5 5 3 7; \tag {142}
$$

$$
\text {(II)}: \begin{array}{l} \mathrm{C} _ {2} \mathrm{H} _ {4} + \mathrm{H} _ {2} = \mathrm{C} _ {2} \mathrm{H} _ {6}; \\ (\mathrm{v}) \quad (\mathrm{x}) \quad (\mathrm{z}) \end{array} \quad \mathrm{K} _ {\mathrm{p}} (\mathrm{II}) = \exp \left(- \frac {\Delta \mathrm{G} ^ {\circ}}{\mathrm{RT}}\right) = \exp (- 1. 1 6 6) = 0. 3 1 1 6. \tag {143}
$$

$$
\text {   Atoms   of   C:   } 2 = 2 \mathrm{v} + \mathrm{y} + 2 \mathrm{z}; \quad \mathrm{v} = 1 - 0. 5 \mathrm{y} - \mathrm{z}, \tag {144}
$$

$$
\text {   Atoms   of   H:   } 1 0 = 4 \mathrm{v} + 2 \mathrm{x} + 4 \mathrm{y} + 6 \mathrm{z}. \tag {145}
$$

Substitution of v from the first equation into the second gives 10 = 4 - 2y - 4z + 2x + 4y + 6z = 4 + 2y + 2z + 2x, from which

$$
z = 3 - x - y. \tag {146}
$$

In turn, the substitution of the right side of this equation for z in the equation for v gives

$$
\mathrm{v} = \mathrm{x} + 0. 5 \mathrm{y} - 2. \tag {147}
$$

These equations eliminate v and z in the succeeding equations. The total number of mols of gaseous species is

$$
v + x + y + z = x + 0. 5 y + 1. \tag {148}
$$

The mol fraction is the same as the partial pressure at 1 atm total pressure, e.g., for $CH_{4}$ , the mol fraction is $y/(x + 0.5y + 1)$ . The substitution of the mol fractions in the equilibrium constants yields

$$
\text {(III)}: \quad K _ {P} (I) = \frac {y ^ {2} (x + 0 . 5 y + 1)}{v x ^ {2}} = \frac {y ^ {2} (x + 0 . 5 y + 1)}{(x + 0 . 5 y - 2) x ^ {2}} = 1 5 3 3 7; \tag {149}
$$

$$
\text {(IV)} \colon \quad \mathrm{K} _ {\mathrm{p}} (\mathrm{II}) = \frac {\mathrm{z} (\mathrm{x} + 0 . 5 \mathrm{y} + 1)}{\mathrm{vx}} = \frac {(3 - \mathrm{x} - \mathrm{y}) (\mathrm{x} + 0 . 5 \mathrm{y} + 1)}{(\mathrm{x} + 0 . 5 \mathrm{y} - 2) \mathrm{x}} = 0. 3 1 1 6. \tag {150}
$$

$\Delta G^{\circ}$ for reaction I has a fairly large negative value; therefore, it is reasonable to assume that reaction I is near completion, or $y \approx 2$ . Substitute this value in III to obtain

$$
(1 5 3 3 7 / 4) (\mathrm{x} - 1) \mathrm{x} ^ {2} - \mathrm{x} - 2 = 0. \tag {151}
$$

For x = 1, the left side is -3.0; for x = 1.001, the left side is +0.841. Therefore, an increase of 0.001 in x causes a total change of -3 - (0.841) = -3.841; a change of -3 in the left side of equation 151 to make it zero would correspond to $\Delta x = -3[0.001/(-3.841)] = 0.00078$ . Therefore, x = 1.00078. Substitute this value of x in IV to obtain

$$
0. 5 \mathrm{y} ^ {2} + 1. 1 5 7 0 9 2 \mathrm{y} - 4. 3 1 1 6 0 0 = 0; \mathrm{y} = 1. 9 9 9 1 8, \tag {152}
$$

where y is the positive root of this quadratic equation. Next substitute this value of y in III to obtain x = 1.001178 by successive approximations. Again set x = 1.001178 in IV, get y = 1.998764; eight more sets of approximations give y = 1.998307 and x = 1.001613. To test the results, substitute in $K_{p}(I)$ and $K_{p}(II)$ as follows:

$$
\mathrm{K} _ {\mathrm{p}} (\mathrm{I}) = \frac {(1 . 9 9 8 3 0 7) ^ {2} (1 . 0 0 1 6 1 3 + 0 . 9 9 9 1 5 4 + 1)}{(1 . 0 0 1 6 1 3 + 0 . 9 9 9 1 5 4 - 2) (1 . 0 0 1 6 1 3) ^ {2}} = 1 5 5 8 3; \tag {153}
$$

$$
K _ {P} (I I) = \frac {(3 - 1 . 0 0 1 6 1 3 - 1 . 9 9 8 3 0 7) (1 . 0 0 1 6 1 3 + 0 . 9 9 9 1 5 4 + 1)}{(1 . 0 0 1 6 1 3 + 0 . 9 9 9 1 5 4 - 2) (1 . 0 0 1 6 1 3)} = 0. 3 1 2 7. \tag {154}
$$

The first set of parentheses in the denominators contain 0.000767; therefore, the approximation should be carried out to six repeatable decimal places. This example indicates the complexity of multispecies and multireaction equilibrium calculations.

1. Devereux, O. F. Topics in Metallurgical Thermodynamics. Wiley, 1983, 494 pp.   
2. Dow Chemical Co., Thermal Research Laboratory. JANAF Thermochemical Tables. NSRDS-NBS 37, 2d ed., 1971, 1141 pp.; supplements available from Dow Chemical Co., Therm. Res. Lab., Midland, MI.   
3. Gaskell, D. R. Metallurgical Thermodynamics. McGraw-Hill, 2d ed., 1981, 560 pp.   
4. Gautam, R., and W. D. Seider. Computation of Phase and Chemical Equilibrium. AIChE J., v. 25, 1979, pp. 991-1015. (This article contains exhaustive references to previous computational methods.)   
5. Gokcen, N. A. Equilibria in Reactions of Hydrogen, and Carbon Monoxide With Dissolved Oxygen in Liquid Iron; Equilibrium in Reduction of Ferrous Oxide With Hydrogen, and Solubility of Oxygen in Liquid Iron. Trans. AIME, v. 206, 1956, pp. 1558-1567.   
6. Gokcen, N. A. Thermodynamics. Techscience Inc., Hawthorne, CA, 1975, 460 pp.   
7. Gordon, S., and. B. J. McBride. Computer Program for Calculation of Complex Chemical Equilibrium Compositions, Rocket Performance, Incident and Reflected Shocks, and Chapman-Jonquet Detonations. NASA SP-273, 1971, 250 pp.   
8. Kelley, K. K. Contributions to the Data on Theoretical Metallurgy. XV. A Reprint of Bulletins 383, 384, 393, and 406. BuMines B 601, 1962, 525 pp.   
9. Lupis, C. H. P. Chemical Thermodynamics of Materials. North-Holland Publ. Co., Amsterdam, 1983, 608 pp.   
10. Martin, L. R., and N. A. Gokcen. Solutions Manual for Thermodyanmics. Techscience Inc., Hawthorne, CA, 1978, 173 pp.   
11. Pankratz, L. B. Thermodynamic Properties of Elements and Oxides. Bu-Mines B 672, 1982, 509 pp.

# CHAPTER 2.--TABLES OF THERMODYNAMIC PROPERTIES

# Elements

Units: T is in K; $Cp^{\circ}$ and $S^{\circ}$ are in cal/mol·K; $H^{\circ} - H_{298}^{\circ}$ , $\Delta Hf^{\circ}$ and $\Delta Gf^{\circ}$ are in kcal/mol.
Source of data: Pankratz (24). $^{1}$

$\mathbf{Ag}(\mathbf{c},1)$   
Silver 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.070</td><td>10.170</td><td>0</td></tr><tr><td>400</td><td>6.170</td><td>11.965</td><td>.623</td></tr><tr><td>600</td><td>6.436</td><td>14.515</td><td>1.881</td></tr><tr><td>800</td><td>6.751</td><td>16.409</td><td>3.200</td></tr><tr><td>1000</td><td>7.116</td><td>17.953</td><td>4.586</td></tr><tr><td>1200</td><td>7.530</td><td>19.286</td><td>6.049</td></tr><tr><td>1235</td><td>7.607</td><td>19.508</td><td>6.315</td></tr><tr><td>1235</td><td>8.000</td><td>21.694</td><td>9.015</td></tr><tr><td>1400</td><td>8.000</td><td>22.697</td><td>10.334</td></tr><tr><td>1600</td><td>8.000</td><td>23.765</td><td>11.934</td></tr><tr><td>1800*</td><td>8.000</td><td>24.707</td><td>13.534</td></tr><tr><td>2000</td><td>8.000</td><td>25.550</td><td>15.134</td></tr></table>

\*Data extrapolated above 1600 K.   
1235.08 K, melting point; $\Delta H^{\circ} = 2.700$

Ag(g)   
Silver (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.321</td><td>0</td><td>68.090</td><td>58.802</td></tr><tr><td>400</td><td>4.968</td><td>42.781</td><td>.506</td><td>67.973</td><td>55.647</td></tr><tr><td>600</td><td>4.968</td><td>44.795</td><td>1.500</td><td>67.709</td><td>49.541</td></tr><tr><td>800</td><td>4.968</td><td>46.224</td><td>2.493</td><td>67.383</td><td>43.531</td></tr><tr><td>1000</td><td>4.968</td><td>47.333</td><td>3.487</td><td>66.991</td><td>37.611</td></tr><tr><td>1200</td><td>4.968</td><td>48.239</td><td>4.480</td><td>66.521</td><td>31.777</td></tr><tr><td>1400</td><td>4.968</td><td>49.004</td><td>5.474</td><td>63.230</td><td>26.400</td></tr><tr><td>1600</td><td>4.968</td><td>49.668</td><td>6.468</td><td>62.624</td><td>21.179</td></tr><tr><td>1800</td><td>4.968</td><td>50.253</td><td>7.461</td><td>62.017</td><td>16.034</td></tr><tr><td>2000</td><td>4.968</td><td>50.776</td><td>8.455</td><td>61.411</td><td>10.959</td></tr></table>

A1(c,1)   
Aluminum 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.820</td><td>6.776</td><td>0</td></tr><tr><td>400</td><td>6.120</td><td>8.530</td><td>.609</td></tr><tr><td>600</td><td>6.660</td><td>11.110</td><td>1.884</td></tr><tr><td>800</td><td>7.390</td><td>13.120</td><td>3.285</td></tr><tr><td>934</td><td>8.060</td><td>14.303</td><td>4.310</td></tr><tr><td>934</td><td>7.590</td><td>17.067</td><td>6.890</td></tr><tr><td>1000</td><td>7.590</td><td>17.589</td><td>7.395</td></tr><tr><td>1200</td><td>7.590</td><td>18.973</td><td>8.913</td></tr><tr><td>1400</td><td>7.590</td><td>20.143</td><td>10.431</td></tr><tr><td>1600</td><td>7.590</td><td>21.156</td><td>11.949</td></tr><tr><td>1800</td><td>7.590</td><td>22.050</td><td>13.467</td></tr><tr><td>2000*</td><td>7.590</td><td>22.850</td><td>14.985</td></tr><tr><td>2200</td><td>7.590</td><td>23.573</td><td>16.503</td></tr><tr><td>2400</td><td>7.590</td><td>24.234</td><td>18.021</td></tr></table>

\*Data extrapolated above 1800 K.   
933.61 K, melting point; $\Delta H^{\circ} = 2.580$

Al(g)   
Aluminum (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.112</td><td>39.302</td><td>0</td><td>78.800</td><td>69.102</td></tr><tr><td>400</td><td>5.047</td><td>40.794</td><td>.517</td><td>78.708</td><td>65.802</td></tr><tr><td>600</td><td>5.002</td><td>42.830</td><td>1.521</td><td>78.437</td><td>59.405</td></tr><tr><td>800</td><td>4.987</td><td>44.266</td><td>2.520</td><td>78.035</td><td>53.118</td></tr><tr><td>1000</td><td>4.980</td><td>45.378</td><td>3.516</td><td>74.921</td><td>47.132</td></tr><tr><td>1200</td><td>4.976</td><td>46.286</td><td>4.512</td><td>74.399</td><td>41.623</td></tr><tr><td>1400</td><td>4.974</td><td>47.053</td><td>5.507</td><td>73.876</td><td>36.202</td></tr><tr><td>1600</td><td>4.972</td><td>47.717</td><td>6.501</td><td>73.352</td><td>30.854</td></tr><tr><td>1800</td><td>4.972</td><td>48.302</td><td>7.496</td><td>72.829</td><td>25.575</td></tr><tr><td>2000</td><td>4.971</td><td>48.826</td><td>8.490</td><td>72.305</td><td>20.353</td></tr></table>

Al $_{2}$ (g)   
Aluminum (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.189</td><td>55.789</td><td>0</td><td>116.400</td><td>103.807</td></tr><tr><td>400</td><td>9.849</td><td>58.590</td><td>.972</td><td>116.154</td><td>99.542</td></tr><tr><td>600</td><td>10.249</td><td>62.689</td><td>2.997</td><td>115.629</td><td>91.348</td></tr><tr><td>800</td><td>10.122</td><td>65.625</td><td>5.037</td><td>114.867</td><td>83.359</td></tr><tr><td>1000</td><td>9.933</td><td>67.862</td><td>7.042</td><td>108.652</td><td>75.968</td></tr><tr><td>1200</td><td>9.790</td><td>69.660</td><td>9.013</td><td>107.587</td><td>69.530</td></tr><tr><td>1400</td><td>9.700</td><td>71.162</td><td>10.962</td><td>106.500</td><td>63.274</td></tr><tr><td>1600</td><td>9.654</td><td>72.453</td><td>12.896</td><td>105.398</td><td>57.172</td></tr><tr><td>1800</td><td>9.640</td><td>73.589</td><td>14.825</td><td>104.291</td><td>51.211</td></tr><tr><td>2000</td><td>9.651</td><td>74.606</td><td>16.754</td><td>103.184</td><td>45.372</td></tr></table>

Am(c,1)   
Americium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.178</td><td>13.023</td><td>0</td></tr><tr><td>400</td><td>6.491</td><td>14.882</td><td>.645</td></tr><tr><td>600</td><td>7.122</td><td>17.632</td><td>2.006</td></tr><tr><td>800</td><td>7.775</td><td>19.770</td><td>3.495</td></tr><tr><td>923</td><td>8.187</td><td>20.911</td><td>4.477</td></tr><tr><td>923</td><td>7.637</td><td>21.111</td><td>4.662</td></tr><tr><td>1000</td><td>7.898</td><td>21.733</td><td>5.260</td></tr><tr><td>1200</td><td>8.616</td><td>23.236</td><td>6.910</td></tr><tr><td>1350</td><td>9.192</td><td>24.284</td><td>8.246</td></tr><tr><td>1350</td><td>9.500</td><td>25.321</td><td>9.646</td></tr><tr><td>1400</td><td>9.500</td><td>25.666</td><td>10.121</td></tr><tr><td>1449</td><td>9.500</td><td>25.993</td><td>10.586</td></tr><tr><td>1449</td><td>10.000</td><td>28.367</td><td>14.026</td></tr><tr><td>1600</td><td>10.000</td><td>29.358</td><td>15.536</td></tr><tr><td>1800</td><td>10.000</td><td>30.536</td><td>17.536</td></tr><tr><td>2000</td><td>10.000</td><td>31.590</td><td>19.536</td></tr></table>

923 K, transition point; $\Delta H^{\circ} = 0.185$   
1350 K, transition point; $\Delta H^{\circ} = 1.400$   
1449 K, melting point; $\Delta H^{\circ} = 3.440$

Am(g)   
Americium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.473</td><td>0</td><td>67.900</td><td>57.927</td></tr><tr><td>400</td><td>4.968</td><td>47.933</td><td>.506</td><td>67.761</td><td>54.541</td></tr><tr><td>600</td><td>4.968</td><td>49.947</td><td>1.500</td><td>67.394</td><td>48.005</td></tr><tr><td>800</td><td>4.968</td><td>51.376</td><td>2.493</td><td>66.898</td><td>41.613</td></tr><tr><td>1000</td><td>4.968</td><td>52.485</td><td>3.487</td><td>66.127</td><td>35.375</td></tr><tr><td>1200</td><td>4.968</td><td>53.391</td><td>4.480</td><td>65.470</td><td>29.284</td></tr><tr><td>1400</td><td>4.968</td><td>54.156</td><td>5.474</td><td>63.253</td><td>23.367</td></tr><tr><td>1600</td><td>4.970</td><td>54.820</td><td>6.468</td><td>58.832</td><td>18.093</td></tr><tr><td>1800</td><td>4.975</td><td>55.405</td><td>7.462</td><td>57.826</td><td>13.062</td></tr><tr><td>2000</td><td>4.989</td><td>55.930</td><td>8.458</td><td>56.822</td><td>8.142</td></tr></table>

Ar(g)   
Argon 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>4.968</td><td>36.983</td><td>0</td></tr><tr><td>400</td><td>4.968</td><td>38.443</td><td>.506</td></tr><tr><td>600</td><td>4.968</td><td>40.457</td><td>1.500</td></tr><tr><td>800</td><td>4.968</td><td>41.886</td><td>2.493</td></tr><tr><td>1000</td><td>4.968</td><td>42.995</td><td>3.487</td></tr><tr><td>1200</td><td>4.968</td><td>43.900</td><td>4.480</td></tr><tr><td>1400</td><td>4.968</td><td>44.666</td><td>5.474</td></tr><tr><td>1600</td><td>4.968</td><td>45.330</td><td>6.468</td></tr><tr><td>1800</td><td>4.968</td><td>45.915</td><td>7.461</td></tr><tr><td>2000</td><td>4.968</td><td>46.438</td><td>8.455</td></tr><tr><td>2200</td><td>4.968</td><td>46.912</td><td>9.448</td></tr><tr><td>2400</td><td>4.968</td><td>47.344</td><td>10.442</td></tr><tr><td>2600</td><td>4.968</td><td>47.742</td><td>11.436</td></tr><tr><td>2800</td><td>4.968</td><td>48.110</td><td>12.429</td></tr><tr><td>3000</td><td>4.968</td><td>48.453</td><td>13.423</td></tr></table>

As[α,1/4As4(g)]   
Arsenic 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H298</td></tr><tr><td>298</td><td>5.892</td><td>8.534</td><td>0</td></tr><tr><td>400</td><td>6.068</td><td>10.295</td><td>.611</td></tr><tr><td>600</td><td>6.334</td><td>12.806</td><td>1.851</td></tr><tr><td>800</td><td>6.599</td><td>14.664</td><td>3.144</td></tr><tr><td>876</td><td>6.700</td><td>15.265</td><td>3.649</td></tr><tr><td>876</td><td>4.923</td><td>24.750</td><td>11.958</td></tr><tr><td>1000</td><td>4.934</td><td>25.402</td><td>12.569</td></tr><tr><td>1200</td><td>4.944</td><td>26.303</td><td>13.557</td></tr></table>

876 K, sublimation point to $As_{4}(g)$ ;   
$\Delta H^{\circ} = 33.235 / \mathrm{mol}$ of $\mathrm{As_4(g)}$

As(g)   
Arsenic (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.611</td><td>0</td><td>72.120</td><td>62.258</td></tr><tr><td>400</td><td>4.968</td><td>43.071</td><td>.506</td><td>72.015</td><td>58.905</td></tr><tr><td>600</td><td>4.968</td><td>45.085</td><td>1.500</td><td>71.769</td><td>52.402</td></tr><tr><td>800</td><td>4.968</td><td>46.514</td><td>2.493</td><td>71.469</td><td>45.989</td></tr><tr><td>1000</td><td>4.968</td><td>47.623</td><td>3.487</td><td>63.038</td><td>40.817</td></tr><tr><td>1200</td><td>4.970</td><td>48.529</td><td>4.480</td><td>63.043</td><td>36.372</td></tr></table>

As $_{2}$ (g)

Arsenic (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.366</td><td>57.546</td><td>0</td><td>52.820</td><td>40.751</td></tr><tr><td>400</td><td>8.594</td><td>60.040</td><td>.866</td><td>52.464</td><td>36.684</td></tr><tr><td>600</td><td>8.778</td><td>63.566</td><td>2.606</td><td>51.724</td><td>28.952</td></tr><tr><td>800</td><td>8.848</td><td>66.102</td><td>4.370</td><td>50.902</td><td>21.483</td></tr><tr><td>1000</td><td>8.882</td><td>68.080</td><td>6.142</td><td>33.824</td><td>16.548</td></tr><tr><td>1200</td><td>8.900</td><td>69.702</td><td>7.920</td><td>33.626</td><td>13.111</td></tr></table>

As $_{3}$ (g)

Arsenic (ideal triatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>14.112</td><td>74.121</td><td>0</td><td>62.480</td><td>44.197</td></tr><tr><td>400</td><td>14.439</td><td>78.321</td><td>1.455</td><td>63.019</td><td>37.867</td></tr><tr><td>600</td><td>14.691</td><td>84.231</td><td>4.374</td><td>64.077</td><td>25.064</td></tr><tr><td>800</td><td>14.781</td><td>88.473</td><td>7.323</td><td>65.087</td><td>11.905</td></tr><tr><td>1000</td><td>14.826</td><td>91.776</td><td>10.284</td><td>53.910</td><td>0.237</td></tr><tr><td>1200</td><td>14.850</td><td>94.482</td><td>13.251</td><td>55.396</td><td>-10.638</td></tr></table>

As $_{4}$ (g)   
Arsenic (ideal tetrameric gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>18.456</td><td>78.232</td><td>0</td><td>36.640</td><td>23.493</td></tr><tr><td>400</td><td>19.056</td><td>83.752</td><td>1.916</td><td>36.112</td><td>19.083</td></tr><tr><td>600</td><td>19.500</td><td>91.580</td><td>5.780</td><td>35.016</td><td>10.802</td></tr><tr><td>800</td><td>19.660</td><td>97.212</td><td>9.696</td><td>33.760</td><td>2.915</td></tr><tr><td>1000</td><td>19.736</td><td>101.608</td><td>13.636</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>19.776</td><td>105.212</td><td>17.588</td><td>0</td><td>0</td></tr></table>

Au(c,1)
Gold 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.075</td><td>11.330</td><td>0</td></tr><tr><td>400</td><td>6.187</td><td>13.133</td><td>.625</td></tr><tr><td>600</td><td>6.397</td><td>15.679</td><td>1.882</td></tr><tr><td>800</td><td>6.597</td><td>17.549</td><td>3.183</td></tr><tr><td>1000</td><td>6.742</td><td>19.036</td><td>4.516</td></tr><tr><td>1200</td><td>7.254</td><td>20.299</td><td>5.903</td></tr><tr><td>1338</td><td>8.306</td><td>21.134</td><td>6.964</td></tr><tr><td>1338</td><td>7.972</td><td>23.345</td><td>9.921</td></tr><tr><td>1400</td><td>7.972</td><td>23.709</td><td>10.419</td></tr><tr><td>1600</td><td>7.972</td><td>24.773</td><td>12.013</td></tr><tr><td>1800</td><td>7.972</td><td>25.712</td><td>13.607</td></tr><tr><td>2000</td><td>7.972</td><td>26.552</td><td>15.202</td></tr></table>

1337.58 K, melting point; $\Delta H^{\circ} = 2.957$

Au(g)
Gold (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>43.116</td><td>0</td><td>87.500</td><td>78.023</td></tr><tr><td>400</td><td>4.968</td><td>44.576</td><td>.506</td><td>87.381</td><td>74.804</td></tr><tr><td>600</td><td>4.968</td><td>46.590</td><td>1.500</td><td>87.118</td><td>68.571</td></tr><tr><td>800</td><td>4.968</td><td>48.019</td><td>2.493</td><td>86.810</td><td>62.434</td></tr><tr><td>1000</td><td>4.970</td><td>49.128</td><td>3.487</td><td>86.471</td><td>56.379</td></tr><tr><td>1200</td><td>4.980</td><td>50.035</td><td>4.482</td><td>86.079</td><td>50.396</td></tr><tr><td>1400</td><td>5.011</td><td>50.805</td><td>5.480</td><td>82.561</td><td>44.627</td></tr><tr><td>1600</td><td>5.075</td><td>51.477</td><td>6.488</td><td>81.975</td><td>39.249</td></tr><tr><td>1800</td><td>5.178</td><td>52.081</td><td>7.513</td><td>81.406</td><td>33.942</td></tr><tr><td>2000</td><td>5.321</td><td>52.633</td><td>8.562</td><td>80.860</td><td>28.698</td></tr></table>

B(beta)
Boron 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>2.650</td><td>1.410</td><td>0</td></tr><tr><td>400</td><td>3.660</td><td>2.330</td><td>.322</td></tr><tr><td>600</td><td>4.990</td><td>4.100</td><td>1.204</td></tr><tr><td>800</td><td>5.580</td><td>5.630</td><td>2.268</td></tr><tr><td>1000</td><td>5.960</td><td>6.920</td><td>3.425</td></tr><tr><td>1200</td><td>6.260</td><td>8.030</td><td>4.648</td></tr><tr><td>1400</td><td>6.560</td><td>9.020</td><td>5.930</td></tr><tr><td>1600</td><td>6.850</td><td>9.910</td><td>7.271</td></tr><tr><td>1800</td><td>7.130</td><td>10.740</td><td>8.669</td></tr><tr><td>2000</td><td>7.410</td><td>11.500</td><td>10.123</td></tr></table>

2350 K, melting point; $\Delta H^{\circ} = 12.0$

B(g)
Boron (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.971</td><td>36.649</td><td>0</td><td>132.800</td><td>122.293</td></tr><tr><td>400</td><td>4.970</td><td>38.110</td><td>.506</td><td>132.984</td><td>118.672</td></tr><tr><td>600</td><td>4.969</td><td>40.125</td><td>1.500</td><td>133.096</td><td>111.481</td></tr><tr><td>800</td><td>4.969</td><td>41.554</td><td>2.494</td><td>133.026</td><td>104.287</td></tr><tr><td>1000</td><td>4.968</td><td>42.663</td><td>3.487</td><td>132.862</td><td>97.119</td></tr><tr><td>1200</td><td>4.968</td><td>43.569</td><td>4.481</td><td>132.633</td><td>89.986</td></tr><tr><td>1400</td><td>4.968</td><td>44.335</td><td>5.475</td><td>132.345</td><td>82.904</td></tr><tr><td>1600</td><td>4.968</td><td>44.998</td><td>6.468</td><td>131.997</td><td>75.856</td></tr><tr><td>1800</td><td>4.968</td><td>45.583</td><td>7.462</td><td>131.593</td><td>68.876</td></tr><tr><td>2000</td><td>4.968</td><td>46.107</td><td>8.456</td><td>131.133</td><td>61.919</td></tr></table>

$B_{2}(g)$ Boron (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>7.301</td><td>48.228</td><td>0</td><td>198.500</td><td>184.962</td></tr><tr><td>400</td><td>7.667</td><td>50.425</td><td>.762</td><td>198.618</td><td>180.312</td></tr><tr><td>600</td><td>8.212</td><td>53.646</td><td>2.355</td><td>198.447</td><td>171.179</td></tr><tr><td>800</td><td>8.521</td><td>56.055</td><td>4.031</td><td>197.995</td><td>162.159</td></tr><tr><td>1000</td><td>8.703</td><td>57.978</td><td>5.755</td><td>197.405</td><td>153.267</td></tr><tr><td>1200</td><td>8.820</td><td>59.576</td><td>7.508</td><td>196.712</td><td>144.493</td></tr><tr><td>1400</td><td>8.902</td><td>60.942</td><td>9.281</td><td>195.921</td><td>135.858</td></tr><tr><td>1600</td><td>8.964</td><td>62.135</td><td>11.068</td><td>195.026</td><td>127.322</td></tr><tr><td>1800</td><td>9.014</td><td>63.194</td><td>12.866</td><td>194.028</td><td>118.943</td></tr><tr><td>2000</td><td>9.056</td><td>64.146</td><td>14.673</td><td>192.927</td><td>110.635</td></tr></table>

Ba(c,1)
Barium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.713</td><td>14.918</td><td>0</td></tr><tr><td>400</td><td>7.897</td><td>17.032</td><td>.738</td></tr><tr><td>582</td><td>13.010</td><td>20.823</td><td>2.607</td></tr><tr><td>600</td><td>8.585</td><td>21.084</td><td>2.761</td></tr><tr><td>768</td><td>10.145</td><td>23.422</td><td>4.358</td></tr><tr><td>800</td><td>10.084</td><td>23.835</td><td>4.682</td></tr><tr><td>1000</td><td>9.777</td><td>26.012</td><td>6.633</td></tr><tr><td>1002</td><td>9.778</td><td>26.032</td><td>6.653</td></tr><tr><td>1002</td><td>10.349</td><td>26.880</td><td>8.505</td></tr><tr><td>1200</td><td>9.765</td><td>29.695</td><td>10.496</td></tr><tr><td>1400*</td><td>9.700</td><td>31.191</td><td>12.437</td></tr><tr><td>1600</td><td>9.700</td><td>32.487</td><td>14.377</td></tr><tr><td>1800</td><td>9.700</td><td>33.629</td><td>16.317</td></tr><tr><td>2000</td><td>9.700</td><td>34.651</td><td>18.257</td></tr></table>

\*Data extrapolated above 1300 K.
582 K, transition point; $\Delta H^{\circ} = 0$ 768 K, transition point; $\Delta H^{\circ} = 0$ 1002 K, melting point; $\Delta H^{\circ} = 1.852$

Ba(g)   
Barium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>40.663</td><td>0</td><td>43.000</td><td>35.324</td></tr><tr><td>400</td><td>4.968</td><td>42.123</td><td>.506</td><td>42.768</td><td>32.732</td></tr><tr><td>600</td><td>4.968</td><td>44.137</td><td>1.500</td><td>41.739</td><td>27.907</td></tr><tr><td>800</td><td>4.968</td><td>45.566</td><td>2.493</td><td>40.811</td><td>23.426</td></tr><tr><td>1000</td><td>4.976</td><td>46.675</td><td>3.487</td><td>39.854</td><td>19.191</td></tr><tr><td>1200</td><td>5.022</td><td>47.586</td><td>4.486</td><td>36.990</td><td>15.521</td></tr><tr><td>1400</td><td>5.171</td><td>48.369</td><td>5.503</td><td>36.066</td><td>12.017</td></tr><tr><td>1600</td><td>5.496</td><td>49.079</td><td>6.566</td><td>35.189</td><td>8.642</td></tr><tr><td>1800</td><td>6.052</td><td>49.756</td><td>7.717</td><td>34.400</td><td>5.371</td></tr><tr><td>2000</td><td>6.848</td><td>50.433</td><td>9.003</td><td>33.746</td><td>2.182</td></tr></table>

Be(c,1)   
Beryllium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>3.930</td><td>2.270</td><td>0</td></tr><tr><td>400</td><td>4.720</td><td>3.540</td><td>.443</td></tr><tr><td>600</td><td>5.610</td><td>5.650</td><td>1.489</td></tr><tr><td>800</td><td>6.080</td><td>7.330</td><td>2.662</td></tr><tr><td>1000</td><td>6.500</td><td>8.730</td><td>3.917</td></tr><tr><td>1200</td><td>6.950</td><td>9.950</td><td>5.262</td></tr><tr><td>1400</td><td>7.380</td><td>11.060</td><td>6.696</td></tr><tr><td>1527</td><td>7.610</td><td>11.710</td><td>7.651</td></tr><tr><td>1527*</td><td>7.700</td><td>12.110</td><td>8.262</td></tr><tr><td>1560</td><td>7.700</td><td>12.270</td><td>8.516</td></tr><tr><td>1560</td><td>7.040</td><td>14.140</td><td>11.435</td></tr><tr><td>1600</td><td>7.040</td><td>14.320</td><td>11.717</td></tr><tr><td>1800</td><td>7.040</td><td>15.140</td><td>13.125</td></tr><tr><td>2000</td><td>7.040</td><td>15.890</td><td>14.533</td></tr><tr><td>2200</td><td>7.040</td><td>16.560</td><td>15.941</td></tr><tr><td>2400</td><td>7.040</td><td>17.170</td><td>17.349</td></tr></table>

\*Enthalpies of transition and fusion and data for Be(β) estimated.   
1527 K, transition point; $\Delta H^{\circ} = 0.611$   
1560 K, melting point; $\Delta H^{\circ} = 2.919$

Be(g)   
Beryllium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>32.545</td><td>0</td><td>77.400</td><td>68.374</td></tr><tr><td>400</td><td>4.968</td><td>34.005</td><td>.506</td><td>77.463</td><td>65.277</td></tr><tr><td>600</td><td>4.968</td><td>36.019</td><td>1.500</td><td>77.411</td><td>59.190</td></tr><tr><td>800</td><td>4.968</td><td>37.449</td><td>2.493</td><td>77.231</td><td>53.136</td></tr><tr><td>1000</td><td>4.968</td><td>38.557</td><td>3.487</td><td>76.970</td><td>47.143</td></tr><tr><td>1200</td><td>4.968</td><td>39.463</td><td>4.481</td><td>76.619</td><td>41.203</td></tr><tr><td>1400</td><td>4.968</td><td>40.229</td><td>5.474</td><td>76.178</td><td>35.341</td></tr><tr><td>1600</td><td>4.968</td><td>40.892</td><td>6.468</td><td>72.151</td><td>29.636</td></tr><tr><td>1800</td><td>4.968</td><td>41.477</td><td>7.461</td><td>71.736</td><td>24.329</td></tr><tr><td>2000</td><td>4.969</td><td>42.001</td><td>8.455</td><td>71.322</td><td>19.100</td></tr></table>

Bi(c,1)   
Bismuth 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.100</td><td>13.560</td><td>0</td></tr><tr><td>400</td><td>6.350</td><td>15.380</td><td>.632</td></tr><tr><td>545</td><td>7.140</td><td>17.440</td><td>1.601</td></tr><tr><td>545</td><td>7.300</td><td>22.400</td><td>4.301</td></tr><tr><td>600</td><td>7.040</td><td>23.090</td><td>4.698</td></tr><tr><td>800</td><td>6.720</td><td>25.070</td><td>6.069</td></tr><tr><td>1000</td><td>6.550</td><td>26.550</td><td>7.395</td></tr><tr><td>1200</td><td>6.500</td><td>27.740</td><td>8.698</td></tr><tr><td>1400</td><td>6.500</td><td>28.740</td><td>9.998</td></tr><tr><td>1600</td><td>6.500</td><td>29.610</td><td>11.298</td></tr><tr><td>1800</td><td>6.500</td><td>30.370</td><td>12.598</td></tr></table>

\*Data extrapolated above 800 K.   
544.59 K, melting point; $\Delta H^{\circ} = 2.700$

Bi(g)   
Bismuth (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td><td> $\Delta Hf^{\circ}$ </td><td> $\Delta Gf^{\circ}$ </td></tr><tr><td>298</td><td>4.968</td><td>44.669</td><td>0</td><td>49.500</td><td>40.225</td></tr><tr><td>400</td><td>4.968</td><td>46.129</td><td>.506</td><td>49.374</td><td>37.074</td></tr><tr><td>600</td><td>4.968</td><td>48.144</td><td>1.500</td><td>46.302</td><td>31.270</td></tr><tr><td>800</td><td>4.968</td><td>49.573</td><td>2.493</td><td>45.924</td><td>26.322</td></tr><tr><td>1000</td><td>4.968</td><td>50.682</td><td>3.487</td><td>45.592</td><td>21.460</td></tr><tr><td>1200</td><td>4.968</td><td>51.587</td><td>4.480</td><td>45.282</td><td>16.666</td></tr><tr><td>1400</td><td>4.970</td><td>52.353</td><td>5.474</td><td>44.976</td><td>11.918</td></tr><tr><td>1600</td><td>4.976</td><td>53.017</td><td>6.469</td><td>44.671</td><td>7.220</td></tr><tr><td>1800</td><td>4.988</td><td>53.604</td><td>7.465</td><td>44.367</td><td>2.546</td></tr></table>

Bi2(g)   
Bismuth (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.830</td><td>65.400</td><td>0</td><td>52.600</td><td>41.187</td></tr><tr><td>400</td><td>8.880</td><td>68.002</td><td>.902</td><td>52.238</td><td>37.341</td></tr><tr><td>600</td><td>8.910</td><td>71.610</td><td>2.682</td><td>45.886</td><td>30.628</td></tr><tr><td>800</td><td>8.930</td><td>74.176</td><td>4.466</td><td>44.928</td><td>25.699</td></tr><tr><td>1000</td><td>8.930</td><td>76.168</td><td>6.252</td><td>44.062</td><td>20.994</td></tr><tr><td>1200</td><td>8.940</td><td>77.796</td><td>8.038</td><td>43.242</td><td>16.463</td></tr><tr><td>1400</td><td>8.940</td><td>79.174</td><td>9.826</td><td>42.430</td><td>12.058</td></tr><tr><td>1600</td><td>8.940</td><td>80.368</td><td>11.614</td><td>41.618</td><td>7.781</td></tr><tr><td>1800</td><td>8.940</td><td>81.422</td><td>13.402</td><td>40.806</td><td>3.578</td></tr></table>

Br $_{2}$ (1,g)  
Bromine 

<table><tr><td>I</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>18.090</td><td>36.379</td><td>0</td></tr><tr><td>333</td><td>18.028</td><td>38.350</td><td>.621</td></tr><tr><td>333</td><td>8.683</td><td>59.592</td><td>7.686</td></tr><tr><td>400</td><td>8.775</td><td>61.197</td><td>8.275</td></tr><tr><td>600</td><td>8.908</td><td>64.784</td><td>10.045</td></tr><tr><td>800</td><td>8.970</td><td>67.356</td><td>11.833</td></tr><tr><td>1000</td><td>9.011</td><td>69.362</td><td>13.632</td></tr><tr><td>1200</td><td>9.042</td><td>71.008</td><td>15.437</td></tr><tr><td>1400</td><td>9.069</td><td>72.404</td><td>17.248</td></tr><tr><td>1600</td><td>9.094</td><td>73.617</td><td>19.065</td></tr><tr><td>1800</td><td>9.118</td><td>74.689</td><td>20.886</td></tr><tr><td>2000</td><td>9.141</td><td>75.651</td><td>22.712</td></tr><tr><td>2200</td><td>9.163</td><td>76.523</td><td>24.542</td></tr><tr><td>2400</td><td>9.185</td><td>77.322</td><td>26.377</td></tr><tr><td>2600</td><td>9.206</td><td>78.058</td><td>28.216</td></tr><tr><td>2800</td><td>9.228</td><td>78.741</td><td>30.060</td></tr><tr><td>3000</td><td>9.249</td><td>79.378</td><td>31.907</td></tr></table>

332.6 K, boiling point; $\Delta H^{\circ} = 7.065$

Br(g)   
Bromine (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.803</td><td>0</td><td>26.735</td><td>19.695</td></tr><tr><td>400</td><td>4.968</td><td>43.263</td><td>.506</td><td>23.104</td><td>18.038</td></tr><tr><td>600</td><td>4.979</td><td>45.279</td><td>1.500</td><td>23.213</td><td>15.480</td></tr><tr><td>800</td><td>5.026</td><td>46.717</td><td>2.500</td><td>23.319</td><td>12.887</td></tr><tr><td>1000</td><td>5.106</td><td>47.846</td><td>3.513</td><td>23.432</td><td>10.267</td></tr><tr><td>1200</td><td>5.199</td><td>48.785</td><td>4.543</td><td>23.560</td><td>7.622</td></tr><tr><td>1400</td><td>5.284</td><td>49.593</td><td>5.592</td><td>23.703</td><td>4.956</td></tr><tr><td>1600</td><td>5.351</td><td>50.304</td><td>6.656</td><td>23.859</td><td>2.266</td></tr><tr><td>1800</td><td>5.398</td><td>50.937</td><td>7.731</td><td>24.023</td><td>-0.443</td></tr><tr><td>2000</td><td>5.428</td><td>51.507</td><td>8.814</td><td>24.193</td><td>-3.170</td></tr><tr><td>2200</td><td>5.443</td><td>52.025</td><td>9.901</td><td>24.365</td><td>-5.915</td></tr><tr><td>2400</td><td>5.446</td><td>52.499</td><td>10.990</td><td>24.537</td><td>-8.675</td></tr><tr><td>2600</td><td>5.442</td><td>52.935</td><td>12.079</td><td>24.706</td><td>-11.450</td></tr><tr><td>2800</td><td>5.432</td><td>53.338</td><td>13.166</td><td>24.871</td><td>-14.238</td></tr><tr><td>3000</td><td>5.418</td><td>53.712</td><td>14.251</td><td>25.033</td><td>-17.037</td></tr></table>

Br $_{2}$ (g)   
Bromine (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.616</td><td>58.640</td><td>0</td><td>7.388</td><td>0.751</td></tr><tr><td>400</td><td>8.775</td><td>61.197</td><td>.887</td><td>0</td><td>0</td></tr><tr><td>600</td><td>8.908</td><td>64.784</td><td>2.657</td><td>0</td><td>0</td></tr><tr><td>800</td><td>8.970</td><td>67.356</td><td>4.445</td><td>0</td><td>0</td></tr><tr><td>1000</td><td>9.011</td><td>69.362</td><td>6.244</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>9.042</td><td>71.008</td><td>8.049</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>9.069</td><td>72.404</td><td>9.860</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>9.094</td><td>73.617</td><td>11.677</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>9.118</td><td>74.689</td><td>13.498</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>9.141</td><td>75.651</td><td>15.324</td><td>0</td><td>0</td></tr><tr><td>2200</td><td>9.163</td><td>76.523</td><td>17.154</td><td>0</td><td>0</td></tr><tr><td>2400</td><td>9.185</td><td>77.322</td><td>18.989</td><td>0</td><td>0</td></tr><tr><td>2600</td><td>9.206</td><td>78.058</td><td>20.828</td><td>0</td><td>0</td></tr><tr><td>2800</td><td>9.228</td><td>78.741</td><td>22.672</td><td>0</td><td>0</td></tr><tr><td>3000</td><td>9.249</td><td>79.378</td><td>24.519</td><td>0</td><td>0</td></tr></table>

C(c)   
Carbon (graphite) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>2.036</td><td>1.372</td><td>0</td></tr><tr><td>400</td><td>2.824</td><td>2.083</td><td>.248</td></tr><tr><td>600</td><td>4.026</td><td>3.474</td><td>.942</td></tr><tr><td>800</td><td>4.739</td><td>4.739</td><td>1.825</td></tr><tr><td>1000</td><td>5.165</td><td>5.845</td><td>2.819</td></tr><tr><td>1200</td><td>5.441</td><td>6.813</td><td>3.881</td></tr><tr><td>1400</td><td>5.635</td><td>7.667</td><td>4.990</td></tr><tr><td>1600</td><td>5.782</td><td>8.430</td><td>6.132</td></tr><tr><td>1800</td><td>5.899</td><td>9.118</td><td>7.301</td></tr><tr><td>2000</td><td>5.997</td><td>9.745</td><td>8.491</td></tr><tr><td>2200</td><td>6.083</td><td>10.320</td><td>9.699</td></tr><tr><td>2400</td><td>6.160</td><td>10.853</td><td>10.924</td></tr><tr><td>2600</td><td>6.231</td><td>11.349</td><td>12.163</td></tr><tr><td>2800</td><td>6.297</td><td>11.813</td><td>13.416</td></tr><tr><td>3000</td><td>6.360</td><td>12.250</td><td>14.682</td></tr></table>

C(c)

Carbon (diamond) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>1.462</td><td>0.566</td><td>0</td><td>0.453</td><td>0.693</td></tr><tr><td>400</td><td>2.446</td><td>1.136</td><td>.200</td><td>.405</td><td>.784</td></tr><tr><td>600</td><td>3.852</td><td>2.418</td><td>.842</td><td>.353</td><td>.987</td></tr><tr><td>800</td><td>4.660</td><td>3.648</td><td>1.700</td><td>.328</td><td>1.201</td></tr><tr><td>1000</td><td>5.162</td><td>4.745</td><td>2.685</td><td>.319</td><td>1.419</td></tr></table>

$C_{2}(g)$   
Carbon (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>10.312</td><td>47.627</td><td>0</td><td>200.200</td><td>186.818</td></tr><tr><td>400</td><td>9.476</td><td>50.544</td><td>1.009</td><td>200.713</td><td>182.162</td></tr><tr><td>600</td><td>8.604</td><td>54.178</td><td>2.795</td><td>201.111</td><td>172.773</td></tr><tr><td>800</td><td>8.509</td><td>56.632</td><td>4.501</td><td>201.051</td><td>163.328</td></tr><tr><td>1000</td><td>8.595</td><td>58.538</td><td>6.210</td><td>200.772</td><td>153.924</td></tr><tr><td>1200</td><td>8.719</td><td>60.116</td><td>7.941</td><td>200.379</td><td>144.591</td></tr><tr><td>1400</td><td>8.852</td><td>61.470</td><td>9.698</td><td>199.918</td><td>135.328</td></tr><tr><td>1600</td><td>8.989</td><td>62.661</td><td>11.482</td><td>199.418</td><td>126.136</td></tr><tr><td>1800</td><td>9.125</td><td>63.728</td><td>13.294</td><td>198.892</td><td>117.006</td></tr><tr><td>2000</td><td>9.256</td><td>64.696</td><td>15.132</td><td>198.350</td><td>107.938</td></tr><tr><td>2200</td><td>9.381</td><td>65.584</td><td>16.996</td><td>197.798</td><td>98.921</td></tr><tr><td>2400</td><td>9.498</td><td>66.405</td><td>18.884</td><td>197.236</td><td>89.958</td></tr><tr><td>2600</td><td>9.607</td><td>67.170</td><td>20.795</td><td>196.669</td><td>81.042</td></tr><tr><td>2800</td><td>9.709</td><td>67.886</td><td>22.726</td><td>196.094</td><td>72.166</td></tr><tr><td>3000</td><td>9.805</td><td>68.559</td><td>24.678</td><td>195.514</td><td>63.337</td></tr></table>

$C_{3}(g)$

Carbon (ideal triatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.020</td><td>56.677</td><td>0</td><td>196.000</td><td>180.329</td></tr><tr><td>400</td><td>8.962</td><td>59.310</td><td>.912</td><td>196.168</td><td>174.944</td></tr><tr><td>600</td><td>9.490</td><td>63.029</td><td>2.750</td><td>195.924</td><td>164.360</td></tr><tr><td>800</td><td>10.153</td><td>65.852</td><td>4.716</td><td>195.241</td><td>153.933</td></tr><tr><td>1000</td><td>10.711</td><td>68.179</td><td>6.804</td><td>194.347</td><td>143.703</td></tr><tr><td>1200</td><td>11.149</td><td>70.173</td><td>8.992</td><td>193.349</td><td>133.668</td></tr><tr><td>1400</td><td>11.495</td><td>71.918</td><td>11.258</td><td>192.288</td><td>123.804</td></tr><tr><td>1600</td><td>11.775</td><td>73.472</td><td>13.586</td><td>191.190</td><td>114.099</td></tr><tr><td>1800</td><td>12.006</td><td>74.873</td><td>15.964</td><td>190.061</td><td>104.527</td></tr><tr><td>2000</td><td>12.202</td><td>76.148</td><td>18.386</td><td>188.913</td><td>95.087</td></tr><tr><td>2200</td><td>12.371</td><td>77.319</td><td>20.844</td><td>187.747</td><td>85.757</td></tr><tr><td>2400</td><td>12.520</td><td>78.402</td><td>23.333</td><td>186.561</td><td>76.538</td></tr><tr><td>2600</td><td>12.652</td><td>79.410</td><td>25.850</td><td>185.361</td><td>67.417</td></tr><tr><td>2800</td><td>12.770</td><td>80.352</td><td>28.393</td><td>184.145</td><td>58.389</td></tr><tr><td>3000</td><td>12.877</td><td>81.236</td><td>30.958</td><td>182.912</td><td>49.454</td></tr></table>

$C_{4}(g)$   
Carbon (ideal tetratomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298*</td><td>11.992</td><td>54.543</td><td>0</td><td>232.000</td><td>217.374</td></tr><tr><td>400</td><td>13.780</td><td>58.333</td><td>1.318</td><td>232.326</td><td>212.326</td></tr><tr><td>600</td><td>16.045</td><td>64.388</td><td>4.320</td><td>232.552</td><td>202.257</td></tr><tr><td>800</td><td>17.463</td><td>69.213</td><td>7.681</td><td>232.381</td><td>192.175</td></tr><tr><td>1000</td><td>18.387</td><td>73.216</td><td>11.272</td><td>231.996</td><td>182.160</td></tr><tr><td>1200</td><td>19.003</td><td>76.627</td><td>15.015</td><td>231.491</td><td>172.241</td></tr><tr><td>1400</td><td>19.426</td><td>79.590</td><td>18.861</td><td>230.901</td><td>162.410</td></tr><tr><td>1600</td><td>19.724</td><td>82.204</td><td>22.778</td><td>230.250</td><td>152.676</td></tr><tr><td>1800</td><td>19.942</td><td>84.541</td><td>26.745</td><td>229.541</td><td>143.017</td></tr><tr><td>2000</td><td>20.104</td><td>86.650</td><td>30.751</td><td>228.787</td><td>133.447</td></tr><tr><td>2200</td><td>20.227</td><td>88.573</td><td>34.784</td><td>227.988</td><td>123.943</td></tr><tr><td>2400</td><td>20.324</td><td>90.337</td><td>38.840</td><td>227.144</td><td>114.524</td></tr><tr><td>2600</td><td>20.400</td><td>91.967</td><td>42.912</td><td>226.260</td><td>105.175</td></tr><tr><td>2800</td><td>20.461</td><td>93.481</td><td>46.999</td><td>225.335</td><td>95.894</td></tr><tr><td>3000</td><td>20.512</td><td>94.894</td><td>51.096</td><td>224.368</td><td>86.686</td></tr></table>

\*All data except enthalpy of formation at 298 K estimated.

$C_{5}(g)$   
Carbon (ideal pentatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298*</td><td>14.611</td><td>57.806</td><td>0</td><td>234.000</td><td>218.810</td></tr><tr><td>400</td><td>17.184</td><td>62.484</td><td>1.628</td><td>234.388</td><td>213.560</td></tr><tr><td>600</td><td>20.318</td><td>70.102</td><td>5.407</td><td>234.697</td><td>203.058</td></tr><tr><td>800</td><td>22.239</td><td>76.231</td><td>9.677</td><td>234.552</td><td>192.523</td></tr><tr><td>1000</td><td>23.484</td><td>81.337</td><td>14.258</td><td>234.163</td><td>182.051</td></tr><tr><td>1200</td><td>24.314</td><td>85.697</td><td>19.043</td><td>233.638</td><td>171.680</td></tr><tr><td>1400</td><td>24.883</td><td>89.490</td><td>23.966</td><td>233.016</td><td>161.399</td></tr><tr><td>1600</td><td>25.286</td><td>92.841</td><td>28.985</td><td>232.325</td><td>151.219</td></tr><tr><td>1800</td><td>25.579</td><td>95.837</td><td>34.073</td><td>231.568</td><td>141.123</td></tr><tr><td>2000</td><td>25.798</td><td>98.543</td><td>39.212</td><td>230.757</td><td>131.121</td></tr><tr><td>2200</td><td>25.965</td><td>101.010</td><td>44.389</td><td>229.894</td><td>121.192</td></tr><tr><td>2400</td><td>26.095</td><td>103.275</td><td>49.595</td><td>228.975</td><td>111.351</td></tr><tr><td>2600</td><td>26.198</td><td>105.368</td><td>54.825</td><td>228.010</td><td>101.590</td></tr><tr><td>2800</td><td>26.281</td><td>107.313</td><td>60.073</td><td>226.993</td><td>91.899</td></tr><tr><td>3000</td><td>26.349</td><td>109.129</td><td>65.336</td><td>225.926</td><td>82.289</td></tr></table>

\*All data except enthalpy of formation at 298 K estimated.

Ca(c,l,g)   
Calcium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.050</td><td>9.940</td><td>0</td></tr><tr><td>400</td><td>6.275</td><td>11.750</td><td>.627</td></tr><tr><td>600</td><td>7.066</td><td>14.430</td><td>1.954</td></tr><tr><td>720</td><td>7.740</td><td>15.770</td><td>2.840</td></tr><tr><td>720</td><td>7.013</td><td>16.080</td><td>3.060</td></tr><tr><td>800</td><td>7.803</td><td>16.860</td><td>3.652</td></tr><tr><td>1000</td><td>9.776</td><td>18.810</td><td>5.410</td></tr><tr><td>1112</td><td>10.880</td><td>19.900</td><td>6.567</td></tr><tr><td>1112</td><td>7.000</td><td>21.740</td><td>8.607</td></tr><tr><td>1200</td><td>7.000</td><td>22.270</td><td>9.223</td></tr><tr><td>1400*</td><td>7.000</td><td>23.350</td><td>10.623</td></tr><tr><td>1600</td><td>7.000</td><td>24.280</td><td>12.023</td></tr><tr><td>1757</td><td>7.000</td><td>24.940</td><td>13.122</td></tr><tr><td>1757</td><td>4.979</td><td>45.786</td><td>49.748</td></tr><tr><td>1800</td><td>4.982</td><td>45.925</td><td>49.963</td></tr><tr><td>2000</td><td>5.008</td><td>46.451</td><td>50.962</td></tr></table>

\*Data extrapolated above 1300 to 1757 K.   
720 K, transition point; $\Delta H^{\circ} = 0.220$   
1112 K, melting point; $\Delta H^{\circ} = 2.040$   
1757 K, boiling point; $\Delta H^{\circ} = 36.626$

Ca(g)   
Calcium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>36.992</td><td>0</td><td>42.500</td><td>34.434</td></tr><tr><td>400</td><td>4.968</td><td>38.452</td><td>.506</td><td>42.379</td><td>31.698</td></tr><tr><td>600</td><td>4.968</td><td>40.466</td><td>1.500</td><td>42.046</td><td>26.424</td></tr><tr><td>800</td><td>4.968</td><td>41.895</td><td>2.493</td><td>41.341</td><td>21.313</td></tr><tr><td>1000</td><td>4.968</td><td>43.004</td><td>3.487</td><td>40.577</td><td>16.383</td></tr><tr><td>1200</td><td>4.968</td><td>43.910</td><td>4.480</td><td>37.757</td><td>11.789</td></tr><tr><td>1400</td><td>4.969</td><td>44.675</td><td>5.474</td><td>37.351</td><td>7.496</td></tr><tr><td>1600</td><td>4.972</td><td>45.339</td><td>6.468</td><td>36.945</td><td>3.251</td></tr><tr><td>1800</td><td>4.982</td><td>45.925</td><td>7.463</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>5.008</td><td>46.451</td><td>8.462</td><td>0</td><td>0</td></tr></table>

$Ca_{2}(g)$   
Calcium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.152</td><td>61.289</td><td>0</td><td>82.660</td><td>70.314</td></tr><tr><td>400</td><td>8.699</td><td>63.918</td><td>.910</td><td>82.316</td><td>66.149</td></tr><tr><td>600</td><td>7.947</td><td>67.289</td><td>2.568</td><td>81.320</td><td>58.263</td></tr><tr><td>800</td><td>7.552</td><td>69.514</td><td>4.113</td><td>79.469</td><td>50.834</td></tr><tr><td>1000</td><td>7.344</td><td>71.175</td><td>5.601</td><td>77.441</td><td>43.886</td></tr><tr><td>1200</td><td>7.226</td><td>72.503</td><td>7.057</td><td>71.271</td><td>37.715</td></tr><tr><td>1400</td><td>7.154</td><td>73.611</td><td>8.494</td><td>69.908</td><td>32.233</td></tr><tr><td>1600</td><td>7.107</td><td>74.563</td><td>9.920</td><td>68.534</td><td>26.929</td></tr><tr><td>1800</td><td>7.075</td><td>75.398</td><td>11.338</td><td>-5.928</td><td>23.686</td></tr><tr><td>2000</td><td>7.052</td><td>76.142</td><td>12.750</td><td>-6.514</td><td>27.006</td></tr></table>

Cd(c,1,g)   
Cadmium 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H298</td></tr><tr><td>298</td><td>6.200</td><td>12.380</td><td>0</td></tr><tr><td>400</td><td>6.490</td><td>14.240</td><td>.646</td></tr><tr><td>594</td><td>7.060</td><td>16.910</td><td>1.960</td></tr><tr><td>594</td><td>7.100</td><td>19.400</td><td>3.440</td></tr><tr><td>600</td><td>7.100</td><td>19.480</td><td>3.483</td></tr><tr><td>800</td><td>7.100</td><td>21.520</td><td>4.903</td></tr><tr><td>1000</td><td>7.100</td><td>23.110</td><td>6.323</td></tr><tr><td>1040</td><td>7.100</td><td>23.380</td><td>6.607</td></tr><tr><td>1040</td><td>4.968</td><td>46.273</td><td>30.416</td></tr><tr><td>1200</td><td>4.968</td><td>46.983</td><td>31.210</td></tr><tr><td>1400</td><td>4.968</td><td>47.749</td><td>32.204</td></tr><tr><td>1600</td><td>4.968</td><td>48.412</td><td>33.197</td></tr><tr><td>1800</td><td>4.968</td><td>48.998</td><td>34.191</td></tr><tr><td>2000</td><td>4.968</td><td>49.521</td><td>35.185</td></tr></table>

594.26 K, melting point; $\Delta H^{\circ} = 1.480$   
1040 K, boiling point; $\Delta H^{\circ} = 23.809$

Cd(g)   
Cadmium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>40.066</td><td>0</td><td>26.730</td><td>18.475</td></tr><tr><td>400</td><td>4.968</td><td>41.526</td><td>.506</td><td>26.590</td><td>15.676</td></tr><tr><td>600</td><td>4.968</td><td>43.540</td><td>1.500</td><td>24.747</td><td>10.311</td></tr><tr><td>800</td><td>4.968</td><td>44.970</td><td>2.493</td><td>24.320</td><td>5.560</td></tr><tr><td>1000</td><td>4.968</td><td>46.078</td><td>3.487</td><td>23.894</td><td>0.926</td></tr><tr><td>1200</td><td>4.968</td><td>46.983</td><td>4.480</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.968</td><td>47.749</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.968</td><td>48.412</td><td>6.468</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.968</td><td>48.998</td><td>7.461</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.968</td><td>49.521</td><td>8.455</td><td>0</td><td>0</td></tr></table>

Ce(c,1)   
Cerium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.440</td><td>17.200</td><td>0</td></tr><tr><td>400</td><td>6.760</td><td>19.140</td><td>.672</td></tr><tr><td>600</td><td>7.460</td><td>22.010</td><td>2.092</td></tr><tr><td>800</td><td>8.230</td><td>24.260</td><td>3.659</td></tr><tr><td>999</td><td>9.020</td><td>26.170</td><td>5.375</td></tr><tr><td>999</td><td>8.990</td><td>26.880</td><td>6.090</td></tr><tr><td>1000</td><td>8.990</td><td>26.890</td><td>6.099</td></tr><tr><td>1071</td><td>8.990</td><td>27.510</td><td>6.737</td></tr><tr><td>1071</td><td>9.010</td><td>28.730</td><td>8.042</td></tr><tr><td>1200</td><td>9.010</td><td>29.750</td><td>9.204</td></tr><tr><td>1400*</td><td>9.010</td><td>31.140</td><td>11.006</td></tr><tr><td>1600</td><td>9.010</td><td>32.340</td><td>12.808</td></tr><tr><td>1800</td><td>9.010</td><td>33.400</td><td>14.610</td></tr><tr><td>2000</td><td>9.010</td><td>34.350</td><td>16.412</td></tr></table>

\*Data extrapolated above 1300 K.   
999 K, transition point; $\Delta H^{\circ} = 0.715$   
1071 K, melting point; $\Delta H^{\circ} = 1.305$

Ce(g)   
Cerium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.515</td><td>45.807</td><td>0</td><td>101.000</td><td>92.471</td></tr><tr><td>400</td><td>5.876</td><td>47.472</td><td>0.578</td><td>100.906</td><td>89.573</td></tr><tr><td>600</td><td>6.971</td><td>50.055</td><td>1.859</td><td>100.767</td><td>83.940</td></tr><tr><td>800</td><td>8.002</td><td>52.207</td><td>3.361</td><td>100.702</td><td>78.344</td></tr><tr><td>1000</td><td>8.731</td><td>54.078</td><td>5.040</td><td>99.941</td><td>72.753</td></tr><tr><td>1200</td><td>9.146</td><td>55.710</td><td>6.832</td><td>98.628</td><td>67.476</td></tr><tr><td>1400</td><td>9.322</td><td>57.136</td><td>8.682</td><td>98.676</td><td>62.282</td></tr><tr><td>1600</td><td>9.354</td><td>58.384</td><td>10.551</td><td>98.743</td><td>57.073</td></tr><tr><td>1800</td><td>9.315</td><td>59.484</td><td>12.419</td><td>98.809</td><td>51.858</td></tr><tr><td>2000</td><td>9.253</td><td>60.462</td><td>14.276</td><td>98.864</td><td>46.640</td></tr></table>

Cl $_{2}$ (g)   
Chlorine 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>8.111</td><td>53.290</td><td>0</td></tr><tr><td>400</td><td>8.437</td><td>55.725</td><td>.845</td></tr><tr><td>600</td><td>8.741</td><td>59.213</td><td>2.567</td></tr><tr><td>800</td><td>8.878</td><td>61.748</td><td>4.331</td></tr><tr><td>1000</td><td>8.956</td><td>63.738</td><td>6.115</td></tr><tr><td>1200</td><td>9.010</td><td>65.376</td><td>7.912</td></tr><tr><td>1400</td><td>9.051</td><td>66.768</td><td>9.718</td></tr><tr><td>1600</td><td>9.086</td><td>67.979</td><td>11.532</td></tr><tr><td>1800</td><td>9.117</td><td>69.051</td><td>13.352</td></tr><tr><td>2000</td><td>9.149</td><td>70.014</td><td>15.179</td></tr><tr><td>2200</td><td>9.184</td><td>70.887</td><td>17.012</td></tr><tr><td>2400</td><td>9.223</td><td>71.688</td><td>18.852</td></tr><tr><td>2600</td><td>9.268</td><td>72.428</td><td>20.701</td></tr><tr><td>2800</td><td>9.319</td><td>73.117</td><td>22.560</td></tr><tr><td>3000</td><td>9.374</td><td>73.761</td><td>24.429</td></tr></table>

Cl(g)

Chlorine (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.219</td><td>39.454</td><td>0</td><td>28.992</td><td>25.173</td></tr><tr><td>400</td><td>5.370</td><td>41.011</td><td>.540</td><td>29.110</td><td>23.850</td></tr><tr><td>600</td><td>5.445</td><td>43.210</td><td>1.625</td><td>29.333</td><td>21.171</td></tr><tr><td>800</td><td>5.389</td><td>44.771</td><td>2.710</td><td>29.537</td><td>18.419</td></tr><tr><td>1000</td><td>5.314</td><td>45.965</td><td>3.780</td><td>29.715</td><td>15.618</td></tr><tr><td>1200</td><td>5.248</td><td>46.928</td><td>4.836</td><td>29.872</td><td>12.784</td></tr><tr><td>1400</td><td>5.196</td><td>47.733</td><td>5.880</td><td>30.013</td><td>9.924</td></tr><tr><td>1600</td><td>5.156</td><td>48.424</td><td>6.915</td><td>30.141</td><td>7.046</td></tr><tr><td>1800</td><td>5.125</td><td>49.029</td><td>7.943</td><td>30.259</td><td>4.153</td></tr><tr><td>2000</td><td>5.101</td><td>49.568</td><td>8.965</td><td>30.368</td><td>1.245</td></tr><tr><td>2200</td><td>5.081</td><td>50.053</td><td>9.984</td><td>30.470</td><td>-1.671</td></tr><tr><td>2400</td><td>5.066</td><td>50.495</td><td>10.998</td><td>30.564</td><td>-4.598</td></tr><tr><td>2600</td><td>5.053</td><td>50.899</td><td>12.010</td><td>30.652</td><td>-7.529</td></tr><tr><td>2800</td><td>5.043</td><td>51.274</td><td>13.020</td><td>30.732</td><td>-10.471</td></tr><tr><td>3000</td><td>5.034</td><td>51.621</td><td>14.027</td><td>30.805</td><td>-13.417</td></tr></table>

Cm(c,1)   
Curium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298*</td><td>6.617</td><td>17.200</td><td>0</td></tr><tr><td>400</td><td>6.828</td><td>19.174</td><td>.685</td></tr><tr><td>600</td><td>7.242</td><td>22.021</td><td>2.092</td></tr><tr><td>800</td><td>7.656</td><td>24.161</td><td>3.581</td></tr><tr><td>1000</td><td>8.070</td><td>25.914</td><td>5.154</td></tr><tr><td>1200</td><td>8.484</td><td>27.422</td><td>6.809</td></tr><tr><td>1400</td><td>8.898</td><td>28.761</td><td>8.548</td></tr><tr><td>1550</td><td>9.208</td><td>29.682</td><td>9.906</td></tr><tr><td>1550</td><td>8.000</td><td>30.182</td><td>10.681</td></tr><tr><td>1600</td><td>8.000</td><td>30.436</td><td>11.081</td></tr><tr><td>1618</td><td>8.000</td><td>30.525</td><td>11.225</td></tr><tr><td>1618</td><td>9.600</td><td>32.688</td><td>14.725</td></tr><tr><td>1800</td><td>9.600</td><td>33.712</td><td>16.472</td></tr><tr><td>2000</td><td>9.600</td><td>34.723</td><td>18.392</td></tr></table>

\*All data except fusion   
temperature estimated.   
1550 K, transition point; $\Delta H^{\circ} = 0.775$   
1618 K, melting point; $\Delta H^{\circ} = 3.500$

Cm(g)   
Curium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>6.717</td><td>47.158</td><td>0</td><td>92.600</td><td>83.668</td></tr><tr><td>400</td><td>6.936</td><td>49.168</td><td>.697</td><td>92.612</td><td>80.614</td></tr><tr><td>600</td><td>6.858</td><td>51.983</td><td>2.085</td><td>92.593</td><td>74.616</td></tr><tr><td>800</td><td>6.555</td><td>53.914</td><td>3.426</td><td>92.445</td><td>68.643</td></tr><tr><td>1000</td><td>6.334</td><td>55.351</td><td>4.713</td><td>92.159</td><td>62.722</td></tr><tr><td>1200</td><td>6.223</td><td>56.494</td><td>5.967</td><td>91.758</td><td>56.872</td></tr><tr><td>1400</td><td>6.191</td><td>57.450</td><td>7.207</td><td>91.259</td><td>51.094</td></tr><tr><td>1600</td><td>6.212</td><td>58.278</td><td>8.447</td><td>89.966</td><td>45.419</td></tr><tr><td>1800</td><td>6.269</td><td>59.012</td><td>9.694</td><td>85.822</td><td>40.282</td></tr><tr><td>2000</td><td>6.354</td><td>59.677</td><td>10.956</td><td>85.164</td><td>35.256</td></tr></table>

Co(c,1)   
Cobalt 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.930</td><td>7.180</td><td>0</td></tr><tr><td>400</td><td>6.340</td><td>8.970</td><td>.625</td></tr><tr><td>600</td><td>7.090</td><td>11.700</td><td>1.972</td></tr><tr><td>700</td><td>7.420</td><td>12.820</td><td>2.700</td></tr><tr><td>700</td><td>7.310</td><td>12.970</td><td>2.808</td></tr><tr><td>800</td><td>7.750</td><td>13.980</td><td>3.560</td></tr><tr><td>1000</td><td>8.840</td><td>15.810</td><td>5.214</td></tr><tr><td>1200</td><td>10.330</td><td>17.550</td><td>7.122</td></tr><tr><td>1394</td><td>13.140</td><td>19.280</td><td>9.379</td></tr><tr><td>1400</td><td>10.570</td><td>19.330</td><td>9.448</td></tr><tr><td>1600</td><td>9.150</td><td>20.610</td><td>11.368</td></tr><tr><td>1768</td><td>9.020</td><td>21.510</td><td>12.890</td></tr><tr><td>1768</td><td>11.516</td><td>23.604</td><td>16.590</td></tr><tr><td>1800</td><td>11.516</td><td>23.811</td><td>16.959</td></tr><tr><td>2000</td><td>11.516</td><td>25.024</td><td>19.262</td></tr><tr><td>2200</td><td>11.516</td><td>26.122</td><td>21.565</td></tr><tr><td>2400</td><td>11.516</td><td>27.124</td><td>23.869</td></tr></table>

700 K, transition point; $\Delta H^{\circ} = 0.108$   
1394 K, Curie point; $\Delta H^{\circ} = 0$   
1768 K, melting point; $\Delta H^{\circ} = 3.700$

Co(g)   
Cobalt (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.503</td><td>42.879</td><td>0</td><td>101.500</td><td>90.856</td></tr><tr><td>400</td><td>5.857</td><td>44.548</td><td>.579</td><td>101.454</td><td>87.223</td></tr><tr><td>600</td><td>6.188</td><td>47.000</td><td>1.791</td><td>101.319</td><td>80.139</td></tr><tr><td>800</td><td>6.259</td><td>48.793</td><td>3.038</td><td>100.978</td><td>73.128</td></tr><tr><td>1000</td><td>6.291</td><td>50.193</td><td>4.292</td><td>100.578</td><td>66.195</td></tr><tr><td>1200</td><td>6.329</td><td>51.343</td><td>5.554</td><td>99.932</td><td>59.380</td></tr><tr><td>1400</td><td>6.363</td><td>52.321</td><td>6.824</td><td>98.876</td><td>52.689</td></tr><tr><td>1600</td><td>6.383</td><td>53.172</td><td>8.099</td><td>98.231</td><td>46.132</td></tr><tr><td>1800</td><td>6.385</td><td>53.924</td><td>9.376</td><td>93.917</td><td>39.714</td></tr><tr><td>2000</td><td>6.374</td><td>54.597</td><td>10.652</td><td>92.890</td><td>33.744</td></tr></table>

Cr(c,1)   
Chromium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.580</td><td>5.650</td><td>0</td></tr><tr><td>312</td><td>7.620</td><td>5.900</td><td>.077</td></tr><tr><td>400</td><td>6.020</td><td>7.350</td><td>.591</td></tr><tr><td>600</td><td>6.730</td><td>9.940</td><td>1.871</td></tr><tr><td>800</td><td>7.220</td><td>11.950</td><td>3.270</td></tr><tr><td>1000</td><td>7.660</td><td>13.600</td><td>4.752</td></tr><tr><td>1200</td><td>8.480</td><td>15.070</td><td>6.362</td></tr><tr><td>1400</td><td>9.270</td><td>16.430</td><td>8.142</td></tr><tr><td>1600</td><td>10.140</td><td>17.730</td><td>10.080</td></tr><tr><td>1800</td><td>10.920</td><td>18.970</td><td>12.188</td></tr><tr><td>2000</td><td>11.650</td><td>20.160</td><td>14.449</td></tr><tr><td>2130</td><td>12.100</td><td>20.910</td><td>15.993</td></tr><tr><td>2130</td><td>9.400</td><td>22.810</td><td>20.040</td></tr><tr><td>2200</td><td>9.400</td><td>23.110</td><td>20.698</td></tr><tr><td>2400</td><td>9.400</td><td>23.930</td><td>22.578</td></tr></table>

311.5 K, transition point; $\Delta H^{\circ} = 0$   
2130 K, melting point; $\Delta H^{\circ} = 4.047$

Cr(g)   
Chromium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.635</td><td>0</td><td>95.000</td><td>84.271</td></tr><tr><td>400</td><td>4.968</td><td>43.094</td><td>.506</td><td>94.915</td><td>80.617</td></tr><tr><td>600</td><td>4.968</td><td>45.109</td><td>1.500</td><td>94.629</td><td>73.528</td></tr><tr><td>800</td><td>4.969</td><td>46.538</td><td>2.493</td><td>94.223</td><td>66.553</td></tr><tr><td>1000</td><td>4.980</td><td>47.648</td><td>3.488</td><td>93.736</td><td>59.688</td></tr><tr><td>1200</td><td>5.023</td><td>48.559</td><td>4.487</td><td>93.125</td><td>52.938</td></tr><tr><td>1400</td><td>5.124</td><td>49.340</td><td>5.501</td><td>92.359</td><td>46.285</td></tr><tr><td>1600</td><td>5.299</td><td>50.034</td><td>6.542</td><td>91.462</td><td>39.776</td></tr><tr><td>1800</td><td>5.544</td><td>50.672</td><td>7.625</td><td>90.437</td><td>33.373</td></tr><tr><td>2000</td><td>5.841</td><td>51.271</td><td>8.763</td><td>89.314</td><td>27.092</td></tr><tr><td>2200</td><td>6.165</td><td>51.843</td><td>9.963</td><td>84.265</td><td>21.052</td></tr><tr><td>2400</td><td>6.492</td><td>52.394</td><td>11.229</td><td>83.651</td><td>15.337</td></tr></table>

Cs(c,1,g)   
Cesium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>7.695</td><td>20.370</td><td>0</td></tr><tr><td>302</td><td>7.747</td><td>20.458</td><td>.026</td></tr><tr><td>302</td><td>7.744</td><td>22.116</td><td>.526</td></tr><tr><td>400</td><td>7.533</td><td>24.268</td><td>1.276</td></tr><tr><td>600</td><td>7.409</td><td>27.293</td><td>2.767</td></tr><tr><td>800</td><td>7.393</td><td>29.421</td><td>4.247</td></tr><tr><td>952</td><td>7.404</td><td>30.695</td><td>5.371</td></tr><tr><td>952</td><td>4.968</td><td>47.710</td><td>21.569</td></tr><tr><td>1000</td><td>4.968</td><td>47.954</td><td>21.807</td></tr><tr><td>1200</td><td>4.969</td><td>48.860</td><td>22.800</td></tr><tr><td>1400</td><td>4.975</td><td>49.627</td><td>23.795</td></tr><tr><td>1600</td><td>4.992</td><td>50.292</td><td>24.791</td></tr><tr><td>1800</td><td>5.031</td><td>50.882</td><td>25.793</td></tr><tr><td>2000</td><td>5.102</td><td>51.415</td><td>26.805</td></tr></table>

301.55 K, melting point; $\Delta H^{\circ} = 0.500$   
952 K, boiling point to ideal   
monatomic gas; $\Delta H^{\circ} = 16.198$

Cs(g)   
Cesium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.942</td><td>0</td><td>18.320</td><td>11.888</td></tr><tr><td>400</td><td>4.968</td><td>43.402</td><td>.506</td><td>17.550</td><td>9.896</td></tr><tr><td>600</td><td>4.968</td><td>45.417</td><td>1.500</td><td>17.053</td><td>6.179</td></tr><tr><td>800</td><td>4.968</td><td>46.846</td><td>2.493</td><td>16.566</td><td>2.626</td></tr><tr><td>1000</td><td>4.968</td><td>47.954</td><td>3.487</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>4.969</td><td>48.860</td><td>4.480</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.975</td><td>49.627</td><td>5.475</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.992</td><td>50.292</td><td>6.471</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>5.031</td><td>50.882</td><td>7.473</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>5.102</td><td>51.415</td><td>8.485</td><td>0</td><td>0</td></tr></table>

Cs2(g)   
Cesium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.114</td><td>68.053</td><td>0</td><td>25.400</td><td>17.257</td></tr><tr><td>400</td><td>9.178</td><td>70.739</td><td>.931</td><td>23.779</td><td>14.898</td></tr><tr><td>600</td><td>9.300</td><td>74.484</td><td>2.779</td><td>22.645</td><td>10.706</td></tr><tr><td>800</td><td>9.421</td><td>77.176</td><td>4.651</td><td>21.557</td><td>6.890</td></tr><tr><td>1000</td><td>9.542</td><td>79.291</td><td>6.548</td><td>-11.666</td><td>4.951</td></tr><tr><td>1200</td><td>9.666</td><td>81.042</td><td>8.468</td><td>-11.732</td><td>8.282</td></tr><tr><td>1400</td><td>9.799</td><td>82.542</td><td>10.415</td><td>-11.775</td><td>11.622</td></tr><tr><td>1600</td><td>9.946</td><td>83.859</td><td>12.389</td><td>-11.793</td><td>14.967</td></tr><tr><td>1800</td><td>10.114</td><td>85.040</td><td>14.395</td><td>-11.791</td><td>18.312</td></tr><tr><td>2000</td><td>10.306</td><td>86.116</td><td>16.436</td><td>-11.774</td><td>21.654</td></tr></table>

Cu(c,1)   
Copper 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>5.841</td><td>7.924</td><td>0</td></tr><tr><td>400</td><td>6.030</td><td>9.669</td><td>.605</td></tr><tr><td>600</td><td>6.330</td><td>12.174</td><td>1.843</td></tr><tr><td>800</td><td>6.570</td><td>14.028</td><td>3.133</td></tr><tr><td>1000</td><td>6.830</td><td>15.521</td><td>4.472</td></tr><tr><td>1200</td><td>7.270</td><td>16.803</td><td>5.879</td></tr><tr><td>1358</td><td>7.970</td><td>17.736</td><td>7.075</td></tr><tr><td>1358</td><td>7.800</td><td>20.034</td><td>10.195</td></tr><tr><td>1400</td><td>7.800</td><td>20.274</td><td>10.526</td></tr><tr><td>1600</td><td>7.800</td><td>21.316</td><td>12.086</td></tr><tr><td>1800</td><td>7.800</td><td>22.234</td><td>13.646</td></tr><tr><td>2000</td><td>7.800</td><td>23.056</td><td>15.206</td></tr><tr><td>2200</td><td>7.800</td><td>23.800</td><td>16.766</td></tr><tr><td>2400</td><td>7.800</td><td>24.478</td><td>18.326</td></tr><tr><td>2600</td><td>7.800</td><td>25.103</td><td>19.886</td></tr><tr><td>2800</td><td>7.800</td><td>25.681</td><td>21.446</td></tr></table>

1357.6 K, melting point; $\Delta H^{\circ} = 3.120$   
2839 K, boiling point; $\Delta H^{\circ} = 71.9$

Cu(g)   
Copper (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>39.743</td><td>0</td><td>80.600</td><td>71.113</td></tr><tr><td>400</td><td>4.968</td><td>41.203</td><td>.506</td><td>80.501</td><td>67.887</td></tr><tr><td>600</td><td>4.968</td><td>43.217</td><td>1.500</td><td>80.257</td><td>61.631</td></tr><tr><td>800</td><td>4.968</td><td>44.646</td><td>2.493</td><td>79.960</td><td>55.466</td></tr><tr><td>1000</td><td>4.968</td><td>45.755</td><td>3.487</td><td>79.615</td><td>49.381</td></tr><tr><td>1200</td><td>4.970</td><td>46.661</td><td>4.480</td><td>79.201</td><td>43.371</td></tr><tr><td>1400</td><td>4.977</td><td>47.427</td><td>5.475</td><td>75.549</td><td>37.535</td></tr><tr><td>1600</td><td>4.997</td><td>48.093</td><td>6.472</td><td>74.986</td><td>32.143</td></tr><tr><td>1800</td><td>5.041</td><td>48.684</td><td>7.475</td><td>74.429</td><td>26.819</td></tr><tr><td>2000</td><td>5.116</td><td>49.219</td><td>8.491</td><td>73.885</td><td>21.559</td></tr></table>

Dy(c,1)   
Dysprosium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.720</td><td>17.900</td><td>0</td></tr><tr><td>400</td><td>6.710</td><td>19.870</td><td>.684</td></tr><tr><td>600</td><td>6.790</td><td>22.600</td><td>2.031</td></tr><tr><td>800</td><td>7.070</td><td>24.590</td><td>3.413</td></tr><tr><td>1000</td><td>7.590</td><td>26.220</td><td>4.875</td></tr><tr><td>1200</td><td>8.550</td><td>27.680</td><td>6.480</td></tr><tr><td>1400</td><td>9.870</td><td>29.090</td><td>8.317</td></tr><tr><td>1600</td><td>11.500</td><td>30.510</td><td>10.450</td></tr><tr><td>1657</td><td>11.990</td><td>30.930</td><td>11.119</td></tr><tr><td>1657</td><td>6.700</td><td>31.530</td><td>12.114</td></tr><tr><td>1682</td><td>6.700</td><td>31.630</td><td>12.281</td></tr><tr><td>1682</td><td>11.930</td><td>33.200</td><td>14.924</td></tr><tr><td>1800</td><td>11.930</td><td>34.010</td><td>16.331</td></tr><tr><td>2000</td><td>11.930</td><td>35.260</td><td>18.717</td></tr></table>

1657 K, transition point; $\Delta H^{\circ} = 0.995$   
1682 K, melting point; $\Delta H^{\circ} = 2.643$

Dy(g)   
Dysprosium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.794</td><td>0</td><td>69.400</td><td>60.785</td></tr><tr><td>400</td><td>4.968</td><td>48.254</td><td>.506</td><td>69.222</td><td>57.868</td></tr><tr><td>600</td><td>4.976</td><td>50.269</td><td>1.500</td><td>68.869</td><td>52.268</td></tr><tr><td>800</td><td>5.024</td><td>51.706</td><td>2.499</td><td>68.486</td><td>46.793</td></tr><tr><td>1000</td><td>5.131</td><td>52.838</td><td>3.514</td><td>68.039</td><td>41.421</td></tr><tr><td>1200</td><td>5.285</td><td>53.786</td><td>4.555</td><td>67.475</td><td>36.148</td></tr><tr><td>1400</td><td>5.463</td><td>54.614</td><td>5.629</td><td>66.712</td><td>30.978</td></tr><tr><td>1600</td><td>5.646</td><td>55.356</td><td>6.740</td><td>65.690</td><td>25.936</td></tr><tr><td>1800</td><td>5.822</td><td>56.031</td><td>7.887</td><td>60.956</td><td>21.318</td></tr><tr><td>2000</td><td>5.982</td><td>56.653</td><td>9.068</td><td>59.751</td><td>16.965</td></tr></table>

Er(c,1)   
Erbium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.710</td><td>17.490</td><td>0</td></tr><tr><td>400</td><td>6.790</td><td>19.480</td><td>.688</td></tr><tr><td>600</td><td>6.970</td><td>22.260</td><td>2.063</td></tr><tr><td>800</td><td>7.270</td><td>24.300</td><td>3.485</td></tr><tr><td>1000</td><td>7.670</td><td>25.970</td><td>4.977</td></tr><tr><td>1200</td><td>8.180</td><td>27.410</td><td>6.560</td></tr><tr><td>1400</td><td>8.790</td><td>28.720</td><td>8.256</td></tr><tr><td>1600</td><td>9.520</td><td>29.940</td><td>10.085</td></tr><tr><td>1795</td><td>10.330</td><td>31.080</td><td>12.017</td></tr><tr><td>1795</td><td>9.250</td><td>33.730</td><td>16.774</td></tr><tr><td>1800</td><td>9.250</td><td>33.750</td><td>16.820</td></tr><tr><td>2000</td><td>9.250</td><td>34.730</td><td>18.671</td></tr></table>

1795 K, melting point; $\Delta H^{\circ} = 4.757$

Er(g)   
Erbium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.347</td><td>0</td><td>75.800</td><td>67.196</td></tr><tr><td>400</td><td>4.968</td><td>47.807</td><td>.506</td><td>75.618</td><td>64.287</td></tr><tr><td>600</td><td>4.969</td><td>49.822</td><td>1.500</td><td>75.237</td><td>58.700</td></tr><tr><td>800</td><td>4.984</td><td>51.253</td><td>2.495</td><td>74.810</td><td>53.248</td></tr><tr><td>1000</td><td>5.042</td><td>52.370</td><td>3.496</td><td>74.319</td><td>47.919</td></tr><tr><td>1200</td><td>5.183</td><td>53.300</td><td>4.517</td><td>73.757</td><td>42.689</td></tr><tr><td>1400</td><td>5.441</td><td>54.116</td><td>5.577</td><td>73.121</td><td>37.567</td></tr><tr><td>1600</td><td>5.832</td><td>54.867</td><td>6.702</td><td>72.417</td><td>32.534</td></tr><tr><td>1800</td><td>6.351</td><td>55.583</td><td>7.918</td><td>66.898</td><td>27.599</td></tr><tr><td>2000</td><td>6.971</td><td>56.283</td><td>9.249</td><td>66.378</td><td>23.272</td></tr></table>

Eu(c,1,g)   
Europium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.610</td><td>18.600</td><td>0</td></tr><tr><td>400</td><td>6.680</td><td>20.559</td><td>.677</td></tr><tr><td>600</td><td>7.240</td><td>23.369</td><td>2.065</td></tr><tr><td>800</td><td>7.880</td><td>25.535</td><td>3.573</td></tr><tr><td>1000</td><td>9.090</td><td>27.419</td><td>5.265</td></tr><tr><td>1090</td><td>9.780</td><td>28.231</td><td>6.114</td></tr><tr><td>1090</td><td>9.110</td><td>30.251</td><td>8.316</td></tr><tr><td>1200</td><td>9.110</td><td>31.127</td><td>9.318</td></tr><tr><td>1400</td><td>9.110</td><td>32.532</td><td>11.140</td></tr><tr><td>1600</td><td>9.110</td><td>33.749</td><td>12.962</td></tr><tr><td>1800</td><td>9.110</td><td>34.820</td><td>14.784</td></tr><tr><td>1800</td><td>5.019</td><td>54.034</td><td>49.369</td></tr><tr><td>2000</td><td>5.095</td><td>54.568</td><td>50.379</td></tr></table>

1090 K, melting point; $\Delta H^{\circ} = 2.202$   
1800 K, boiling point; $\Delta H^{\circ} = 34.585$

Eu(g)   
Europium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>45.097</td><td>0</td><td>41.900</td><td>34.000</td></tr><tr><td>400</td><td>4.968</td><td>46.557</td><td>.506</td><td>41.729</td><td>31.330</td></tr><tr><td>600</td><td>4.968</td><td>48.571</td><td>1.500</td><td>41.335</td><td>26.214</td></tr><tr><td>800</td><td>4.968</td><td>50.000</td><td>2.493</td><td>40.820</td><td>21.248</td></tr><tr><td>1000</td><td>4.968</td><td>51.109</td><td>3.487</td><td>40.122</td><td>16.432</td></tr><tr><td>1200</td><td>4.968</td><td>52.014</td><td>4.480</td><td>37.062</td><td>11.998</td></tr><tr><td>1400</td><td>4.971</td><td>52.780</td><td>5.474</td><td>36.234</td><td>7.887</td></tr><tr><td>1600</td><td>4.984</td><td>53.445</td><td>6.470</td><td>35.408</td><td>3.894</td></tr><tr><td>1800</td><td>5.019</td><td>54.034</td><td>7.469</td><td>34.585</td><td>0</td></tr><tr><td>2000</td><td>5.095</td><td>54.568</td><td>8.479</td><td>0</td><td>0</td></tr></table>

$F_{2}(g)$   
Fluorine 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>7.481</td><td>48.443</td><td>0</td></tr><tr><td>400</td><td>7.883</td><td>50.700</td><td>.783</td></tr><tr><td>600</td><td>8.399</td><td>54.005</td><td>2.417</td></tr><tr><td>800</td><td>8.670</td><td>56.462</td><td>4.127</td></tr><tr><td>1000</td><td>8.829</td><td>58.415</td><td>5.878</td></tr><tr><td>1200</td><td>8.935</td><td>60.035</td><td>7.655</td></tr><tr><td>1400</td><td>9.012</td><td>61.418</td><td>9.450</td></tr><tr><td>1600</td><td>9.074</td><td>62.626</td><td>11.259</td></tr><tr><td>1800</td><td>9.126</td><td>63.698</td><td>13.079</td></tr><tr><td>2000</td><td>9.172</td><td>64.662</td><td>14.909</td></tr><tr><td>2200</td><td>9.214</td><td>65.538</td><td>16.747</td></tr><tr><td>2400</td><td>9.253</td><td>66.341</td><td>18.594</td></tr><tr><td>2600</td><td>9.290</td><td>67.083</td><td>20.448</td></tr><tr><td>2800</td><td>9.326</td><td>67.773</td><td>22.310</td></tr><tr><td>3000</td><td>9.361</td><td>68.418</td><td>24.179</td></tr></table>

F(g)   
Fluorine (ideal monatomic gas) 

<table><tr><td> $\mathrm{I}$ </td><td> $\mathrm{Cp}^{\circ}$ </td><td> $\mathrm{S}^{\circ}$ </td><td> $\mathrm{H}^{\circ}-\mathrm{H}_{298}^{\circ}$ </td><td> $\Delta \mathrm{Hf}^{\circ}$ </td><td> $\Delta \mathrm{Gf}^{\circ}$ </td></tr><tr><td>298</td><td>5.437</td><td>37.917</td><td>0</td><td>18.860</td><td>14.777</td></tr><tr><td>400</td><td>5.361</td><td>39.505</td><td>.550</td><td>19.018</td><td>13.356</td></tr><tr><td>600</td><td>5.218</td><td>41.650</td><td>1.607</td><td>19.259</td><td>10.470</td></tr><tr><td>800</td><td>5.133</td><td>43.138</td><td>2.641</td><td>19.438</td><td>7.512</td></tr><tr><td>1000</td><td>5.083</td><td>44.277</td><td>3.663</td><td>19.584</td><td>4.515</td></tr><tr><td>1200</td><td>5.052</td><td>45.201</td><td>4.676</td><td>19.709</td><td>1.488</td></tr><tr><td>1400</td><td>5.032</td><td>45.978</td><td>5.684</td><td>19.819</td><td>-1.558</td></tr><tr><td>1600</td><td>5.018</td><td>46.649</td><td>6.689</td><td>19.920</td><td>-4.618</td></tr><tr><td>1800</td><td>5.009</td><td>47.240</td><td>7.692</td><td>20.013</td><td>-7.691</td></tr><tr><td>2000</td><td>5.001</td><td>47.767</td><td>8.693</td><td>20.099</td><td>-10.773</td></tr><tr><td>2200</td><td>4.996</td><td>48.244</td><td>9.692</td><td>20.179</td><td>-13.866</td></tr><tr><td>2400</td><td>4.992</td><td>48.678</td><td>10.691</td><td>20.254</td><td>-16.964</td></tr><tr><td>2600</td><td>4.988</td><td>49.078</td><td>11.689</td><td>20.325</td><td>-20.070</td></tr><tr><td>2800</td><td>4.986</td><td>49.447</td><td>12.687</td><td>20.392</td><td>-23.177</td></tr><tr><td>3000</td><td>4.984</td><td>49.791</td><td>13.683</td><td>20.453</td><td>-26.293</td></tr></table>

Fe(c,1)   
Iron 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>5.970</td><td>6.520</td><td>0</td></tr><tr><td>400</td><td>6.540</td><td>8.354</td><td>.637</td></tr><tr><td>600</td><td>7.660</td><td>11.216</td><td>2.056</td></tr><tr><td>800</td><td>9.070</td><td>13.594</td><td>3.716</td></tr><tr><td>1000</td><td>12.950</td><td>15.920</td><td>5.814</td></tr><tr><td>1043</td><td>20.000</td><td>16.540</td><td>6.448</td></tr><tr><td>1185</td><td>9.900</td><td>17.967</td><td>8.030</td></tr><tr><td>1185</td><td>8.080</td><td>18.149</td><td>8.245</td></tr><tr><td>1200</td><td>8.110</td><td>18.250</td><td>8.367</td></tr><tr><td>1400</td><td>8.510</td><td>19.530</td><td>10.028</td></tr><tr><td>1600</td><td>8.910</td><td>20.693</td><td>11.770</td></tr><tr><td>1667</td><td>9.050</td><td>21.061</td><td>12.372</td></tr><tr><td>1667</td><td>9.830</td><td>21.181</td><td>12.572</td></tr><tr><td>1800</td><td>10.130</td><td>21.946</td><td>13.897</td></tr><tr><td>1811</td><td>10.170</td><td>22.008</td><td>14.012</td></tr><tr><td>1811</td><td>11.000</td><td>23.830</td><td>17.312</td></tr><tr><td>2000*</td><td>11.000</td><td>24.922</td><td>19.391</td></tr><tr><td>2200</td><td>11.000</td><td>25.970</td><td>21.591</td></tr><tr><td>2400</td><td>11.000</td><td>26.927</td><td>23.791</td></tr><tr><td>2600</td><td>11.000</td><td>27.808</td><td>25.991</td></tr><tr><td>2800</td><td>11.000</td><td>28.623</td><td>28.191</td></tr><tr><td>3000</td><td>11.000</td><td>29.382</td><td>30.391</td></tr></table>

\*Data extrapolated above 1900 K.   
1043 K, Curie point; $\Delta H^{\circ} = 0$   
1185 K, transition point; $\Delta H^{\circ} = 0.215$   
1667 K, transition point; $\Delta H^{\circ} = 0.200$   
1811 K, melting point; $\Delta H^{\circ} = 3.300$

Fe(g)   
Iron (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>6.136</td><td>43.112</td><td>0</td><td>99.300</td><td>88.390</td></tr><tr><td>400</td><td>6.102</td><td>44.915</td><td>.625</td><td>99.288</td><td>84.664</td></tr><tr><td>600</td><td>5.784</td><td>47.331</td><td>1.814</td><td>99.058</td><td>77.389</td></tr><tr><td>800</td><td>5.529</td><td>48.957</td><td>2.944</td><td>98.528</td><td>70.238</td></tr><tr><td>1000</td><td>5.375</td><td>50.173</td><td>4.033</td><td>97.519</td><td>63.266</td></tr><tr><td>1200</td><td>5.302</td><td>51.145</td><td>5.099</td><td>96.032</td><td>56.558</td></tr><tr><td>1400</td><td>5.295</td><td>51.961</td><td>6.158</td><td>95.430</td><td>50.027</td></tr><tr><td>1600</td><td>5.343</td><td>52.671</td><td>7.221</td><td>94.751</td><td>43.586</td></tr><tr><td>1800</td><td>5.432</td><td>53.305</td><td>8.298</td><td>93.701</td><td>37.255</td></tr><tr><td>2000</td><td>5.548</td><td>53.883</td><td>9.396</td><td>89.305</td><td>31.383</td></tr><tr><td>2200</td><td>5.681</td><td>54.418</td><td>10.518</td><td>88.227</td><td>25.641</td></tr><tr><td>2400</td><td>5.823</td><td>54.919</td><td>11.669</td><td>87.178</td><td>19.997</td></tr><tr><td>2600</td><td>5.969</td><td>55.390</td><td>12.848</td><td>86.157</td><td>14.444</td></tr><tr><td>2800</td><td>6.116</td><td>55.838</td><td>14.056</td><td>85.165</td><td>8.963</td></tr><tr><td>3000</td><td>6.264</td><td>56.265</td><td>15.294</td><td>84.203</td><td>3.554</td></tr></table>

Ga(c,1)   
Gallium 

<table><tr><td>f</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.250</td><td>9.758</td><td>0</td></tr><tr><td>303</td><td>6.540</td><td>9.858</td><td>.031</td></tr><tr><td>303</td><td>6.810</td><td>14.269</td><td>1.367</td></tr><tr><td>400</td><td>6.490</td><td>16.105</td><td>2.007</td></tr><tr><td>600</td><td>6.380</td><td>18.709</td><td>3.290</td></tr><tr><td>800</td><td>6.350</td><td>20.538</td><td>4.561</td></tr><tr><td>1000*</td><td>6.350</td><td>21.955</td><td>5.831</td></tr><tr><td>1200</td><td>6.350</td><td>23.112</td><td>7.101</td></tr><tr><td>1400</td><td>6.350</td><td>24.091</td><td>8.371</td></tr><tr><td>1600</td><td>6.350</td><td>24.939</td><td>9.641</td></tr><tr><td>1800</td><td>6.350</td><td>25.687</td><td>10.911</td></tr><tr><td>2000</td><td>6.350</td><td>26.356</td><td>12.181</td></tr></table>

\*Data extrapolated above 900 K.   
302.9 K, melting point; $\Delta H^{\circ} = 1.336$

Ga(g)
Gallium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>6.058</td><td>40.375</td><td>0</td><td>66.200</td><td>57.072</td></tr><tr><td>400</td><td>6.447</td><td>42.222</td><td>.641</td><td>64.834</td><td>54.387</td></tr><tr><td>600</td><td>6.290</td><td>44.829</td><td>1.926</td><td>64.836</td><td>49.164</td></tr><tr><td>800</td><td>5.909</td><td>46.585</td><td>3.145</td><td>64.784</td><td>43.946</td></tr><tr><td>1000</td><td>5.629</td><td>47.871</td><td>4.297</td><td>64.666</td><td>38.750</td></tr><tr><td>1200</td><td>5.445</td><td>48.880</td><td>5.403</td><td>64.502</td><td>33.580</td></tr><tr><td>1400</td><td>5.324</td><td>49.710</td><td>6.479</td><td>64.308</td><td>28.441</td></tr><tr><td>1600</td><td>5.242</td><td>50.415</td><td>7.535</td><td>64.094</td><td>23.332</td></tr><tr><td>1800</td><td>5.185</td><td>51.029</td><td>8.577</td><td>63.866</td><td>18.250</td></tr><tr><td>2000</td><td>5.143</td><td>51.573</td><td>9.610</td><td>63.629</td><td>13.195</td></tr></table>

Gd(c,l)
Gadolinium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H298°</td></tr><tr><td>298</td><td>8.860</td><td>16.240</td><td>0</td></tr><tr><td>400</td><td>6.730</td><td>18.410</td><td>.753</td></tr><tr><td>600</td><td>6.960</td><td>21.180</td><td>2.112</td></tr><tr><td>800</td><td>7.380</td><td>23.240</td><td>3.545</td></tr><tr><td>1000</td><td>7.770</td><td>24.920</td><td>5.059</td></tr><tr><td>1200</td><td>8.250</td><td>26.380</td><td>6.658</td></tr><tr><td>1400</td><td>8.820</td><td>27.700</td><td>8.363</td></tr><tr><td>1533</td><td>9.250</td><td>28.520</td><td>9.565</td></tr><tr><td>1533</td><td>6.830</td><td>29.130</td><td>10.500</td></tr><tr><td>1585</td><td>6.830</td><td>29.350</td><td>10.855</td></tr><tr><td>1585</td><td>8.880</td><td>30.870</td><td>13.258</td></tr><tr><td>1600</td><td>8.880</td><td>30.950</td><td>13.391</td></tr><tr><td>1800</td><td>8.880</td><td>32.000</td><td>15.167</td></tr><tr><td>2000</td><td>8.880</td><td>32.930</td><td>16.943</td></tr></table>

291.8 K, Curie point; $\Delta H^{\circ} = 0$   
1533 K, transition point; $\Delta H^{\circ} = 0.935$   
1585 K, melting point; $\Delta H^{\circ} = 2.403$

Gd(g)
Gadolinium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>6.584</td><td>46.416</td><td>0</td><td>95.000</td><td>86.003</td></tr><tr><td>400</td><td>6.522</td><td>48.343</td><td>.668</td><td>94.915</td><td>82.942</td></tr><tr><td>600</td><td>6.319</td><td>50.952</td><td>1.953</td><td>94.841</td><td>76.978</td></tr><tr><td>800</td><td>6.079</td><td>52.736</td><td>3.193</td><td>94.648</td><td>71.051</td></tr><tr><td>1000</td><td>5.887</td><td>54.070</td><td>4.388</td><td>94.329</td><td>65.179</td></tr><tr><td>1200</td><td>5.793</td><td>55.134</td><td>5.554</td><td>93.896</td><td>59.391</td></tr><tr><td>1400</td><td>5.810</td><td>56.027</td><td>6.713</td><td>93.350</td><td>53.692</td></tr><tr><td>1600</td><td>5.923</td><td>56.809</td><td>7.885</td><td>89.494</td><td>48.120</td></tr><tr><td>1800</td><td>6.108</td><td>57.517</td><td>9.087</td><td>88.920</td><td>42.989</td></tr><tr><td>2000</td><td>6.337</td><td>58.172</td><td>10.331</td><td>88.388</td><td>37.904</td></tr></table>

Ge(c,l)
Germanium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.580</td><td>7.430</td><td>0</td></tr><tr><td>400</td><td>5.850</td><td>9.110</td><td>.585</td></tr><tr><td>600</td><td>6.030</td><td>11.530</td><td>1.775</td></tr><tr><td>800</td><td>6.190</td><td>13.280</td><td>2.996</td></tr><tr><td>1000</td><td>6.500</td><td>14.690</td><td>4.264</td></tr><tr><td>1200</td><td>6.860</td><td>15.910</td><td>5.599</td></tr><tr><td>1210</td><td>6.870</td><td>15.970</td><td>5.670</td></tr><tr><td>1210</td><td>6.600</td><td>23.270</td><td>14.500</td></tr><tr><td>1400</td><td>6.600</td><td>24.230</td><td>15.750</td></tr><tr><td>1600*</td><td>6.600</td><td>25.110</td><td>17.070</td></tr><tr><td>1800</td><td>6.600</td><td>25.890</td><td>18.390</td></tr><tr><td>2000</td><td>6.600</td><td>26.590</td><td>19.710</td></tr></table>

\*Data extrapolated above 1500 K.   
1210.4 K, melting point; $\Delta H^{\circ} = 8.830$

Ge(g)
Germanium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>7.345</td><td>40.104</td><td>0</td><td>89.500</td><td>79.758</td></tr><tr><td>400</td><td>7.426</td><td>42.283</td><td>.756</td><td>89.671</td><td>76.402</td></tr><tr><td>600</td><td>6.995</td><td>45.226</td><td>2.204</td><td>89.929</td><td>69.711</td></tr><tr><td>800</td><td>6.460</td><td>47.162</td><td>3.548</td><td>90.052</td><td>62.946</td></tr><tr><td>1000</td><td>6.059</td><td>48.558</td><td>4.797</td><td>90.033</td><td>56.165</td></tr><tr><td>1200</td><td>5.799</td><td>49.638</td><td>5.981</td><td>89.882</td><td>49.408</td></tr><tr><td>1400</td><td>5.646</td><td>50.519</td><td>7.124</td><td>80.874</td><td>44.069</td></tr><tr><td>1600</td><td>5.566</td><td>51.267</td><td>8.244</td><td>80.674</td><td>38.823</td></tr><tr><td>1800</td><td>5.531</td><td>51.920</td><td>9.353</td><td>80.463</td><td>33.609</td></tr><tr><td>2000</td><td>5.524</td><td>52.503</td><td>10.459</td><td>80.249</td><td>28.423</td></tr></table>

$H_{2}(g)$ Hydrogen 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.892</td><td>31.207</td><td>0</td></tr><tr><td>400</td><td>6.974</td><td>33.247</td><td>.707</td></tr><tr><td>600</td><td>7.009</td><td>36.082</td><td>2.106</td></tr><tr><td>800</td><td>7.080</td><td>38.107</td><td>3.514</td></tr><tr><td>1000</td><td>7.219</td><td>39.700</td><td>4.943</td></tr><tr><td>1200</td><td>7.407</td><td>41.033</td><td>6.405</td></tr><tr><td>1400</td><td>7.615</td><td>42.190</td><td>7.907</td></tr><tr><td>1600</td><td>7.821</td><td>43.220</td><td>9.451</td></tr><tr><td>1800</td><td>8.016</td><td>44.153</td><td>11.035</td></tr><tr><td>2000</td><td>8.193</td><td>45.007</td><td>12.656</td></tr><tr><td>2200</td><td>8.354</td><td>45.795</td><td>14.311</td></tr><tr><td>2400</td><td>8.499</td><td>46.529</td><td>15.996</td></tr><tr><td>2600</td><td>8.631</td><td>47.214</td><td>17.709</td></tr><tr><td>2800</td><td>8.752</td><td>47.858</td><td>19.448</td></tr><tr><td>3000</td><td>8.864</td><td>48.466</td><td>21.209</td></tr></table>

H(g)
Hydrogen (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>27.392</td><td>0</td><td>52.103</td><td>48.588</td></tr><tr><td>400</td><td>4.968</td><td>28.852</td><td>.506</td><td>52.256</td><td>47.364</td></tr><tr><td>600</td><td>4.968</td><td>30.866</td><td>1.500</td><td>52.550</td><td>44.855</td></tr><tr><td>800</td><td>4.968</td><td>32.295</td><td>2.493</td><td>52.839</td><td>42.246</td></tr><tr><td>1000</td><td>4.968</td><td>33.404</td><td>3.487</td><td>53.118</td><td>39.565</td></tr><tr><td>1200</td><td>4.968</td><td>34.309</td><td>4.480</td><td>53.381</td><td>36.830</td></tr><tr><td>1400</td><td>4.968</td><td>35.075</td><td>5.474</td><td>53.624</td><td>34.051</td></tr><tr><td>1600</td><td>4.968</td><td>35.739</td><td>6.468</td><td>53.846</td><td>31.239</td></tr><tr><td>1800</td><td>4.968</td><td>36.324</td><td>7.461</td><td>54.047</td><td>28.401</td></tr><tr><td>2000</td><td>4.968</td><td>36.847</td><td>8.455</td><td>54.230</td><td>25.543</td></tr><tr><td>2200</td><td>4.968</td><td>37.321</td><td>9.448</td><td>54.396</td><td>22.664</td></tr><tr><td>2400</td><td>4.968</td><td>37.753</td><td>10.442</td><td>54.547</td><td>19.775</td></tr><tr><td>2600</td><td>4.968</td><td>38.151</td><td>11.436</td><td>54.685</td><td>16.870</td></tr><tr><td>2800</td><td>4.968</td><td>38.519</td><td>12.429</td><td>54.808</td><td>13.956</td></tr><tr><td>3000</td><td>4.968</td><td>38.862</td><td>13.423</td><td>54.922</td><td>11.035</td></tr></table>

$D_{2}(g)$ Deuterium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.978</td><td>34.620</td><td>0</td></tr><tr><td>400</td><td>6.989</td><td>36.672</td><td>.711</td></tr><tr><td>600</td><td>7.079</td><td>39.519</td><td>2.116</td></tr><tr><td>800</td><td>7.290</td><td>41.582</td><td>3.551</td></tr><tr><td>1000</td><td>7.561</td><td>43.237</td><td>5.036</td></tr><tr><td>1200</td><td>7.830</td><td>44.640</td><td>6.575</td></tr><tr><td>1400</td><td>8.070</td><td>45.865</td><td>8.166</td></tr><tr><td>1600</td><td>8.275</td><td>46.957</td><td>9.801</td></tr><tr><td>1800</td><td>8.450</td><td>47.942</td><td>11.474</td></tr><tr><td>2000</td><td>8.598</td><td>48.840</td><td>13.179</td></tr></table>

D(g)
Deuterium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>29.455</td><td>0</td><td>52.992</td><td>49.371</td></tr><tr><td>400</td><td>4.968</td><td>30.915</td><td>.506</td><td>53.143</td><td>48.111</td></tr><tr><td>600</td><td>4.968</td><td>32.930</td><td>1.500</td><td>53.434</td><td>45.532</td></tr><tr><td>800</td><td>4.968</td><td>34.359</td><td>2.493</td><td>53.709</td><td>42.855</td></tr><tr><td>1000</td><td>4.968</td><td>35.467</td><td>3.487</td><td>53.961</td><td>40.112</td></tr><tr><td>1200</td><td>4.968</td><td>36.373</td><td>4.480</td><td>54.185</td><td>37.321</td></tr><tr><td>1400</td><td>4.968</td><td>37.139</td><td>5.474</td><td>54.383</td><td>34.494</td></tr><tr><td>1600</td><td>4.968</td><td>37.802</td><td>6.468</td><td>54.559</td><td>31.642</td></tr><tr><td>1800</td><td>4.968</td><td>38.387</td><td>7.461</td><td>54.716</td><td>28.767</td></tr><tr><td>2000</td><td>4.968</td><td>38.911</td><td>8.455</td><td>54.858</td><td>25.876</td></tr></table>

He(g)
Helium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>4.968</td><td>30.125</td><td>0</td></tr><tr><td>400</td><td>4.968</td><td>31.585</td><td>.506</td></tr><tr><td>600</td><td>4.968</td><td>33.599</td><td>1.500</td></tr><tr><td>800</td><td>4.968</td><td>35.028</td><td>2.493</td></tr><tr><td>1000</td><td>4.968</td><td>36.137</td><td>3.487</td></tr><tr><td>1200</td><td>4.968</td><td>37.043</td><td>4.480</td></tr><tr><td>1400</td><td>4.968</td><td>37.809</td><td>5.474</td></tr><tr><td>1600</td><td>4.968</td><td>38.472</td><td>6.468</td></tr><tr><td>1800</td><td>4.968</td><td>39.057</td><td>7.461</td></tr><tr><td>2000</td><td>4.968</td><td>39.581</td><td>8.455</td></tr><tr><td>2200</td><td>4.968</td><td>40.054</td><td>9.448</td></tr><tr><td>2400</td><td>4.968</td><td>40.486</td><td>10.442</td></tr><tr><td>2600</td><td>4.968</td><td>40.884</td><td>11.436</td></tr><tr><td>2800</td><td>4.968</td><td>41.252</td><td>12.429</td></tr><tr><td>3000</td><td>4.968</td><td>41.595</td><td>13.423</td></tr></table>

Hf(c,1)   
Hafnium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.150</td><td>10.410</td><td>0</td></tr><tr><td>400</td><td>6.340</td><td>12.240</td><td>.636</td></tr><tr><td>600</td><td>6.700</td><td>14.880</td><td>1.939</td></tr><tr><td>800</td><td>7.060</td><td>16.860</td><td>3.316</td></tr><tr><td>1000</td><td>7.430</td><td>18.470</td><td>4.765</td></tr><tr><td>1200</td><td>7.790</td><td>19.860</td><td>6.287</td></tr><tr><td>1400</td><td>8.160</td><td>21.090</td><td>7.882</td></tr><tr><td>1600</td><td>8.520</td><td>22.200</td><td>9.550</td></tr><tr><td>1800</td><td>8.890</td><td>23.230</td><td>11.291</td></tr><tr><td>2000</td><td>9.250</td><td>24.180</td><td>13.105</td></tr><tr><td>2013</td><td>9.270</td><td>24.240</td><td>13.225</td></tr><tr><td>2013*</td><td>7.887</td><td>25.040</td><td>14.835</td></tr><tr><td>2200</td><td>8.147</td><td>25.750</td><td>16.331</td></tr><tr><td>2400</td><td>8.679</td><td>26.480</td><td>18.009</td></tr><tr><td>2470</td><td>8.927</td><td>26.773</td><td>18.625</td></tr><tr><td>2470</td><td>8.000</td><td>29.033</td><td>24.305</td></tr><tr><td>2600</td><td>8.000</td><td>29.443</td><td>25.345</td></tr><tr><td>2800</td><td>8.000</td><td>30.036</td><td>26.945</td></tr><tr><td>3000</td><td>8.000</td><td>30.588</td><td>28.545</td></tr></table>

\*Data estimated above 2013 K   
2013 K, transition point; $\Delta H^{\circ} = 1.610$   
2470 K, melting point; $\Delta H^{\circ} = 5.680$

Hf(g)   
Hafnium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.972</td><td>44.643</td><td>0</td><td>148.000</td><td>137.793</td></tr><tr><td>400</td><td>5.010</td><td>46.108</td><td>.508</td><td>147.872</td><td>134.325</td></tr><tr><td>600</td><td>5.285</td><td>48.182</td><td>1.533</td><td>147.594</td><td>127.613</td></tr><tr><td>800</td><td>5.734</td><td>49.762</td><td>2.634</td><td>147.318</td><td>120.996</td></tr><tr><td>1000</td><td>6.196</td><td>51.092</td><td>3.827</td><td>147.062</td><td>114.440</td></tr><tr><td>1200</td><td>6.596</td><td>52.259</td><td>5.108</td><td>146.821</td><td>107.942</td></tr><tr><td>1400</td><td>6.906</td><td>53.300</td><td>6.460</td><td>146.578</td><td>101.484</td></tr><tr><td>1600</td><td>7.123</td><td>54.237</td><td>7.864</td><td>146.314</td><td>95.055</td></tr><tr><td>1800</td><td>7.260</td><td>55.085</td><td>9.304</td><td>146.013</td><td>88.674</td></tr><tr><td>2000</td><td>7.338</td><td>55.854</td><td>10.764</td><td>145.659</td><td>82.311</td></tr><tr><td>2200</td><td>7.379</td><td>56.556</td><td>12.236</td><td>143.905</td><td>76.132</td></tr><tr><td>2400</td><td>7.403</td><td>57.199</td><td>13.715</td><td>143.706</td><td>69.980</td></tr></table>

Hg(1,g)  
Mercury 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.687</td><td>18.140</td><td>0</td></tr><tr><td>400</td><td>6.552</td><td>20.084</td><td>.673</td></tr><tr><td>600</td><td>6.486</td><td>22.721</td><td>1.974</td></tr><tr><td>630</td><td>6.497</td><td>23.038</td><td>2.167</td></tr><tr><td>630</td><td>4.968</td><td>45.507</td><td>16.318</td></tr><tr><td>800</td><td>4.968</td><td>46.695</td><td>17.163</td></tr><tr><td>1000</td><td>4.968</td><td>47.804</td><td>18.157</td></tr><tr><td>1200</td><td>4.968</td><td>48.710</td><td>19.151</td></tr><tr><td>1400</td><td>4.968</td><td>49.476</td><td>20.144</td></tr><tr><td>1600</td><td>4.968</td><td>50.139</td><td>21.138</td></tr><tr><td>1800</td><td>4.968</td><td>50.724</td><td>22.131</td></tr><tr><td>2000</td><td>4.968</td><td>51.248</td><td>23.125</td></tr></table>

234.29 K, melting point; $\Delta H^{\circ} = 0.549$   
629.81 K, boiling point; $\Delta H^{\circ} = 14.151$

Hg(g)   
Mercury (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.792</td><td>0</td><td>14.670</td><td>7.618</td></tr><tr><td>400</td><td>4.968</td><td>43.252</td><td>.506</td><td>14.503</td><td>5.236</td></tr><tr><td>600</td><td>4.968</td><td>45.266</td><td>1.500</td><td>14.196</td><td>.669</td></tr><tr><td>800</td><td>4.968</td><td>46.695</td><td>2.493</td><td>0</td><td>0</td></tr><tr><td>1000</td><td>4.968</td><td>47.804</td><td>3.487</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>4.968</td><td>48.710</td><td>4.481</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.968</td><td>49.476</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.968</td><td>50.139</td><td>6.468</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.968</td><td>50.724</td><td>7.461</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.968</td><td>51.248</td><td>8.455</td><td>0</td><td>0</td></tr></table>

Ho(c,1)

Holmium 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.490</td><td>17.930</td><td>0</td></tr><tr><td>400</td><td>6.650</td><td>19.860</td><td>.670</td></tr><tr><td>600</td><td>6.760</td><td>22.590</td><td>2.016</td></tr><tr><td>800</td><td>6.950</td><td>24.550</td><td>3.378</td></tr><tr><td>1000</td><td>7.610</td><td>26.170</td><td>4.832</td></tr><tr><td>1200</td><td>8.580</td><td>27.640</td><td>6.447</td></tr><tr><td>1400</td><td>9.890</td><td>29.060</td><td>8.291</td></tr><tr><td>1600</td><td>11.540</td><td>30.480</td><td>10.429</td></tr><tr><td>1701*</td><td>12.460</td><td>31.210</td><td>11.641</td></tr><tr><td>1701</td><td>6.700</td><td>31.870</td><td>12.762</td></tr><tr><td>1743</td><td>6.700</td><td>32.040</td><td>13.043</td></tr><tr><td>1743</td><td>10.500</td><td>33.710</td><td>15.954</td></tr><tr><td>1800</td><td>10.500</td><td>34.050</td><td>16.553</td></tr><tr><td>2000</td><td>10.500</td><td>35.150</td><td>18.653</td></tr></table>

\*Data for Ho(β) and Ho(1) estimated   
1701 K, transition point; $\Delta H^{\circ} = 1.121$   
1743 K, melting point; $\Delta H^{\circ} = 2.911$

Ho(g)   
Holmium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.718</td><td>0</td><td>71.900</td><td>63.317</td></tr><tr><td>400</td><td>4.968</td><td>48.178</td><td>.506</td><td>71.736</td><td>60.409</td></tr><tr><td>600</td><td>4.969</td><td>50.192</td><td>1.500</td><td>71.384</td><td>54.823</td></tr><tr><td>800</td><td>4.978</td><td>51.623</td><td>2.494</td><td>71.016</td><td>49.358</td></tr><tr><td>1000</td><td>5.012</td><td>52.736</td><td>3.492</td><td>70.560</td><td>43.994</td></tr><tr><td>1200</td><td>5.083</td><td>53.656</td><td>4.501</td><td>69.954</td><td>38.735</td></tr><tr><td>1400</td><td>5.188</td><td>54.447</td><td>5.528</td><td>69.137</td><td>33.595</td></tr><tr><td>1600</td><td>5.320</td><td>55.148</td><td>6.578</td><td>68.049</td><td>28.580</td></tr><tr><td>1800</td><td>5.466</td><td>55.783</td><td>7.657</td><td>63.004</td><td>23.885</td></tr><tr><td>2000</td><td>5.616</td><td>56.367</td><td>8.765</td><td>62.012</td><td>19.578</td></tr></table>

$I_{2}(c,l,g)$   
Iodine 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>13.011</td><td>27.758</td><td>0</td></tr><tr><td>387</td><td>15.188</td><td>31.365</td><td>1.232</td></tr><tr><td>387</td><td>19.281</td><td>40.954</td><td>4.941</td></tr><tr><td>400</td><td>19.281</td><td>41.602</td><td>5.196</td></tr><tr><td>458</td><td>19.281</td><td>44.232</td><td>6.321</td></tr><tr><td>458</td><td>8.930</td><td>66.093</td><td>16.342</td></tr><tr><td>600</td><td>8.980</td><td>68.507</td><td>17.611</td></tr><tr><td>800</td><td>9.025</td><td>71.097</td><td>19.411</td></tr><tr><td>1000</td><td>9.061</td><td>73.115</td><td>21.220</td></tr><tr><td>1200</td><td>9.092</td><td>74.770</td><td>23.036</td></tr><tr><td>1400</td><td>9.122</td><td>76.174</td><td>24.857</td></tr><tr><td>1600</td><td>9.151</td><td>77.394</td><td>26.684</td></tr><tr><td>1800</td><td>9.179</td><td>78.473</td><td>28.517</td></tr><tr><td>2000</td><td>9.207</td><td>79.442</td><td>30.356</td></tr><tr><td>2200</td><td>9.234</td><td>80.320</td><td>32.200</td></tr><tr><td>2400</td><td>9.261</td><td>81.125</td><td>34.050</td></tr><tr><td>2600</td><td>9.289</td><td>81.867</td><td>35.905</td></tr><tr><td>2800</td><td>9.316</td><td>82.557</td><td>37.765</td></tr><tr><td>3000</td><td>9.343</td><td>83.200</td><td>39.631</td></tr></table>

386.8 K, melting point; $\Delta H^{\circ} = 3.709$   
458.4 K, boiling point; $\Delta H^{\circ} = 10.021$

I(g)   
Iodine (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>43.182</td><td>0</td><td>25.517</td><td>16.780</td></tr><tr><td>400</td><td>4.968</td><td>44.642</td><td>.506</td><td>23.425</td><td>13.889</td></tr><tr><td>600</td><td>4.968</td><td>46.656</td><td>1.500</td><td>18.212</td><td>10.770</td></tr><tr><td>800</td><td>4.968</td><td>48.086</td><td>2.493</td><td>18.305</td><td>8.275</td></tr><tr><td>1000</td><td>4.970</td><td>49.194</td><td>3.487</td><td>18.394</td><td>5.757</td></tr><tr><td>1200</td><td>4.977</td><td>50.101</td><td>4.482</td><td>18.481</td><td>3.222</td></tr><tr><td>1400</td><td>4.992</td><td>50.869</td><td>5.478</td><td>18.567</td><td>.672</td></tr><tr><td>1600</td><td>5.018</td><td>51.537</td><td>6.479</td><td>18.654</td><td>-1.890</td></tr><tr><td>1800</td><td>5.052</td><td>52.130</td><td>7.486</td><td>18.745</td><td>-4.464</td></tr><tr><td>2000</td><td>5.093</td><td>52.665</td><td>8.500</td><td>18.839</td><td>-7.049</td></tr><tr><td>2200</td><td>5.137</td><td>53.152</td><td>9.523</td><td>18.940</td><td>-9.642</td></tr><tr><td>2400</td><td>5.182</td><td>53.601</td><td>10.555</td><td>19.047</td><td>-12.245</td></tr><tr><td>2600</td><td>5.226</td><td>54.017</td><td>11.596</td><td>19.160</td><td>-14.857</td></tr><tr><td>2800</td><td>5.267</td><td>54.406</td><td>12.645</td><td>19.280</td><td>-17.477</td></tr><tr><td>3000</td><td>5.304</td><td>54.771</td><td>13.702</td><td>19.404</td><td>-20.110</td></tr></table>

$I_{2}(g)$   
Iodine (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.814</td><td>62.277</td><td>0</td><td>14.919</td><td>4.627</td></tr><tr><td>400</td><td>8.901</td><td>64.881</td><td>.903</td><td>10.626</td><td>1.314</td></tr><tr><td>600</td><td>8.980</td><td>68.507</td><td>2.692</td><td>0</td><td>0</td></tr><tr><td>800</td><td>9.025</td><td>71.097</td><td>4.492</td><td>0</td><td>0</td></tr><tr><td>1000</td><td>9.061</td><td>73.115</td><td>6.301</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>9.092</td><td>74.770</td><td>8.117</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>9.122</td><td>76.174</td><td>9.938</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>9.151</td><td>77.394</td><td>11.765</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>9.179</td><td>78.473</td><td>13.598</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>9.207</td><td>79.442</td><td>15.437</td><td>0</td><td>0</td></tr><tr><td>2200</td><td>9.234</td><td>80.320</td><td>17.281</td><td>0</td><td>0</td></tr><tr><td>2400</td><td>9.261</td><td>81.125</td><td>19.131</td><td>0</td><td>0</td></tr><tr><td>2600</td><td>9.289</td><td>81.867</td><td>20.986</td><td>0</td><td>0</td></tr><tr><td>2800</td><td>9.316</td><td>82.557</td><td>22.846</td><td>0</td><td>0</td></tr><tr><td>3000</td><td>9.343</td><td>83.200</td><td>24.712</td><td>0</td><td>0</td></tr></table>

In(c,1)   
Indium 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.389</td><td>13.820</td><td>0</td></tr><tr><td>400</td><td>6.925</td><td>15.763</td><td>.676</td></tr><tr><td>430</td><td>7.250</td><td>16.270</td><td>.886</td></tr><tr><td>430</td><td>7.050</td><td>18.085</td><td>1.666</td></tr><tr><td>600</td><td>7.010</td><td>20.430</td><td>2.862</td></tr><tr><td>800</td><td>6.970</td><td>22.441</td><td>4.260</td></tr><tr><td>1000*</td><td>6.950</td><td>23.993</td><td>5.651</td></tr><tr><td>1200</td><td>6.950</td><td>25.260</td><td>7.041</td></tr><tr><td>1400</td><td>6.950</td><td>26.332</td><td>8.431</td></tr><tr><td>1600</td><td>6.950</td><td>27.260</td><td>9.821</td></tr><tr><td>1800</td><td>6.950</td><td>28.078</td><td>11.211</td></tr><tr><td>2000</td><td>6.950</td><td>28.810</td><td>12.601</td></tr></table>

\*Data extrapolated above 800 K.   
429.78 K, melting point; $\Delta H^{\circ} = 0.780$

In(g)   
Indium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.979</td><td>41.508</td><td>0</td><td>58.150</td><td>49.895</td></tr><tr><td>400</td><td>5.056</td><td>42.979</td><td>.510</td><td>57.984</td><td>47.098</td></tr><tr><td>600</td><td>5.512</td><td>45.104</td><td>1.562</td><td>56.850</td><td>42.046</td></tr><tr><td>800</td><td>6.062</td><td>46.768</td><td>2.721</td><td>56.611</td><td>37.149</td></tr><tr><td>1000</td><td>6.391</td><td>48.161</td><td>3.971</td><td>56.470</td><td>32.302</td></tr><tr><td>1200</td><td>6.482</td><td>49.337</td><td>5.261</td><td>56.370</td><td>27.478</td></tr><tr><td>1400</td><td>6.422</td><td>50.334</td><td>6.554</td><td>56.273</td><td>22.670</td></tr><tr><td>1600</td><td>6.295</td><td>51.183</td><td>7.826</td><td>56.155</td><td>17.878</td></tr><tr><td>1800</td><td>6.147</td><td>51.916</td><td>9.070</td><td>56.009</td><td>13.101</td></tr><tr><td>2000</td><td>6.003</td><td>52.556</td><td>10.285</td><td>55.834</td><td>8.342</td></tr></table>

Ir(c)  
Iridium 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>5.996</td><td>8.481</td><td>0</td></tr><tr><td>400</td><td>6.102</td><td>10.257</td><td>.616</td></tr><tr><td>600</td><td>6.362</td><td>12.779</td><td>1.862</td></tr><tr><td>800</td><td>6.640</td><td>14.647</td><td>3.162</td></tr><tr><td>1000</td><td>6.927</td><td>16.160</td><td>4.518</td></tr><tr><td>1200</td><td>7.221</td><td>17.448</td><td>5.933</td></tr><tr><td>1400</td><td>7.521</td><td>18.584</td><td>7.407</td></tr><tr><td>1600</td><td>7.830</td><td>19.608</td><td>8.942</td></tr><tr><td>1800</td><td>8.147</td><td>20.549</td><td>10.540</td></tr><tr><td>2000</td><td>8.475</td><td>21.424</td><td>12.202</td></tr></table>

2716 K, melting point; $\Delta H^{\circ} = 6.247$

Ir(g)   
Iridium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.241</td><td>0</td><td>159.000</td><td>147.742</td></tr><tr><td>400</td><td>4.976</td><td>47.701</td><td>.506</td><td>158.890</td><td>143.912</td></tr><tr><td>600</td><td>5.075</td><td>49.733</td><td>1.509</td><td>158.647</td><td>136.475</td></tr><tr><td>800</td><td>5.314</td><td>51.222</td><td>2.546</td><td>158.384</td><td>129.124</td></tr><tr><td>1000</td><td>5.622</td><td>52.441</td><td>3.639</td><td>158.121</td><td>121.840</td></tr><tr><td>1200</td><td>5.933</td><td>53.494</td><td>4.795</td><td>157.862</td><td>114.607</td></tr><tr><td>1400</td><td>6.215</td><td>54.430</td><td>6.010</td><td>157.603</td><td>107.419</td></tr><tr><td>1600</td><td>6.455</td><td>55.276</td><td>7.278</td><td>157.336</td><td>100.267</td></tr><tr><td>1800</td><td>6.654</td><td>56.048</td><td>8.589</td><td>157.049</td><td>93.151</td></tr><tr><td>2000</td><td>6.815</td><td>56.758</td><td>9.937</td><td>156.735</td><td>86.067</td></tr></table>

K(c,1,g)

Potassium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>7.050</td><td>15.457</td><td>0</td></tr><tr><td>336</td><td>7.696</td><td>16.353</td><td>.284</td></tr><tr><td>336</td><td>7.707</td><td>18.012</td><td>.842</td></tr><tr><td>400</td><td>7.528</td><td>19.332</td><td>1.327</td></tr><tr><td>600</td><td>7.203</td><td>22.315</td><td>2.796</td></tr><tr><td>800</td><td>7.112</td><td>24.369</td><td>4.224</td></tr><tr><td>1000</td><td>7.256</td><td>25.967</td><td>5.657</td></tr><tr><td>1044</td><td>7.326</td><td>26.280</td><td>5.976</td></tr><tr><td>1044</td><td>4.968</td><td>44.521</td><td>25.014</td></tr><tr><td>1200</td><td>4.968</td><td>45.215</td><td>25.791</td></tr><tr><td>1400</td><td>4.970</td><td>45.981</td><td>26.784</td></tr><tr><td>1600</td><td>4.975</td><td>46.645</td><td>27.779</td></tr><tr><td>1800</td><td>4.988</td><td>47.231</td><td>28.775</td></tr><tr><td>2000</td><td>5.013</td><td>47.758</td><td>29.775</td></tr><tr><td>2200</td><td>5.057</td><td>48.238</td><td>30.781</td></tr><tr><td>2400</td><td>5.122</td><td>48.680</td><td>31.799</td></tr><tr><td>2600</td><td>5.213</td><td>49.094</td><td>32.832</td></tr><tr><td>2800</td><td>5.334</td><td>49.484</td><td>33.886</td></tr><tr><td>3000</td><td>5.489</td><td>49.857</td><td>34.968</td></tr></table>

336.35 K, melting point; $\Delta H^{\circ} = 0.558$   
1043.7 K, boiling point to ideal   
monatomic gas; $\Delta H^{\circ} = 19.038$

K(g)   
Potassium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>38.297</td><td>0</td><td>21.310</td><td>14.500</td></tr><tr><td>400</td><td>4.968</td><td>39.757</td><td>.506</td><td>20.489</td><td>12.319</td></tr><tr><td>600</td><td>4.968</td><td>41.771</td><td>1.500</td><td>20.014</td><td>8.340</td></tr><tr><td>800</td><td>4.968</td><td>43.200</td><td>2.493</td><td>19.579</td><td>4.514</td></tr><tr><td>1000</td><td>4.968</td><td>44.309</td><td>3.487</td><td>19.140</td><td>.798</td></tr><tr><td>1200</td><td>4.968</td><td>45.215</td><td>4.481</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.970</td><td>45.981</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.975</td><td>46.645</td><td>6.469</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.988</td><td>47.231</td><td>7.465</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>5.013</td><td>47.758</td><td>8.465</td><td>0</td><td>0</td></tr><tr><td>2200</td><td>5.057</td><td>48.238</td><td>9.471</td><td>0</td><td>0</td></tr><tr><td>2400</td><td>5.122</td><td>48.680</td><td>10.489</td><td>0</td><td>0</td></tr><tr><td>2600</td><td>5.213</td><td>49.094</td><td>11.522</td><td>0</td><td>0</td></tr><tr><td>2800</td><td>5.334</td><td>49.484</td><td>12.576</td><td>0</td><td>0</td></tr><tr><td>3000</td><td>5.489</td><td>49.857</td><td>13.658</td><td>0</td><td>0</td></tr></table>

Kr(g)   
Krypton 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>4.968</td><td>39.191</td><td>0</td></tr><tr><td>400</td><td>4.968</td><td>40.651</td><td>.506</td></tr><tr><td>600</td><td>4.968</td><td>42.665</td><td>1.500</td></tr><tr><td>800</td><td>4.968</td><td>44.094</td><td>2.493</td></tr><tr><td>1000</td><td>4.968</td><td>45.203</td><td>3.487</td></tr><tr><td>1200</td><td>4.968</td><td>46.109</td><td>4.480</td></tr><tr><td>1400</td><td>4.968</td><td>46.875</td><td>5.474</td></tr><tr><td>1600</td><td>4.968</td><td>47.538</td><td>6.468</td></tr><tr><td>1800</td><td>4.968</td><td>48.123</td><td>7.461</td></tr><tr><td>2000</td><td>4.968</td><td>48.647</td><td>8.455</td></tr><tr><td>2200</td><td>4.968</td><td>49.120</td><td>9.448</td></tr><tr><td>2400</td><td>4.968</td><td>49.552</td><td>10.442</td></tr><tr><td>2600</td><td>4.968</td><td>49.950</td><td>11.436</td></tr><tr><td>2800</td><td>4.968</td><td>50.318</td><td>12.429</td></tr><tr><td>3000</td><td>4.968</td><td>50.661</td><td>13.423</td></tr></table>

$K_{2}(g)$   
Potassium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.057</td><td>59.666</td><td>0</td><td>30.374</td><td>21.802</td></tr><tr><td>400</td><td>9.121</td><td>62.336</td><td>.926</td><td>28.646</td><td>19.177</td></tr><tr><td>600</td><td>9.230</td><td>66.056</td><td>2.761</td><td>27.543</td><td>14.687</td></tr><tr><td>800</td><td>9.332</td><td>68.725</td><td>4.617</td><td>26.543</td><td>10.553</td></tr><tr><td>1000</td><td>9.432</td><td>70.818</td><td>6.494</td><td>25.554</td><td>6.670</td></tr><tr><td>1200</td><td>9.532</td><td>72.547</td><td>8.390</td><td>-12.818</td><td>8.642</td></tr><tr><td>1400</td><td>9.631</td><td>74.024</td><td>10.307</td><td>-12.887</td><td>12.226</td></tr><tr><td>1600</td><td>9.730</td><td>75.316</td><td>12.243</td><td>-12.941</td><td>15.817</td></tr><tr><td>1800</td><td>9.826</td><td>76.468</td><td>14.198</td><td>-12.978</td><td>19.411</td></tr><tr><td>2000</td><td>9.927</td><td>77.508</td><td>16.174</td><td>-13.002</td><td>23.014</td></tr><tr><td>2200</td><td>10.026</td><td>78.459</td><td>18.169</td><td>-13.019</td><td>26.618</td></tr><tr><td>2400</td><td>10.124</td><td>79.336</td><td>20.184</td><td>-13.040</td><td>30.218</td></tr><tr><td>2600</td><td>10.223</td><td>80.150</td><td>22.219</td><td>-13.071</td><td>33.828</td></tr><tr><td>2800</td><td>10.322</td><td>80.911</td><td>24.273</td><td>-13.125</td><td>37.435</td></tr><tr><td>3000</td><td>10.420</td><td>81.627</td><td>26.348</td><td>-13.214</td><td>41.047</td></tr></table>

La(c,1)   
Lanthanum 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.480</td><td>13.600</td><td>0</td></tr><tr><td>400</td><td>6.550</td><td>15.510</td><td>.664</td></tr><tr><td>550</td><td>6.620</td><td>17.610</td><td>1.651</td></tr><tr><td>550</td><td>6.500</td><td>17.770</td><td>1.738</td></tr><tr><td>600</td><td>6.640</td><td>18.340</td><td>2.067</td></tr><tr><td>800</td><td>7.240</td><td>20.330</td><td>3.453</td></tr><tr><td>1000</td><td>7.900</td><td>22.020</td><td>4.966</td></tr><tr><td>1134</td><td>8.370</td><td>23.040</td><td>6.055</td></tr><tr><td>1134</td><td>9.480</td><td>23.700</td><td>6.801</td></tr><tr><td>1193</td><td>9.480</td><td>24.180</td><td>7.360</td></tr><tr><td>1193</td><td>8.200</td><td>25.420</td><td>8.841</td></tr><tr><td>1200</td><td>8.200</td><td>25.470</td><td>8.898</td></tr><tr><td>1400</td><td>8.200</td><td>26.730</td><td>10.538</td></tr><tr><td>1600*</td><td>8.200</td><td>27.830</td><td>12.178</td></tr><tr><td>1800</td><td>8.200</td><td>28.790</td><td>13.818</td></tr><tr><td>2000</td><td>8.200</td><td>29.660</td><td>15.458</td></tr></table>

\*Data extrapolated above 1400 K.   
550 K, transition point; $\Delta H^{\circ} = 0.087$   
1134 K, transition point; $\Delta H^{\circ} = 0.746$   
1193 K, melting point; $\Delta H^{\circ} = 1.481$

La(g)   
Lanthanum (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.438</td><td>43.564</td><td>0</td><td>103.000</td><td>94.066</td></tr><tr><td>400</td><td>5.894</td><td>45.228</td><td>.578</td><td>102.914</td><td>91.027</td></tr><tr><td>600</td><td>6.455</td><td>47.736</td><td>1.819</td><td>102.752</td><td>85.114</td></tr><tr><td>800</td><td>6.874</td><td>49.650</td><td>3.152</td><td>102.699</td><td>79.243</td></tr><tr><td>1000</td><td>7.248</td><td>51.226</td><td>4.566</td><td>102.600</td><td>73.394</td></tr><tr><td>1200</td><td>7.497</td><td>52.572</td><td>6.042</td><td>100.144</td><td>67.622</td></tr><tr><td>1400</td><td>7.618</td><td>53.738</td><td>7.556</td><td>100.018</td><td>62.207</td></tr><tr><td>1600</td><td>7.657</td><td>54.758</td><td>9.084</td><td>99.906</td><td>56.821</td></tr><tr><td>1800</td><td>7.658</td><td>55.660</td><td>10.616</td><td>99.798</td><td>51.432</td></tr><tr><td>2000</td><td>7.648</td><td>56.467</td><td>12.146</td><td>99.688</td><td>46.074</td></tr></table>

Li(c,l,g)   
Lithium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.887</td><td>6.954</td><td>0</td></tr><tr><td>400</td><td>6.599</td><td>8.774</td><td>.632</td></tr><tr><td>454</td><td>6.934</td><td>9.627</td><td>.998</td></tr><tr><td>454</td><td>7.265</td><td>11.207</td><td>1.715</td></tr><tr><td>600</td><td>7.060</td><td>13.216</td><td>2.763</td></tr><tr><td>800</td><td>6.916</td><td>15.218</td><td>4.155</td></tr><tr><td>1000</td><td>6.892</td><td>16.759</td><td>5.536</td></tr><tr><td>1200</td><td>6.868</td><td>18.013</td><td>6.912</td></tr><tr><td>1400</td><td>6.840</td><td>19.070</td><td>8.283</td></tr><tr><td>1600</td><td>6.800</td><td>19.981</td><td>9.647</td></tr><tr><td>1638</td><td>6.792</td><td>20.140</td><td>9.905</td></tr><tr><td>1638</td><td>4.970</td><td>41.605</td><td>45.065</td></tr><tr><td>1800</td><td>4.974</td><td>42.076</td><td>45.872</td></tr><tr><td>2000</td><td>4.983</td><td>42.601</td><td>46.868</td></tr><tr><td>2200</td><td>5.001</td><td>43.076</td><td>47.866</td></tr><tr><td>2400</td><td>5.031</td><td>43.513</td><td>48.869</td></tr><tr><td>2600</td><td>5.074</td><td>43.917</td><td>49.879</td></tr><tr><td>2800</td><td>5.134</td><td>44.295</td><td>50.900</td></tr><tr><td>3000</td><td>5.209</td><td>44.652</td><td>51.934</td></tr></table>

453.7 K, melting point; $\Delta H^{\circ} = 0.717$   
1638 K, boiling point to ideal   
monatomic gas; $\Delta H^{\circ} = 35.160$

Li(g)   
Lithium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>33.143</td><td>0</td><td>38.410</td><td>30.602</td></tr><tr><td>400</td><td>4.968</td><td>34.603</td><td>.506</td><td>38.284</td><td>27.952</td></tr><tr><td>600</td><td>4.968</td><td>36.618</td><td>1.500</td><td>37.147</td><td>23.106</td></tr><tr><td>800</td><td>4.968</td><td>38.047</td><td>2.493</td><td>36.748</td><td>18.485</td></tr><tr><td>1000</td><td>4.968</td><td>39.156</td><td>3.487</td><td>36.361</td><td>13.964</td></tr><tr><td>1200</td><td>4.968</td><td>40.061</td><td>4.481</td><td>35.979</td><td>9.521</td></tr><tr><td>1400</td><td>4.968</td><td>40.827</td><td>5.474</td><td>35.601</td><td>5.141</td></tr><tr><td>1600</td><td>4.970</td><td>41.491</td><td>6.468</td><td>35.231</td><td>.815</td></tr><tr><td>1800</td><td>4.974</td><td>42.076</td><td>7.462</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.983</td><td>42.601</td><td>8.458</td><td>0</td><td>0</td></tr><tr><td>2200</td><td>5.001</td><td>43.076</td><td>9.456</td><td>0</td><td>0</td></tr><tr><td>2400</td><td>5.031</td><td>43.513</td><td>10.459</td><td>0</td><td>0</td></tr><tr><td>2600</td><td>5.074</td><td>43.917</td><td>11.469</td><td>0</td><td>0</td></tr><tr><td>2800</td><td>5.134</td><td>44.295</td><td>12.490</td><td>0</td><td>0</td></tr><tr><td>3000</td><td>5.209</td><td>44.652</td><td>13.524</td><td>0</td><td>0</td></tr></table>

Li2(g)   
Lithium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.662</td><td>47.050</td><td>0</td><td>50.400</td><td>40.519</td></tr><tr><td>400</td><td>8.826</td><td>49.616</td><td>.890</td><td>50.026</td><td>37.199</td></tr><tr><td>600</td><td>9.022</td><td>53.236</td><td>2.677</td><td>47.551</td><td>31.469</td></tr><tr><td>800</td><td>9.136</td><td>55.848</td><td>4.493</td><td>46.583</td><td>26.253</td></tr><tr><td>1000</td><td>9.224</td><td>57.897</td><td>6.330</td><td>45.658</td><td>21.279</td></tr><tr><td>1200</td><td>9.301</td><td>59.585</td><td>8.182</td><td>44.758</td><td>16.487</td></tr><tr><td>1400</td><td>9.374</td><td>61.025</td><td>10.050</td><td>43.884</td><td>11.845</td></tr><tr><td>1600</td><td>9.444</td><td>62.281</td><td>11.932</td><td>43.038</td><td>7.328</td></tr><tr><td>1800</td><td>9.512</td><td>63.397</td><td>13.827</td><td>-27.517</td><td>9.842</td></tr><tr><td>2000</td><td>9.579</td><td>64.403</td><td>15.736</td><td>-27.600</td><td>13.998</td></tr><tr><td>2200</td><td>9.646</td><td>65.319</td><td>17.659</td><td>-27.673</td><td>18.160</td></tr><tr><td>2400</td><td>9.712</td><td>66.161</td><td>19.595</td><td>-27.743</td><td>22.333</td></tr><tr><td>2600</td><td>9.778</td><td>66.941</td><td>21.543</td><td>-27.815</td><td>26.507</td></tr><tr><td>2800</td><td>9.843</td><td>67.668</td><td>23.506</td><td>-27.894</td><td>30.688</td></tr><tr><td>3000</td><td>9.909</td><td>68.350</td><td>25.481</td><td>-27.987</td><td>34.875</td></tr></table>

Lu(c,1)   
Lutetium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.400</td><td>12.180</td><td>0</td></tr><tr><td>400</td><td>6.420</td><td>14.060</td><td>.653</td></tr><tr><td>600</td><td>6.500</td><td>16.680</td><td>1.945</td></tr><tr><td>800</td><td>6.790</td><td>18.590</td><td>3.269</td></tr><tr><td>1000</td><td>7.240</td><td>20.150</td><td>4.670</td></tr><tr><td>1200</td><td>7.850</td><td>21.520</td><td>6.176</td></tr><tr><td>1400</td><td>8.640</td><td>22.790</td><td>7.822</td></tr><tr><td>1600</td><td>9.580</td><td>24.000</td><td>9.640</td></tr><tr><td>1800</td><td>10.680</td><td>25.190</td><td>11.663</td></tr><tr><td>1936</td><td>11.450</td><td>25.990</td><td>13.169</td></tr><tr><td>1936*</td><td>11.450</td><td>28.290</td><td>17.626</td></tr><tr><td>2000</td><td>11.450</td><td>28.670</td><td>18.355</td></tr></table>

\*Fusion and Lu(1) data estimated.   
1936 K, melting point; $\Delta H^{\circ} = 4.457$

Lu(g)   
Lutetium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.986</td><td>44.142</td><td>0</td><td>102.200</td><td>92.671</td></tr><tr><td>400</td><td>5.085</td><td>45.618</td><td>.512</td><td>102.059</td><td>89.436</td></tr><tr><td>600</td><td>5.530</td><td>47.757</td><td>1.570</td><td>101.825</td><td>83.179</td></tr><tr><td>800</td><td>5.977</td><td>49.413</td><td>2.723</td><td>101.654</td><td>76.996</td></tr><tr><td>1000</td><td>6.231</td><td>50.778</td><td>3.948</td><td>101.478</td><td>70.850</td></tr><tr><td>1200</td><td>6.320</td><td>51.924</td><td>5.205</td><td>101.229</td><td>64.744</td></tr><tr><td>1400</td><td>6.314</td><td>52.898</td><td>6.469</td><td>100.847</td><td>58.696</td></tr><tr><td>1600</td><td>6.268</td><td>53.738</td><td>7.728</td><td>100.288</td><td>52.707</td></tr><tr><td>1800</td><td>6.209</td><td>54.473</td><td>8.976</td><td>99.513</td><td>46.804</td></tr><tr><td>2000</td><td>6.150</td><td>55.124</td><td>10.211</td><td>94.056</td><td>41.148</td></tr></table>

Mg(c,1,g)   
Magnesium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>5.950</td><td>7.810</td><td>0</td></tr><tr><td>400</td><td>6.240</td><td>9.600</td><td>.621</td></tr><tr><td>600</td><td>6.800</td><td>12.240</td><td>1.926</td></tr><tr><td>800</td><td>7.360</td><td>14.270</td><td>3.342</td></tr><tr><td>922</td><td>7.710</td><td>15.340</td><td>4.261</td></tr><tr><td>922</td><td>7.680</td><td>17.660</td><td>6.400</td></tr><tr><td>1000</td><td>7.880</td><td>18.290</td><td>7.010</td></tr><tr><td>1200</td><td>8.400</td><td>19.770</td><td>8.640</td></tr><tr><td>1363</td><td>8.820</td><td>20.860</td><td>10.040</td></tr><tr><td>1363</td><td>4.968</td><td>43.051</td><td>40.290</td></tr><tr><td>1400</td><td>4.968</td><td>43.185</td><td>40.474</td></tr><tr><td>1600</td><td>4.968</td><td>43.848</td><td>41.468</td></tr><tr><td>1800</td><td>4.968</td><td>44.433</td><td>42.461</td></tr><tr><td>2000</td><td>4.969</td><td>44.957</td><td>43.455</td></tr></table>

922 K, melting point; $\Delta H^{\circ} = 2.139$   
1363 K, boiling point; $\Delta H^{\circ} = 30.250$

Mg(g)   
Magnesium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>35.501</td><td>0</td><td>35.000</td><td>26.744</td></tr><tr><td>400</td><td>4.968</td><td>36.961</td><td>.506</td><td>34.885</td><td>23.941</td></tr><tr><td>600</td><td>4.968</td><td>38.975</td><td>1.500</td><td>34.574</td><td>18.533</td></tr><tr><td>800</td><td>4.968</td><td>40.404</td><td>2.493</td><td>34.151</td><td>13.244</td></tr><tr><td>1000</td><td>4.968</td><td>41.513</td><td>3.487</td><td>31.477</td><td>8.254</td></tr><tr><td>1200</td><td>4.968</td><td>42.419</td><td>4.481</td><td>30.841</td><td>3.662</td></tr><tr><td>1400</td><td>4.968</td><td>43.185</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.968</td><td>43.848</td><td>6.468</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.968</td><td>44.433</td><td>7.461</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.969</td><td>44.957</td><td>8.455</td><td>0</td><td>0</td></tr></table>

$Mg_{2}(g)$   
Magnesium (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>7.658</td><td>58.282</td><td>0</td><td>68.590</td><td>55.870</td></tr><tr><td>400</td><td>7.376</td><td>60.489</td><td>.764</td><td>68.112</td><td>51.596</td></tr><tr><td>600</td><td>7.151</td><td>63.428</td><td>2.212</td><td>66.950</td><td>43.581</td></tr><tr><td>800</td><td>7.066</td><td>65.472</td><td>3.633</td><td>65.539</td><td>35.993</td></tr><tr><td>1000</td><td>7.026</td><td>67.044</td><td>5.042</td><td>59.612</td><td>29.148</td></tr><tr><td>1200</td><td>7.005</td><td>68.323</td><td>6.445</td><td>57.755</td><td>23.215</td></tr><tr><td>1400</td><td>6.991</td><td>69.402</td><td>7.844</td><td>-4.514</td><td>19.241</td></tr><tr><td>1600</td><td>6.983</td><td>70.335</td><td>9.242</td><td>-5.104</td><td>22.674</td></tr><tr><td>1800</td><td>6.977</td><td>71.157</td><td>10.638</td><td>-5.694</td><td>26.182</td></tr><tr><td>2000</td><td>6.973</td><td>71.892</td><td>12.032</td><td>-6.288</td><td>29.756</td></tr></table>

Mn(c,1)   
Manganese 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.280</td><td>7.650</td><td>0</td></tr><tr><td>400</td><td>6.760</td><td>9.560</td><td>.664</td></tr><tr><td>600</td><td>7.630</td><td>12.470</td><td>2.104</td></tr><tr><td>800</td><td>8.350</td><td>14.760</td><td>3.704</td></tr><tr><td>980</td><td>8.850</td><td>16.510</td><td>5.254</td></tr><tr><td>980</td><td>8.980</td><td>17.050</td><td>5.786</td></tr><tr><td>1000</td><td>9.010</td><td>17.230</td><td>5.966</td></tr><tr><td>1200</td><td>9.210</td><td>18.890</td><td>7.787</td></tr><tr><td>1360</td><td>9.370</td><td>20.060</td><td>9.274</td></tr><tr><td>1360</td><td>10.300</td><td>20.430</td><td>9.781</td></tr><tr><td>1400</td><td>10.380</td><td>20.730</td><td>10.195</td></tr><tr><td>1410</td><td>10.400</td><td>20.810</td><td>10.299</td></tr><tr><td>1410</td><td>10.810</td><td>21.130</td><td>10.748</td></tr><tr><td>1517</td><td>11.020</td><td>21.930</td><td>11.916</td></tr><tr><td>1517*</td><td>11.000</td><td>23.830</td><td>14.798</td></tr><tr><td>1600</td><td>11.000</td><td>24.420</td><td>15.711</td></tr><tr><td>1800</td><td>11.000</td><td>25.720</td><td>17.911</td></tr><tr><td>2000</td><td>11.000</td><td>26.880</td><td>20.111</td></tr></table>

\*Fusion and Mn(1) data estimated.   
980 K, transition point; $\Delta H^{\circ} = 0.532$   
1360 K, transition point; $\Delta H^{\circ} = 0.507$   
1410 K, transition point; $\Delta H^{\circ} = 0.449$   
1517 K, melting point; $\Delta H^{\circ} = 2.882$

Mn(g)   
Manganese (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.493</td><td>0</td><td>67.100</td><td>57.010</td></tr><tr><td>400</td><td>4.968</td><td>42.953</td><td>.506</td><td>66.942</td><td>53.585</td></tr><tr><td>600</td><td>4.968</td><td>44.967</td><td>1.500</td><td>66.496</td><td>46.998</td></tr><tr><td>800</td><td>4.968</td><td>46.396</td><td>2.493</td><td>65.889</td><td>40.580</td></tr><tr><td>1000</td><td>4.968</td><td>47.505</td><td>3.487</td><td>64.621</td><td>34.346</td></tr><tr><td>1200</td><td>4.968</td><td>48.410</td><td>4.480</td><td>63.793</td><td>28.369</td></tr><tr><td>1400</td><td>4.968</td><td>49.176</td><td>5.474</td><td>62.379</td><td>22.555</td></tr><tr><td>1600</td><td>4.969</td><td>49.840</td><td>6.468</td><td>57.857</td><td>17.185</td></tr><tr><td>1800</td><td>4.971</td><td>50.425</td><td>7.462</td><td>56.651</td><td>12.182</td></tr><tr><td>2000</td><td>4.977</td><td>50.949</td><td>8.456</td><td>55.445</td><td>7.307</td></tr></table>

Mo(c,1)   
Molybdenum 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>5.750</td><td>6.850</td><td>0</td></tr><tr><td>400</td><td>5.980</td><td>8.573</td><td>.598</td></tr><tr><td>600</td><td>6.315</td><td>11.066</td><td>1.830</td></tr><tr><td>800</td><td>6.539</td><td>12.915</td><td>3.116</td></tr><tr><td>1000</td><td>6.766</td><td>14.396</td><td>4.445</td></tr><tr><td>1200</td><td>7.068</td><td>15.656</td><td>5.827</td></tr><tr><td>1400</td><td>7.395</td><td>16.770</td><td>7.273</td></tr><tr><td>1600</td><td>7.763</td><td>17.781</td><td>8.788</td></tr><tr><td>1800</td><td>8.200</td><td>18.720</td><td>10.383</td></tr><tr><td>2000</td><td>8.717</td><td>19.610</td><td>12.074</td></tr><tr><td>2200</td><td>9.327</td><td>20.468</td><td>13.877</td></tr><tr><td>2400</td><td>10.089</td><td>21.311</td><td>15.814</td></tr><tr><td>2600</td><td>10.977</td><td>22.153</td><td>17.920</td></tr><tr><td>2800</td><td>11.982</td><td>23.002</td><td>20.212</td></tr><tr><td>2890</td><td>12.500</td><td>23.389</td><td>21.314</td></tr><tr><td>2890</td><td>8.190</td><td>26.080</td><td>29.091</td></tr><tr><td>3000</td><td>8.190</td><td>26.386</td><td>29.992</td></tr></table>

2890 K, melting point; $\Delta H^{\circ} = 7.777$

Mo(g)   
Molybdenum (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>43.461</td><td>0</td><td>157.500</td><td>146.584</td></tr><tr><td>400</td><td>4.968</td><td>44.921</td><td>.506</td><td>157.408</td><td>142.869</td></tr><tr><td>600</td><td>4.968</td><td>46.935</td><td>1.500</td><td>157.170</td><td>135.649</td></tr><tr><td>800</td><td>4.968</td><td>48.365</td><td>2.493</td><td>156.877</td><td>128.517</td></tr><tr><td>1000</td><td>4.968</td><td>49.473</td><td>3.487</td><td>156.542</td><td>121.465</td></tr><tr><td>1200</td><td>4.970</td><td>50.379</td><td>4.481</td><td>156.154</td><td>114.486</td></tr><tr><td>1400</td><td>4.977</td><td>51.146</td><td>5.475</td><td>155.702</td><td>107.576</td></tr><tr><td>1600</td><td>4.998</td><td>51.811</td><td>6.472</td><td>155.184</td><td>100.736</td></tr><tr><td>1800</td><td>5.043</td><td>52.402</td><td>7.476</td><td>154.593</td><td>93.965</td></tr><tr><td>2000</td><td>5.125</td><td>52.937</td><td>8.492</td><td>153.918</td><td>87.264</td></tr><tr><td>2200</td><td>5.254</td><td>53.432</td><td>9.529</td><td>153.152</td><td>80.631</td></tr><tr><td>2400</td><td>5.440</td><td>53.896</td><td>10.597</td><td>152.283</td><td>74.079</td></tr><tr><td>2600</td><td>5.689</td><td>54.341</td><td>11.709</td><td>151.289</td><td>67.600</td></tr><tr><td>2800</td><td>6.005</td><td>54.774</td><td>12.877</td><td>150.165</td><td>61.203</td></tr><tr><td>3000</td><td>6.391</td><td>55.201</td><td>14.116</td><td>141.624</td><td>55.179</td></tr></table>

$N_{2}(g)$   
Nitrogen 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.961</td><td>45.770</td><td>0</td></tr><tr><td>400</td><td>6.991</td><td>47.818</td><td>.710</td></tr><tr><td>600</td><td>7.196</td><td>50.685</td><td>2.126</td></tr><tr><td>800</td><td>7.513</td><td>52.798</td><td>3.596</td></tr><tr><td>1000</td><td>7.815</td><td>54.508</td><td>5.130</td></tr><tr><td>1200</td><td>8.060</td><td>55.955</td><td>6.718</td></tr><tr><td>1400</td><td>8.250</td><td>57.213</td><td>8.350</td></tr><tr><td>1600</td><td>8.396</td><td>58.324</td><td>10.015</td></tr><tr><td>1800</td><td>8.508</td><td>59.320</td><td>11.706</td></tr><tr><td>2000</td><td>8.597</td><td>60.221</td><td>13.417</td></tr><tr><td>2200</td><td>8.668</td><td>61.044</td><td>15.144</td></tr><tr><td>2400</td><td>8.726</td><td>61.801</td><td>16.883</td></tr><tr><td>2600</td><td>8.775</td><td>62.501</td><td>18.634</td></tr><tr><td>2800</td><td>8.815</td><td>63.153</td><td>20.393</td></tr><tr><td>3000</td><td>8.850</td><td>63.762</td><td>22.159</td></tr></table>

N(g)   
Nitrogen (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>36.613</td><td>0</td><td>112.970</td><td>108.877</td></tr><tr><td>400</td><td>4.968</td><td>38.073</td><td>.506</td><td>113.121</td><td>107.455</td></tr><tr><td>600</td><td>4.968</td><td>40.088</td><td>1.500</td><td>113.407</td><td>104.560</td></tr><tr><td>800</td><td>4.968</td><td>41.517</td><td>2.493</td><td>113.665</td><td>101.571</td></tr><tr><td>1000</td><td>4.968</td><td>42.626</td><td>3.487</td><td>113.892</td><td>98.520</td></tr><tr><td>1200</td><td>4.968</td><td>43.531</td><td>4.480</td><td>114.091</td><td>95.427</td></tr><tr><td>1400</td><td>4.968</td><td>44.297</td><td>5.474</td><td>114.269</td><td>92.302</td></tr><tr><td>1600</td><td>4.968</td><td>44.961</td><td>6.468</td><td>114.431</td><td>89.152</td></tr><tr><td>1800</td><td>4.968</td><td>45.546</td><td>7.461</td><td>114.578</td><td>85.983</td></tr><tr><td>2000</td><td>4.969</td><td>46.069</td><td>8.455</td><td>114.717</td><td>82.800</td></tr><tr><td>2200</td><td>4.971</td><td>46.543</td><td>9.449</td><td>114.847</td><td>79.601</td></tr><tr><td>2400</td><td>4.975</td><td>46.975</td><td>10.443</td><td>114.972</td><td>76.393</td></tr><tr><td>2600</td><td>4.982</td><td>47.374</td><td>11.439</td><td>115.092</td><td>73.171</td></tr><tr><td>2800</td><td>4.993</td><td>47.743</td><td>12.436</td><td>115.209</td><td>69.943</td></tr><tr><td>3000</td><td>5.010</td><td>48.088</td><td>13.436</td><td>115.326</td><td>66.705</td></tr></table>

Na(c,1,g)   
Sodium 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td></tr><tr><td>298</td><td>6.730</td><td>12.298</td><td>0</td></tr><tr><td>371</td><td>7.468</td><td>13.841</td><td>.515</td></tr><tr><td>371</td><td>7.596</td><td>15.517</td><td>1.137</td></tr><tr><td>400</td><td>7.531</td><td>16.086</td><td>1.356</td></tr><tr><td>600</td><td>7.124</td><td>19.056</td><td>2.818</td></tr><tr><td>800</td><td>6.918</td><td>21.072</td><td>4.219</td></tr><tr><td>1000</td><td>6.918</td><td>22.612</td><td>5.599</td></tr><tr><td>1177</td><td>7.091</td><td>23.752</td><td>6.835</td></tr><tr><td>1177</td><td>4.968</td><td>43.535</td><td>30.120</td></tr><tr><td>1200</td><td>4.968</td><td>43.631</td><td>30.236</td></tr><tr><td>1400</td><td>4.968</td><td>44.398</td><td>31.229</td></tr><tr><td>1600</td><td>4.968</td><td>45.061</td><td>32.223</td></tr><tr><td>1800</td><td>4.970</td><td>45.646</td><td>33.217</td></tr><tr><td>2000</td><td>4.973</td><td>46.170</td><td>34.211</td></tr><tr><td>2200</td><td>4.979</td><td>46.644</td><td>35.206</td></tr><tr><td>2400</td><td>4.992</td><td>47.078</td><td>36.203</td></tr><tr><td>2600</td><td>5.013</td><td>47.478</td><td>37.203</td></tr><tr><td>2800</td><td>5.044</td><td>47.851</td><td>38.209</td></tr><tr><td>3000</td><td>5.089</td><td>48.200</td><td>39.222</td></tr></table>

371 K, melting point; $\Delta H^{\circ} = 0.622$   
1177 K, boiling point to ideal   
monatomic gas; $\Delta H^{\circ} = 23.285$

Na(g)   
Sodium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>36.714</td><td>0</td><td>25.755</td><td>18.475</td></tr><tr><td>400</td><td>4.968</td><td>38.174</td><td>.506</td><td>24.905</td><td>16.070</td></tr><tr><td>600</td><td>4.968</td><td>40.188</td><td>1.500</td><td>24.437</td><td>11.758</td></tr><tr><td>800</td><td>4.968</td><td>41.617</td><td>2.493</td><td>24.029</td><td>7.593</td></tr><tr><td>1000</td><td>4.968</td><td>42.726</td><td>3.487</td><td>23.643</td><td>3.529</td></tr><tr><td>1200</td><td>4.968</td><td>43.632</td><td>4.481</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.968</td><td>44.398</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.968</td><td>45.061</td><td>6.468</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.970</td><td>45.646</td><td>7.462</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.973</td><td>46.170</td><td>8.456</td><td>0</td><td>0</td></tr><tr><td>2200</td><td>4.979</td><td>46.644</td><td>9.451</td><td>0</td><td>0</td></tr><tr><td>2400</td><td>4.992</td><td>47.078</td><td>10.448</td><td>0</td><td>0</td></tr><tr><td>2600</td><td>5.013</td><td>47.478</td><td>11.448</td><td>0</td><td>0</td></tr><tr><td>2800</td><td>5.044</td><td>47.851</td><td>12.454</td><td>0</td><td>0</td></tr><tr><td>3000</td><td>5.089</td><td>48.200</td><td>13.467</td><td>0</td><td>0</td></tr></table>

$Na_{2}(g)$ Sodium (ideal diatomic gas) 

<table><tr><td>f</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.963</td><td>54.994</td><td>0</td><td>32.870</td><td>23.807</td></tr><tr><td>400</td><td>9.043</td><td>57.640</td><td>.917</td><td>31.075</td><td>20.888</td></tr><tr><td>600</td><td>9.150</td><td>61.328</td><td>2.737</td><td>29.971</td><td>16.041</td></tr><tr><td>800</td><td>9.237</td><td>63.973</td><td>4.576</td><td>29.008</td><td>11.545</td></tr><tr><td>1000</td><td>9.319</td><td>66.043</td><td>6.432</td><td>28.104</td><td>7.285</td></tr><tr><td>1200</td><td>9.399</td><td>67.749</td><td>8.303</td><td>-19.299</td><td>4.117</td></tr><tr><td>1400</td><td>9.477</td><td>69.204</td><td>10.191</td><td>-19.397</td><td>8.032</td></tr><tr><td>1600</td><td>9.555</td><td>70.474</td><td>12.094</td><td>-19.482</td><td>11.955</td></tr><tr><td>1800</td><td>9.633</td><td>71.604</td><td>14.013</td><td>-19.551</td><td>15.887</td></tr><tr><td>2000</td><td>9.711</td><td>72.623</td><td>15.948</td><td>-19.604</td><td>19.830</td></tr><tr><td>2200</td><td>9.788</td><td>73.552</td><td>17.897</td><td>-19.645</td><td>23.774</td></tr><tr><td>2400</td><td>9.865</td><td>74.407</td><td>19.863</td><td>-19.673</td><td>27.725</td></tr><tr><td>2600</td><td>9.942</td><td>75.200</td><td>21.844</td><td>-19.692</td><td>31.674</td></tr><tr><td>2800</td><td>10.020</td><td>75.940</td><td>23.840</td><td>-19.708</td><td>35.626</td></tr><tr><td>3000</td><td>10.097</td><td>76.634</td><td>25.851</td><td>-19.723</td><td>39.575</td></tr></table>

Nb(g)
Niobium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>7.208</td><td>44.490</td><td>0</td><td>175.200</td><td>164.529</td></tr><tr><td>400</td><td>7.086</td><td>46.595</td><td>.729</td><td>175.318</td><td>160.864</td></tr><tr><td>600</td><td>6.704</td><td>49.395</td><td>2.108</td><td>175.459</td><td>153.604</td></tr><tr><td>800</td><td>6.402</td><td>51.280</td><td>3.417</td><td>175.491</td><td>146.307</td></tr><tr><td>1000</td><td>6.185</td><td>52.684</td><td>4.674</td><td>175.432</td><td>139.018</td></tr><tr><td>1200</td><td>6.034</td><td>53.797</td><td>5.895</td><td>175.296</td><td>131.764</td></tr><tr><td>1400</td><td>5.941</td><td>54.720</td><td>7.092</td><td>175.092</td><td>124.524</td></tr><tr><td>1600</td><td>5.902</td><td>55.510</td><td>8.275</td><td>174.813</td><td>117.325</td></tr><tr><td>1800</td><td>5.917</td><td>56.205</td><td>9.456</td><td>174.467</td><td>110.162</td></tr><tr><td>2000</td><td>5.981</td><td>56.832</td><td>10.645</td><td>174.056</td><td>103.032</td></tr><tr><td>2200</td><td>6.089</td><td>57.406</td><td>11.851</td><td>173.580</td><td>95.951</td></tr><tr><td>2400</td><td>6.234</td><td>57.942</td><td>13.083</td><td>173.042</td><td>88.917</td></tr><tr><td>2600</td><td>6.407</td><td>58.448</td><td>14.347</td><td>172.439</td><td>81.938</td></tr><tr><td>2800</td><td>6.599</td><td>58.930</td><td>15.647</td><td>165.589</td><td>75.121</td></tr><tr><td>3000</td><td>6.802</td><td>59.392</td><td>16.987</td><td>165.329</td><td>68.693</td></tr></table>

Nb(c,1)
Niobium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.880</td><td>8.700</td><td>0</td></tr><tr><td>400</td><td>6.090</td><td>10.460</td><td>.611</td></tr><tr><td>600</td><td>6.280</td><td>12.970</td><td>1.849</td></tr><tr><td>800</td><td>6.480</td><td>14.800</td><td>3.126</td></tr><tr><td>1000</td><td>6.680</td><td>16.270</td><td>4.442</td></tr><tr><td>1200</td><td>6.890</td><td>17.520</td><td>5.799</td></tr><tr><td>1400</td><td>7.160</td><td>18.600</td><td>7.200</td></tr><tr><td>1600</td><td>7.470</td><td>19.580</td><td>8.662</td></tr><tr><td>1800</td><td>7.810</td><td>20.480</td><td>10.189</td></tr><tr><td>2000</td><td>8.200</td><td>21.320</td><td>11.789</td></tr><tr><td>2200</td><td>8.620</td><td>22.120</td><td>13.471</td></tr><tr><td>2400</td><td>9.090</td><td>22.890</td><td>15.241</td></tr><tr><td>2600</td><td>9.590</td><td>23.640</td><td>17.108</td></tr><tr><td>2740</td><td>9.970</td><td>24.150</td><td>18.476</td></tr><tr><td>2740*</td><td>8.000</td><td>26.450</td><td>24.778</td></tr><tr><td>2800</td><td>8.000</td><td>26.620</td><td>25.258</td></tr><tr><td>3000</td><td>8.000</td><td>27.180</td><td>26.858</td></tr></table>

\*Fusion and Nb(1) data estimated.
2740 K, melting point; $\Delta H^{\circ} = 6.302$

Nd(c,1)
Neodymium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.550</td><td>17.100</td><td>0</td></tr><tr><td>400</td><td>6.880</td><td>19.070</td><td>.683</td></tr><tr><td>600</td><td>7.660</td><td>22.000</td><td>2.134</td></tr><tr><td>800</td><td>8.710</td><td>24.340</td><td>3.765</td></tr><tr><td>1000</td><td>10.030</td><td>26.420</td><td>5.635</td></tr><tr><td>1128</td><td>10.990</td><td>27.680</td><td>6.980</td></tr><tr><td>1128</td><td>10.660</td><td>28.320</td><td>7.704</td></tr><tr><td>1200</td><td>10.660</td><td>28.980</td><td>8.472</td></tr><tr><td>1289</td><td>10.660</td><td>29.740</td><td>9.420</td></tr><tr><td>1289</td><td>10.520</td><td>31.060</td><td>11.127</td></tr><tr><td>1400</td><td>10.520</td><td>31.930</td><td>12.295</td></tr><tr><td>1600</td><td>10.520</td><td>33.330</td><td>14.399</td></tr><tr><td>1800</td><td>10.520</td><td>34.570</td><td>16.503</td></tr><tr><td>2000</td><td>10.520</td><td>35.680</td><td>18.607</td></tr></table>

1128 K, transition point; $\Delta H^{\circ}$ 0.724
1289 K, melting point; $\Delta H^{\circ} = 1.707$

Nd(g)
Neodymium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.280</td><td>45.243</td><td>0</td><td>78.300</td><td>69.909</td></tr><tr><td>400</td><td>5.672</td><td>46.849</td><td>.558</td><td>78.175</td><td>67.063</td></tr><tr><td>600</td><td>6.285</td><td>49.275</td><td>1.758</td><td>77.924</td><td>61.559</td></tr><tr><td>800</td><td>6.643</td><td>51.137</td><td>3.054</td><td>77.589</td><td>56.151</td></tr><tr><td>1000</td><td>6.868</td><td>52.645</td><td>4.407</td><td>77.072</td><td>50.847</td></tr><tr><td>1200</td><td>7.033</td><td>53.912</td><td>5.798</td><td>75.626</td><td>45.708</td></tr><tr><td>1400</td><td>7.168</td><td>55.006</td><td>7.218</td><td>73.223</td><td>40.917</td></tr><tr><td>1600</td><td>7.287</td><td>55.972</td><td>8.664</td><td>72.565</td><td>36.338</td></tr><tr><td>1800</td><td>7.391</td><td>56.836</td><td>10.132</td><td>71.929</td><td>31.850</td></tr><tr><td>2000</td><td>7.480</td><td>57.619</td><td>11.619</td><td>71.312</td><td>27.434</td></tr></table>

Ne(g)   
Neon 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>4.968</td><td>34.947</td><td>0</td></tr><tr><td>400</td><td>4.968</td><td>36.407</td><td>.506</td></tr><tr><td>600</td><td>4.968</td><td>38.421</td><td>1.500</td></tr><tr><td>800</td><td>4.968</td><td>39.850</td><td>2.493</td></tr><tr><td>1000</td><td>4.968</td><td>40.959</td><td>3.487</td></tr><tr><td>1200</td><td>4.968</td><td>41.865</td><td>4.480</td></tr><tr><td>1400</td><td>4.968</td><td>42.631</td><td>5.474</td></tr><tr><td>1600</td><td>4.968</td><td>43.294</td><td>6.468</td></tr><tr><td>1800</td><td>4.968</td><td>43.879</td><td>7.461</td></tr><tr><td>2000</td><td>4.968</td><td>44.403</td><td>8.455</td></tr><tr><td>2200</td><td>4.968</td><td>44.876</td><td>9.448</td></tr><tr><td>2400</td><td>4.968</td><td>45.308</td><td>10.442</td></tr><tr><td>2600</td><td>4.968</td><td>45.706</td><td>11.436</td></tr><tr><td>2800</td><td>4.968</td><td>46.074</td><td>12.429</td></tr><tr><td>3000</td><td>4.968</td><td>46.417</td><td>13.423</td></tr></table>

Ni(c,1)   
Nickel 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.210</td><td>7.140</td><td>0</td></tr><tr><td>400</td><td>6.810</td><td>9.050</td><td>.664</td></tr><tr><td>600</td><td>8.330</td><td>12.050</td><td>2.156</td></tr><tr><td>631</td><td>9.520</td><td>12.490</td><td>2.425</td></tr><tr><td>800</td><td>7.410</td><td>14.270</td><td>3.691</td></tr><tr><td>1000</td><td>7.700</td><td>15.950</td><td>5.201</td></tr><tr><td>1200</td><td>8.050</td><td>17.390</td><td>6.775</td></tr><tr><td>1400</td><td>8.460</td><td>18.660</td><td>8.425</td></tr><tr><td>1600</td><td>8.910</td><td>19.820</td><td>10.162</td></tr><tr><td>1728</td><td>9.210</td><td>20.520</td><td>11.321</td></tr><tr><td>1728</td><td>9.300</td><td>22.890</td><td>15.421</td></tr><tr><td>1800</td><td>9.300</td><td>23.270</td><td>16.091</td></tr><tr><td>2000</td><td>9.300</td><td>24.250</td><td>17.951</td></tr></table>

631 K, Curie point; $\Delta H^{\circ} = 0$   
1728 K, melting point; $\Delta H^{\circ} = 4.100$

Ni(g)   
Nickel (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.583</td><td>43.519</td><td>0</td><td>102.800</td><td>91.954</td></tr><tr><td>400</td><td>5.702</td><td>45.175</td><td>.574</td><td>102.710</td><td>88.260</td></tr><tr><td>600</td><td>5.912</td><td>47.531</td><td>1.738</td><td>102.382</td><td>81.093</td></tr><tr><td>800</td><td>5.971</td><td>49.243</td><td>2.928</td><td>102.037</td><td>74.059</td></tr><tr><td>1000</td><td>5.937</td><td>50.573</td><td>4.120</td><td>101.719</td><td>67.096</td></tr><tr><td>1200</td><td>5.865</td><td>51.649</td><td>5.301</td><td>101.326</td><td>60.215</td></tr><tr><td>1400</td><td>5.781</td><td>52.547</td><td>6.465</td><td>100.840</td><td>53.398</td></tr><tr><td>1600</td><td>5.698</td><td>53.313</td><td>7.613</td><td>100.251</td><td>46.662</td></tr><tr><td>1800</td><td>5.622</td><td>53.980</td><td>8.745</td><td>95.454</td><td>40.176</td></tr><tr><td>2000</td><td>5.556</td><td>54.569</td><td>9.863</td><td>94.712</td><td>34.074</td></tr></table>

Np(c,1)   
Neptunium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>7.080</td><td>12.060</td><td>0</td></tr><tr><td>400</td><td>8.127</td><td>14.266</td><td>.767</td></tr><tr><td>553</td><td>10.572</td><td>17.258</td><td>2.191</td></tr><tr><td>553*</td><td>9.400</td><td>19.681</td><td>3.531</td></tr><tr><td>600</td><td>9.400</td><td>20.448</td><td>3.972</td></tr><tr><td>800</td><td>9.400</td><td>23.152</td><td>5.852</td></tr><tr><td>849</td><td>9.400</td><td>23.711</td><td>6.313</td></tr><tr><td>849</td><td>8.700</td><td>25.195</td><td>7.573</td></tr><tr><td>912</td><td>8.700</td><td>25.818</td><td>8.121</td></tr><tr><td>912</td><td>10.850</td><td>27.178</td><td>9.361</td></tr><tr><td>1000</td><td>10.850</td><td>28.177</td><td>10.316</td></tr><tr><td>1200</td><td>10.850</td><td>30.155</td><td>12.486</td></tr><tr><td>1400</td><td>10.850</td><td>31.828</td><td>14.656</td></tr><tr><td>1600</td><td>10.850</td><td>33.277</td><td>16.826</td></tr><tr><td>1800</td><td>10.850</td><td>34.555</td><td>18.996</td></tr><tr><td>2000</td><td>10.850</td><td>35.698</td><td>21.166</td></tr><tr><td>2200</td><td>10.850</td><td>36.732</td><td>23.336</td></tr><tr><td>2400</td><td>10.850</td><td>37.676</td><td>25.506</td></tr></table>

\*Data above 553 K are estimated except fusion data.   
553 K, transition point; $\Delta H^{\circ} = 1.340$   
849 K, transition point; $\Delta H^{\circ} = 1.260$   
912 K, melting point; $\Delta H^{\circ} = 1.240$

Np(g)   
Neptunium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td><td> $\Delta Hf^{\circ}$ </td><td> $\Delta Gf^{\circ}$ </td></tr><tr><td>298</td><td>4.977</td><td>47.229</td><td>0</td><td>111.100</td><td>10.614</td></tr><tr><td>400</td><td>5.033</td><td>48.698</td><td>.509</td><td>11.842</td><td>97.069</td></tr><tr><td>600</td><td>5.377</td><td>50.793</td><td>1.545</td><td>108.673</td><td>90.466</td></tr><tr><td>800</td><td>5.961</td><td>52.417</td><td>2.676</td><td>107.924</td><td>84.512</td></tr><tr><td>1000</td><td>6.616</td><td>53.817</td><td>3.934</td><td>104.718</td><td>79.078</td></tr><tr><td>1200</td><td>7.237</td><td>55.079</td><td>5.320</td><td>103.934</td><td>74.025</td></tr><tr><td>1400</td><td>7.795</td><td>56.237</td><td>6.824</td><td>103.268</td><td>69.095</td></tr><tr><td>1600</td><td>8.293</td><td>57.311</td><td>8.434</td><td>102.708</td><td>64.254</td></tr><tr><td>1800</td><td>8.740</td><td>58.315</td><td>10.138</td><td>102.242</td><td>59.474</td></tr><tr><td>2000</td><td>9.139</td><td>59.256</td><td>11.927</td><td>101.861</td><td>54.745</td></tr><tr><td>2200</td><td>9.495</td><td>60.145</td><td>13.791</td><td>101.555</td><td>50.046</td></tr><tr><td>2400</td><td>9.807</td><td>60.984</td><td>15.722</td><td>101.316</td><td>45.377</td></tr></table>

$0_{2}(g)$   
Oxygen 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H298°</td></tr><tr><td>298</td><td>7.021</td><td>49.005</td><td>0</td></tr><tr><td>400</td><td>7.196</td><td>51.090</td><td>.723</td></tr><tr><td>600</td><td>7.670</td><td>54.097</td><td>2.209</td></tr><tr><td>800</td><td>8.062</td><td>56.360</td><td>3.785</td></tr><tr><td>1000</td><td>8.334</td><td>58.190</td><td>5.426</td></tr><tr><td>1200</td><td>8.525</td><td>59.728</td><td>7.113</td></tr><tr><td>1400</td><td>8.670</td><td>61.053</td><td>8.833</td></tr><tr><td>1600</td><td>8.795</td><td>62.219</td><td>10.580</td></tr><tr><td>1800</td><td>8.909</td><td>63.262</td><td>12.350</td></tr><tr><td>2000</td><td>9.020</td><td>64.206</td><td>14.143</td></tr><tr><td>2200</td><td>9.129</td><td>65.071</td><td>15.958</td></tr><tr><td>2400</td><td>9.235</td><td>65.870</td><td>17.795</td></tr><tr><td>2600</td><td>9.337</td><td>66.613</td><td>19.652</td></tr><tr><td>2800</td><td>9.435</td><td>67.309</td><td>21.529</td></tr><tr><td>3000</td><td>9.528</td><td>67.963</td><td>23.426</td></tr></table>

0(g)

Oxygen (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.237</td><td>38.468</td><td>0</td><td>59.554</td><td>55.390</td></tr><tr><td>400</td><td>5.134</td><td>39.991</td><td>.528</td><td>59.721</td><td>53.942</td></tr><tr><td>600</td><td>5.049</td><td>42.053</td><td>1.544</td><td>59.993</td><td>50.991</td></tr><tr><td>800</td><td>5.015</td><td>43.501</td><td>2.550</td><td>60.211</td><td>47.955</td></tr><tr><td>1000</td><td>4.999</td><td>44.618</td><td>3.552</td><td>60.393</td><td>44.870</td></tr><tr><td>1200</td><td>4.990</td><td>45.528</td><td>4.550</td><td>60.548</td><td>41.751</td></tr><tr><td>1400</td><td>4.984</td><td>46.297</td><td>5.548</td><td>60.685</td><td>38.607</td></tr><tr><td>1600</td><td>4.981</td><td>46.962</td><td>6.544</td><td>60.808</td><td>35.444</td></tr><tr><td>1800</td><td>4.978</td><td>47.549</td><td>7.540</td><td>60.919</td><td>32.267</td></tr><tr><td>2000</td><td>4.978</td><td>48.073</td><td>8.536</td><td>61.019</td><td>29.079</td></tr><tr><td>2200</td><td>4.978</td><td>48.548</td><td>9.531</td><td>61.106</td><td>25.878</td></tr><tr><td>2400</td><td>4.981</td><td>48.981</td><td>10.527</td><td>61.184</td><td>22.673</td></tr><tr><td>2600</td><td>4.986</td><td>49.380</td><td>11.524</td><td>61.252</td><td>19.461</td></tr><tr><td>2800</td><td>4.994</td><td>49.750</td><td>12.522</td><td>61.312</td><td>16.244</td></tr><tr><td>3000</td><td>5.004</td><td>50.094</td><td>13.522</td><td>61.363</td><td>13.025</td></tr></table>

$O_{3}(g)$   
Oxygen, ozone (ideal triatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>9.378</td><td>57.080</td><td>0</td><td>34.200</td><td>39.098</td></tr><tr><td>400</td><td>10.455</td><td>59.992</td><td>1.012</td><td>34.128</td><td>40.785</td></tr><tr><td>600</td><td>11.916</td><td>64.536</td><td>3.263</td><td>34.150</td><td>44.115</td></tr><tr><td>800</td><td>12.704</td><td>68.083</td><td>5.733</td><td>34.256</td><td>47.421</td></tr><tr><td>1000</td><td>13.151</td><td>70.971</td><td>8.323</td><td>34.384</td><td>50.698</td></tr><tr><td>1200</td><td>13.426</td><td>73.395</td><td>10.982</td><td>34.513</td><td>53.949</td></tr><tr><td>1400</td><td>13.611</td><td>75.479</td><td>13.687</td><td>34.638</td><td>57.178</td></tr><tr><td>1600</td><td>13.743</td><td>77.305</td><td>16.423</td><td>34.753</td><td>60.391</td></tr><tr><td>1800</td><td>13.843</td><td>78.930</td><td>19.182</td><td>34.857</td><td>63.590</td></tr><tr><td>2000</td><td>13.922</td><td>80.393</td><td>21.959</td><td>34.944</td><td>66.777</td></tr></table>

Os(c)   
Osmium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>5.905</td><td>7.800</td><td>0</td></tr><tr><td>400</td><td>5.998</td><td>9.548</td><td>.606</td></tr><tr><td>600</td><td>6.180</td><td>12.015</td><td>1.824</td></tr><tr><td>800</td><td>6.362</td><td>13.817</td><td>3.078</td></tr><tr><td>1000</td><td>6.544</td><td>15.256</td><td>4.369</td></tr><tr><td>1200</td><td>6.726</td><td>16.465</td><td>5.695</td></tr><tr><td>1400</td><td>6.908</td><td>17.516</td><td>7.059</td></tr><tr><td>1600</td><td>7.090</td><td>18.450</td><td>8.459</td></tr><tr><td>1800</td><td>7.272</td><td>19.296</td><td>9.895</td></tr><tr><td>2000</td><td>7.454</td><td>20.071</td><td>11.367</td></tr></table>

3300 K, melting point; $\Delta H^{\circ} = 7.6$

0s(g)   
Osmium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>46.000</td><td>0</td><td>189.000</td><td>177.611</td></tr><tr><td>400</td><td>4.974</td><td>47.461</td><td>.506</td><td>188.900</td><td>173.735</td></tr><tr><td>600</td><td>5.043</td><td>49.487</td><td>1.506</td><td>188.682</td><td>166.199</td></tr><tr><td>800</td><td>5.231</td><td>50.961</td><td>2.532</td><td>188.454</td><td>158.739</td></tr><tr><td>1000</td><td>5.522</td><td>52.158</td><td>3.606</td><td>188.237</td><td>151.335</td></tr><tr><td>1200</td><td>5.866</td><td>53.195</td><td>4.744</td><td>188.049</td><td>143.973</td></tr><tr><td>1400</td><td>6.211</td><td>54.125</td><td>5.952</td><td>187.893</td><td>136.640</td></tr><tr><td>1600</td><td>6.521</td><td>54.975</td><td>7.226</td><td>187.767</td><td>129.327</td></tr><tr><td>1800</td><td>6.782</td><td>55.759</td><td>8.557</td><td>187.662</td><td>122.029</td></tr><tr><td>2000</td><td>6.998</td><td>56.485</td><td>9.936</td><td>187.569</td><td>114.741</td></tr></table>

$P[c,1,1/4P_{4}(g)]$

Phosphorus (white) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>5.698</td><td>9.820</td><td>0</td></tr><tr><td>317</td><td>5.820</td><td>10.179</td><td>.110</td></tr><tr><td>317</td><td>6.292</td><td>10.674</td><td>.267</td></tr><tr><td>400*</td><td>6.292</td><td>12.131</td><td>.787</td></tr><tr><td>550</td><td>6.292</td><td>14.109</td><td>1.731</td></tr><tr><td>550</td><td>4.627</td><td>19.396</td><td>4.639</td></tr><tr><td>600</td><td>4.684</td><td>19.803</td><td>4.872</td></tr><tr><td>800</td><td>4.804</td><td>21.169</td><td>5.822</td></tr><tr><td>1000</td><td>4.861</td><td>22.248</td><td>6.789</td></tr><tr><td>1200</td><td>4.894</td><td>23.137</td><td>7.765</td></tr><tr><td>1400</td><td>4.913</td><td>23.893</td><td>8.746</td></tr><tr><td>1600</td><td>4.926</td><td>24.550</td><td>9.730</td></tr><tr><td>1800</td><td>4.935</td><td>25.131</td><td>10.716</td></tr><tr><td>2000</td><td>4.941</td><td>25.651</td><td>11.703</td></tr></table>

\*Data extrapolated 400-550 K.   
317.3 K, melting point; $\Delta H^{\circ} = 0.157$   
550 K, boiling point; $\Delta H^{\circ}$   
= 2.908/mol of P.

P(c)
Phosphorus, red 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H°298</td><td>ΔHf°</td><td>ΔGf°</td></tr><tr><td>298</td><td>5.069</td><td>5.460</td><td>0</td><td>-4.200</td><td>-2.900</td></tr><tr><td>300</td><td>5.082</td><td>5.491</td><td>.009</td><td>-4.202</td><td>-2.893</td></tr><tr><td>400</td><td>5.540</td><td>7.019</td><td>.542</td><td>-4.445</td><td>-2.400</td></tr><tr><td>500</td><td>5.852</td><td>8.292</td><td>1.112</td><td>-4.504</td><td>-1.882</td></tr><tr><td>600</td><td>6.165</td><td>9.386</td><td>1.713</td><td>-7.359</td><td>-1.109</td></tr><tr><td>700</td><td>6.500</td><td>10.361</td><td>2.346</td><td>-7.198</td><td>-0.079</td></tr></table>

704 K, sublimation point; $\Delta H^{\circ} = 7.1$

P(g)
Phosphorus (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>38.980</td><td>0</td><td>75.620</td><td>66.926</td></tr><tr><td>400</td><td>4.968</td><td>40.440</td><td>.506</td><td>75.339</td><td>64.015</td></tr><tr><td>600</td><td>4.968</td><td>42.454</td><td>1.500</td><td>72.248</td><td>58.657</td></tr><tr><td>800</td><td>4.968</td><td>43.883</td><td>2.493</td><td>72.291</td><td>54.120</td></tr><tr><td>1000</td><td>4.968</td><td>44.992</td><td>3.487</td><td>72.318</td><td>49.574</td></tr><tr><td>1200</td><td>4.969</td><td>45.898</td><td>4.481</td><td>72.336</td><td>45.023</td></tr><tr><td>1400</td><td>4.974</td><td>46.664</td><td>5.475</td><td>72.349</td><td>40.470</td></tr><tr><td>1600</td><td>4.987</td><td>47.329</td><td>6.471</td><td>72.361</td><td>35.915</td></tr><tr><td>1800</td><td>5.015</td><td>47.918</td><td>7.471</td><td>72.375</td><td>31.358</td></tr><tr><td>2000</td><td>5.062</td><td>48.449</td><td>8.478</td><td>72.395</td><td>26.799</td></tr></table>

$P_{2}(g)$ Phosphorus (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>7.657</td><td>52.110</td><td>0</td><td>34.370</td><td>24.689</td></tr><tr><td>400</td><td>8.050</td><td>54.418</td><td>.801</td><td>33.597</td><td>21.535</td></tr><tr><td>600</td><td>8.485</td><td>57.776</td><td>2.460</td><td>27.086</td><td>16.184</td></tr><tr><td>800</td><td>8.690</td><td>60.248</td><td>4.180</td><td>26.906</td><td>12.578</td></tr><tr><td>1000</td><td>8.800</td><td>62.200</td><td>5.930</td><td>26.722</td><td>9.018</td></tr><tr><td>1200</td><td>8.868</td><td>63.811</td><td>7.697</td><td>26.537</td><td>5.493</td></tr><tr><td>1400</td><td>8.914</td><td>65.182</td><td>9.476</td><td>26.354</td><td>2.000</td></tr><tr><td>1600</td><td>8.949</td><td>66.374</td><td>11.262</td><td>26.172</td><td>-1.466</td></tr><tr><td>1800</td><td>8.976</td><td>67.430</td><td>13.055</td><td>25.993</td><td>-4.909</td></tr><tr><td>2000</td><td>8.998</td><td>68.377</td><td>14.852</td><td>25.816</td><td>-8.334</td></tr></table>

$P_{4}(g)$ Phosphorus (ideal tetratomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>16.051</td><td>66.893</td><td>0</td><td>14.125</td><td>5.892</td></tr><tr><td>400</td><td>17.510</td><td>71.837</td><td>1.717</td><td>12.694</td><td>3.369</td></tr><tr><td>600</td><td>18.735</td><td>79.212</td><td>5.363</td><td>0</td><td>0</td></tr><tr><td>800</td><td>19.214</td><td>84.676</td><td>9.164</td><td>0</td><td>0</td></tr><tr><td>1000</td><td>19.445</td><td>88.991</td><td>13.033</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>19.574</td><td>92.549</td><td>16.936</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>19.652</td><td>95.572</td><td>20.859</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>19.703</td><td>98.200</td><td>24.795</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>19.738</td><td>100.523</td><td>28.739</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>19.764</td><td>102.604</td><td>32.689</td><td>0</td><td>0</td></tr></table>

Pa(c,1)
Protactinium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H298°</td></tr><tr><td>298*</td><td>6.601</td><td>12.400</td><td>0</td></tr><tr><td>400</td><td>6.903</td><td>14.382</td><td>.688</td></tr><tr><td>600</td><td>7.497</td><td>17.293</td><td>2.128</td></tr><tr><td>800</td><td>8.091</td><td>19.531</td><td>3.686</td></tr><tr><td>1000</td><td>8.685</td><td>21.401</td><td>5.364</td></tr><tr><td>1200</td><td>9.279</td><td>23.037</td><td>7.160</td></tr><tr><td>1400</td><td>9.873</td><td>24.511</td><td>9.076</td></tr><tr><td>1443</td><td>10.001</td><td>24.812</td><td>9.503</td></tr><tr><td>1443</td><td>9.500</td><td>25.912</td><td>11.090</td></tr><tr><td>1600</td><td>9.500</td><td>26.893</td><td>12.581</td></tr><tr><td>1800</td><td>9.500</td><td>28.012</td><td>14.481</td></tr><tr><td>1845</td><td>9.500</td><td>28.247</td><td>14.909</td></tr><tr><td>1845</td><td>11.300</td><td>29.845</td><td>17.859</td></tr><tr><td>2000</td><td>11.300</td><td>30.757</td><td>19.610</td></tr><tr><td>2200</td><td>11.300</td><td>31.834</td><td>21.870</td></tr><tr><td>2400</td><td>11.300</td><td>32.817</td><td>24.130</td></tr></table>

\*All data estimated.

1443 K, transition point; $\Delta H^{\circ} = 1.587$

1845 K, melting point; $\Delta H^{\circ} = 2.950$

Pa(g)
Protactinium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.476</td><td>47.308</td><td>0</td><td>145.000</td><td>134.592</td></tr><tr><td>400</td><td>5.813</td><td>48.966</td><td>.575</td><td>144.887</td><td>131.053</td></tr><tr><td>600</td><td>6.328</td><td>51.424</td><td>1.791</td><td>144.663</td><td>124.184</td></tr><tr><td>800</td><td>6.797</td><td>53.309</td><td>3.104</td><td>144.418</td><td>117.396</td></tr><tr><td>1000</td><td>7.233</td><td>54.874</td><td>4.508</td><td>144.144</td><td>110.671</td></tr><tr><td>1200</td><td>7.603</td><td>56.226</td><td>5.993</td><td>143.833</td><td>104.006</td></tr><tr><td>1400</td><td>7.905</td><td>57.422</td><td>7.545</td><td>143.469</td><td>97.394</td></tr><tr><td>1600</td><td>8.153</td><td>58.494</td><td>9.151</td><td>141.570</td><td>91.008</td></tr><tr><td>1800</td><td>8.364</td><td>59.467</td><td>10.803</td><td>141.322</td><td>84.703</td></tr><tr><td>2000</td><td>8.548</td><td>60.357</td><td>12.495</td><td>137.885</td><td>78.685</td></tr><tr><td>2200</td><td>8.712</td><td>61.180</td><td>14.221</td><td>137.351</td><td>72.790</td></tr><tr><td>2400</td><td>8.859</td><td>61.945</td><td>15.979</td><td>136.849</td><td>66.942</td></tr></table>

Pb(c,1)   
Lead 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H298</td></tr><tr><td>298</td><td>6.370</td><td>15.490</td><td>0</td></tr><tr><td>400</td><td>6.530</td><td>17.380</td><td>.655</td></tr><tr><td>600</td><td>7.030</td><td>20.120</td><td>2.013</td></tr><tr><td>601</td><td>7.030</td><td>20.130</td><td>2.017</td></tr><tr><td>601</td><td>7.320</td><td>22.040</td><td>3.164</td></tr><tr><td>800</td><td>7.170</td><td>24.120</td><td>4.610</td></tr><tr><td>1000</td><td>7.020</td><td>25.700</td><td>6.028</td></tr><tr><td>1200</td><td>6.880</td><td>26.970</td><td>7.417</td></tr><tr><td>1400*</td><td>6.840</td><td>28.020</td><td>8.788</td></tr><tr><td>1600</td><td>6.840</td><td>28.920</td><td>10.156</td></tr><tr><td>1800</td><td>6.840</td><td>29.740</td><td>11.524</td></tr><tr><td>2000</td><td>6.840</td><td>30.460</td><td>12.892</td></tr></table>

\*Data extrapolated above 1300 K.   
600.65 K, melting point; $\Delta H^{\circ} = 1.147$

Pb(g)   
Lead (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>41.890</td><td>0</td><td>46.750</td><td>38.879</td></tr><tr><td>400</td><td>4.968</td><td>43.350</td><td>.506</td><td>46.601</td><td>36.213</td></tr><tr><td>600</td><td>4.968</td><td>45.365</td><td>1.500</td><td>46.237</td><td>31.090</td></tr><tr><td>800</td><td>4.969</td><td>46.794</td><td>2.493</td><td>44.633</td><td>26.494</td></tr><tr><td>1000</td><td>4.978</td><td>47.903</td><td>3.488</td><td>44.210</td><td>22.007</td></tr><tr><td>1200</td><td>5.017</td><td>48.814</td><td>4.487</td><td>43.820</td><td>17.607</td></tr><tr><td>1400</td><td>5.113</td><td>49.594</td><td>5.499</td><td>43.461</td><td>13.257</td></tr><tr><td>1600</td><td>5.290</td><td>50.287</td><td>6.537</td><td>43.131</td><td>8.944</td></tr><tr><td>1800</td><td>5.554</td><td>50.924</td><td>7.620</td><td>42.846</td><td>4.715</td></tr><tr><td>2000</td><td>5.899</td><td>51.527</td><td>8.764</td><td>42.622</td><td>.488</td></tr></table>

$\mathsf{Pb}_{2}(\mathsf{g})$   
Lead (ideal diatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>8.825</td><td>67.216</td><td>0</td><td>79.500</td><td>68.696</td></tr><tr><td>400</td><td>8.964</td><td>69.830</td><td>.907</td><td>79.097</td><td>65.069</td></tr><tr><td>600</td><td>9.115</td><td>73.496</td><td>2.716</td><td>78.190</td><td>58.236</td></tr><tr><td>800</td><td>9.219</td><td>76.133</td><td>4.549</td><td>74.829</td><td>52.515</td></tr><tr><td>1000</td><td>9.309</td><td>78.200</td><td>6.402</td><td>73.846</td><td>47.046</td></tr><tr><td>1200</td><td>9.393</td><td>79.905</td><td>8.273</td><td>72.939</td><td>41.781</td></tr><tr><td>1400</td><td>9.475</td><td>81.359</td><td>10.159</td><td>72.083</td><td>36.636</td></tr><tr><td>1600</td><td>9.555</td><td>82.629</td><td>12.063</td><td>71.251</td><td>31.589</td></tr><tr><td>1800</td><td>9.635</td><td>83.759</td><td>13.982</td><td>70.434</td><td>26.732</td></tr><tr><td>2000</td><td>9.714</td><td>84.779</td><td>15.916</td><td>69.632</td><td>21.914</td></tr></table>

Pd(c,1)   
Palladium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>6.188</td><td>9.013</td><td>0</td></tr><tr><td>400</td><td>6.359</td><td>10.855</td><td>.639</td></tr><tr><td>600</td><td>6.643</td><td>13.488</td><td>1.940</td></tr><tr><td>800</td><td>6.903</td><td>15.435</td><td>3.295</td></tr><tr><td>1000</td><td>7.161</td><td>17.003</td><td>4.701</td></tr><tr><td>1200</td><td>7.421</td><td>18.332</td><td>6.159</td></tr><tr><td>1400</td><td>7.686</td><td>19.496</td><td>7.670</td></tr><tr><td>1600</td><td>7.956</td><td>20.539</td><td>9.234</td></tr><tr><td>1800</td><td>8.231</td><td>21.492</td><td>10.852</td></tr><tr><td>1827</td><td>8.269</td><td>21.615</td><td>11.075</td></tr><tr><td>1827</td><td>9.060</td><td>23.788</td><td>15.045</td></tr><tr><td>2000</td><td>9.060</td><td>24.608</td><td>16.612</td></tr><tr><td>2200</td><td>9.060</td><td>25.471</td><td>18.424</td></tr><tr><td>2400</td><td>9.060</td><td>26.260</td><td>20.236</td></tr></table>

1827 K, melting point; $\Delta H^{\circ} = 3.970$

Pd(g)   
Palladium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>39.902</td><td>0</td><td>90.400</td><td>81.190</td></tr><tr><td>400</td><td>4.968</td><td>41.362</td><td>.506</td><td>90.267</td><td>78.064</td></tr><tr><td>600</td><td>4.969</td><td>43.376</td><td>1.500</td><td>89.960</td><td>72.027</td></tr><tr><td>800</td><td>4.984</td><td>44.807</td><td>2.494</td><td>89.599</td><td>66.101</td></tr><tr><td>1000</td><td>5.084</td><td>45.928</td><td>3.499</td><td>89.198</td><td>60.273</td></tr><tr><td>1200</td><td>5.380</td><td>46.877</td><td>4.541</td><td>88.782</td><td>54.528</td></tr><tr><td>1400</td><td>5.939</td><td>47.745</td><td>5.668</td><td>88.398</td><td>48.849</td></tr><tr><td>1600</td><td>6.743</td><td>48.588</td><td>6.933</td><td>88.099</td><td>43.221</td></tr><tr><td>1800</td><td>7.688</td><td>49.436</td><td>8.375</td><td>87.923</td><td>37.624</td></tr><tr><td>2000</td><td>8.632</td><td>50.295</td><td>10.008</td><td>83.796</td><td>32.422</td></tr><tr><td>2200</td><td>9.442</td><td>51.158</td><td>11.819</td><td>83.795</td><td>27.284</td></tr><tr><td>2400</td><td>10.033</td><td>52.006</td><td>13.770</td><td>83.934</td><td>22.144</td></tr></table>

$\operatorname{Pr}(\mathbf{c},\mathbf{l})$   
Praseodymium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.560</td><td>17.670</td><td>0</td></tr><tr><td>400</td><td>6.790</td><td>19.630</td><td>.679</td></tr><tr><td>600</td><td>7.530</td><td>22.500</td><td>2.103</td></tr><tr><td>800</td><td>8.500</td><td>24.800</td><td>3.705</td></tr><tr><td>1000</td><td>9.650</td><td>26.820</td><td>5.514</td></tr><tr><td>1068</td><td>10.010</td><td>27.460</td><td>6.183</td></tr><tr><td>1068</td><td>9.190</td><td>28.170</td><td>6.940</td></tr><tr><td>1200</td><td>9.190</td><td>29.240</td><td>8.153</td></tr><tr><td>1204</td><td>9.190</td><td>29.280</td><td>8.190</td></tr><tr><td>1204</td><td>10.270</td><td>30.640</td><td>9.836</td></tr><tr><td>1400</td><td>10.270</td><td>32.190</td><td>11.849</td></tr><tr><td>1600*</td><td>10.270</td><td>33.560</td><td>13.904</td></tr><tr><td>1800</td><td>10.270</td><td>34.770</td><td>15.958</td></tr><tr><td>2000</td><td>10.270</td><td>35.860</td><td>18.012</td></tr></table>

\*Data extrapolated above 1400 K.   
1068 K, transition point; $\Delta H^{\circ} = 0.757$   
1204 K, melting point; $\Delta H^{\circ} = 1.646$

Pr(g)   
Praseodymium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>5.105</td><td>45.339</td><td>0</td><td>85.000</td><td>76.750</td></tr><tr><td>400</td><td>5.385</td><td>46.876</td><td>.533</td><td>84.854</td><td>73.956</td></tr><tr><td>600</td><td>5.987</td><td>49.177</td><td>1.673</td><td>84.570</td><td>68.564</td></tr><tr><td>800</td><td>6.413</td><td>50.962</td><td>2.916</td><td>84.211</td><td>63.281</td></tr><tr><td>1000</td><td>6.662</td><td>52.423</td><td>4.226</td><td>83.712</td><td>58.109</td></tr><tr><td>1200</td><td>6.771</td><td>53.649</td><td>5.571</td><td>82.418</td><td>53.127</td></tr><tr><td>1400</td><td>6.783</td><td>54.695</td><td>6.928</td><td>80.079</td><td>48.572</td></tr><tr><td>1600</td><td>6.739</td><td>55.598</td><td>8.281</td><td>79.377</td><td>44.116</td></tr><tr><td>1800</td><td>6.671</td><td>56.388</td><td>9.622</td><td>78.664</td><td>39.752</td></tr><tr><td>2000</td><td>6.598</td><td>57.087</td><td>10.949</td><td>77.937</td><td>35.483</td></tr></table>

Pt(c,1)   
Platinum 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>6.180</td><td>9.950</td><td>0</td></tr><tr><td>400</td><td>6.330</td><td>11.789</td><td>.638</td></tr><tr><td>600</td><td>6.580</td><td>14.401</td><td>1.928</td></tr><tr><td>800</td><td>6.830</td><td>16.328</td><td>3.269</td></tr><tr><td>1000</td><td>7.080</td><td>17.879</td><td>4.659</td></tr><tr><td>1200</td><td>7.330</td><td>19.192</td><td>6.100</td></tr><tr><td>1400</td><td>7.590</td><td>20.342</td><td>7.593</td></tr><tr><td>1600</td><td>7.840</td><td>21.371</td><td>9.135</td></tr><tr><td>1800</td><td>8.090</td><td>22.309</td><td>10.729</td></tr><tr><td>2000</td><td>8.340</td><td>23.175</td><td>12.372</td></tr><tr><td>2042</td><td>8.370</td><td>23.349</td><td>12.725</td></tr><tr><td>2042*</td><td>8.300</td><td>25.649</td><td>17.421</td></tr><tr><td>2200</td><td>8.300</td><td>26.268</td><td>18.733</td></tr></table>

\*Fusion and Pt(1) data estimated.   
2042 K, melting point; $\Delta H^{\circ} = 4.696$

Pt(g)   
Platinum (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>6.102</td><td>45.960</td><td>0</td><td>135.100</td><td>124.364</td></tr><tr><td>400</td><td>6.459</td><td>47.815</td><td>.644</td><td>135.106</td><td>120.696</td></tr><tr><td>600</td><td>6.260</td><td>50.417</td><td>1.926</td><td>135.098</td><td>113.488</td></tr><tr><td>800</td><td>5.877</td><td>52.164</td><td>3.138</td><td>134.969</td><td>106.300</td></tr><tr><td>1000</td><td>5.609</td><td>53.444</td><td>4.285</td><td>134.726</td><td>99.161</td></tr><tr><td>1200</td><td>5.447</td><td>54.451</td><td>5.389</td><td>134.389</td><td>92.078</td></tr><tr><td>1400</td><td>5.358</td><td>55.283</td><td>6.469</td><td>133.976</td><td>85.059</td></tr><tr><td>1600</td><td>5.318</td><td>55.996</td><td>7.535</td><td>133.500</td><td>78.100</td></tr><tr><td>1800</td><td>5.310</td><td>56.621</td><td>8.598</td><td>132.969</td><td>71.207</td></tr><tr><td>2000</td><td>5.326</td><td>57.182</td><td>9.661</td><td>132.389</td><td>64.375</td></tr><tr><td>2200</td><td>5.356</td><td>57.691</td><td>10.729</td><td>127.096</td><td>57.965</td></tr></table>

Pu(c,1)   
Plutonium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td></tr><tr><td>298</td><td>7.850</td><td>13.420</td><td>0</td></tr><tr><td>395</td><td>9.253</td><td>15.796</td><td>.821</td></tr><tr><td>395</td><td>8.098</td><td>17.872</td><td>1.641</td></tr><tr><td>400</td><td>8.124</td><td>17.974</td><td>1.682</td></tr><tr><td>480</td><td>8.540</td><td>19.492</td><td>2.348</td></tr><tr><td>480</td><td>8.406</td><td>19.773</td><td>2.483</td></tr><tr><td>588</td><td>9.135</td><td>21.550</td><td>3.431</td></tr><tr><td>588</td><td>8.880</td><td>21.789</td><td>3.571</td></tr><tr><td>600</td><td>8.880</td><td>21.968</td><td>3.677</td></tr><tr><td>730</td><td>8.880</td><td>23.709</td><td>4.831</td></tr><tr><td>730</td><td>8.500</td><td>23.737</td><td>4.851</td></tr><tr><td>752</td><td>8.500</td><td>23.989</td><td>5.038</td></tr><tr><td>752</td><td>8.230</td><td>24.574</td><td>5.478</td></tr><tr><td>800</td><td>8.230</td><td>25.084</td><td>5.874</td></tr><tr><td>913</td><td>8.230</td><td>26.171</td><td>6.804</td></tr><tr><td>913</td><td>10.100</td><td>26.910</td><td>7.479</td></tr><tr><td>1000</td><td>10.100</td><td>27.830</td><td>8.357</td></tr><tr><td>1200</td><td>10.100</td><td>29.671</td><td>10.377</td></tr><tr><td>1400*</td><td>10.100</td><td>31.228</td><td>12.397</td></tr><tr><td>1600</td><td>10.100</td><td>32.577</td><td>14.417</td></tr><tr><td>1800</td><td>10.100</td><td>33.766</td><td>16.437</td></tr><tr><td>2000</td><td>10.100</td><td>34.830</td><td>18.457</td></tr><tr><td>2200</td><td>10.100</td><td>35.793</td><td>20.477</td></tr><tr><td>2400</td><td>10.100</td><td>36.672</td><td>22.497</td></tr></table>

\*Data extrapolated above 1200 K.   
395 K, transition point; $\Delta H^{\circ} = 0.820$   
480 K, transition point; $\Delta H^{\circ} = 0.135$   
588 K, transition point; $\Delta H^{\circ} = 0.140$   
730 K, transition point; $\Delta H^{\circ} = 0.020$   
752 K, transition point; $\Delta H^{\circ} = 0.440$   
913 K, melting point; $\Delta H^{\circ} = 0.675$

Pu(g)   
Plutonium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.984</td><td>42.317</td><td>0</td><td>82.500</td><td>73.884</td></tr><tr><td>400</td><td>5.103</td><td>43.795</td><td>.512</td><td>81.330</td><td>71.002</td></tr><tr><td>600</td><td>5.823</td><td>45.981</td><td>1.596</td><td>80.419</td><td>66.011</td></tr><tr><td>800</td><td>6.842</td><td>47.794</td><td>2.861</td><td>79.487</td><td>61.319</td></tr><tr><td>1000</td><td>7.840</td><td>49.429</td><td>4.330</td><td>78.473</td><td>56.874</td></tr><tr><td>1200</td><td>8.789</td><td>50.943</td><td>5.994</td><td>78.117</td><td>52.591</td></tr><tr><td>1400</td><td>9.695</td><td>52.367</td><td>7.843</td><td>77.946</td><td>48.351</td></tr><tr><td>1600</td><td>10.520</td><td>53.716</td><td>9.866</td><td>77.949</td><td>44.127</td></tr><tr><td>1800</td><td>11.217</td><td>54.997</td><td>12.042</td><td>78.105</td><td>39.889</td></tr><tr><td>2000</td><td>11.769</td><td>56.208</td><td>14.343</td><td>78.386</td><td>35.630</td></tr><tr><td>2200</td><td>12.196</td><td>57.351</td><td>16.741</td><td>78.764</td><td>31.336</td></tr><tr><td>2400</td><td>12.539</td><td>58.427</td><td>19.216</td><td>79.219</td><td>27.007</td></tr></table>

Rb(c,1,g)   
Rubidium 

<table><tr><td>T</td><td>Cp°</td><td>S°</td><td>H°-H°298</td></tr><tr><td>298</td><td>7.424</td><td>18.350</td><td>0</td></tr><tr><td>313</td><td>7.744</td><td>18.710</td><td>.110</td></tr><tr><td>313</td><td>8.222</td><td>20.386</td><td>.634</td></tr><tr><td>400</td><td>7.856</td><td>22.371</td><td>1.336</td></tr><tr><td>600</td><td>7.251</td><td>25.426</td><td>2.841</td></tr><tr><td>800</td><td>6.932</td><td>27.463</td><td>4.255</td></tr><tr><td>975</td><td>6.900</td><td>28.829</td><td>5.460</td></tr><tr><td>975</td><td>4.968</td><td>46.510</td><td>22.690</td></tr><tr><td>1000</td><td>4.968</td><td>46.638</td><td>22.817</td></tr><tr><td>1200</td><td>4.968</td><td>47.544</td><td>23.811</td></tr><tr><td>1400</td><td>4.970</td><td>48.310</td><td>24.804</td></tr><tr><td>1600</td><td>4.976</td><td>48.974</td><td>25.799</td></tr><tr><td>1800</td><td>4.992</td><td>49.561</td><td>26.795</td></tr><tr><td>2000</td><td>5.022</td><td>50.088</td><td>27.797</td></tr></table>

312.64 K, melting point; $\Delta H^{\circ} = 0.524$   
974.5 K, boiling point to ideal   
monatomic gas; $\Delta H^{\circ} = 17.230$

Rb(g)   
Rubidium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_{298}^o$ </td><td> $\Delta Hf^o$ </td><td> $\Delta Gf^o$ </td></tr><tr><td>298</td><td>4.968</td><td>40.626</td><td>0</td><td>19.330</td><td>12.688</td></tr><tr><td>400</td><td>4.968</td><td>42.086</td><td>.506</td><td>18.500</td><td>10.614</td></tr><tr><td>600</td><td>4.968</td><td>44.100</td><td>1.500</td><td>17.989</td><td>6.785</td></tr><tr><td>800</td><td>4.968</td><td>45.529</td><td>2.493</td><td>17.568</td><td>3.115</td></tr><tr><td>1000</td><td>4.968</td><td>46.638</td><td>3.487</td><td>0</td><td>0</td></tr><tr><td>1200</td><td>4.968</td><td>47.544</td><td>4.481</td><td>0</td><td>0</td></tr><tr><td>1400</td><td>4.970</td><td>48.310</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.976</td><td>48.974</td><td>6.469</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.992</td><td>49.561</td><td>7.465</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>5.022</td><td>50.088</td><td>8.467</td><td>0</td><td>0</td></tr></table>

Re(c)  
Rhenium 

<table><tr><td>Γ</td><td>Cp°</td><td>S°</td><td>H°-H298°</td></tr><tr><td>298</td><td>6.035</td><td>8.730</td><td>0</td></tr><tr><td>400</td><td>6.188</td><td>10.527</td><td>.623</td></tr><tr><td>600</td><td>6.441</td><td>13.083</td><td>1.886</td></tr><tr><td>800</td><td>6.703</td><td>14.972</td><td>3.200</td></tr><tr><td>1000</td><td>6.965</td><td>16.496</td><td>4.567</td></tr><tr><td>1200</td><td>7.225</td><td>17.789</td><td>5.986</td></tr><tr><td>1400</td><td>7.485</td><td>18.922</td><td>7.457</td></tr><tr><td>1600</td><td>7.745</td><td>19.938</td><td>8.980</td></tr><tr><td>1800</td><td>8.005</td><td>20.866</td><td>10.555</td></tr><tr><td>2000</td><td>8.265</td><td>21.723</td><td>12.182</td></tr></table>

3453 K, melting point; $\Delta H^{\circ} = 7.9$