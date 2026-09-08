# Thermodynamic Data for Mineral Technology

By L. B. Pankratz, J. M. Stuve, and N. A. Gokcen

![](images/e0b21f834cead42dd224de7cfb4e8fb73b7c6a4210dece26d4c82fd1bcda3a98.jpg)

<details>
<summary>text_image</summary>

U.S. DEPARTMENT OF THE INTERIOR
MARCH 3, 1849
</details>

UNITED STATES DEPARTMENT OF THE INTERIOR

William P. Clark, Secretary

BUREAU OF MINES

Robert C. Horton, Director

As the Nation's principal conservation agency, the Department of the Interior has responsibility for most of our nationally owned public lands and natural resources. This includes fostering the wisest use of our land and water resources, protecting our fish and wildlife, preserving the environmental and cultural values of our national parks and historical places, and providing for the enjoyment of life through outdoor recreation. The Department assesses our energy and mineral resources and works to assure that their development is in the best interests of all our people. The Department also has a major responsibility for American Indian reservation communities and for people who live in island territories under U.S. administration.

Library of Congress Cataloging In Publication Data

Pankratz, L. B.

Thermodynamic data for mineral technology.

(Bulletin/United States Department of the Interior, Bureau of Mines; 677)

Includes bibliographies.

Supt. of Docs. no.: 1 28.27:

1. Mineralogy—Thermal properties. 2. Phase rule and equilibrium. 3. Mineral industries. I. Stuve, J. M. II. Gokcen, N. A. III. Title. IV. Series: Bulletin (United States. Bureau of Mines); 677.

QE364.P285

1984

549.131

84-600195

# CONTENTS

Page

Abstract.... 1

Introduction.... 1

Chapter 1.--Thermodynamic background and applications.... 4

Laws of thermodynamics.... 4

The first law of thermodynamics.... 4

Enthalpy change of phase transformation.... 7

Thermochemistry.... 10

Adiabatic flame temperature.... 14

The second law of thermodynamics.... 16

The third law of thermodynamics.... 18

Interpolation of tabulated data.... 22

Extrapolation of tabulated data above listed temperatures.... 24

The second law method for calculation of $\Delta H^{\circ}$ and $\Delta S^{\circ}$ 25

Gibbs energy function and the third law method for $\Delta H_{298}^{\circ}$ 28

Complex equilibria.... 30

Additional examples and practical applications.... 33

References.... 46

Chapter 2.--Thermodynamic property tables.... 47

Elements.... 47

Antimonides....92

Arsenides....95

Borides....98

Bromides.... 103

Carbides.... 127

Carbonates.... 140

Chlorides.... 144

Fluorides.... 179

Hydrides.... 218

Iodides.... 228

Nitrides.... 248

Oxides.... 261

Phosphides.... 296

Selenides.... 299

Silicates.... 307

Silicides.... 313

Sulfates.... 319

Sulfides.... 327

Tellurides.... 346

References.... 353

UNIT OF MEASURE ABBREVIATIONS USED IN THIS BULLETIN   
```txt
atm atmosphere (1 atm = 101325 pascals)
cal thermochemical calorie (1 cal = 4.1840 joules)
cal/mol calorie per mol
cal/mol·K calorie per mol per kelvin
K kelvin (the unit of thermodynamic temperature)
kcal/mol kilocalorie per mol
mm millimeter
mol mol (gram formula weight or molar mass)
mol pct mol percent
Pa pascal
pct percent 
```

OTHER ABBREVIATIONS AND SYMBOLS USED IN THIS BULLETIN   
```txt
° Standard state, pure phase at 1 atm
T Thermodynamic temperature in kelvins
Cp Heat capacity at constant pressure
S Entropy
H - H298 Enthalpy increment between T and 298.15 K
ΔH Enthalpy change (ΔHf = enthalpy of formation)
ΔG Gibbs energy change (ΔGf = Gibbs energy of formation)
P Pressure in atmosphere, 1 atm = 101325 Pa
R Gas constant, 1.98719 cal/K·mol
F Faraday constant, 23060.9 cal/volt·equivalent 
```

# ABSTRACT

Thermodynamic data on the elements, oxides, sulfides, halides, and selected hydrides, carbides, nitrides, carbonates, sulfates, silicates, and miscellaneous compounds were reviewed, evaluated, and compiled at the Bureau of Mines. Values for $C_{p}^{\circ}$ , $S^{\circ}$ , $H^{\circ} - H_{298}^{\circ}$ , $\Delta Hf^{\circ}$ , and $\Delta Gf^{\circ}$ are given in tabular form. A brief thermodynamic background and a number of examples and applications are presented.

# INTRODUCTION

This compilation is part of the Bureau of Mines continuing effort to provide information for use as guidelines in mineral technology advancement, pollution control, and energy economy.

The values in this compilation are the result of a review and evaluation of relevant thermodynamic data through 1979 for the elements and oxides, and through 1982 for the remaining compounds. The sources of data are given at the beginning of each section of tables. Estimates were used where the necessary data were lacking. Estimated and extrapolated values are indicated in the tables with asterisks (\*) and footnotes immediately below the table. When $C_{p}^{o}$ is shown to have been estimated, then $S^{o}$ and $H^{o} - H_{298}^{o}$ have also been estimated. An asterisk and a footnote are also used to indicate decomposition, metastable phases, and irreversible transformations.

The selected experimental data were fit with a polynomial as a function of temperature by using a computer program. The resulting polynomial was then used in a subroutine of the program to calculate standard heat capacities, relative enthalpies, and entropies at selected temperatures. In addition, the tables for the elements in their unstable states and the compounds include the values for the standard enthalpy of formation and Gibbs energy of formation. (See, e.g., the tables for Mg(g) below 1363 K and for MgO(c) at all temperatures.) To conserve space, most of the tables are given at 200 K intervals for the range of stability of each phase. The transformation temperatures have been rounded off in the tables, with the exact temperature listed in the footnotes. A thermodynamic value D midway between the values A and B, which are 200 K apart, can be obtained from

$$
D = 0. 3 7 5 A + 0. 7 5 B - 0. 1 2 5 C,
$$

where A, B, and C are the successive values at temperatures such as 800, 1000 and 1200 respectively, and D is the value at 900 K. If the values are required for temperatures between 800 and 900 K, then an additional linear interpolation is adequate.

Tablulated values are for the substances in their standard states indicated by the superscript °. Standard states, for the pure condensed substances, are the most stable form at 1 atm pressure at the specified temperature. For vitreous and metastable substances, the superscript refers to the pure substance. For gases, the standard state is the pure substance at unit fugacity whether the gas is or is not stable at unit fugacity. Where possible, all phases of an element or compound are presented in a single table. Transformation temperatures and thermodynamic properties at these temperatures are included in the tables. Immediately below each such table, the nature of the transformations is given along with the associated enthalpies.

The symbol $\Delta$ refers to the formation reaction from the elements as reactants in their standard stable states. Thus, $\Delta$ in the table for $\mathrm{Mg(g)}$ at $600\mathrm{K}$ refers to $\mathrm{Mg(c)} = \mathrm{Mg(g)}$ , and at $1000\mathrm{K}$ , $\mathrm{Mg(1)} = \mathrm{Mg(g)}$ . Likewise, $\Delta$ in the table for $\mathrm{MgO(c)}$ at $600\mathrm{K}$ refers to $\mathrm{Mg(c)} + 0.5\mathrm{O}_2(\mathrm{g}) = \mathrm{MgO(c)}$ , and at $1000\mathrm{K}$ , $\mathrm{Mg(1)} + 0.5\mathrm{O}_2(\mathrm{g}) = \mathrm{MgO(c)}$ . In contrast, for all the compounds containing S, Se, and Te, the standard

enthalpies and Gibbs energies of formation were calculated by using the diatomic gaseous species in their standard states as one of the reactants, whether these gaseous species are stable or not at unit fugacity. E.g., $\Delta \mathrm{Hf}_{298}^{\circ}$ in the table for $\mathrm{Cu}_{2}\mathrm{S}(\mathrm{c})$ refers to $0.5\mathrm{S}_{2}(\mathrm{g}) + 2\mathrm{Cu}(\mathrm{c}) = \mathrm{Cu}_{2}\mathrm{S}(\mathrm{c})$ ; however, if the formation from rhombic sulfur is desired, then $\Delta \mathrm{Hf}_{298}^{\circ}$ for $\mathrm{S}(\mathrm{c}) = 0.5\mathrm{S}_{2}(\mathrm{g})$ must be added to the first $\Delta \mathrm{Hf}_{298}^{\circ}$ .

The common practice of tabulating five- and sometimes six-digit values has been followed. E.g., enthalpy values are given to the nearest calorie. The number of digits given is not intended to reflect the accuracy of the experimental values used. It is an effort to produce internal consistency in the tabulated values. The succeeding section, "Thermodynamic Background and Applications," illustrates the use and selected applications of the tables.

# CHAPTER 1.--THERMODYNAMIC BACKGROUND AND APPLICATIONS

This section is a brief review of thermodynamic background and summary based on standard recent texts and treatises on thermodynamics. (See, e.g., a few selected references (1, 3, 6, 9). $^{3}$ ) The objective here is to present a comprehensive basis for using the tables presented in this compilation. Numerical examples are given where necessary. $^{4}$

# Laws of Thermodynamics

Thermodynamics deals with the conservation and interconversion of various forms of energy, and the relationships between energy and changes in properties of matter. Thermodynamic state of a system is defined in this publication in terms of the variables of state, which are pressure, temperature, and numbers of mols of components. Other variables of state, such as gravitational, electrical, and magnetic fields, are not considered here. A closed system is separated from its surroundings by a rigid or movable boundary that prevents the exchange of components with the surroundings. In a closed system, the variables of state may be taken as pressure, P, and temperature, T, and in an open system, P, T, and the numbers of mols of components, $\ell$ . The volume of the system, V, may be used instead of P or T, and further, other variables may appear in certain equations, replacing P, T, and $\ell$ . Thermodynamics formulates all types of relations involving energy and variables of state.

# The First Law of Thermodynamics

The law of conservation of energy is a law of physics that is inherently related to the first law of thermodynamics. The best statement of the first law is by Carathéodory (6):

There is a function E, called the energy of system, which is a function of variables of state, and the change in the value of E, $\Delta E$ , is zero when the system proceeds from a given initial state to a final state and then returns to the initial state.

If $\Delta E$ were not zero, then energy must be either created or destroyed, contrary to the law of conservation of energy. The first law therefore requires that all the processes that take a closed system from the same initial state to the same final state cause the same change in energy, $\Delta E$ . Since $E$ is the starting function for the derivation of all the remaining thermodynamic properties designated as $\Lambda$ , it is also necessary that for any process, $\Delta \Lambda$ must also be the same, irrespective of what process is followed in making the same change in $\Lambda$ . For a closed system consisting of a single phase, $E$ may be taken as a function of any set of two variables out of $P$ , $V$ , and $T$ . We take $E$ as a function of $T$ and $V$ , i.e., $E = E(T, V)$ , and if the system is permitted to exchange only thermal energy $dQ$ and only the reversible work of expansion and compression $dW = -PdV$ , then the first law requires that

$$
\mathrm{dE} = \mathrm{dQ} - \mathrm{PdV}. \tag {1}
$$

This equation shows that indeed E is a function of V since dV appears on the right side as required by the properties of differential equations. It is possible to define a new function called the enthalpy, H, by adding the product PV to E so that $H \equiv E + PV$ . The total differential of H is

$$
\mathrm{dH} \equiv \mathrm{d} (\mathrm{E} + \mathrm{PV}) = \mathrm{dE} + \mathrm{PdV} + \mathrm{VdP} = \mathrm{dQ} + \mathrm{VdP}, \tag {2}
$$

where dE = dQ - PdV is substituted for dE to obtain the last equality. Note that one of the variables of state for dH is now P because dP appears as the differential on the right side. A process is defined by a specified path on the pressure-temperature coordinates that takes a given closed system from $(P_{1}, T_{1})$ to $(P_{2}, T_{2})$ , and for such a process the change in enthaply is written as

$$
\mathrm{H} \left(\mathrm{P} _ {2}, \mathrm{T} _ {2}\right) - \mathrm{H} \left(\mathrm{P} _ {1}, \mathrm{T} _ {1}\right) \equiv \Delta \mathrm{H}, \tag {3}
$$

where $\Delta$ always refers to the final state minus the initial state. At constant pressure, VdP = 0 in equation 2, and

$$
C _ {p} \equiv \left(\frac {\partial H}{\partial T}\right) _ {p}, \tag {4}
$$

