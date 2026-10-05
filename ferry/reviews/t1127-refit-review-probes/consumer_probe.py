#!/usr/bin/env python3
"""t-1127 review probe: Al/Si consequences through production entry points (run from repo root at base and head).
Reports: wall P_sat (condensation._antoine_psat_pa), Antoine dew temperature at fixed partial pressures,
Ellingham effective pressure (ellingham_graph.effective_equilibrium_pressure_Pa) and the builtin provider's
emitted melt-source pressure for a pure parent oxide."""
import sys, math, copy, warnings
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import yaml
from simulator.vapour_rail.catalog import vapor_pressure_compatibility_view
data = vapor_pressure_compatibility_view(yaml.safe_load(open("data/vapor_pressures.yaml").read()))
from simulator import condensation as cond
from simulator.chemistry import ellingham_graph as eg
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView

def safe(f):
    try: return f()
    except Exception as e: return f"{type(e).__name__}: {str(e)[:90]}"

for sp in ("Al", "Si"):
    print(f"== {sp}")
    for T in (1000.0, 1200.0, 1400.0, 1600.0, 1800.0):
        ex, w = {}, []
        p = safe(lambda: cond._antoine_psat_pa(sp, T, vapor_pressure_data=data, antoine_extrapolations=ex, antoine_extrapolation_warnings=w, enforce_hot_train_applicability=False))
        print(f"  wall P_sat T={T:.0f} K: {p if isinstance(p,str) else f'{p:.4e} Pa'}  extrapolation_records={len(ex)} warnings={len(w)}")
    for ppa in (1e-3, 1e-1, 10.0):
        d = safe(lambda: cond.antoine_dew_temperature_diagnostic(sp, ppa, vapor_pressure_data=data))
        if isinstance(d, dict):
            t = d.get("temperature_K")
            print(f"  dew T at p={ppa:g} Pa: status={d.get('status')} T={t if t is None else round(t,1)}")
        else: print(f"  dew T at p={ppa:g} Pa: {d}")
    for T in (1600.0, 1800.0, 2000.0):
        for pO2 in (1e-9, 1e-12):
            e = safe(lambda: eg.effective_equilibrium_pressure_Pa(sp, T, pO2, vapor_pressure_data=data))
            print(f"  ellingham effective P T={T:.0f} pO2={pO2:g}: {e if isinstance(e,str) else f'{e:.4e} Pa'}")
    ox = data["metals"][sp]["parent_oxide"]
    prov = BuiltinVaporPressureProvider(data)
    for T in (1600.0, 1800.0, 2000.0):
        r = safe(lambda: prov.dispatch(IntentRequest(intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(accounts={"process.cleaned_melt": {ox: 1.0}}, species_formula_registry={}),
            temperature_C=T-273.15, pressure_bar=1e-6, control_inputs={"pO2_bar": 1e-9})))
        if isinstance(r, str): print(f"  builtin provider T={T:.0f}: {r}"); continue
        vp = (r.diagnostic or {}).get("vapor_pressures_Pa", {}).get(sp)
        prov_ = (r.diagnostic or {}).get("vapor_pressure_numerator_provenance", {}).get(sp)
        rail = prov_.get("pressure_rail") if isinstance(prov_, dict) else None
        blk = prov_.get("coefficient_block") if isinstance(prov_, dict) else None
        print(f"  builtin provider {ox}=1 T={T:.0f} pO2=1e-9: status={r.status} P_{sp}={vp} rail={rail} block={blk}")
