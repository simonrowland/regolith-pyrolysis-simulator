import json, sys
import simulator.battery.generators.usgs_b1452 as g
p='data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json'
d=json.load(open(p)); rows=d['rows']
fws=[g._formula_weight_as_published(d,row_index=i) for i in range(len(rows))]
unc={i for i in range(1,len(rows)) if g._is_298k_uncertainty_pair(fws[i-1],fws[i])}
import bisect
def c(r,k):
    x=r['cells'].get(k) or {}
    return (x.get('as_published') or '')
for i,r in enumerate(rows):
    line=r.get('source_text_line')
    pg=max(0,bisect.bisect_right(g.TABLE_298K_PAGE_START_LINES,line)-1)+12
    print(f"{i}\tL{line}\tp{pg}\t{'U' if i in unc else '-'}\tfw={fws[i]}\t{c(r,'name_and_formula')[:45]}\tS={c(r,'entropy_s298')}\tH={c(r,'formation_enthalpy')}\tG={c(r,'formation_gibbs_energy')}\tK={c(r,'log_kf')}")