where $C_{p}$ is called the heat capacity at constant pressure. [The heat capacity at constant volume, $C_{v} = (\partial E/\partial T)_{v}$ , is not used in this compilation because most of the chemical and metallurgical processes occur at constant pressure; hence, the "heat capacity" will always refer to $C_{p}$ .] The first law requires that for any process that takes the system from the initial state $(P_{1}, T_{1})$ to the final state $(P_{2}, T_{2})$ , $\Delta H$ must be the same because PV itself also represents a form of energy. When a pure substance melts or transforms from one crystalline state into another at a fixed temperature, then $C_{p}$ becomes infinity despite the fact that $\Delta H$ for such a transformation is finite.

The standard state is the state of pure elements and pure compounds in their stable states at 1 atm of pressure (1 atm = 101325 Pa). For the standard states, H and $C_{p}$ , are designated as $H^{\circ}$ and $C_{p}^{\circ}$ , which are both independent of pressure. The tabulated values of relative standard enthalpy $H^{\circ} - H_{298}^{\circ}$ can be fitted with a polynomial in T and then differentiated according to equation 4 to obtain $C_{p}^{\circ}$ as a function of T, or conversely, $C_{p}^{\circ}$ can be expressed as a function of T, and $dH^{\circ} = C_{p}^{\circ}dT$ can be integrated within the temperature range of existence of a stable phase to obtain $H^{\circ} - H_{298}^{\circ}$ as a function of T as follows:

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ} \equiv \int_ {2 9 8} ^ {\mathrm{T}} \mathrm{C} _ {\mathrm{P}} ^ {\circ} \mathrm{dT}. \tag {5}
$$

The subscript T in $H_{T}^{\circ}$ is for emphasis here in the text, and does not appear in the tables; thus, $H^{\circ}$ in the tables always refers to the listed temperatures. The results for $C_{p}^{\circ}$ and $H^{\circ}-H_{298}^{\circ}$ are tabulated usually in 200 K intervals in this compilation. (The dimensional units used for $C_{p}^{\circ}$ are calories per mol per kelvin, and for $H^{\circ}-H_{298}^{\circ}$ , calories per mol in the text but kilocalories per mol in the tables.)

Example 1: The data on $C_{p}^{\circ}$ for Mg(c), reproduced as table 1 here, can be fitted with the following equation by using the values of $C_{p}^{\circ}$ at 400 and 800 K.

$$
C _ {P} ^ {\circ} = 5. 1 2 + 0. 0 0 2 8 T. \tag {6}
$$

Application of equation 5 yields

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {8} = [ 5. 1 2 \mathrm{T} + 0. 0 0 1 4 \mathrm{T} ^ {2} ] _ {2 9 8} ^ {\mathrm{T}} = - 1 6 5 1 + 5. 1 2 \mathrm{T} + 0. 0 0 1 4 \mathrm{T} ^ {2}, \tag {7}
$$

where -1651 is the lower integration limit. For T = 800, $H_{800}^{\circ} - H_{298}^{\circ} = 3341$ , in very close agreement with the listed value based on polynomials in T. A simple three-term equation, used by Kelley (8) and later by Pankratz (11) in Bureau publications, is

$$
C _ {p} ^ {\circ} = a + 2 b T - \left(c / T ^ {2}\right), \tag {8}
$$

where $a, b$ , and $c$ are empirical constants. Integration of this equation yields

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ} = a \mathrm{T} + b \mathrm{T} ^ {2} + \frac {c}{\mathrm{T}} + \underline {{d}}, \tag {9}
$$

where $\underline{\mathbf{d}}$ is the integration constant for the right side, obtained by the substitution of 298.15 in the first three terms in equation 9; i.e., $-\underline{\mathbf{d}} = ax298.15 + bx298.15^2 + c/298.15$ .

# Enthalpy Change of Phase Transformation

A first-order phase transformation occurs at a fixed temperature and pressure under equilibrium conditions. Let $H_{T}^{\circ}$ , (I) and $H_{T}^{\circ}$ , (II) be the enthalpies of phase I and phase II, respectively, and let phase II be stable infinitesimally above the transformation temperature $T'$ . The transformation reaction is [phase I → phase II] for which $\Delta H_{T}^{\circ} = H_{T}^{\circ}$ , (II) - $H_{T}^{\circ}$ , (I). The standard enthalpy is tabulated as $H_{T}^{\circ} - H_{298}^{\circ}$ where $H_{298}^{\circ}$ refers to the pure elements or compounds stable or assumed to be stable at 298.15 K, and $H_{T}^{\circ}$ refers to any stable state at T, as in table 1. Let the stable phase for a substance be phase I from 298.15 to $T'$ , phase II from $T'$ to $T''$ , and phase III above $T''$ , and let $(298.15 < T' < T'' < T)$ . The standard enthalpy change upon heating to T, in reference to $H_{298}^{\circ}$ (I), is

TABLE 1. - Thermodynamic properties of Mg(c,l,g). $C_{p}^{\circ}$ and $S^{\circ}$ are in calories per mol per kelvin on the tables and in the text; however, $H^{\circ} - H_{298}^{\circ}$ is in kilocalories per mol on the tables but in calories per mol in the text.   
$\mathbf{Mg}(\mathbf{c},1,\mathbf{g})$   
Magnesium 

<table><tr><td>T</td><td> $Cp^o$ </td><td> $S^o$ </td><td> $H^o-H_2^o_{98}$ </td></tr><tr><td>298</td><td>5.950</td><td>7.810</td><td>0</td></tr><tr><td>400</td><td>6.240</td><td>9.600</td><td>.621</td></tr><tr><td>600</td><td>6.800</td><td>12.240</td><td>1.926</td></tr><tr><td>800</td><td>7.360</td><td>14.270</td><td>3.342</td></tr><tr><td>922</td><td>7.710</td><td>15.340</td><td>4.261</td></tr><tr><td>922</td><td>7.680</td><td>17.660</td><td>6.400</td></tr><tr><td>1000</td><td>7.880</td><td>18.290</td><td>7.010</td></tr><tr><td>1200</td><td>8.400</td><td>19.770</td><td>8.640</td></tr><tr><td>1363</td><td>8.820</td><td>20.860</td><td>10.040</td></tr><tr><td>1363</td><td>4.968</td><td>43.051</td><td>40.290</td></tr><tr><td>1400</td><td>4.968</td><td>43.185</td><td>40.474</td></tr><tr><td>1600</td><td>4.968</td><td>43.848</td><td>41.468</td></tr><tr><td>1800</td><td>4.968</td><td>44.433</td><td>42.461</td></tr><tr><td>2000</td><td>4.969</td><td>44.957</td><td>43.455</td></tr></table>

922 K, melting point; $\Delta H^{\circ} = 2.139$ 1363 K, boiling point; $\Delta H^{\circ} = 30.250$

TABLE 2. - Thermodynamic properties of Mg(g). $\Delta Hf^{\circ}$ and $\Delta Gf^{\circ}$ are in kilocalories per mol on the tables but in calories per mol in the text.   
Mg(g)
Magnesium (ideal monatomic gas) 

<table><tr><td>T</td><td> $Cp^{\circ}$ </td><td> $S^{\circ}$ </td><td> $H^{\circ}-H_{298}^{\circ}$ </td><td> $\Delta Hf^{\circ}$ </td><td> $\Delta Gf^{\circ}$ </td></tr><tr><td>298</td><td>4.968</td><td>35.501</td><td>0</td><td>35.000</td><td>26.744</td></tr><tr><td>400</td><td>4.968</td><td>36.961</td><td>.506</td><td>34.885</td><td>23.941</td></tr><tr><td>600</td><td>4.968</td><td>38.975</td><td>1.500</td><td>34.574</td><td>18.533</td></tr><tr><td>800</td><td>4.968</td><td>40.404</td><td>2.493</td><td>34.151</td><td>13.244</td></tr><tr><td>1000</td><td>4.968</td><td>41.513</td><td>3.487</td><td>31.477</td><td>8.254</td></tr><tr><td>1200</td><td>4.968</td><td>42.419</td><td>4.481</td><td>30.841</td><td>3.662</td></tr><tr><td>1400</td><td>4.968</td><td>43.185</td><td>5.474</td><td>0</td><td>0</td></tr><tr><td>1600</td><td>4.968</td><td>43.848</td><td>6.468</td><td>0</td><td>0</td></tr><tr><td>1800</td><td>4.968</td><td>44.433</td><td>7.461</td><td>0</td><td>0</td></tr><tr><td>2000</td><td>4.969</td><td>44.957</td><td>8.455</td><td>0</td><td>0</td></tr></table>

$$
\begin{array}{l} \mathrm{H} _ {\mathrm{T}} ^ {\circ} (\text {III}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{I}) \equiv \left[ \mathrm{H} _ {\mathrm{T}} ^ {\circ}, (\mathrm{I}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{I}) \right] + \Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ}, (\mathrm{I} \rightarrow \mathrm{II}) + \left[ \mathrm{H} _ {\mathrm{T} ^ {\prime \prime}} ^ {\circ} (\mathrm{II}) - \mathrm{H} _ {\mathrm{T} ^ {\prime}} ^ {\circ}, (\mathrm{II}) \right] \\ + \Delta H _ {T ^ {\prime \prime}} ^ {\circ} (I I \rightarrow I I I) + \left[ H _ {T} ^ {\circ} (I I I) - H _ {T ^ {\prime \prime}} ^ {\circ} (I I I) \right]. \tag {10} \\ \end{array}
$$

The alternative form of this equation is obtained by substituting equation 5 for the terms in the first, third, and fifth set of brackets:

$$
\begin{array}{l} \mathrm{H} _ {\mathrm{T}} ^ {\circ} (\text {III}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{I}) = \int_ {2 9 8} ^ {\mathrm{T} ^ {\prime}} \mathrm{C} _ {\mathrm{P}} ^ {\circ} (\mathrm{I}) \mathrm{dT} + \Delta \mathrm{H} _ {\mathrm{T} ^ {\prime}} ^ {\circ} (\mathrm{I} \rightarrow \mathrm{II}) + \int_ {\mathrm{T} ^ {\prime}} ^ {\mathrm{T} ^ {\prime \prime}} \mathrm{C} _ {\mathrm{P}} ^ {\circ} (\mathrm{II}) \mathrm{dT} \\ + \Delta \mathrm{H} _ {\mathrm{T} ^ {\prime \prime}} ^ {\circ} (\mathrm{II} \rightarrow \mathrm{III}) + \int_ {\mathrm{T} ^ {\prime \prime}} ^ {\mathrm{T}} \mathrm{C} _ {\mathrm{p}} ^ {\circ} (\mathrm{III}) \mathrm{dT}. \tag {11} \\ \end{array}
$$

For substances such as $\mathrm{Mg(g)}$ , $\mathrm{S}_2(\mathrm{g})$ , and $\mathrm{H}_2\mathrm{O}(\mathrm{g})$ , $\mathrm{H}_{298}^{\circ}$ refers to the gaseous state instead of the more stable condensed states below their boiling points. The tables indicate that the reference state is the "ideal gas," with the superscript $(^{\circ})$ specifying 1 atm standard state, as shown in table 2. Tables are also included in which $\mathrm{H}_{298}^{\circ}$ refers to the stable phases of these substances, e.g., crystalline magnesium, $\mathrm{Mg(c)}$ , crystalline sulfur, $\mathrm{S(c)}$ , and liquid water, $\mathrm{H}_2\mathrm{O}(1)$ .

Example 2: The $C_{p}^{o}$ data for Mg(c) in table 1 was fitted with equation 6 by using the values of $C_{p}^{o}$ at 400 and 800 K and repeated here for convenience:

$$
C _ {p} ^ {\circ} (c) = C _ {p} ^ {\circ} (I) = 5. 1 2 + 0. 0 0 2 8 T. \tag {12}
$$

For the melting process at 922 K, $\Delta Hg_{22}(c\rightarrow1)=\Delta H_{T}^{\circ}(I\rightarrow II)=2139\ \text{cal/mol}$ , and $C_{p}^{\circ}(1)$ for the liquid can be closely represented by

$$
C _ {p} ^ {\circ} (1) = C _ {p} ^ {\circ} (I I) = 5. 2 8 + 0. 0 0 2 6 T. \tag {13}
$$

