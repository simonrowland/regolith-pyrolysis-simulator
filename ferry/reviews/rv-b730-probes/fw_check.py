import json, collections, sys
import simulator.battery.generators.usgs_b1452 as g
from simulator.battery.stable_ids import _name_slug
p='data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json'
d=json.load(open(p))
gen=g.generate_record(d)
bad=collections.defaultdict(list); n=0
names={}
for i in range(len(d['rows'])):
    nm=g._row_name(d,i)
    if nm: names.setdefault(_name_slug(nm),[]).append(i)
for o in gen.observations:
    n+=1
    f=o.identity.species.formula
    oid=o.observation_id
    slug=oid.split(':name=')[1].split(':v=')[0]
    rows=names.get(slug,[])
    fws=set(g._formula_weight_as_published(d,row_index=r) for r in rows)
    m=g._formula_mass(f)
    for fw in fws:
        fwd=g._formula_weight_decimal(fw)
        if fwd is None or m is None: continue
        if abs(m-fwd)/fwd>0.005:
            bad[(f,str(fwd),round(float(m),3),slug)].append(oid.split(':')[2])
print('observations',n)
for k,v in sorted(bad.items()): print(k, sorted(v))
