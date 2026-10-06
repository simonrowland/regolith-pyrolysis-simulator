import json
from simulator.battery.validate import _term_composition
out={}
for f in ["Fe.9470","W.465","Fe.95O","Fe.90S","NbC.98","VC.5","MoN.5","ZrC.96","Fe.947O","Na76K22AlSiO4","K0.5Na1.5AlSiO4","CaSO4.0.5H2O","SrHgO.4CO2.5H2O","N1.5617O.41959Ar.00937C.00032"]:
    try: out[f]=_term_composition(f)
    except Exception as e: out[f]=repr(e)
print(json.dumps(out))