For the boiling process at 1363 K, $\Delta H_{1363}^{\circ}(1\rightarrow g) = \Delta H_{T}^{\circ}((II\rightarrow III) = 30250$ , and $C_{p}^{\circ}(g)$ for the gas is

$$
C _ {p} ^ {\circ} (g) = C _ {p} ^ {\circ} (I I I) = 4. 9 6 8. \tag {14}
$$

Substitution of these equations in equation 11 yields

$$
\begin{array}{l} \mathrm{H} _ {\mathrm{T}} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {2 9 8} ^ {8} (\mathrm{c}) = [ 5. 1 2 \mathrm{T} + 0. 0 0 1 4 \mathrm{T} ^ {2} ] _ {\mathrm{T} = 2 9 8} ^ {\mathrm{T} ^ {\prime} = 9 2 2} + 2 1 3 9 \\ + [ 5. 2 8 \mathrm{T} + 0. \dot {0} 0 1 3 \mathrm{T} ^ {2} ] _ {\mathrm{T} ^ {\prime} = 9 2 2} ^ {\mathrm{T} ^ {\prime \prime} = 1 3 6 3} + 3 0 2 5 0 + [ 4. 9 6 8 \mathrm{T} ] _ {\mathrm{T} ^ {\prime \prime} = 1 3 6 3} ^ {\mathrm{T}} \\ = 3 3 5 1 6 + 4. 9 6 8 \mathrm{T}. \tag {15} \\ \end{array}
$$

At 2000 K, $H_{2000}^{\circ}(g) - H_{298}^{\circ}(c) = 43452$ from the preceding equation, in nearly perfect agreement with 43455 in table 1. For the liquid phase region, where $(922 < T < 1363)$ , equation 11 needs to be terminated after the second integral, i.e.,

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} (1) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = 4 2 6 + 5. 2 8 \mathrm{T} + 0. 0 0 1 3 \mathrm{T} ^ {2}. \tag {16}
$$

At 1000 K, $H_{1000}^{\circ}(1) - H_{298}^{\circ}(c) = 7006$ , which is in very close agreement with 7010 listed in table 1. Note that $[H_{T}^{\circ}(1) - H_{298}^{\circ}(c)]$ , used here, does not appear in table 1 with (1) and (c), but the $\mathrm{Mg}(c,1,g)$ in the table title signifies that $H_{298}^{\circ}$ refers to $H_{298}^{\circ}(c)$ , and $H^{\circ}$ refers to the stable phase at the listed temperatures. Table 2 is entirely for $\mathrm{Mg}(g)$ (gaseous magnesium) from 298.15 to 2000 K. For gaseous $Mg, C_{p}^{\circ} = 4.968$ ; therefore,

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{g}) = - 1 4 8 1 + 4. 9 6 8 \mathrm{T}. \tag {17}
$$

Subtraction of equation (17) from equation (15) yields

$$
\mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = 3 4 9 9 7 \approx 3 5 0 0 0, \tag {18}
$$

where 35000 is the enthalpy of sublimation of Mg(c), or in terms of the notation of table 2 for Mg(g), the standard enthalpy of formation of Mg(g) from the stable phase Mg(c) at 298.15 K. This example is dealt with again in the next section.

# Thermochemistry

Thermochemistry deals with the energy and the enthalpy changes accompanying chemical reactions and phase transformations. In fact, phase transformations can be considered as reactions in thermodynamics. A selected reaction and the accompanying $\Delta H_{298}^{\circ}$ at constant pressure are as follows:

$$
\mathrm{C} (\mathrm{gr}) + \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} _ {2} (\mathrm{g}); \quad \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = - 9 4 0 5 1 \mathrm{cal/mol} \mathrm{CO} _ {2}. \tag {19}
$$

Throughout the remainder of this publication, $\Delta$ refers to a phase transformation or a chemical reaction, not to the heating and cooling processes; e.g., henceforth, to avoid confusion, $\Delta$ is not used to denote $\Delta H^{\circ} = H_{T}^{\circ} - H_{298}^{\circ}$ . Reaction 19 in thermochemistry is assumed to convert the reactants entirely into the product(s), which is only $\mathrm{CO}_{2}(\mathrm{g})$ in this example. All of the reactants and the products are in their standard states, signifying that C(gr) is the pure element in its stable state at 1 atm pressure and the gases are ideal, each at 1 atm pressure. Reaction 19 is the formation of $\mathrm{CO}_{2}$ from its elements in their standard states; hence, $\Delta H_{298}^{\circ}$ of equation 19 is also the standard enthalpy of formation of $\mathrm{CO}_{2}$ , which is denoted by $\Delta Hf_{298}^{\circ}$ .

For the pure elements in their stable standard states, $\Delta \mathrm{Hf}^{\circ}$ is obviously zero. The enthalpy of reaction at any temperature is calculated by combining the enthalpies of formation; thus, at 298.15 K,

$$
\mathrm{C} (\mathrm{gr}) + \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} _ {2} (\mathrm{g}); \Delta \mathrm{Hf} _ {2 9 8} ^ {8} = - 9 4 0 5 1 \tag {20}
$$

$$
C (g r) + 0. 5 O _ {2} (g) = C O (g); \Delta H f _ {2 9 8} ^ {0} = - 2 6 4 1 7 \tag {21}
$$

$$
\mathrm{CO} (\mathrm{g}) + 0. 5 \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} _ {2} (\mathrm{g}); \Delta \mathrm{H} _ {2 9 8} ^ {8} = - 9 4 0 5 1 + 2 6 4 1 7 \tag {22}
$$

$$
= - 6 7 6 3 4.
$$

The last reaction is obtained by subtracting the second reaction from the first; therefore, $\Delta Hf^{\circ}$ is often written underneath each reactant and product for convenience in calculating $\Delta Hf^{\circ}$ :

$$
\mathrm{CO} (\mathrm{g}) + 0. 5 \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} _ {2} (\mathrm{g}),
$$

$$
- 2 6 4 1 7 \quad 0. 0 \quad - 9 4 0 5 1; \quad \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = - 9 4 0 5 1 - (- 2 6 4 1 7) = - 6 7 6 3 4 \mathrm{cal}. \tag {23}
$$

The same procedure can be followed for calculating $\Delta H^{\circ}$ at 1000 K by using the tables for CO and $CO_{2}$ to obtain

$$
\Delta \mathrm{H} \text {I} 0 0 0 = - 9 4 3 1 2 + 2 6 7 6 6 = - 6 7 5 4 6. \tag {24}
$$

The effect of temperature on $\Delta H_{T}^{\circ}$ can be calculated from the following identity:

$$
\Delta \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) \equiv \Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ} - \Delta \mathrm{H} _ {2 9 8} ^ {\circ}, \tag {25}
$$

where the left side is obtained from the tables, in the columns for $H_{T}^{\circ} - H_{298}^{\circ}$ . Thus, for $\mathrm{CO(g)} + 0.5 \mathrm{O}_{2}(\mathrm{g}) = \mathrm{CO}_{2}(\mathrm{g})$ , equation 25 is

$$
\begin{array}{l} \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for} \mathrm{CO} _ {2}) - \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for} \mathrm{CO}) \\ - 0. 5 \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text { for } 0 _ {2}) \equiv \Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ} - \Delta \mathrm{H} _ {2 9 8} ^ {\circ}. \tag {26} \\ \end{array}
$$

This equation is rearranged to obtain

$$
\begin{array}{l} \Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ} = \Delta \mathrm{H} _ {2 9 8} ^ {\circ} + \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for CO} _ {2}) - \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for CO}) \\ - 0. 5 \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text { for } \mathrm{O} _ {2}). \tag {27} \\ \end{array}
$$

At T = 1000 K, $H_{1000}^{\circ} - H_{298}^{\circ} = 7984$ for $CO_{2}$ ; $H_{1000}^{\circ} - H_{298}^{\circ} = 5183$ for CO; $0.5(H_{1000}^{\circ} - H_{298}^{\circ}) = 2713$ for 0.5 $O_{2}$ ; $\Delta H_{298}^{\circ} = -67634$ ; hence,

$$
\Delta \mathrm{H} _ {1 0 0 0} ^ {\circ} = - 6 7 6 3 4 + 7 9 8 4 - 5 1 8 3 - 2 7 1 3 = - 6 7 5 4 6, \tag {28}
$$

which is evidently identical with the result in equation 24.

Equation 27 can be generalized as follows:

$$
\begin{array}{l} \Delta H _ {T} ^ {\circ} = \Delta H _ {2 9 8} ^ {\circ} + \sum \alpha_ {i} \left(H _ {T} ^ {\circ} - H _ {2 9 8} ^ {\circ}\right) (f o r i) \\ \begin{array}{c} \text {all} \\ \text {products(i)} \end{array} \\ - \sum \beta_ {j} \left(H _ {T} ^ {\circ} - H _ {2 9 8} ^ {\circ}\right) (\text { for   } j). \tag {29} \\ \begin{array}{c} \text {all} \\ \text {reactants(j)} \end{array} \\ \end{array}
$$

The factors $\alpha_{i}$ and $\beta_{j}$ are the stoichiometric coefficients. This equation may be

rewritten by using equation 5 when there is no phase transformation for the reactants and the products from 298.15 to T; thus,

$$
\Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ} = \Delta \mathrm{H} _ {2 9 8} ^ {8} + \int_ {2 9 8} ^ {\mathrm{T}} \left[ \sum_ {\mathrm{i}} \alpha_ {\mathrm{i}} \mathrm{C} _ {\mathrm{p}} ^ {\circ} (\mathrm{i}) - \sum_ {\mathrm{j}} \beta_ {\mathrm{j}} \mathrm{C} _ {\mathrm{p}} ^ {\circ} (\mathrm{j}) \right] \mathrm{dT} \equiv \int_ {2 9 8} ^ {\mathrm{T}} \Delta \mathrm{C} _ {\mathrm{p}} ^ {\circ} \mathrm{dT}, \tag {30}
$$

where the identity defines $\Delta C_p^\circ$ , which is the sum of $\alpha_i C_p^\circ(i)$ for the products minus the sum of $\beta_j C_p^\circ(j)$ for the reactants. Note that the last equality is the integral of another form of equation 4, which may be written as

$$
\frac {d \Delta H ^ {\circ}}{d T} = \Delta C _ {p} ^ {\circ}; \quad \Delta H _ {T} ^ {\circ} - \Delta H _ {2 9 8} ^ {\circ} = \int_ {2 9 8} ^ {T} \Delta C _ {p} ^ {\circ} d T. \tag {31}
$$

Since $H^{\circ}$ is independent of pressure, $\partial$ is replaced by d in equation 4. Equations 29 and 31, in their appropriately modified forms, constitute the basis for calculation of temperature dependence for numerous thermodynamic properties, e.g.,

$$
\Delta S _ {T} ^ {\circ} = S _ {2 9 8} ^ {\circ} + \int_ {2 9 8} ^ {T} \left(\frac {d \Delta S ^ {\circ}}{d T}\right) d T = \Delta S _ {2 9 8} ^ {\circ} + \int_ {2 9 8} ^ {T} \frac {\Delta C ^ {\circ}}{T} d T, \tag {32}
$$

where $d\Delta S^{\circ} = (\Delta C_{p}^{\circ}/T)dT$ is defined in the next section, "The Second Law of Thermodynamics."

Application of equation 31, or the last equality in equation 30, to reaction 23 requires empirically expressed equations for $C_{p}^{\circ}$ as functions of temperature. The values of $C_{p}^{\circ}$ for $CO_{2}$ vary fairly strongly with temperature; therefore, a quadratic equation for $C_{p}^{\circ}$ is obtained by using the tabulated data for $C_{p}^{\circ}$ at 298.15, 600, and 1000 K, but for CO and $O_{2}$ , linear equations using the tabulated $C_{p}^{\circ}$ at 400 and 1000 K are adequate. The results are

$$
C _ {p} ^ {\circ} \left(\mathrm{CO} _ {2}\right) = 5. 4 8 6 + 0. 0 1 3 0 3 \mathrm{T} - 5. 5 3 1 \times 1 0 ^ {- 6} \mathrm{T} ^ {2} \tag {33}
$$

$$
- C _ {P} ^ {\circ} (C O) = - 6. 4 0 1 - 0. 0 0 1 5 3 T \tag {34}
$$

$$
- 0. 5 \mathrm{C} _ {\mathrm{p}} ^ {\circ} \left(\mathrm{O} _ {2}\right) = - 3. 2 1 9 - 0. 0 0 0 9 5 \mathrm{T} \tag {35}
$$

$$
\Delta C _ {p} ^ {\circ} = - 4. 1 3 4 + 0. 0 1 0 5 5 T - 5. 5 3 1 \times 1 0 ^ {- 6} T ^ {2}. \tag {36}
$$

It should be emphasized that the equations for $C_{p}^{\circ}$ are valid roughly from 298 to 1000 K. Substitution of $\Delta C_{p}^{\circ}$ of equation 36 in equation 30, integration of the result, and substitution of $\Delta H_{298}^{\circ} = -67634$ yield

$$
\Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ} = - 6 6 8 2 2 - 4. 1 3 4 \mathrm{T} + 0. 0 0 5 2 7 5 \mathrm{T} ^ {2} - 1. 8 4 4 \mathrm{x} 1 0 ^ {- 6} \mathrm{T} ^ {3}. \tag {37}
$$

