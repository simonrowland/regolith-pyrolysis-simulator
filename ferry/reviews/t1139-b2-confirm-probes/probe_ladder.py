"""Probe the B2 ladder at 07dc6017c: per-parent table, basis consistency, homologue reachability."""
import math, json
from simulator.vapour_rail.activity import (
    resolve_trace_parent_activity, trace_parent_gamma_report, StandardStateIdentity,
    _gamma_rows, _select_gamma_rows, _select_banded_row, _gamma_at, _TRACE_HOMOLOGUE,
)
from simulator.chemistry.melt_activity import single_cation_mole_fractions, pure_liquid_reference_coefficient
SS = StandardStateIdentity(convention="raoultian_pure_endmember", phase="liquid", reference_pressure_bar=1.0)
print("== per-parent report")
for T in (1500.0, 1673.0, 1923.0):
    rep = trace_parent_gamma_report(T)
    for f, r in rep.items():
        print(f"{T:6.0f} {f:7s} {r['verdict']:22s} rung={r['rung']} g={r['gamma']!r:24s} flag={r['flag']} homol={r['homologue']} std_state_claim={'standard_state' in r}")
print("== homologue targets resolved on their own")
table = _gamma_rows(None)
for src, tgt in _TRACE_HOMOLOGUE.items():
    for T in (1500.0, 1673.0):
        a = resolve_trace_parent_activity(tgt, temperature_K=T, activity_exponent=1.0, standard_state=SS)
        kind, grp = _select_gamma_rows(tgt, table)
        print(f"{src}->{tgt} T={T:.0f} target rung={a.derivation.get('rung')} verdict={a.verdict.value} kind={kind} n={len(grp)} banded={[r['validity_range_K'] for r in grp]}")
print("== Cu2O / GeO2 candidate gamma envelope (published rows, ignored by selector)")
for f in ("Cu2O", "GeO2", "SiO2", "Na2O"):
    kind, grp = _select_gamma_rows(f, table)
    for T in (1500.0, 1673.0, 1923.0):
        gs = [_gamma_at(r, T) for r in grp]
        print(f"{f} T={T:.0f} kind={kind} n={len(grp)} gammas={[f'{g:.4g}' for g in gs]} min={min(gs):.4g} max={max(gs):.4g}")
print("== basis consistency at dilution: melt {In2O3:1e-6, SiO2:1}, 1923 K")
inv = {"In2O3": 1e-6, "SiO2": 1.0}
xc = single_cation_mole_fractions(inv)["In2O3"]
xmol = 1e-6 / (1 + 1e-6)
p = resolve_trace_parent_activity("In2O3", temperature_K=1923.0, activity_exponent=1.0, standard_state=SS, mole_fraction=xc)
s = resolve_trace_parent_activity("InO1.5", temperature_K=1923.0, activity_exponent=1.0, standard_state=SS, mole_fraction=xc)
print("X_cation", xc, "X_molecular(Fegley eq.)", xmol)
print("In2O3 request: gamma", p.derivation["gamma"], "a", p.value, "| Fegley-basis a=gamma*X_mol", p.derivation["gamma"]*xmol, "ratio", p.value/(p.derivation["gamma"]*xmol))
print("InO1.5 request: gamma", s.derivation["gamma"], "a", s.value, "implied a(In2O3)=a^2", s.value**2, "vs In2O3-request a", p.value, "ratio", p.value/(s.value**2))
print("pure ref: InO1.5 at X=1:", resolve_trace_parent_activity("InO1.5", temperature_K=1923.0, activity_exponent=1.0, standard_state=SS, mole_fraction=1.0).value)
for T in (1500.0, 1673.0):
    g = resolve_trace_parent_activity("GaO1.5", temperature_K=T, activity_exponent=1.0, standard_state=SS, mole_fraction=1.0)
    print("GaO1.5", T, g.derivation["gamma"], g.verdict.value, g.derivation["flag"], "parent row gamma", _gamma_at(_select_gamma_rows('Ga2O3', table)[1][0], T), "sqrt", math.sqrt(_gamma_at(_select_gamma_rows('Ga2O3', table)[1][0], T)))
print("== basalt-like melt basis factor (mol): SiO2 .749 Al2O3 .147 FeO .139 MgO .248 CaO .178 Na2O .048 In2O3 1e-7")
b = {"SiO2": .749, "Al2O3": .147, "FeO": .139, "MgO": .248, "CaO": .178, "Na2O": .048, "In2O3": 1e-7, "PbO": 1e-7}
xc = single_cation_mole_fractions(b)
tot = sum(b.values())
for k in ("In2O3", "PbO"):
    print(k, "X_cation", xc[k], "X_mol", b[k]/tot, "ratio", xc[k]/(b[k]/tot))
print("== established-basis check: rows with standard_state_as_printed None:", sum(1 for r in table if r['standard_state_as_printed'] is None), "of", len(table))
