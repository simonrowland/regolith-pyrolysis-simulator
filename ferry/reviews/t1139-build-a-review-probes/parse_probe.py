import sys
sys.path.insert(0, sys.argv[1])
from simulator.accounting.formulas import parse_formula
for f in ["AlO1.5","CuO0.5","GaO1.5","InO1.5","H2SO4.2H2O","CaSO4.2H2O","MgSO4.7H2O","CuSO4·5H2O","Na2CO3.10H2O","AlO1.5Si","Ca0.5Mg0.5SiO3","Fe0.95O","O1.5Al","Al2O3.2SiO2.2H2O","NaO0.5","Mg2SiO4.0.5H2O", "H2O.5", "FeO1.5.H2O"]:
    try:
        r=parse_formula(f); print(f"{f:22s} -> {dict(r.elements)}")
    except Exception as e:
        print(f"{f:22s} -> ERR {type(e).__name__}: {e}")