From this equation, $\Delta H_{000}^{\circ} = -67525$ which is very close to -67546 previously obtained from the tables for CO and $\mathrm{CO}_{2}$ . Actually, $\Delta C_{p}^{\circ}$ is small for most reactions involving only gaseous inorganic reactants and products, and $\Delta H_{T}^{\circ}$ usually varies weakly with temperature.

# Adiabatic Flame Temperature

When the combustion of a substance at 298.15° K gives the products also at 298.15° K, the resulting effect is $\Delta H_{298}^{\circ}$ of reaction. Since the value of $\Delta H_{298}^{\circ}$ for combustion is negative, or $\Delta H_{298}^{\circ}$ is dissipated to the surroundings, it is possible to devise an adiabatic process by returning $\Delta H_{298}^{\circ}$ into the combustion products to raise their temperature:

$$
- \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = \sum_ {\text {products}} \alpha_ {i} \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) = \int_ {2 9 8} ^ {\mathrm{T}} \sum_ {\text {products}} \alpha_ {i} \mathrm{C} _ {\mathrm{pi}} ^ {\circ} \mathrm{dT}. \tag {38}
$$

The resulting temperature, T, is called the adiabatic flame temperature, which can be determined either from the tabular values of $H_{T}^{\circ} - H_{298}^{\circ}$ or by integrating the sum of the heat capacities of products only as shown in equation 38 when there are no phase transitions.

Example 3: Calculate the adiabatic flame temperature for CO burned in a theoretically sufficient amount of air. For simplicity, assume that air is 0.2 mol fraction $O_{2}$ and 0.8 mol fraction $N_{2}$ ; use $\Delta H_{298}^{\circ} = -67634$ for the enthalpy of combustion of CO. (Actual composition of air is closer to 0.21 mol fraction of $O_{2}$ ; this example is adapted from reference 10.)

Solution: One mol of CO requires 2.5 mols of air in which 2 mols are $N_{2}$ . The combustion products are 1 mol of $CO_{2}$ and 2 mols of $N_{2}$ . Equation 38 for this case is

$$
- \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = + 6 7 6 3 4 = \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for} \mathrm{CO} _ {2}) + 2 \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\text {for} \mathrm{N} _ {2}). \tag {39}
$$

The right side can be calculated from the tables for $CO_{2}$ and $N_{2}$ at temperatures estimated to be close to the adiabatic temperature:

$$
\begin{array}{l} 2 2 0 0 \mathrm{K}: 2 4 7 5 5 + 2 \mathrm{x} 1 5 1 4 4 = 5 5 0 4 3, \\ 2 4 0 0 \mathrm{K}: 2 7 6 7 4 + 2 \mathrm{x} 1 6 8 8 3 = 6 1 4 4 0, \\ 2 6 0 0 \mathrm{K}: \quad 3 0 6 1 3 + 2 \mathrm{x} 1 8 6 3 4 = 6 7 8 8 1, \\ 2 8 0 0 \mathrm{K}: 3 3 5 6 7 + 2 \mathrm{x} 2 0 3 9 3 = 7 4 3 5 3. \\ \end{array}
$$

It is evident that the required temperature is between 2400 and 2600 K. Assuming that the right side of equation 39 varies linearly with temperature in this range, the linear interpolation yields T = 2592 K.

An alternative method is to express $H_{T}^{\circ}-H_{298}^{\circ}$ as a quadratic function of T for $CO_{2}$ and $N_{2}$ in the range of 2000 to 3000 K and solve for T. Thus, if we use the results for 2200, 2600, and 2800 K in a quadratic equation, we obtain the following equation for the right-hand side (RHS) of equation 39:

$$
\mathrm{RHS} = - 1 3 0 4 0 + 2 9. 9 7 5 \mathrm{T} + 4. 4 1 7 \times 1 0 ^ {- 4} \mathrm{T} ^ {2}. \tag {40}
$$

Equation 39 now becomes, after rearrangement,

$$
4. 4 1 7 \mathrm{x} 1 0 ^ {- 4} \mathrm{T} ^ {2} + 2 9. 9 7 5 \mathrm{T} - 8 0 6 7 4 = 0, \tag {41}
$$

from which, T = 2592, in agreement with the preceding computation.

Example 4: Recompute the preceding example but with the air preheated to 1000 K prior to combustion.

Solution: The procedure is simplified by cooling the air to 298.15 K, extracting the corresponding $\Delta H_{cool}^{\circ}$ and adding it to -67634, and using $-(\Delta H_{cool}^{\circ} - 67634)$

for the left side of equation 39. For the air consisting of 2 mols of $N_{2}$ and 0.5 mol of $O_{2}$ , cooling from 1000 to 298.15 K yields

$$
\Delta \mathrm{H} _ {\text {cool}} ^ {\circ} = - 2 \mathrm{x} 5 1 3 0 - 0. 5 \mathrm{x} 5 4 2 6 = - 1 2 9 7 3 \text {cal}, \tag {42}
$$

and equation 39 becomes

$$
- (- 6 7 6 3 4 - 1 2 9 7 3) = - 1 3 0 4 0 + 2 9. 9 7 5 \mathrm{T} + 4. 4 1 7 \times 1 0 ^ {- 4} \mathrm{T} ^ {2}, \tag {43}
$$

from which T = 2992 K. Thus, preheating the air from 298.15 to 1000 K, increases the adiabatic flame temperature by 400 K. This is important from the practical point of view because a process may not be possible unless an appropriate temperature is attained.

# The Second Law of Thermodynamics

The second law of thermodynamics is stated as follows:

It is impossible to construct a system that can convert heat from a uniform temperature into mechanical energy without leaving any effect elsewhere.

It should be emphasized that dQ in equation 1 is not a property of any system. However, the second law and the Carnot theorem lead to the existence of a property called the entropy, which is given by

$$
\mathrm{dS} = \frac {\mathrm{dQ} (\text {reversible})}{\mathrm{T}} = \frac {\mathrm{dE} + \mathrm{PdV}}{\mathrm{T}}. \tag {44}
$$

The proof of the existence of entropy is beyond the scope of this section (see references 1, 3, 6, and 9). Rearrangement of this equation yields the combined expression of the first and second laws of thermodynamics:

$$
\mathrm{dE} = \mathrm{TdS} - \mathrm{PdV}. \tag {45}
$$

The independent variables in this equation are S and V; i.e., E is a function of S and V because dS and dV appear on the right side. For $H \equiv E + PV$ , equation 45 becomes

$$
\mathrm{dH} = \mathrm{TdS} + \mathrm{VdP}; (\mathrm{dH}) _ {\mathrm{p}} = \mathrm{T} (\mathrm{dS}) _ {\mathrm{p}}, \tag {46}
$$

where the second equation is for the constant pressure condition, and if the system is in the standard state for an element or a compound at 1 atm, then $dH^{\circ} = TdS^{\circ}$ , and both $H^{\circ}$ and $S^{\circ}$ are only dependent on temperature for a given closed phase. Summation of $dS^{\circ} = dH^{\circ}/T$ over small ranges of temperature yields the change in entropy:

$$
\sum_ {2 9 8} ^ {T} d S ^ {\circ} = S _ {T} ^ {\circ} - S _ {2 9 8} ^ {\circ} = \sum_ {2 9 8} ^ {T} \frac {d H ^ {\circ}}{T}. \tag {47}
$$

For a first order phase transformation, e.g., phase I → phase II at a constant temperature, T', the sum of dH° is $\Delta H_{T'}^{\circ}$ , and $S_{T'}^{\circ}$ (phase II) - $S_{T'}^{\circ}$ (phase I) = $\Delta H_{T'}^{\circ}$ (I→II)/T'. In a range of temperature where there is no phase transformation, the summation in equation 47 can be replaced with integrals. For this purpose, we rearrange equation 4 at P = 1 atm to write dH° = $C_{p}^{\circ}$ dT and equation 47 as

$$
S _ {T} ^ {\circ} - S _ {2 9 8} ^ {\circ} = \int_ {2 9 8} ^ {T} \frac {C _ {p} ^ {\circ}}{T} d T. \tag {48}
$$

Computation of $S_{T}^{\circ} - S_{298}^{\circ}$ follows the same pattern as the computation of $H_{T}^{\circ} - H_{298}^{\circ}$ shown by equation 11. Thus, for only one phase transition, phase I → phase II at $T'$ [T > T' > 298.15], the change in entropy from 298.15 to T is given by

$$
\begin{array}{l} S _ {T} ^ {\circ} (\text { phase   II }) - S _ {2 9 8} ^ {\circ} (\text { phase   I }) = \int_ {2 9 8} ^ {T ^ {\prime}} \frac {C _ {p} ^ {\circ} (\text { phase   I })}{T} d T + \frac {\Delta H ^ {\circ} (I \rightarrow I I)}{T ^ {\prime}} \\ + \int_ {T ^ {\prime}} ^ {T} \frac {C _ {p} ^ {\circ} (\text { phase   II })}{T} d T. \tag {49} \\ \end{array}
$$

The standard entropy, $S^{\circ}$ , like $C_{p}^{\circ}$ , is expressed in calories per mol per kelvin throughout this publication.

Example 5: Express $S_{T}^{\circ}(1) - S_{298}^{\circ}(c)$ as a function of temperature for Mg, by using the data in example 2.

Solution: For $\mathrm{Mg(c)}$ , equation 12 gives $C_p^\circ(c)$ , and for $c \to 1$ transformation, $\Delta H^\circ(c \to 1) = \Delta H_T^\circ$ , = 2139 cal/mol with $T' = 922$ ; for $\mathrm{Mg(1)}$ , equation 13 gives $C_p^\circ(1)$ . Substitution of these results in equation 49 gives

$$
\begin{array}{l} \mathrm{S} _ {\mathrm{T}} ^ {\circ} (1) - \mathrm{S} _ {2 9 8} ^ {\circ} (\mathrm{c}) = \int_ {2 9 8} ^ {9 2 2} \left[ \frac {5 . 1 2}{\mathrm{T}} + 0. 0 0 2 8 \right] \mathrm{dT} + \frac {2 1 3 9}{9 2 2} + \int_ {9 2 2} ^ {\mathrm{T}} \left[ \frac {5 . 2 8}{\mathrm{T}} + 0. 0 0 2 6 \right] \mathrm{dT} \\ = 5. 1 2 \ln \frac {9 2 2}{2 9 8 . 1 5} + 0. 0 0 2 8 (9 2 2 - 2 9 8. 1 5) + \frac {2 1 3 9}{9 2 2} + 5. 2 8 \ln \frac {\mathrm{T}}{9 2 2} \\ + 0. 0 0 2 6 (T - 9 2 2) = 5. 2 8 \ln T + 0. 0 0 2 6 T - 2 8. 5 9 4. \tag {50} \\ \end{array}
$$

At 1000 K, this equation yields $S_{1000}^{\circ} - S_{298}^{\circ} = 10.479$ , and the table for $\mathrm{Mg}(c,1,g)$ (table 1) gives $S_{1000}^{\circ} - S_{298}^{\circ} = 18.290 - 7.810 = 10.480$ , in very close agreement with equation 50. The results in the table are based on the $C_{p}^{\circ}$ equations that contain more than two terms.

# The Third Law of Thermodynamics

The third law of thermodynamics is best stated as follows:

The entropy of each pure element or pure compound in its stable molecular configurations is zero at zero kelvin.

This law permits calculation of entropy at any temperature because $S_{0}^{\circ}$ (at zero kelvin) = 0. The values of S298 listed in this compilation have been obtained by using a relationship of the type shown by equation 49 from 0 to 298.15 K. The third law also requires that $C_{p}^{\circ}/T$ approach zero faster than T approach zero (6).

The equations based on the second and third laws of thermodynamics are obtained by simple changes of independent variables. It was already noted that $E = E(S, V)$ , or E is a function of independent variables S and V as shown in equation 45. If we write a new function, $A \equiv E - TS$ , differentiate it, and then use equation 45 on the right side, we obtain $dA = -SdT - PdV$ , and A is called the Helmholtz energy. A more useful equation is obtained by using the definition $G \equiv H - TS$ , differentiation of this equation, and substitution of equation 46; the result is

$$
\mathrm{dG} \equiv \mathrm{dH} - \mathrm{TdS} - \mathrm{SdT} = \mathrm{VdP} - \mathrm{SdT}. \tag {51}
$$

It is important to observe that the independent variables in this equation are P and T, namely, $G = G(P, T)$ , and these variables are more convenient than the independent variables for E, H, and A. The change of variables accomplished in this manner is a very simple form of Legendre transforms in mathematics. Integration of equation 51 at a given temperature for 1 mol of gas obeying PV = RT is

$$
G (P, T) - G \left(P _ {1}, T\right) = + \int_ {P _ {1}} ^ {P} R T \frac {d P}{P} = - R T \ln P _ {1} + R T \ln P. \tag {52}
$$

