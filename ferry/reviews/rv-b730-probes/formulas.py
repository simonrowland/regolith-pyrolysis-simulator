import sys, json
from simulator.accounting.formulas import parse_formula
cases = ["MgSO4.7H2O","Na2B4O7.10H2O","CaCl2.2H2O","Mn.98O","Ni.947O","Fe.947O","Fe0.947O","3CaO.Al2O3","3CaO·Al2O3",
 "Al2O3.2SiO2.2H2O","3Al2O3.2SiO2","CuSO4.5H2O","H2SO4.2H2O","NaCl.2H2O","SrHgO.4CO2.5H2O","5MgO.4CO2.5H2O","CaO.2Al2O3","CaO.6Al2O3",
 "CaSO4·2H2O","CaSO4.0.5H2O","CaSO4·0.5H2O","NbC.98","Fe.90S","Fe.95O","VC.5","MoN.5","ZrC.96","W.465","Fe.9470","TbO1.714","AlO1.5","CuO0.5",
 "(Na.78K.22)AlSiO4","Na.76K.22AlSiO4","Mg.9Fe.1SiO3","(Mg.9Fe.1)SiO3","Mg1.5Fe.5SiO4","K0.5Na1.5AlSiO4","Ca.5Mg.5CO3","Fe.5Mg.5O",
 "Mg1.8Fe0.2SiO4","Ba0.543Sr0.457TiO3","Ni0.4Zn0.6Fe2O4","N1.5617O.41959Ar.00937C.00032","Na2O.Al2O3.6SiO2","K2O.Al2O3.6SiO2","FeO.Fe2O3",
 "MgO.SiO2","Fe.947O(cr)","Fe.947O(l)","2CaO.SiO2","CaO.SiO2","Na2CO3.H2O","Na2CO3.10H2O","CaCO3.H2O","Fe2O3.H2O","Al2O3.H2O","UO2.5","O2.5U","H2O.5"]
out={}
for f in cases:
    try:
        e=parse_formula(f).elements
        out[f]={k:round(float(v),6) for k,v in sorted(e.items())}
    except Exception as ex:
        out[f]=f"{type(ex).__name__}: {ex}"
json.dump(out,sys.stdout,indent=0,ensure_ascii=False)
