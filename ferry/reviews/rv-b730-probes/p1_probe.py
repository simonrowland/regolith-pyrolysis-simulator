import sys, json
from simulator.accounting.formulas import parse_formula
cases=["K0.5Na1.5AlSiO4","CaSO4.0.5H2O","CaSO4·0.5H2O","Na.76K.22AlSiO4","Na0.76K0.22AlSiO4","(Na.76K.22)AlSiO4",
"Mg1.5Fe.5SiO4","Ca.5Mg.5CO3","Fe.947O","Fe.9470","W.465","CuSO4.5H2O","H2SO4.2H2O","NaCl.2H2O","SrHgO.4CO2.5H2O",
"MgSO4.7H2O","Na2B4O7.10H2O","CaCl2.2H2O","Mn.98O","Ni.947O","3CaO.Al2O3","Al2O3.2SiO2.2H2O"]
out={}
for f in cases:
    try: out[f]={k:round(float(v),6) for k,v in sorted(parse_formula(f).elements.items())}
    except Exception as e: out[f]=f"{type(e).__name__}: {e}"
print(json.dumps(out))