If $P_{1}$ is set to unity, then $G(1,T)$ is designated as $G^{\circ} = G^{\circ}(T)$ and equation 52 becomes

$$
G (P, T) = G ^ {\circ} + R T \ln P, \tag {53}
$$

where $G = G(P, T)$ for a pure substance has been called the chemical potential by J. W. Gibbs, and renamed the Gibbs energy, first in Europe. The term "chemical potential" arises from the experimental observation that a pure gas "B" tends to go from a confining chamber Y, with a membrane permeable to B, into an adjoining chamber Z, containing a mixture of gases, if G(pure B in chamber Y) is greater than G(B in chamber Z); further, when equilibrium is attained, then G of B in both chambers becomes equal. Another example is the vaporization process, i.e., $\text{Mg}(1) \rightarrow \text{Mg}(g)$ , for which $G(1) = G^{\circ}(1)$ if the liquid is pure, and for $G(g)$ of the gas we write $G(g) = G^{\circ}(g) + \text{RTlnP}$ ; at equilibrium $G^{\circ}(1) = G(g)$ ; hence,

$$
G (1) \equiv G ^ {\circ} (1) = G ^ {\circ} (g) + R T \ln P. \tag {54}
$$

Rearrangement of this equation gives

$$
G ^ {\circ} (g) - G ^ {\circ} (1) \equiv \Delta G ^ {\circ} = - R T 1 n P. \tag {55}
$$

The left side of this equation is for the equilibrium vaporization of Mg(1);

$$
\mathrm{Mg} (1) = \mathrm{Mg} (\mathrm{g}). \tag {56}
$$

From the definition of $G^{\circ}$ as $G^{\circ} = H^{\circ} - TS^{\circ}$ and from the fact that reaction 56 is considered to take place at a constant temperature, $\Delta G^{\circ}$ is related to $\Delta H^{\circ}$ and $\Delta S^{\circ}$ by

$$
\Delta G ^ {\circ} = \Delta H ^ {\circ} - T \Delta S ^ {\circ}. \tag {57}
$$

This equation is very important in thermodynamics. An identical argument for the decomposition of limestone yields

$$
\mathrm{CaCO} _ {3} (\mathrm{c}) = \mathrm{CaO} (\mathrm{c}) + \mathrm{CO} _ {2} (\mathrm{g}); \Delta \mathrm{G} ^ {\circ} = - \mathrm{RTlnP} \left(\mathrm{CO} _ {2}\right). \tag {58}
$$

For a reaction in which some of the stoichiometric coefficients are different from unity, the corresponding $\Delta G^{\circ}$ must contain the stoichiometric coefficients, e.g.,

$$
\mathrm{N} _ {2} (\mathrm{g}) + 3 \mathrm{H} _ {2} (\mathrm{g}) = 2 \mathrm{NH} _ {3}, \tag {59}
$$

for which $\Delta G^{\circ}$ is given by

$$
2 G ^ {\circ} \left(\mathrm{NH} _ {3}\right) - G ^ {\circ} \left(\mathrm{N} _ {2}\right) - 3 G ^ {\circ} \left(\mathrm{H} _ {2}\right) \equiv \Delta G ^ {\circ} = - \mathrm{RTln} \frac {\left[ \mathrm{P} \left(\mathrm{NH} _ {3}\right) \right] ^ {2}}{\mathrm{P} \left(\mathrm{N} _ {2}\right) \left[ \mathrm{P} \left(\mathrm{H} _ {2}\right) \right] ^ {3}} \dots \tag {60}
$$

The terms after the logarithmic notations in 55, 58, and 60 are called the equilibrium constant $K_{p}$ in terms of the partial pressures (or fugacities when they are available). Thus, $K_{p} = P$ in equation 55, $K_{p} = P(CO_{2})$ in equation 58, and $K_{p} = [P(NH_{3})]^{2}/P(N_{2})[P(H_{2})]^{3}$ in equation 60 at sufficiently low total pressures, e.g., 1 or 2 atm, where $P(NH_{3})$ , ..... are the partial pressures. (See, e.g., reference 6 for a treatment using the fugacities at high pressures.)

The numerical values of $\Delta G^{\circ}$ as well as $\Delta H^{\circ}$ in the examples are in calories per mol or calories per reaction. This convention is essential when $\Delta S^{\circ}$ is in calories per mol per kelvin because $T\Delta S^{\circ}$ is then in calories per mol and $\Delta H^{\circ}$ must therefore also be in calories per mol for the dimensional consistency in equation 57.

Example 6: Calculate the vapor pressure of Mg(g) over Mg(1) at 1000 K and 1400 K.

Solution: The reaction and the equilibrium relationships are given by equations 56 and 55, respectively. Table 2 for Mg(g) gives $\Delta G^{\circ} = 8254$ at 1000 K; therefore,

$$
\Delta G ^ {\circ} = 8 2 5 4 = - 1 0 0 0 \mathrm{RlnP}, \tag {61}
$$

from which P = 0.0157 atm = 1591 Pa. If it is necessary to compute the pressure above the boiling point, e.g. at 1400 K, the values for Mg(1) in table 1 must be extrapolated beyond the range where the liquid is stable at pressures higher than 1 atm. In general, $\Delta G^{\circ}$ is close to being a linear function of T at temperatures usually above 1000 K, within experimental errors in a small range of temperature, e.g., $\pm100$ K, and often $\pm200$ K. To obtain an equation for $\Delta G^{\circ}$ , we take $\Delta G^{\circ} = 3662$ cal at 1200 K and $\Delta G^{\circ} = 0$ at 1363 K, the boiling point of Mg(1) where P = 1 atm, and write

$$
3 6 6 2 = \Delta H ^ {\circ} - 1 2 0 0 \Delta S ^ {\circ}; \tag {62}
$$

$$
0 = \Delta H ^ {\circ} - 1 3 6 3 \Delta S ^ {\circ}. \tag {63}
$$

We solve for $\Delta H^{\circ}$ and $\Delta S^{\circ}$ to obtain

$$
\Delta G ^ {\circ} = 3 0 6 2 2 - 2 2. 4 6 6 \mathrm{T} = - \mathrm{RTlnP}, \tag {64}
$$

where $\Delta H^{\circ}$ and $\Delta S^{\circ}$ are assumed to be temperature independent within the small range of 1200 to 1400 K. Actually $\Delta H^{\circ}$ and $\Delta S^{\circ}$ do vary slightly with temperature, but the assumption that they are constant affects the values of $\Delta G^{\circ}$ to a small extent, owing to the partial cancellation of errors between $\Delta H^{\circ}$ and $-T\Delta S^{\circ}$ . At 1400 K, this equation yields P = 1.348 atm.

Example 7: Calculate $\Delta G^{\circ}$ and the equilibrium constant for the following reaction at 1000 and 2000 K:

$$
\mathrm{CO} (\mathrm{g}) + 0. 5 \mathrm{O} _ {2} (\mathrm{g}) = \mathrm{CO} _ {2} (\mathrm{g}). \tag {65}
$$

Solution: The values of $\Delta G^{\circ}$ can be written under the reactants and the products to obtain $\Delta G^{\circ}$ for the reaction, in the same way as in equations 23 and 24; thus the values of $\Delta Gf^{\circ}$ for the required temperatures are as follows:

<table><tr><td rowspan="2">T, K</td><td colspan="3"> $\Delta Gf^{\circ}$ </td><td rowspan="2"> $\Delta G^{\circ}$ (reaction 65)</td><td rowspan="2"> $K_p$ </td></tr><tr><td>CO</td><td>0.5  $O_2$ </td><td> $CO_2$ </td></tr><tr><td>1000</td><td>-47857</td><td>*0.0</td><td>-94619</td><td>-46762</td><td> $1.66x10^{10}$ </td></tr><tr><td>2000</td><td>-68343</td><td>0.0</td><td>-94728</td><td>-26385</td><td>764</td></tr></table>

$^{*}\Delta Gf^{\circ}$ is zero for the elements in their standard states.

It must be recalled that the standard state is the pure stable element or compound at 1 atm pressure. For gases, the standard state is the pure gas whose properties are extrapolated from very low pressures to 1 atm so that the standard state is an ideally behaving gas at 1 atm pressure. The symbol $\Delta Gf^{\circ}$ refers to the standard Gibbs energy of formation of a compound in its standard state from the elements in their standard states; hence, it is obvious that $\Delta Gf^{\circ}$ is zero for the elements in their standard states.

An alternative, longer method for calculating $\Delta G^{\circ}$ at 1000 K is illustrated by the following example. First, $\Delta H_{1000}^{\circ} = -67546$ is calculated as in equation 24. Second, the value of $\Delta S_{1000}^{\circ}$ for the reaction is obtained by the same procedure, i.e.,

$$
\begin{array}{l} \Delta S _ {1 0 0 0} ^ {\circ} = S _ {1 0 0 0} ^ {\circ} \left(\mathrm{CO} _ {2}\right) - 0. 5 S _ {1 0 0 0} ^ {\circ} \left(\mathrm{O} _ {2}\right) - S _ {1 0 0 0} ^ {\circ} (\mathrm{CO}) \\ = 6 4. 3 4 2 - 0. 5 \times 5 8. 1 9 0 - 5 6. 0 3 1 = - 2 0. 7 8 4 \mathrm{cal} / \mathrm{K}. \tag {66} \\ \end{array}
$$

Then the substitution of the values for $\Delta H\mathring{1}000$ and $\Delta S\mathring{1}000$ in equation 57 gives

$$
\Delta G _ {1 0 0 0} ^ {\circ} = - 6 7 5 4 6 + 1 0 0 0 \times 2 0. 7 8 4 = - 4 6 7 6 2. \tag {67}
$$

This method simply illustrates the interconsistency of various data in the tables, and it is not meant to be a recommended calculational procedure.

# Interpolation of Tabulated Data

Published compilations of thermodynamic data are usually presented in 100 K intervals. In this publication, the chosen intervals are 200 K, except when the

tables are short enough to justify 100 K. The reason for this procedure is to maintain this publication in a manageable size without sacrificing accuracy. The values halfway within each 200 K interval can be obtained by a simple and accurate procedure called the Lagrangian interpolation. If A, B, and C are the successive values of a tabulated thermodynamic property, then the interpolated value D at the average of the temperatures for A and B is

$$
D = 0. 3 7 5 A + 0. 7 5 B - 0. 1 2 5 C. \tag {68}
$$

This equation is exact when the curve passing through A, B, and C is parabolic. However, when the interval between A and C is small, the curve is very closely approximated by a parabolic section.

Example 8: Calculate the value of $H_{1100}^{\circ}-H_{298}^{\circ}$ for $CO_{2}(g)$ from $H_{1000}^{\circ}-H_{298}^{\circ}=7984=A$ , $H_{1200}^{\circ}-H_{298}^{\circ}=10632=B$ , and $H_{1400}^{\circ}-H_{298}^{\circ}=13362=C$ . Repeat the same type of calculation for $S_{1100}^{\circ}$ .

Solution: Substitution of the foregoing values in equation 68 yields

$$
\begin{array}{l} \mathrm{H} _ {1 1 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ} = \mathrm{D} = 0. 3 7 5 \times 7 9 8 4 + 0. 7 5 \times 1 0 6 3 2 \\ - 0. 1 2 5 \times 1 3 3 6 2 = 9 2 9 8. \tag {69} \\ \end{array}
$$

The value listed by Pankratz, based on a polynomial equation, is 9296, which is very nearly identical with the result in equation 69. The simple linear interpolation between 1000 and 1200 K gives 9308, which is off by only 0.13 pct and is adequate for most practical purposes.

The same type of interpolation for the entropy at 1100 K yields $S_{1100}^{\circ} = 65.587$ , versus Pankratz' tabulated value, $S_{1100}^{\circ} = 65.592$ .

The interpolations are much simpler when $C_{p}^{\circ}$ is independent of temperature, as for Al(1) and Mg(g). For example, $C_{p}^{\circ} = 7.590$ for Al(1); therefore, a linear interpolation for $H_{T}^{\circ} - H_{298}^{\circ}$ is exact even for larger temperature intervals. For example, $H_{1400}^{\circ} - H_{298}^{\circ}$ is the average of the corresponding values at 1200 and 1600 K; i.e., $H_{1400}^{\circ} - H_{298}^{\circ} = 0.5(8913 + 11949) = 10431$ . For $S_{T}^{\circ}$ , it is simpler to use a

slightly different form of equation 48, with the lower integration limit as the lower temperature from which the extrapolation is to be made. Assuming that $S_{1000}^{\circ}=17.589$ is known for $\mathrm{Al}(1)$ , it is possible to compute $S_{T}^{\circ}$ for $\mathrm{Al}(1)$ at any temperature using the preceding value of $C_{p}^{\circ}=7.590$ :

$$
S _ {T} ^ {\circ} = S _ {1 0 0 0} ^ {\circ} + \int_ {0 0 0} ^ {T} 7. 5 9 0 \frac {d T}{T} = S _ {1 0 0 0} ^ {\circ} + 7. 5 9 0 \ln \frac {T}{1 0 0 0}. \tag {70}
$$

For T = 1400, this equation gives

$$
\mathrm{S} _ {1 4 0 0} ^ {\prime} = 1 7. 5 8 9 + 7. 5 9 0 \ln \frac {1 4 0 0}{1 0 0 0} = 2 0. 1 4 3, \tag {71}
$$

which is the same as the tabulated value. In this case, equation 68 may again be used for the intermediate range between two successive listed temperatures, but equation 70 is simpler and preferable.

After the calculation of an intervening value by a suitable preceding method, linear interpolations of thermodynamic properties can be made for the temperatures that are fractions of 100 K. This is the recommended procedure in other compilations that use 100 K intervals.

# Extrapolation of Tabulated Data Above Listed Temperatures

The data in this compilation are generally listed up to the highest practical temperatures justified by the accuracy of data. Extrapolation above the highest temperature can be carried out by using a power series, linear, or quadratic, or cubic in T. It can be shown that by using an equation for $C_{p}^{\circ}$ of $CO_{2}$ linear in T, the values of $C_{p}^{\circ}$ can be extrapolated to 4000 K within 0.7 pct, and $H_{4000}^{\circ} - H_{298}^{\circ}$ , within 0.07 pct. Considerably better results can be obtained by using higher order equations, but this is rarely necessary. Note that $\Delta Hf^{\circ}$ varies at a slower fractional rate than $H_{T}^{\circ} - H_{298}^{\circ}$ . A linear variation of $\Delta Hf^{\circ}$ can be used to obtain its values at temperatures up to 4000 K. For example, the listed data at 2800 and 3000 K for $CO_{2}$ can be used to obtain $\Delta Hf_{4000}^{\circ}$ as follows:

$$
\begin{array}{l} \Delta \mathrm{Hf} _ {4 0 0 0} ^ {\circ} = \Delta \mathrm{Hf} _ {3 0 0 0} ^ {\circ} + \left(\frac {4 0 0 0 - 3 0 0 0}{2 0 0}\right) \left(\Delta \mathrm{H} _ {3 0 0 0} ^ {\circ} - \Delta \mathrm{H} _ {2 8 0 0} ^ {\circ}\right) = - 9 5 6 2 4 \\ + 5 (- 9 5 6 2 4 + 9 5 4 2 9) = - 9 6 5 9 9, \tag {72} \\ \end{array}
$$

which agrees within 0.35 pct of the tabulated value in the JANAF Thermochemical Tables (2). For $\Delta G^{\circ}$ , the variation is even smaller and either linear extrapolation or the data for 3000 K can be used as follows: $\Delta Gf_{3000}^{\circ} = -94523$ , $\Delta Hf_{3000}^{\circ} = -95624$ ; substitution in $\Delta Gf_{3000}^{\circ} = \Delta Hf_{3000}^{\circ} - 3000\Delta Sf_{3000}^{\circ}$ yields $\Delta Sf_{3000}^{\circ} = -0.367$ . Assuming that $\Delta Hf_{3000}^{\circ}$ and $\Delta Sf_{3000}^{\circ}$ do not vary greatly with temperature, $\Delta Gf_{T}^{\circ}$ can be expressed as a linear function of temperature as follows:

$$
\Delta \mathrm{Gf} ^ {\circ} = \Delta \mathrm{Hf} _ {3 0 0 0} ^ {\circ} - \mathrm{T} \Delta \mathrm{Sf} _ {3 0 0 0} ^ {\circ} = - 9 5 6 2 4 + 0. 3 6 7 \mathrm{T}. \tag {73}
$$

At 4000 K, $\Delta Gf^{\circ} = -94156$ , which differs only 0.09 pct from the value listed in JANAF. Actually $\Delta Hf^{\circ}$ and $\Delta Sf^{\circ}$ do vary to a small extent with temperature, but their variation is such that the errors very often cancel out considerably in equation 73, and $\Delta Gf^{\circ}$ calculated in this way becomes fairly accurate, as mentioned earlier. Most pyrometallurgical processes take place below 2000 K, and most sets of data are likely to be in error well in excess of 1 pct above 3000 K; therefore, extrapolation above 3000 K with more polynomials in T is seldom needed.

# The Second Law Method for Calculation of $\Delta H^{\circ}$ and $\Delta S^{\circ}$

The second law method for calculation of $\Delta H_{T}^{\circ}$ and $\Delta S_{T}^{\circ}$ and their extrapolation to 298.15 K is based on the linearity of $\ln K_{p}$ versus (1/T) within a short range of temperature because of the following relationship:

$$
- \frac {\Delta G ^ {\circ}}{R T} = 1 n K _ {p} = - \frac {\Delta H ^ {\circ}}{R T} + \frac {\Delta S ^ {\circ}}{R}. \tag {74}
$$

(Instead of $\ln K_{p}$ versus $(1/T)$ , a plot of $\Delta G_{T}^{\circ} = -RT\ln K_{p}$ versus T also yields a straight line.) The resulting linear relationship determines the values of $\Delta H_{T}^{\circ}$ , and $\Delta S_{T}^{\circ}$ , where $T'$ is an appropriate average for all the data points. The values of $\Delta H_{298}^{\circ}$ and $\Delta S_{298}^{\circ}$ can be calculated from the following identities:

$$
\Delta \left(\mathrm{H} _ {\mathrm{T}} ^ {\circ}, - \mathrm{H} _ {2 9 8} ^ {\circ}\right) \equiv \Delta \mathrm{H} _ {\mathrm{T}} ^ {\circ}, - \Delta \mathrm{H} _ {2 9 8} ^ {\circ}; \quad \Delta \left(\mathrm{S} _ {\mathrm{T}} ^ {\circ}, - \mathrm{S} _ {2 9 8} ^ {\circ}\right) \equiv \Delta \mathrm{S} _ {\mathrm{T}} ^ {\circ}, - \Delta \mathrm{S} _ {2 9 8} ^ {\circ}, \tag {75}
$$

where $\Delta$ refers to the reaction for which $K_{p}$ has been measured, and $\Delta$ implicitly accounts for the stoichiometric coefficients as shown in the succeeding example. The use of $\Delta$ in this way is not explicit, but the practice of using $\Delta$ in writing equation 75, and a similar equation for the Gibbs energy function, is universal.

Example 9: Assume that the molar ratios of CO:CO₂ over WO₂ are determined at selected temperatures at a total CO + CO₂ pressure of 1 atm as follows:

<table><tr><td> $CO/CO_{2}$ :</td><td>2.878</td><td>2.663</td><td>2.370</td></tr><tr><td>T, K:</td><td>911</td><td>990</td><td>1121</td></tr></table>

In actual experiments, considerably more than three sets of measurements are made; however, for simplicity, only three sets of measurements are used here to illustrate the general procedure. The reaction investigated and its equilibrium constant are as follows:

$$
\mathrm{W} (\mathrm{c}) + 2 \mathrm{CO} _ {2} (\mathrm{g}) = \mathrm{WO} _ {2} (\mathrm{c}) + 2 \mathrm{CO} (\mathrm{g}); \quad \mathrm{K} _ {\mathrm{p}} = \left[ \frac {\mathrm{P} (\mathrm{CO})}{\mathrm{P} \left(\mathrm{CO} _ {2}\right)} \right] ^ {2}. \tag {76}
$$

A least squares plot of $\ln K_{p}$ versus 1/T (not shown here) gives

$$
\ln K _ {p} = \frac {1 8 8 7}{T} + 0. 0 4 6 8. \tag {77}
$$

The corresponding equation for $\Delta G^{\circ}$ is

$$
\Delta G ^ {\circ} = - 3 7 5 0 - 0. 0 9 3 \mathrm{T}, \tag {78}
$$

which is valid within the experimental range of temperature. The average of all values for $(1/T)$ is 0.00100, which corresponds to 1000 K; therefore,

$$
\Delta H _ {1 0 0 0} ^ {\circ} = - 3 7 5 0; \quad \Delta S _ {1 0 0 0} ^ {\circ} = 0. 0 9 3. \tag {79}
$$

Note that the average of T is 1007 K, and in this simplified example it is not greatly different from the average of 1/T; however, this is generally not the case when there are numerous values of $K_{p}$ over a wider range of temperature. The value of $\Delta(H_{T}^{\circ}, -H_{298}^{\circ})$ in equation 75 for reaction 76 is

$$
\begin{array}{l} \Delta \left(\mathrm{H} _ {1 0 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) = \left(\mathrm{H} _ {1 0 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) \left(\mathrm{WO} _ {2}\right) + 2 \left(\mathrm{H} _ {1 0 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\mathrm{CO}) - \left(\mathrm{H} _ {1 0 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) (\mathrm{W}) \\ - 2 \left(\mathrm{H} _ {1 0 0 0} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}\right) \left(\mathrm{CO} _ {2}\right) = 1 1 8 9 5 + 2 \times 5 1 8 3 - 4 3 7 0 - 2 \times 7 9 8 4 \\ = 1 9 2 3, \tag {80} \\ \end{array}
$$

or, simply,

$$
\Delta \left(\mathrm{H} \text {I} 0 0 0 - \mathrm{H} 2 9 8\right) \equiv \Delta \mathrm{H} \text {I} 0 0 0 - \Delta \mathrm{H} 2 9 8 \equiv - 3 7 5 0 - \Delta \mathrm{H} 2 9 8 = 1 9 2 3, \tag {81}
$$

where $\Delta H_{000}^{\circ} = -3750$ is from equation 79. Equation 81 yields $\Delta H_{298}^{\circ} = -5673$ , which is called the second law value of $\Delta H_{298}^{\circ}$ for reaction 76. Combination of this $\Delta H_{298}^{\circ}$ and $\Delta H_{298}^{\circ}$ for the following reaction

$$
2 \mathrm{CO} (\mathrm{g}) + 0 _ {2} (\mathrm{g}) = 2 \mathrm{CO} _ {2} (\mathrm{g}); \quad \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = - 1 3 5 2 6 8 \tag {82}
$$

yields the standard enthalpy of formation of $WO_{2}(c)$ ; i.e.,

$$
W (c) + O _ {2} (g) = W O _ {2} (c); \quad \Delta H f _ {2 9 8} ^ {8} = - 1 4 0 9 4 1. \tag {83}
$$

The same procedure can be used for $\Delta(S_{1000}^{\circ} - S_{298}^{\circ})$ to obtain $\Delta S_{298}^{\circ}$ for reaction 76 by the second law method. Each term $S_{1000}^{\circ} - S_{298}^{\circ}$ in $\Delta(S_{1000}^{\circ} - S_{298}^{\circ})$ can be calculated when $C_p^{\circ}$ and the enthalpies of transitions in the range of 298.15 to 1000 K are known for each species, but some or all of the $S_{298}^{\circ}$ values may not be known. We assume that the required $C_p^{\circ}$ values for all the species in reaction 76 are known, so that $\Delta C_p^{\circ}$ is closely represented by

$$
\Delta C _ {p} ^ {\circ} = 4. 2 0 - 0. 0 0 2 2 2 \mathrm{T}. \tag {84}
$$

There is no phase transition for the reactants and the products in reaction 76; therefore, for this simple example,

$$
\begin{array}{l} \Delta \left(S _ {1 0 0 0} ^ {\circ} - S _ {2 9 8} ^ {\circ}\right) \equiv \Delta S _ {1 0 0 0} ^ {\circ} - \Delta S _ {2 9 8} ^ {\circ} = \int_ {2 9 8} ^ {1 0 0 0} \frac {\Delta^ {C _ {p} ^ {\circ}}}{T} d T \\ = 4. 2 0 \ln \frac {1 0 0 0}{2 9 8 . 1 5} - 0. 0 0 2 2 2 (1 0 0 0 - 2 9 8. 1 5) = 3. 5 2 5. \tag {85} \\ \end{array}
$$

The substitution of $\Delta S_{1000}^{\circ} = 0.093$ from equation 79 in the preceding equation yields

$$
0. 0 9 3 - \Delta S _ {2 9 8} ^ {8} = 3. 5 2 5; \tag {86}
$$

hence, $\Delta S_{298}^{\circ} = -3.432$ , and this value is called the second law value of $\Delta S_{298}^{\circ}$ for reaction 76. If, for example, the value of $S_{298}^{\circ}$ for $\mathrm{WO}_2$ is not known from the measurements of $C_p^{\circ}$ from 0 to 298 K, but the values of $S_{298}^{\circ}$ for the remaining species are known, i.e., $S_{298}^{\circ}(\mathrm{CO}) = 47.217$ , $S_{298}^{\circ}(\mathrm{W}) = 7.800$ , $S_{298}^{\circ}(\mathrm{CO}_2) = 51.070$ , then it is possible to calculate $S_{298}^{\circ}(\mathrm{WO}_2)$ by the second law method. The required relationship is

$$
\begin{array}{l} \Delta S _ {2 9 8} ^ {\circ} \equiv - 3. 4 3 2 \equiv S _ {2 9 8} ^ {\circ} \left(W O _ {2}\right) + 2 S _ {2 9 8} ^ {\circ} (C O) - S _ {2 9 8} ^ {\circ} (W) - 2 S _ {2 9 8} ^ {\circ} \left(C O _ {2}\right) \\ = S _ {2 9 8} ^ {\circ} \left(W O _ {2}\right) + 2 x 4 7. 2 1 7 - 7. 8 0 0 - 2 x 5 1. 0 7 0. \tag {87} \\ \end{array}
$$

This equation yields $S_{298}^{\circ}(WO_{2}) = 12.074$ , which is the second law value of the $S_{298}^{\circ}(WO_{2})$ . The third law value of $S_{298}^{\circ}(WO_{2})$ is actually known from the measurements of $C_{p}^{\circ}$ from near 0 K to about 300 K, followed by the integration of $(C_{p}^{\circ}/T)dT$ from 0 to 298.15 K; the result is $S_{298}^{\circ}(WO_{2}) = 12.08$ , in excellent agreement with the second law value. Reconciliation of an occasionally large difference between the second and third law values of $S_{298}^{\circ}$ for an element or a compound is very important in selecting the correct value for $S_{298}^{\circ}$ .

Gibbs Energy Function and the Third Law Method for $\Delta H_{298}^{\circ}$

The Gibbs energy function (Gef) is defined by

$$
\mathrm{Gef} \equiv \frac {\mathrm{G} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}}{\mathrm{T}}. \tag {88}
$$

It should be emphasized here that the term "Gibbs energy function" is not used in referring to G, $G^{\circ}$ , $\Delta G$ , and $\Delta G^{\circ}$ . Substitution of $G_{T}^{\circ} = H_{T}^{\circ} - TS_{T}^{\circ}$ in the numerator of equation 88 gives $[-TS_{T}^{\circ} + (H_{T}^{\circ} - H_{298}^{\circ})]$ ; hence, equation 88 becomes

$$
\mathrm{Gef} = - \mathrm{S} _ {\mathrm{T}} ^ {\circ} + \frac {\mathrm{H} _ {\mathrm{T}} ^ {\circ} - \mathrm{H} _ {2 9 8} ^ {\circ}}{\mathrm{T}}. \tag {89}
$$

Therefore, Gef is obtained from the tables of this publication by

$$
\text { Gef } = - (\text { third   column }) + [ (\text { fourth   column }) \times 1 0 0 0 / \mathrm{T} ]. \tag {90}
$$

The values of Gef are therefore in calorie per mol per kelvin, as for $C_{p}^{\circ}$ and $S^{\circ}$ . When T is set to 298.15, the second term in equation 89 becomes zero and Gef = $-S_{298}^{\circ}$ . The Gibbs energy function is often tabulated in other compilations, but it is omitted in this publication to save space because it can be obtained by using equation 90. This function is useful for two reasons: (1) Its value for a reaction varies slowly with temperature, and (2) it is very useful and convenient in calculation of $\Delta H_{298}^{\circ}$ when the data for $\Delta G_{T}^{\circ}$ or $K_{p}$ at high temperatures are available.

Calculation of $\Delta H_{298}^{\circ}$ from high-temperature data for $\Delta G_{\mathrm{T}}^{\circ}$ or for $K_{\mathrm{p}}$ , by using Gef, is called the third law method, which is based on the following identity derived from equation 88:

$$
\Delta \text { G   e   f } \equiv \frac {\Delta \mathrm{G} _ {\mathrm{T}} ^ {\circ}}{\mathrm{T}} - \frac {\Delta \mathrm{H} _ {2 9 8} ^ {\circ}}{\mathrm{T}}. \tag {91}
$$

The symbol $\Delta$ refers to the reaction to which $\Delta G_{\mathrm{T}}^{\circ}$ refers and accounts for the stoichiometric coefficients. For each experimental value of $\Delta G_{\mathrm{T}}^{\circ}$ , one value of $\Delta H_{298}^{\circ}$ is calculated, and then all the values of $\Delta H_{298}^{\circ}$ are arithmetically averaged to obtain the final value of $\Delta H_{298}^{\circ}$ .

Example 10: The values of Gef and $\Delta$ Gef in calorie per indicated mols per kelvin for reaction 76 at 800, 1000, and 1200 K are as follows:

<table><tr><td rowspan="2">T, K</td><td colspan="4">Gef</td><td rowspan="2">ΔGef for reaction 76</td></tr><tr><td>W(c)</td><td>2CO2(g)</td><td>WO2(c)</td><td>2CO(g)</td></tr><tr><td>800</td><td>-9.965</td><td>-109.408</td><td>-17.602</td><td>-99.524</td><td>2.247</td></tr><tr><td>1000</td><td>-10.890</td><td>-112.716</td><td>-20.080</td><td>-101.696</td><td>1.830</td></tr><tr><td>1200</td><td>-11.722</td><td>-115.788</td><td>-22.356</td><td>-103.674</td><td>1.480</td></tr></table>

For example, at 800 K for W(c), $S_{800}^{0} = 13.820$ and $H_{800}^{0} - H_{298}^{0} = 3084$ , and substitution of these values in equation 89 yields Gef = -13.820 + (3084/800) = -9.965. This value is listed in the table. Likewise, at 800 K for $CO_{2}(g)$ , $S_{800}^{0} = 61.520$ and $H_{800}^{0} - H_{298}^{0} = 5453$ , and substitution of these values in equation 89 yields Gef

= -61.520 + (5453/800) = -54.704; however, for 2 mols of $CO_{2}$ , Gef is -109.408, as listed in the table. The values of $\Delta$ Gef for reaction 76 are listed in the last column.

Example 11: Calculate the values of $\Delta H_{298}^{\circ}$ for reaction 76 by the third law method from the values of $K_{p}$ at 911, 990, and 1121 K in example 9.

Solution: The values of $\Delta$ Gef vary slowly with temperature as shown in the last column of the table in the preceding example; hence, linear interpolations to 911, 990, and 1121 K are quite satisfactory. For example, $\Delta$ Gef at 911 K is 2.247 + [(911 - 800)/200](1.830 - 2.247) = 2.016. The results for $\Delta$ Gef in calories per kelvin and $\Delta$ H298 in calories, both for reaction 76 as written, are summarized in the following table:

<table><tr><td>T, K</td><td>CO:CO2</td><td>Kp</td><td>ΔGef, cal/K</td><td>ΔH298, cal</td></tr><tr><td>911</td><td>2.878</td><td>8.283</td><td>2.016</td><td>-5664</td></tr><tr><td>990</td><td>2.663</td><td>7.092</td><td>1.851</td><td>-5686</td></tr><tr><td>1121</td><td>2.371</td><td>5.622</td><td>1.618</td><td>-5660</td></tr></table>

Average = -5670

For example, the calculation for 911 K, with $\Delta G_{T}^{\circ}/T = -R\ln K = -4.2013$ and with equation 91 results in

$$
\Delta \text {Gef} = 2. 0 1 6 = - 4. 2 0 1 3 - \frac {\Delta \mathrm{H} _ {2 9 8} ^ {\circ}}{9 1 1}; \quad \Delta \mathrm{H} _ {2 9 8} ^ {\circ} = - 5 6 6 4 \mathrm{cal}. \tag {92}
$$

The average value, $\Delta H_{298}^{\circ} = -5670$ cal, is nearly identical with $\Delta H_{298}^{\circ} = -5673$ cal, obtained by the second law method. Note that while a single value of $K_p$ yields one third law value for $\Delta H_{298}^{\circ}$ , at least two values of $K_p$ are necessary for the second law method for the linear correlation required by equation 77 and for the calculation of $\Delta H_{298}^{\circ}$ . The third law method must be preferred over the second law method when reliable values of $\Delta$ Gef are available.

# Complex Equilibria

Calculations of the equilibrium concentrations of the species in one reaction, formed from the given amounts of initial species, can be carried out easily for one

reaction. When there are two or more simultaneous reactions, the calculations become complex and often necessitate a computer $(4, 7)$ . The procedure requires the mass balance or atomic balance and the equilibrium constants to solve for all the species formed from the starting species after the attainment of equilibrium. As a simple example, assume that 2 mols of graphite and 1 mol of $H_{2}O$ at 1200 K and 1 atm are injected into a closed chamber to form CO, $H_{2}$ , and $CO_{2}$ at 1200 K and 1 atm. The required independent reactions and their equilibrium constants are as follows:

(I): C(gr) + H₂O(g) = CO(g) + H₂(g); Kₚ(I) = 38.06, (93)

(v) (w) (x) (y)

(II): $\mathrm{CO(g) + H_2O(g) = CO_2(g) + H_2(g)}$ $\mathrm{K_p(II) = 0.7335}$ (94)

(x) (w) (z) (y)

where the symbol in parentheses under each reactant or product is the mols at equilibrium. The standard Gibbs energy change $\Delta G^{\circ}(I)$ for the first reaction is obtained by writing $\Delta Gf^{\circ}(I) = \Delta Gf^{\circ}(CO) - \Delta Gf^{\circ}(H_2O) = -52049(CO) + 43371(H_2O) = -8678$ cal. Similarly, for the second reaction, $\Delta G^{\circ}(II) = -94681(CO_2) + 43371(H_2O) + 52049(CO) = 739$ cal. From $\Delta G^{\circ}(I) = -1200R\ln K_p(I) = -8678$ cal, $K_p(I)$ is 38.06, and likewise, from $\Delta G^{\circ}(II)$ , $K_p(II)$ is 0.7335. There are five species, and the number of mols of each species after the attainment of equilibrium is written in parentheses under each formula. The material balance for carbon, hydrogen, and oxygen yields

$$
2 = \mathrm{v} + \mathrm{x} + \mathrm{z}, (\text {carbon}); 2 = 2 \mathrm{w} + 2 \mathrm{y}, (\text {hydrogen}); 1 = \mathrm{w} + \mathrm{x} + 2 \mathrm{z}, (\text {oxygen}), \tag {95}
$$

where the left side in each of the three equations is the initial number of atoms of each element prior to the occurrence of either reaction, and the right side, the number of atoms of each element in all the species after both reactions attain equilibrium. These three equations for mass balance and the two equilibrium constants are sufficient to solve for the five unknowns. From v = 2 - x - z, w = 1 - y, and 2z = 1 - x - w = y - x, the variables v, w, and z are eliminated, and the reactions are rewritten as

(I): C + $H_{2}O$ = CO + $H_{2}$ , (96)

$(2 - \frac{x}{2} - \frac{y}{2})$ (1 - y) (x) (y)

$$
\text {(II)}: \quad \mathrm{CO} + \mathrm{H} _ {2} \mathrm{O} = \mathrm{CO} _ {2} + \mathrm{H} _ {2}. \tag {97}
$$

$$
(\mathrm{x}) \quad (1 - \mathrm{y}) \quad (\mathrm{y} - \mathrm{x}) / 2 \quad (\mathrm{y})
$$

The total number of mols of gaseous species is

$$
1 - \mathrm{y} + \mathrm{x} + \mathrm{y} + (\mathrm{y} - \mathrm{x}) / 2 = 1 + (\mathrm{x} + \mathrm{y}) / 2. \tag {98}
$$

The partial pressure of each species is the same as its mol fraction because the total pressure is 1 atm; hence, the equilibrium constants are expressed by

$$
\mathrm{K} _ {\mathrm{p}} (\mathrm{I}) = \frac {\mathrm{xy}}{(1 - \mathrm{y}) [ 1 + (\mathrm{x} + \mathrm{y}) / 2 ]} = 3 8. 0 6, \tag {99}
$$

$$
\mathrm{K} _ {\mathrm{p}} (\mathrm{II}) = \frac {\mathrm{y} (\mathrm{y} - \mathrm{x}) / 2}{\mathrm{x} (1 - \mathrm{y})} = 0. 7 3 3 5. \tag {100}
$$

Simplification and rearrangement of these equations give

$$
\mathrm{y} ^ {2} + 1. 0 5 2 5 5 \mathrm{xy} + \mathrm{y} - \mathrm{x} - 2 = 0; \quad \mathrm{y} ^ {2} + 0. 4 6 7 \mathrm{xy} - 1. 4 6 7 \mathrm{x} = 0. \tag {101}
$$

The second equation yields

$$
\mathrm{x} = \mathrm{y} ^ {2} / (1. 4 6 7 - 0. 4 6 7 \mathrm{y}). \tag {102}
$$

Substitution of this equation in the first equation in 101 gives

$$
\mathrm{y} ^ {3} + 4. 1 0 0 4 \mathrm{y} - 5. 0 1 0 7 = 0. \tag {103}
$$

This equation can be solved easily by successive approximation because inspection shows that y is very close to unity, and in fact for y = 1, the left side is 0.0897 instead of zero, and for y = 0.98, -0.0511. Likewise, for y = 0.990 and 0.985, the left side is +0.0190 and -0.0161, respectively, and the linear interpolation between these values yields y = 0.9873 to four decimal places. The value of x from the value of y substituted in equation 102 is 0.9690, and the results for all the species in mols are as follows:

$$
\mathrm{C}: \mathrm{v} = 1. 0 2 1 8; \quad \mathrm{H} _ {2} \mathrm{O}: \mathrm{w} = 0. 0 1 2 7; \quad \mathrm{CO}: \mathrm{x} = 0. 9 6 9 0;
$$

$$
\mathrm{H} _ {2}: \mathrm{y} = 0. 9 8 7 3; \quad \mathrm{CO} _ {2}: \mathrm{z} = 0. 0 0 9 2.
$$

In complex reactions, it is necessary to have special computer programs (4, 7) for calculating the number of mols of all species; such tasks become increasingly complex at high temperatures largely because of the occurrence of many simultaneous reactions involving many species.

# Additional Examples and Practical Applications

The examples presented in the preceding sections should be adequate for effectively using the tables of this compilation. Additional examples and practical applications in this section, mostly taken or adapted from references 1 and 7, are intended to give the reader further confidence in using and applying the tabular data in various areas.

Example 12: Express the standard enthalpy of liquid zinc and gaseous zinc, both relative to the standard enthalpy of solid zinc, from the following data:

$$
\begin{array}{l} C _ {p} ^ {\circ} (c) = 5. 3 5 + 0. 0 0 2 4 T, \\ \Delta \mathrm{H} ^ {\circ} (\text { melt. }) = 1 7 5 0 \text { cal / mol   at } 6 9 2. 7 3 \mathrm{K} (\text { melting   point }), \\ \mathrm{C} _ {\mathrm{p}} ^ {\circ} (1) = 7. 5 0, \\ \Delta H ^ {\circ} (\text { v   a   p   . }) = 2 7 5 6 5 \text { c   a   l   /   m   o   l   a   t } 1 1 8 0 \mathrm{K} (\text { b   o   i   l   i   n   g   p   o   i   n   t }), \\ C _ {p} ^ {\circ} (g) = 4. 9 7. \\ \end{array}
$$

$C_{p}(c)$ is obtained by using the tabulated values at 298.15 and 600 K, and rounding off the terms as indicated; $C_{p}^{\circ}(g)$ is rounded off to two decimal places.

Solution: The substitution of the foregoing values in equation 11 for the liquid yields:

$$
\begin{array}{l} \mathrm{H} _ {\mathrm{T}} ^ {\circ} (1) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = \int_ {2 9 8} ^ {6 9 2. 7 3} \mathrm{C} _ {\mathrm{p}} ^ {\circ} (\mathrm{c}) \mathrm{dT} + \Delta \mathrm{H} ^ {\circ} (\text {melt.}) + \int_ {6 9 2. 7 3} ^ {\mathrm{T}} \mathrm{C} _ {\mathrm{p}} ^ {\circ} (1) \mathrm{dT} \\ = [ 5. 3 5 \mathrm{T} + 0. 0 0 1 2 \mathrm{T} ^ {2} ] _ {2 9 8} ^ {6 9 2 \cdot 7 3} + 1 7 5 0 + 7. 5 0 (\mathrm{T} - 6 9 2. 7 3) \\ = - 8 6 5 + 7. 5 0 \mathrm{T}. \tag {104} \\ \end{array}
$$

For the second part, we take advantage of equation 104 by writing it as

$$
\mathrm{H} _ {1 1 8 0} ^ {\circ} (1) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = - 8 6 5 + 7. 5 0 \mathrm{x} 1 1 8 0 = 7 9 8 5, \tag {105}
$$

and adding to it the following two equations:

$$
\mathrm{H} _ {1 1 8 0} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {1 1 8 0} ^ {\circ} (1) = 2 7 5 6 5 = \Delta \mathrm{H} _ {1 1 8 0} ^ {\circ} (\text {vap.}), \tag {106}
$$

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {1 1 8 0} ^ {\circ} (\mathrm{g}) = \int_ {1 1 8 0} ^ {\mathrm{T}} 4. 9 7 \mathrm{dT} = 4. 9 7 \mathrm{T} - 5 8 6 5. \tag {107}
$$

The result for the left side is $H_{T}^{\circ}(g) - H_{298}^{\circ}(c)$ , and the result for the right side is given in the following equation:

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} (\mathrm{g}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = 2 9 6 8 5 + 4. 9 7 \mathrm{T}. \tag {108}
$$

At 1300 K, this equation gives 36146, and the linear interpolation from the table for $\mathrm{Zn}(c,1,g)$ gives 36147, in nearly perfect agreement.

Example 13: What is the amount of zinc, initially at 298.15 K, to be added into 1 mol of liquid zinc at 692.73 K to obtain equal amounts of liquid and solid? Use the data for Zn(c) and Zn(1) in the preceding example.

Solution: As a basis for calculation, we take 1 mol of Zn(1) at 692.73 K, and add n mols of Zn(c) initially at 298.15 K to form 1 + n mols, and to obtain $(1 + n)/2$ mols of liquid and $(1 + n)/2$ mols of solid as required by the example. The initial amount of liquid, minus the final amount of liquid, i.e., $1 - [(1 + n)/2] = (1 - n)/2$ , is the amount of liquid solidified to raise the temperature of added solid zinc. From the preceding example,

$$
\Delta \mathrm{H} _ {6 9 2. 7 3} ^ {\circ} (\text {melt.}) = 1 7 5 0, \tag {109}
$$

$$
\mathrm{H} _ {\mathrm{T}} ^ {\circ} (\mathrm{c}) - \mathrm{H} _ {2 9 8} ^ {\circ} (\mathrm{c}) = 5. 3 5 \mathrm{T} + 0. 0 0 1 2 \mathrm{T} ^ {2} ] _ {2 9 8} ^ {6 9 2 \cdot 7 3} = 2 5 8 0. \tag {110}
$$

The resulting enthalpy balance requires that $(1 - n)/2$ mols of solidified zinc provide the enthalpy for raising the temperature of n mols of $\mathrm{Zn(c)}$ initially at 298.15 K:

$$
\left(\frac {1 - n}{2}\right) 1 7 5 0 = (n) 2 5 8 0. \tag {111}
$$

This equation gives n = 0.2533 mol, which is the answer.

Example 14: A mixture of steam and oxygen at 1000 K reacts completely with a column of graphite in a furnace also at 1000 K to yield a mixture of $H_{2}$ and CO at the same temperature. Calculate the composition of the steam-oxygen mixture assuming that the furnace temperature is not affected by the reaction; i.e., the overall reaction does not generate enthalpy.

Solution: We start with 1 mol of $H_{2}O(g)$ and x mols of $O_{2}(g)$ , both at 1000 K, and react these gases with C(gr) in the furnace as follows:

$$
\mathrm{H} _ {2} \mathrm{O} (\mathrm{g}) + \mathrm{xO} _ {2} (\mathrm{g}) + (1 + 2 \mathrm{x}) \mathrm{C} (\mathrm{gr}) = \mathrm{H} _ {2} (\mathrm{g}) + (1 + 2 \mathrm{x}) \mathrm{CO} (\mathrm{g}). \tag {112}
$$

The process does not generate enthalpy because $H_{2}O + C$ react endothermically to absorb the enthalpy generated by the exothermic reaction of $O_{2} + C$ . The reaction is assumed to go to completion; therefore, the substitution of the standard enthalpies of formation for $CO(g)$ and for $H_{2}O(g)$ , both at 1000 K, from the tables into the following enthalpy balance yields

$$
\begin{array}{l} \Delta \mathrm{H} _ {1 0 0 0} ^ {\circ} = 0 = (1 + 2 \mathrm{x}) \Delta \mathrm{Hf} _ {1 0 0 0} ^ {\circ} (\mathrm{CO}) - \Delta \mathrm{Hf} _ {1 0 0 0} ^ {\circ} \left(\mathrm{H} _ {2} \mathrm{O}\right) \\ = - (1 + 2 x) 2 6 7 6 6 + 5 9 2 4 3 = 3 2 4 7 7 - 5 3 5 3 2 x = 0. \tag {113} \\ \end{array}
$$

The value of x from this equation and the molar composition of the gas mixture are as follows:

$$
\mathrm{x} = 0. 6 0 6 7; \mathrm{H} _ {2} (\mathrm{g}): \frac {1}{1 + (1 + 2 \mathrm{x})} = 0. 3 1 1 2 \mathrm{mol} \text { fraction };
$$

$$
\mathrm{CO} (\mathrm{g}): \text {   balance,   or   } 1 - 0. 3 1 1 2 = 0. 6 8 8 8 \text {   mol   fraction.   }
$$

Example 15: Two mols of Zn(s) at 25°C are added adiabatically into 1 mol of Zn(1) at 700 K and 1 atm pressure. Calculate the entropy change from the following data: $C_{p}^{\circ}(\text{solid}) = 5.35 + 0.0024T$ , $\Delta H_{692.73}^{\circ}(\text{melt.}) = 1750$ and $C_{p}^{\circ}(1) = 7.50$ .

Solution: The process is adiabatic at constant pressure, and example 13 shows that the final temperature must be below the freezing point; therefore,

$$
\Delta H _ {P} = 0 = 2 \int_ {2 9 8} ^ {T} C _ {P} ^ {\circ} (c) d T + \int_ {7 0 0} ^ {6 9 2. 7 3} C _ {P} ^ {\circ} (1) d T - \Delta H ^ {\circ} (\text {melt.}) + \int_ {6 9 2. 7 3} ^ {T} C _ {P} ^ {\circ} (c) d T, \tag {114}
$$

where the first term is for heating 2 mols of $\mathrm{Zn(c)}$ , and the remaining terms are for cooling 1 mol of $\mathrm{Zn(1)}$ . Integration and substitution of the limits yield

$$
0 = (2 x 5. 3 5 T + 0. 0 0 2 4 T ^ {2} - 3 4 0 4) - 5 5 - (1 7 5 0) + 5. 3 5 T + 0. 0 0 1 2 T ^ {2} - 4 2 8 2. \tag {115}
$$

This equation is rearranged to obtain

$$
0. 0 0 3 6 \mathrm{T} ^ {2} + 1 6. 0 5 \mathrm{T} - 9 4 9 1 = 0. \tag {116}
$$

This quadratic equation gives T = 528.7 K as its positive root.

The process is irreversible, but since T is known, $\Delta S^{\circ}$ can be calculated by equation 49 first by heating 2 mols of $\mathrm{Zn(c)}$ to 528.7 K, and then cooling 1 mol of $\mathrm{Zn(1)}$ to 528.7 K:

$$
\begin{array}{l} \Delta S ^ {\circ} = 2 \int_ {2 9 8} ^ {5 2 8. 7} \frac {C _ {p} ^ {\circ} (c)}{T} d T + \int_ {7 0 0} ^ {6 9 2. 7 3} \frac {C _ {p} ^ {\circ} (1)}{T} d T - \frac {\Delta H ^ {\circ} (\text {melt.})}{6 9 2 . 7 3} + \int_ {6 9 2. 7 3} ^ {5 2 8. 7} \frac {C _ {p} ^ {\circ} (c)}{T} d T \\ = 1 0. 7 \ln (5 2 8. 7 / 2 9 8. 1 5) + 0. 0 0 4 8 (5 2 8. 7 - 2 9 8. 1 5) \\ + 7. 5 \ln (6 9 2. 7 3 / 7 0 0) - 1 7 5 0 / 6 9 2. 7 3 + 5. 3 5 \ln (5 2 8. 7 / 6 9 2. 7 3) \\ + 0. 0 0 2 4 (5 2 8. 7 - 6 9 2. 7 3) = 2. 7 9 2. \tag {117} \\ \end{array}
$$

Example 16: An ideal gas mixture is formed at 1 atm and 300 K by mixing two pure ideal gases, 0.25 mol Ar and 0.75 mol Ne, each at 1 atm and 300 K. Part 1: